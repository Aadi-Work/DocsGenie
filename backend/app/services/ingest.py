from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from app.llm.factory import get_llm
from app.models.schemas import AccessLevel, TemplateMeta
from app.rag.retriever import HybridRetriever
from app.services.catalog import AccessControl, CatalogService
from app.services.doc_generator import DocumentGenerator
from app.services.document_parser import extract_text_from_bytes


@dataclass
class UploadAnalysisResult:
    extracted_text_preview: str
    detected_doc_type: str
    summary: str
    selected_template: TemplateMeta
    selection_reason: str
    confidence: float
    filled_fields: dict[str, str]
    missing_fields: list[str]
    filename: Optional[str] = None
    download_url: Optional[str] = None
    auto_generated: bool = False


class DocumentIngestService:
    """
    Upload → parse → LLM analyze → auto-select template → optional generate.
    """

    def __init__(
        self,
        catalog: CatalogService,
        retriever: HybridRetriever,
        acl: AccessControl,
        generator: DocumentGenerator,
    ):
        self.catalog = catalog
        self.retriever = retriever
        self.acl = acl
        self.generator = generator
        self.llm = get_llm()

    async def compose(
        self,
        *,
        prompt: str = "",
        text: str = "",
        file_name: Optional[str] = None,
        file_data: Optional[bytes] = None,
        template_id: Optional[str] = None,
        template_source: str = "local",
        onedrive_item_id: Optional[str] = None,
        onedrive_token: Optional[str] = None,
        username: str = "consultant",
        auto_generate: bool = True,
    ) -> UploadAnalysisResult:
        """
        Build a document from:
        - optional uploaded file (parsed)
        - optional pasted text
        - optional prompt (can name the template)
        - template selected from local catalog or OneDrive
        """
        chunks: list[str] = []
        if prompt.strip():
            chunks.append(f"User prompt:\n{prompt.strip()}")
        if text.strip():
            chunks.append(f"User text:\n{text.strip()}")
        parsed_name = file_name or "input.txt"
        if file_data:
            extracted = extract_text_from_bytes(parsed_name, file_data)
            chunks.append(f"Uploaded file ({parsed_name}):\n{extracted}")
        combined = "\n\n".join(chunks).strip()
        if not combined:
            raise ValueError("Provide text, an uploaded file, or a prompt with content.")

        # If OneDrive template picked, pull its text and use it to guide selection/fill
        od_template_text = ""
        od_template_name = ""
        if template_source == "onedrive" and onedrive_item_id:
            from app.services.onedrive import OneDriveService

            od = OneDriveService()
            token = onedrive_token or "mock-token"
            hint = username if "@" in username else (
                "joiner@ymsli.com" if username == "joiner" else "demo.user@ymsli.com"
            )
            od_name, od_bytes = await od.download(token, onedrive_item_id, username_hint=hint)
            od_template_name = od_name
            try:
                od_template_text = extract_text_from_bytes(od_name, od_bytes)
            except Exception:
                od_template_text = od_bytes.decode("utf-8", errors="ignore")[:8000]
            if not template_id:
                # Prefer matching local template by OneDrive filename / content
                template_id = None

        user_hint = prompt.strip()
        if od_template_name:
            user_hint = f"{user_hint}\nUse OneDrive template file: {od_template_name}".strip()

        # Resolve local template (always generate through local approved template engine)
        templates = self.acl.filter_templates(username, self.catalog.list_templates())
        if not templates:
            raise PermissionError("No templates accessible for this user.")

        selected: Optional[TemplateMeta] = None
        selection_reason = ""
        confidence = 0.5

        if template_id and template_source != "onedrive":
            selected = next((t for t in templates if t.id == template_id), None)
            if not selected:
                raise ValueError(f"Template '{template_id}' not found or not accessible.")
            selection_reason = "Selected from local templates."
            confidence = 1.0
        elif template_source == "onedrive" and (od_template_name or onedrive_item_id):
            selected, selection_reason, confidence = await self._auto_select(
                text=f"{od_template_name}\n{od_template_text}\n{combined}",
                user_hint=user_hint or od_template_name,
                filename=od_template_name or parsed_name,
                templates=templates,
            )
            selection_reason = (
                f"Matched local generator to OneDrive template '{od_template_name}'. {selection_reason}"
            )
        else:
            # From prompt / content — auto pick (prompt may name the template)
            selected, selection_reason, confidence = await self._auto_select(
                text=combined,
                user_hint=user_hint,
                filename=parsed_name,
                templates=templates,
            )

        assert selected is not None

        analysis_text = combined
        if od_template_text:
            analysis_text = (
                f"OneDrive template structure ({od_template_name}):\n{od_template_text[:3000]}\n\n"
                f"Source data to fill:\n{combined}"
            )

        if not self.acl.can_access(username, selected, AccessLevel.write) and auto_generate:
            auto_generate = False

        analysis = await self._analyze_with_llm(analysis_text, selected, user_hint, parsed_name)
        filled = analysis.get("filled_fields") or {}
        for ph in selected.placeholders:
            filled.setdefault(ph, analysis.get("answers", {}).get(ph, f"[Extracted pending: {ph}]"))

        missing = [
            ph for ph in selected.placeholders if not filled.get(ph) or str(filled[ph]).startswith("[")
        ]

        out = UploadAnalysisResult(
            extracted_text_preview=combined[:1200],
            detected_doc_type=str(analysis.get("detected_doc_type") or selected.name),
            summary=str(analysis.get("summary") or selection_reason),
            selected_template=selected,
            selection_reason=selection_reason,
            confidence=float(analysis.get("confidence") or confidence),
            filled_fields={k: str(v) for k, v in filled.items()},
            missing_fields=missing,
        )

        if auto_generate:
            gen_name, _ = self.generator.generate(selected, out.filled_fields)
            self.catalog.record_usage(selected.id, "compose_generate", username)
            out.filename = gen_name
            out.download_url = f"/api/files/{gen_name}"
            out.auto_generated = True

        return out

    async def analyze_and_generate(
        self,
        *,
        filename: str,
        data: bytes,
        username: str = "consultant",
        user_hint: str = "",
        template_id: Optional[str] = None,
        auto_generate: bool = True,
    ) -> UploadAnalysisResult:
        text = extract_text_from_bytes(filename, data)
        templates = self.acl.filter_templates(username, self.catalog.list_templates())
        if not templates:
            raise PermissionError("No templates accessible for this user.")

        selected: Optional[TemplateMeta] = None
        selection_reason = ""
        confidence = 0.5

        if template_id:
            selected = next((t for t in templates if t.id == template_id), None)
            if not selected:
                raise ValueError(f"Template '{template_id}' not found or not accessible.")
            selection_reason = "Explicitly selected by user."
            confidence = 1.0
        else:
            selected, selection_reason, confidence = await self._auto_select(
                text=text,
                user_hint=user_hint,
                filename=filename,
                templates=templates,
            )

        if not self.acl.can_access(username, selected, AccessLevel.write) and auto_generate:
            # Still allow analysis; generation will be skipped
            auto_generate = False

        analysis = await self._analyze_with_llm(text, selected, user_hint, filename)
        filled = analysis.get("filled_fields") or {}
        # Ensure all placeholders exist
        for ph in selected.placeholders:
            filled.setdefault(ph, analysis.get("answers", {}).get(ph, f"[Extracted pending: {ph}]"))

        missing = [ph for ph in selected.placeholders if not filled.get(ph) or str(filled[ph]).startswith("[")]

        out = UploadAnalysisResult(
            extracted_text_preview=text[:1200],
            detected_doc_type=str(analysis.get("detected_doc_type") or selected.name),
            summary=str(analysis.get("summary") or selection_reason),
            selected_template=selected,
            selection_reason=selection_reason,
            confidence=float(analysis.get("confidence") or confidence),
            filled_fields={k: str(v) for k, v in filled.items()},
            missing_fields=missing,
        )

        if auto_generate:
            gen_name, _ = self.generator.generate(selected, out.filled_fields)
            self.catalog.record_usage(selected.id, "upload_generate", username)
            out.filename = gen_name
            out.download_url = f"/api/files/{gen_name}"
            out.auto_generated = True

        return out

    async def _auto_select(
        self,
        *,
        text: str,
        user_hint: str,
        filename: str,
        templates: list[TemplateMeta],
    ) -> tuple[TemplateMeta, str, float]:
        # Hybrid: retrieval + LLM ranking
        query = f"{user_hint} {filename} {text[:1500]}".strip()
        hits = self.retriever.search(query, limit=5)
        allowed_ids = {t.id for t in templates}
        ranked = [(t, s) for t, s in hits if t.id in allowed_ids]

        catalog_brief = [
            {
                "id": t.id,
                "name": t.name,
                "category": t.category.value,
                "tags": t.tags,
                "description": t.description,
            }
            for t in templates
        ]
        llm_pick = await self.llm.json_complete(
            system=(
                "You select the best YMSLI document template for an uploaded file. "
                "Return JSON only: "
                '{"template_id":"...","reason":"...","detected_doc_type":"...","confidence":0.0}'
            ),
            user=json.dumps(
                {
                    "filename": filename,
                    "user_hint": user_hint,
                    "document_excerpt": text[:4000],
                    "templates": catalog_brief,
                    "retriever_top": [{"id": t.id, "score": s} for t, s in ranked[:5]],
                }
            ),
        )

        pick_id = (llm_pick.get("template_id") or "").strip()
        selected = next((t for t in templates if t.id == pick_id), None)
        if selected:
            return (
                selected,
                str(llm_pick.get("reason") or "Selected by Gemini/LLM from document content."),
                float(llm_pick.get("confidence") or 0.8),
            )

        if ranked:
            t, score = ranked[0]
            return t, f"Selected via semantic retrieval (score={score:.2f}).", min(0.75, 0.4 + score / 10)

        # Keyword fallback
        blob = f"{filename} {user_hint} {text[:2000]}".lower()
        rules = [
            ("tmpl-mom", ("minutes", "mom", "attendees", "agenda", "meeting")),
            ("tmpl-qmm-proposal", ("proposal", "qmm", "commercial", "scope of work")),
            ("tmpl-project-plan", ("project plan", "milestone", "schedule", "gantt")),
            ("tmpl-status-report", ("status report", "rag", "weekly status", "blockers")),
            ("tmpl-design-doc", ("architecture", "design document", "tsd", "nfr")),
            ("tmpl-api-spec", ("api", "endpoint", "openapi", "swagger")),
            ("tmpl-test-script", ("test case", "test script", "expected result")),
            ("tmpl-onboarding", ("onboarding", "new joiner", "buddy")),
        ]
        best: Optional[TemplateMeta] = None
        best_hits = 0
        for tid, keys in rules:
            hits_n = sum(1 for k in keys if k in blob)
            if hits_n > best_hits:
                cand = next((t for t in templates if t.id == tid), None)
                if cand:
                    best = cand
                    best_hits = hits_n
        if best:
            return best, "Selected via keyword heuristics from uploaded content.", 0.55
        return templates[0], "Fallback to first accessible template.", 0.3

    async def _analyze_with_llm(
        self,
        text: str,
        template: TemplateMeta,
        user_hint: str,
        filename: str,
    ) -> dict[str, Any]:
        result = await self.llm.json_complete(
            system=(
                "Extract fields to fill a document template from uploaded content. "
                "Return JSON only with keys: "
                "detected_doc_type, summary, confidence, filled_fields (object mapping placeholder->value), "
                "answers (same as filled_fields). "
                "Use only facts present in the document; if unknown use a short placeholder like '[Not found in source]'."
            ),
            user=json.dumps(
                {
                    "filename": filename,
                    "user_hint": user_hint,
                    "template": {
                        "id": template.id,
                        "name": template.name,
                        "placeholders": template.placeholders,
                        "content_outline": template.content_outline,
                    },
                    "document_text": text[:8000],
                }
            ),
        )
        if not result:
            # Heuristic extraction for mock/offline
            return self._heuristic_extract(text, template, filename)
        if "filled_fields" not in result and "answers" in result:
            result["filled_fields"] = result["answers"]
        return result

    def _heuristic_extract(self, text: str, template: TemplateMeta, filename: str) -> dict[str, Any]:
        filled: dict[str, str] = {}
        patterns = {
            "Project Name": r"(?:project\s*name|project)\s*[:\-]\s*(.+)",
            "Client Name": r"(?:client\s*name|client)\s*[:\-]\s*(.+)",
            "Meeting Date": r"(?:meeting\s*date|date)\s*[:\-]\s*(.+)",
            "Attendees": r"(?:attendees?)\s*[:\-]\s*(.+)",
            "Agenda": r"(?:agenda)\s*[:\-]\s*(.+)",
            "Prepared By": r"(?:prepared\s*by|author)\s*[:\-]\s*(.+)",
            "Service Name": r"(?:service\s*name|service)\s*[:\-]\s*(.+)",
            "Base URL": r"(?:base\s*url|url)\s*[:\-]\s*(.+)",
        }
        for ph in template.placeholders:
            if ph in patterns:
                m = re.search(patterns[ph], text, re.I)
                if m:
                    filled[ph] = m.group(1).strip().split("\n")[0][:200]
            if ph not in filled:
                # soft contains
                for line in text.splitlines():
                    if ":" in line and ph.lower().split()[0] in line.lower():
                        filled[ph] = line.split(":", 1)[1].strip()[:200]
                        break
            filled.setdefault(ph, f"[Not found in source: {ph}]")

        doc_type = template.name
        lower = f"{filename} {text[:500]}".lower()
        if "meeting" in lower or "mom" in lower:
            doc_type = "Meeting Minutes"
        elif "proposal" in lower or "qmm" in lower:
            doc_type = "Proposal"
        return {
            "detected_doc_type": doc_type,
            "summary": f"Parsed '{filename}' and mapped fields into {template.name}.",
            "confidence": 0.6,
            "filled_fields": filled,
            "answers": filled,
        }
