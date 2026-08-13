from __future__ import annotations

import json
import re
from typing import Any

from app.llm.base import LLMProvider


class MockLLM(LLMProvider):
    """Deterministic offline LLM for hackathon demos without cloud keys."""

    async def complete(self, system: str, user: str, temperature: float = 0.2) -> str:
        text = user.lower()
        sys = system.lower()
        if "select the best" in sys or ("template_id" in sys and "uploaded" in sys):
            return json.dumps(self._select_template(user))
        if "extract fields" in sys or "filled_fields" in sys:
            return json.dumps(self._extract_upload_fields(user))
        if "classify" in sys or "intent" in sys:
            return json.dumps(self._intent(text))
        if "extract" in sys or "answers" in sys:
            return json.dumps(self._extract_answers(user))
        if "fill" in sys or "placeholder" in sys:
            return json.dumps(self._fill_from_context(user))
        return (
            "I can help you search approved YMSLI templates, upload a document for auto-fill, "
            "or create a document. Try uploading a MOM/notes file or say 'Create a project plan'."
        )

    def _select_template(self, user: str) -> dict[str, Any]:
        try:
            payload = json.loads(user)
        except json.JSONDecodeError:
            payload = {}
        blob = f"{payload.get('filename','')} {payload.get('user_hint','')} {payload.get('document_excerpt','')}".lower()
        mapping = [
            ("tmpl-mom", ("minutes", "mom", "attendees", "agenda", "meeting")),
            ("tmpl-qmm-proposal", ("proposal", "qmm", "commercial", "client")),
            ("tmpl-project-plan", ("project plan", "milestone", "schedule")),
            ("tmpl-status-report", ("status report", "weekly", "blockers", "rag")),
            ("tmpl-design-doc", ("architecture", "design", "tsd")),
            ("tmpl-api-spec", ("api", "endpoint", "openapi")),
            ("tmpl-test-script", ("test case", "test script")),
            ("tmpl-onboarding", ("onboarding", "joiner")),
        ]
        best_id = "tmpl-mom"
        best_n = -1
        best_type = "Meeting Minutes"
        for tid, keys in mapping:
            n = sum(1 for k in keys if k in blob)
            if n > best_n:
                best_n = n
                best_id = tid
                best_type = keys[0]
        return {
            "template_id": best_id,
            "reason": f"Mock LLM matched document cues to {best_id}",
            "detected_doc_type": best_type,
            "confidence": 0.72 if best_n > 0 else 0.4,
        }

    def _extract_upload_fields(self, user: str) -> dict[str, Any]:
        try:
            payload = json.loads(user)
        except json.JSONDecodeError:
            payload = {}
        text = str(payload.get("document_text") or "")
        placeholders = ((payload.get("template") or {}).get("placeholders")) or []
        filled: dict[str, str] = {}
        for ph in placeholders:
            m = re.search(rf"{re.escape(ph)}\s*[:\-]\s*(.+)", text, re.I)
            if m:
                filled[ph] = m.group(1).strip().split("\n")[0][:200]
            else:
                # try first word of placeholder
                key = ph.split()[0]
                m2 = re.search(rf"{re.escape(key)}[^\n:]{{0,20}}[:\-]\s*(.+)", text, re.I)
                filled[ph] = m2.group(1).strip().split("\n")[0][:200] if m2 else f"[Not found in source: {ph}]"
        return {
            "detected_doc_type": (payload.get("template") or {}).get("name") or "Document",
            "summary": "Extracted fields from uploaded document (mock LLM).",
            "confidence": 0.65,
            "filled_fields": filled,
            "answers": filled,
        }

    def _intent(self, text: str) -> dict[str, Any]:
        create_words = ("create", "generate", "draft", "prepare", "make", "fill", "write")
        history_words = ("version", "history", "changelog", "previous", "commits", "git")
        search_words = ("find", "search", "latest", "show", "locate", "where")
        drive_words = ("onedrive", "one drive", "sharepoint", "my drive", "graph")
        access_words = ("permission", "access", "readonly", "read-only", "write access", "can i edit")

        if any(w in text for w in access_words) and any(w in text for w in drive_words + ("file", "document", "qmm", "mom")):
            intent = "onedrive_access"
        elif any(w in text for w in drive_words) and any(w in text for w in history_words):
            intent = "onedrive_versions"
        elif any(w in text for w in drive_words) or "from onedrive" in text or "in onedrive" in text:
            intent = "onedrive_search"
        elif any(w in text for w in history_words):
            intent = "version_history"
        elif any(w in text for w in create_words):
            intent = "create"
        elif any(w in text for w in search_words):
            intent = "search"
        else:
            intent = "search"

        keywords = []
        catalog = [
            "mom", "minutes", "meeting", "proposal", "qmm", "project plan",
            "plan", "design", "test script", "api", "spec", "specification",
            "onboarding", "status report", "onedrive",
        ]
        for kw in catalog:
            if kw in text:
                keywords.append(kw)
        return {"intent": intent, "keywords": keywords, "confidence": 0.85}

    def _extract_answers(self, user: str) -> dict[str, Any]:
        answers: dict[str, str] = {}
        # Patterns like "Project: Orion" or Q1 answers in numbered form
        for line in user.splitlines():
            if ":" in line:
                key, val = line.split(":", 1)
                key = key.strip().lstrip("0123456789.-) ").strip()
                if key and val.strip():
                    answers[key] = val.strip()
        # Free-form fallbacks
        m = re.search(r"project\s+(?:name\s+)?(?:is\s+)?([A-Za-z0-9 _-]+)", user, re.I)
        if m:
            answers.setdefault("Project Name", m.group(1).strip())
        m = re.search(r"date\s+(?:is\s+)?([0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})", user, re.I)
        if m:
            answers.setdefault("Meeting Date", m.group(1).strip())
        m = re.search(r"attendees?\s*(?:are|:)?\s*(.+)", user, re.I)
        if m:
            answers.setdefault("Attendees", m.group(1).strip())
        return {"answers": answers}

    def _fill_from_context(self, user: str) -> dict[str, Any]:
        try:
            payload = json.loads(user)
        except json.JSONDecodeError:
            payload = {}
        placeholders = payload.get("placeholders", [])
        answers = payload.get("answers", {})
        filled: dict[str, str] = {}
        for ph in placeholders:
            # Direct match or case-insensitive
            if ph in answers:
                filled[ph] = answers[ph]
                continue
            found = next((v for k, v in answers.items() if k.lower() == ph.lower()), None)
            if found:
                filled[ph] = found
            else:
                filled[ph] = f"[To be confirmed: {ph}]"
        return {"filled": filled}
