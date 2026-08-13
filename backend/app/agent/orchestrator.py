from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from app.llm.factory import get_llm
from app.models.schemas import (
    AccessLevel,
    ChatMessage,
    ChatResponse,
    TemplateMeta,
)
from app.rag.retriever import HybridRetriever
from app.services.catalog import AccessControl, CatalogService
from app.services.doc_generator import DocumentGenerator
from app.services.onedrive import OneDriveService


@dataclass
class SessionState:
    session_id: str
    username: str
    stage: str = "idle"  # idle | clarifying | ready | done
    template: Optional[TemplateMeta] = None
    questions: list[str] = field(default_factory=list)
    answers: dict[str, str] = field(default_factory=dict)
    messages: list[ChatMessage] = field(default_factory=list)
    pending_question_idx: int = 0
    graph_token: Optional[str] = None


class AgentOrchestrator:
    """
    Semantic Kernel-style multi-step agent:
    intent → retrieve template → ask context questions → auto-fill → generate doc.
    Also supports OneDrive Graph search, ACL checks, and version timelines.
    """

    def __init__(
        self,
        catalog: CatalogService,
        retriever: HybridRetriever,
        acl: AccessControl,
        generator: DocumentGenerator,
        onedrive: Optional[OneDriveService] = None,
    ):
        self.catalog = catalog
        self.retriever = retriever
        self.acl = acl
        self.generator = generator
        self.onedrive = onedrive or OneDriveService()
        self.llm = get_llm()
        self.sessions: dict[str, SessionState] = {}

    def get_or_create(self, session_id: Optional[str], username: str) -> SessionState:
        if session_id and session_id in self.sessions:
            state = self.sessions[session_id]
            state.username = username
            return state
        sid = session_id or str(uuid.uuid4())
        state = SessionState(session_id=sid, username=username)
        self.sessions[sid] = state
        return state

    async def handle(
        self,
        message: str,
        session_id: Optional[str],
        username: str,
        graph_token: Optional[str] = None,
    ) -> ChatResponse:
        state = self.get_or_create(session_id, username)
        if graph_token:
            state.graph_token = graph_token
        state.messages.append(ChatMessage(role="user", content=message))

        # Continue clarification loop if mid-flow
        if state.stage == "clarifying" and state.template:
            return await self._continue_clarification(state, message)

        intent_data = await self.llm.json_complete(
            system=(
                "Classify user intent for a template hub with OneDrive. "
                "Return JSON: {intent: search|create|version_history|onedrive_search|"
                "onedrive_versions|onedrive_access, keywords: string[], confidence: number}"
            ),
            user=message,
        )
        intent = (intent_data.get("intent") or "search").lower()

        if intent == "onedrive_search":
            return await self._onedrive_search(state, message)
        if intent == "onedrive_versions":
            return await self._onedrive_versions(state, message)
        if intent == "onedrive_access":
            return await self._onedrive_access(state, message)
        if intent == "version_history":
            return await self._version_history(state, message)
        if intent == "create":
            return await self._start_create(state, message)
        return await self._search(state, message)

    def _drive_user(self, state: SessionState) -> str:
        # Map demo roles to mock OneDrive identities
        mapping = {
            "consultant": "demo.user@ymsli.com",
            "approver": "demo.user@ymsli.com",
            "joiner": "joiner@ymsli.com",
        }
        return mapping.get(state.username, state.username if "@" in state.username else "demo.user@ymsli.com")

    async def _onedrive_search(self, state: SessionState, message: str) -> ChatResponse:
        token = state.graph_token or "mock-token"
        hint = self._drive_user(state)
        try:
            items = await self.onedrive.search(token, message, username_hint=hint)
        except Exception as exc:
            return self._respond(state, f"OneDrive search failed: {exc}", "idle")
        if not items:
            # fallback list Templates
            items = await self.onedrive.list_folder(token, folder="Templates", username_hint=hint)
        if not items:
            return self._respond(
                state,
                "No OneDrive files found (or none you can read). Sign in and check the Templates folder.",
                "idle",
            )
        lines = [
            f"OneDrive results for **{hint}** ({len(items)} accessible):",
            "",
        ]
        for item in items[:8]:
            badge = "write" if item.can_write else "read-only"
            lines.append(
                f"- **{item.name}** `{item.id}` · {badge} · {item.path}"
            )
        lines.append("")
        lines.append("Ask for **version history** or **access** on a file name to inspect commits/permissions.")
        return self._respond(state, "\n".join(lines), "idle")

    async def _onedrive_versions(self, state: SessionState, message: str) -> ChatResponse:
        token = state.graph_token or "mock-token"
        hint = self._drive_user(state)
        items = await self.onedrive.search(token, message, username_hint=hint)
        if not items:
            items = await self.onedrive.list_folder(token, folder="Templates", username_hint=hint)
        files = [i for i in items if i.kind.value == "file"]
        if not files:
            return self._respond(state, "No OneDrive file matched for version history.", "idle")
        target = files[0]
        try:
            timeline = await self.onedrive.version_timeline(token, target.id, username_hint=hint)
        except Exception as exc:
            return self._respond(state, f"Could not load versions: {exc}", "idle")
        access = timeline["access"]
        lines = [
            f"Version timeline for **{timeline['item_name']}** (GitHub-style):",
            f"Your access: **{access['access']}** — {access['rationale']}",
            "",
            "### Hub commits",
        ]
        for c in timeline.get("hub_commits") or []:
            lines.append(
                f"- `{c['sha']}` **{c['message']}** — {c['author']} · {c['created_at'][:19]}"
            )
        if not timeline.get("hub_commits"):
            lines.append("- (no hub commits yet)")
        lines.append("")
        lines.append("### OneDrive snapshots")
        for v in timeline.get("onedrive_versions") or []:
            lines.append(
                f"- `{v['id']}` · {v.get('modified_by') or 'unknown'} · {str(v.get('last_modified') or '')[:19]} · {v.get('size', 0)} bytes"
            )
        if not access.get("can_write"):
            lines.append("")
            lines.append("_Read-only: you can view history but cannot restore or push new commits._")
        return self._respond(state, "\n".join(lines), "idle")

    async def _onedrive_access(self, state: SessionState, message: str) -> ChatResponse:
        token = state.graph_token or "mock-token"
        hint = self._drive_user(state)
        items = await self.onedrive.search(token, message, username_hint=hint)
        if not items:
            items = await self.onedrive.list_folder(token, folder="Templates", username_hint=hint)
        files = [i for i in items if i.kind.value == "file"] or items
        if not files:
            return self._respond(state, "No OneDrive item found to check access.", "idle")
        target = files[0]
        report = await self.onedrive.get_access(token, target.id, username_hint=hint)
        lines = [
            f"Access report for **{report.item_name or target.name}**:",
            f"- Signed in as: **{report.current_user}**",
            f"- Effective access: **{report.access.value}**",
            f"- Can read: **{report.can_read}**",
            f"- Can write: **{report.can_write}**",
            f"- Why: {report.rationale}",
            "",
            "Permissions:",
        ]
        for p in report.permissions:
            lines.append(
                f"- {', '.join(p.roles) or 'role?'} → {p.granted_to or p.granted_to_type or 'link/org'}"
            )
        if not report.permissions:
            lines.append("- (no explicit ACL entries returned)")
        return self._respond(state, "\n".join(lines), "idle")

    async def _search(self, state: SessionState, message: str) -> ChatResponse:
        hits = self.retriever.search(message, limit=5)
        allowed = [
            t for t, _ in hits if self.acl.can_access(state.username, t, AccessLevel.read)
        ]
        if not allowed:
            reply = (
                "I couldn't find an approved template you can access for that request. "
                "Try MOM, project plan, QMM proposal, API spec, or test script."
            )
            return self._respond(state, reply, "idle")

        top = allowed[0]
        latest = self.catalog.latest_version(top)
        lines = [
            f"Found **{len(allowed)}** relevant template(s). Top match:",
            f"**{top.name}** (`{top.id}`) — {top.category.value} · {top.output_format.value.upper()}",
            f"Latest approved version: **{latest.version}** ({latest.created_at[:10]})",
            f"{top.description}",
            "",
            "Other matches:",
        ]
        for t in allowed[1:4]:
            v = self.catalog.latest_version(t)
            lines.append(f"- {t.name} · v{v.version} · {t.output_format.value}")
        lines.append("")
        lines.append("Say **create** with the template name to auto-fill it.")
        self.catalog.record_usage(top.id, "search", state.username)
        return self._respond(state, "\n".join(lines), "idle", template=top, search_results=allowed)

    async def _version_history(self, state: SessionState, message: str) -> ChatResponse:
        tmpl = self.retriever.best_match(message)
        if not tmpl or not self.acl.can_access(state.username, tmpl, AccessLevel.read):
            return self._respond(state, "No accessible template matched for version history.", "idle")
        lines = [f"Version history for **{tmpl.name}**:", ""]
        for v in sorted(tmpl.versions, key=lambda x: x.created_at, reverse=True):
            lines.append(
                f"- **v{v.version}** [{v.status}] {v.created_at[:10]} — {v.changelog or 'No notes'} (by {v.created_by})"
            )
        latest = self.catalog.latest_version(tmpl)
        lines.append("")
        lines.append(f"You should use approved **v{latest.version}** for new documents.")
        return self._respond(state, "\n".join(lines), "idle", template=tmpl)

    async def _start_create(self, state: SessionState, message: str) -> ChatResponse:
        tmpl = self.retriever.best_match(message)
        if not tmpl:
            return self._respond(
                state,
                "I couldn't identify which template to use. Try naming MOM, proposal, project plan, design doc, API spec, or test script.",
                "idle",
            )
        if not self.acl.can_access(state.username, tmpl, AccessLevel.write):
            if self.acl.can_access(state.username, tmpl, AccessLevel.read):
                return self._respond(
                    state,
                    f"You can view **{tmpl.name}**, but write/create requires a consultant or approver role.",
                    "idle",
                    template=tmpl,
                )
            return self._respond(state, "You don't have access to create that template.", "idle")

        # Pre-extract any answers already present in the prompt
        extracted = await self.llm.json_complete(
            system="Extract answers from the user message. Return JSON {answers: {placeholder: value}}",
            user=message,
        )
        prefill = extracted.get("answers") or {}

        state.template = tmpl
        state.questions = list(tmpl.context_questions)[:4]
        state.answers = {}
        state.pending_question_idx = 0
        state.stage = "clarifying"

        # Map prefill loosely onto placeholders / question labels
        for q in state.questions:
            for k, v in prefill.items():
                if k.lower() in q.lower() or q.lower() in k.lower():
                    state.answers[q] = v

        unanswered = [q for q in state.questions if q not in state.answers]
        if not unanswered:
            return await self._finalize(state)

        state.pending_question_idx = state.questions.index(unanswered[0])
        latest = self.catalog.latest_version(tmpl)
        reply = (
            f"I'll create **{tmpl.name}** from approved **v{latest.version}** "
            f"({tmpl.output_format.value.upper()}).\n\n"
            f"I need a few details ({len(unanswered)} left):\n"
            f"**1/{len(state.questions)}. {unanswered[0]}**"
        )
        return self._respond(
            state,
            reply,
            "clarifying",
            template=tmpl,
            questions=state.questions,
            answers=state.answers,
        )

    async def _continue_clarification(self, state: SessionState, message: str) -> ChatResponse:
        assert state.template is not None
        current_q = state.questions[state.pending_question_idx]
        # Allow "Q: A" multi-answer paste
        extracted = await self.llm.json_complete(
            system="Extract answers. Return JSON {answers: {key: value}}",
            user=message,
        )
        multi = extracted.get("answers") or {}
        if multi:
            for q in state.questions:
                for k, v in multi.items():
                    if k.lower() in q.lower() or q.lower() in k.lower():
                        state.answers[q] = v
            if current_q not in state.answers:
                state.answers[current_q] = message.strip()
        else:
            state.answers[current_q] = message.strip()

        unanswered = [q for q in state.questions if q not in state.answers]
        if unanswered:
            next_q = unanswered[0]
            state.pending_question_idx = state.questions.index(next_q)
            answered_n = len(state.questions) - len(unanswered)
            reply = (
                f"Got it.\n\n"
                f"**{answered_n + 1}/{len(state.questions)}. {next_q}**"
            )
            return self._respond(
                state,
                reply,
                "clarifying",
                template=state.template,
                questions=state.questions,
                answers=state.answers,
            )
        return await self._finalize(state)

    async def _finalize(self, state: SessionState) -> ChatResponse:
        assert state.template is not None
        tmpl = state.template

        # Map question answers → placeholder keys
        mapped: dict[str, str] = {}
        for ph in tmpl.placeholders:
            mapped[ph] = (
                state.answers.get(ph)
                or next(
                    (v for q, v in state.answers.items() if ph.lower() in q.lower() or q.lower() in ph.lower()),
                    f"[Pending: {ph}]",
                )
            )
        # Also keep raw Q&A
        for q, a in state.answers.items():
            mapped.setdefault(q, a)

        fill_payload = await self.llm.json_complete(
            system="Fill placeholders. Return JSON {filled: {placeholder: value}}",
            user=str({"placeholders": tmpl.placeholders, "answers": mapped}),
        )
        filled = fill_payload.get("filled") or mapped

        filename, _path = self.generator.generate(tmpl, filled)
        version = self.catalog.latest_version(tmpl).version
        self.catalog.record_usage(tmpl.id, "generate", state.username)

        download_url = f"/api/files/{filename}"
        reply = (
            f"Created **{tmpl.name}** from approved **v{version}**.\n\n"
            f"Filled fields: {', '.join(f'{k}' for k in list(filled.keys())[:6])}\n\n"
            f"Your document is ready: **{filename}**\n"
            f"Download: {download_url}"
        )
        state.stage = "done"
        response = self._respond(
            state,
            reply,
            "done",
            template=tmpl,
            questions=state.questions,
            answers=state.answers,
            download_url=download_url,
        )
        # Reset for next turn while keeping history
        state.stage = "idle"
        state.template = None
        state.questions = []
        state.answers = {}
        state.pending_question_idx = 0
        return response

    def _respond(
        self,
        state: SessionState,
        reply: str,
        stage: str,
        template: Optional[TemplateMeta] = None,
        questions: Optional[list[str]] = None,
        answers: Optional[dict[str, str]] = None,
        download_url: Optional[str] = None,
        search_results: Optional[list[TemplateMeta]] = None,
    ) -> ChatResponse:
        state.messages.append(ChatMessage(role="assistant", content=reply))
        state.stage = stage
        return ChatResponse(
            session_id=state.session_id,
            reply=reply,
            stage=stage,
            template=template,
            questions=questions or [],
            answers=answers or {},
            download_url=download_url,
            search_results=search_results or [],
            messages=list(state.messages),
        )
