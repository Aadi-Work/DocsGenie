"""Match TemplateHub-Agent PS-08 templates to S3 objects and drive the employee form."""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.office.agent.bfl_intelligence import BFL_FORMAT_HELP, BFL_SAMPLE_NOTES, polish_function_row
from app.office.agent.brd_intelligence import BRD_FORMAT_HELP, BRD_SAMPLE_NOTES, polish_brd_context
from app.office.agent.template_extract import LIST_KEYS, _pipe_rows, extract_for_template, sanitize_ppt_context, separate_ppt_sections
from app.utils.file_utils import snake

log = logging.getLogger(__name__)

SPECS_DIR = Path(__file__).resolve().parent / "specs"

# Same four guided templates as TemplateHub-Agent streamlit FORM_TEMPLATES.
# S3 files are chosen by semantic score (prefer *Sample* over seed *Template*).
FORM_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "mom",
        "name": "Minutes of Meeting",
        "sample_file": "MOM Sample.xlsx",
        "format": "xlsx",
        "hints": ("mom sample", "mom_sample", "minutes of meeting", "meeting minutes", "mom template", "mom_template"),
        "tokens": ("mom", "minutes"),
        "exts": {".xlsx", ".xls", ".xlsm"},
        "filler": "mom",
    },
    {
        "id": "bfl",
        "name": "Business Function List",
        "sample_file": "BFL Sample.xlsx",
        "format": "xlsx",
        "hints": ("bfl sample", "bfl_sample", "business function list", "business function"),
        "tokens": ("bfl",),
        "exts": {".xlsx", ".xls", ".xlsm"},
        "filler": "bfl",
    },
    {
        "id": "poc_list",
        "name": "POC List",
        "sample_file": "POC List sample.xlsx",
        "format": "xlsx",
        "hints": ("poc list sample", "poc list", "poc_list", "proof of concept"),
        "tokens": ("poc",),
        "exts": {".xlsx", ".xls", ".xlsm"},
        "filler": "poc",
    },
    {
        "id": "brd",
        "name": "Business Requirements Document",
        "sample_file": "BRD Sample.docx",
        "format": "docx",
        "hints": ("brd sample", "brd_sample", "business requirements document", "business requirement"),
        "tokens": ("brd",),
        "exts": {".docx", ".doc"},
        "filler": "brd",
    },
    {
        "id": "sample_ppt",
        "name": "Hackathon / Salesforce PPT",
        "sample_file": "Sample_ppt.pptx",
        "format": "pptx",
        "hints": (
            "sample_ppt",
            "sample ppt",
            "sample_ppt.pptx",
            "hackathon deck",
            "salesforce ppt",
            "ppt ai",
        ),
        "tokens": ("sample_ppt", "hackathon"),
        "exts": {".pptx", ".ppt"},
        "filler": "sample_ppt",
    },
]

SAMPLE_NOTES = {
    "mom": (
        "Architecture Review for Universal Template Engine on 13 August 2026 at "
        "Conference Room A / Teams. Rahul took notes. Participants: Ayushi, Rahul, "
        "Sana, Daniel. Decided AI emits JSON only; renderer edits Office files; "
        "never modify original. Actions: Rahul complete XLSX parser by 19 Aug 2026; "
        "Sana finalize taxonomy by 17 Aug 2026."
    ),
    "bfl": BFL_SAMPLE_NOTES,
    "poc_list": (
        "Project: YNS FnO ERP\nWorkstream: A2D - Acquire to Dispose\nUpdated by: Rahul Mehta\n\n"
        "POCs:\n"
        "POC-A2D-001 | A2D - Acquire to Dispose | Fixed asset master | Create FA master | "
        "Maintain FA master data | Groups configured | Open FA list; create record | "
        "Standard | 16638\n"
        "POC-A2D-002 | A2D - Acquire to Dispose | Asset acquisition | Capex to FA | "
        "Capitalize approved asset | Capex approved | Run acquisition journal | "
        "Standard | 16640"
    ),
    "brd": BRD_SAMPLE_NOTES,
    "sample_ppt": (
        "Team name: Info Nexus\n"
        "Problem statement ID: PS-014\n"
        "Theme: Customer Support Automation\n"
        "Team members: Aditi Rao • Karan Mehta • Priya Nair • Sam Fernandes\n\n"
        "Pitch: For support agents drowning in tickets, Info Nexus auto-triages and drafts first replies so response time drops from hours to minutes.\n\n"
        "What breaks today:\n"
        "Incoming support tickets sit unsorted in a shared inbox for 2-4 hours before anyone reads them.\n"
        "Frontline agents across 6 product lines feel it most, especially during month-end volume spikes.\n"
        "Each delayed ticket costs roughly 45 minutes of agent time and measurably hurts CSAT scores.\n"
        "One agent told us: I spend the first hour of my shift just figuring out what's urgent.\n\n"
        "What you built:\n"
        "Info Nexus reads every incoming ticket and classifies it by urgency and category within seconds.\n"
        "It drafts a suggested first response the agent can approve, edit, or discard with one click.\n"
        "Support agents use it the moment a new ticket lands, right inside their existing queue view.\n"
        "[Insert product screenshot of the triage queue here]\n\n"
        "Demo URL: https://demo.yamaha-hackathon.internal/info-nexus\n"
        "Demo moment: 0:38 — Info Nexus reads the raw ticket and returns a category, priority, and drafted reply.\n"
        "Hardest input: A garbled, multi-language ticket with no subject line and three unrelated complaints bundled together.\n"
        "User outcome: A ready-to-send reply and a priority tag, in under 2 seconds per ticket.\n\n"
        "AI decision: The AI classifies each incoming ticket by urgency and category, then drafts a first-response reply.\n"
        "Input / source: Zendesk ticket webhook\n"
        "Processing: FastAPI service on AWS Lambda\n"
        "AI layer: Amazon Bedrock (Claude) + RAG\n"
        "Output / user: Agent console (Zendesk sidebar app)\n"
        "What you need next: Read access to 6 months of historical tickets, and two more weeks to expand KB coverage."
    ),
}

FORMAT_HELP = {
    "mom": (
        "Paste loose meeting notes in chat. The bot maps date, venue, attendees, summary, "
        "and action rows (`Action | Owner | Due date | Status | Remarks`) and only asks for gaps."
    ),
    "bfl": BFL_FORMAT_HELP,
    "poc_list": (
        "Paste a project name, workstream, and POC rows in chat "
        "(`ID | Cycle | Sub-Process | Title | Description | Pre-req | Steps | Decision | Azure IDs`). "
        "Labeled headings are optional."
    ),
    "brd": BRD_FORMAT_HELP,
    "sample_ppt": (
        "Source file is **Sample_ppt.pptx** from S3. Slide 2 **What you built** is four plain-language "
        "bullets (what it does, what the user can do, when they use it, screenshot note) — same shape as "
        "`change_request.json`. Paste labeled fields **or loose notes**, or attach a file, then Smart-fill."
    ),
}

_BY_ID = {p["id"]: p for p in FORM_TEMPLATES}

# Employee UI: these profiles show the review form + attach. Everything else uses chat.
FORM_ENTRY_PROFILES = {"bfl", "sample_ppt", "brd"}


def uses_form_entry(profile_id: str | None) -> bool:
    return str(profile_id or "") in FORM_ENTRY_PROFILES


def _hay(filename: str = "", name: str = "", template_id: str = "") -> str:
    bits = [Path(filename or "").name, name or "", template_id or ""]
    return re.sub(r"[\s._-]+", " ", " ".join(bits)).strip().lower()


def score_profile(profile_id: str, *, filename: str = "", name: str = "", template_id: str = "") -> int:
    profile = _BY_ID.get(profile_id)
    if not profile:
        return 0
    hay = _hay(filename, name, template_id)
    if not hay:
        return 0
    ext = Path(filename or name or "").suffix.lower()
    if ext and ext not in profile["exts"]:
        return 0
    score = 0
    sample = re.sub(r"[\s._-]+", " ", Path(profile["sample_file"]).stem).lower()
    if sample and sample in hay:
        score += 100
    if "sample" in hay:
        score += 25
    if "template" in hay and "sample" not in hay:
        score -= 8
    for hint in profile["hints"]:
        if hint in hay:
            score += 20
    for token in profile["tokens"]:
        if re.search(rf"(?:^| ){re.escape(token)}(?: |$)", hay):
            score += 12
    return score


def match_profile(filename: str = "", name: str = "", template_id: str = "") -> str | None:
    best_id: str | None = None
    best = 0
    for profile in FORM_TEMPLATES:
        score = score_profile(profile["id"], filename=filename, name=name, template_id=template_id)
        if score > best:
            best = score
            best_id = profile["id"]
    return best_id if best >= 12 else None


def filler_kind(profile_id: str | None) -> str | None:
    if not profile_id:
        return None
    return str(_BY_ID.get(profile_id, {}).get("filler") or profile_id)


@lru_cache
def load_spec(profile_id: str) -> dict[str, Any]:
    path = SPECS_DIR / f"{profile_id}.spec.json"
    if not path.exists():
        raise KeyError(f"Unknown profile spec: {profile_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def field_config(profile_id: str) -> list[dict[str, Any]]:
    spec = load_spec(profile_id)
    fields: list[dict[str, Any]] = []
    for slot in sorted(spec.get("slots") or [], key=lambda s: -int(s.get("ask_priority") or 0)):
        ident = str(slot.get("id") or "")
        if not ident:
            continue
        fields.append(
            {
                "id": ident,
                "label": str(slot.get("label") or ident),
                "question": str(slot.get("question") or slot.get("help") or ident),
                "required": bool(slot.get("required", True)),
                "field_type": str(slot.get("type") or "string"),
                "help": str(slot.get("help") or ""),
                "source": "spec",
            }
        )
    return fields


def empty_form(profile_id: str) -> dict[str, str]:
    spec = load_spec(profile_id)
    form: dict[str, str] = {}
    for slot in spec.get("slots") or []:
        ident = str(slot.get("id") or "")
        default = slot.get("default")
        if default == "today":
            from datetime import date

            form[ident] = date.today().isoformat()
        else:
            form[ident] = "" if default in (None, "") else str(default)
    return form


def _list_to_lines(value: Any, keys: list[str] | None = None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value

    def _cell(raw: Any) -> str:
        if isinstance(raw, list):
            return " ; ".join(str(x).strip() for x in raw if str(x).strip())
        return re.sub(r"[\r\n]+", " ; ", str(raw or "")).strip()

    lines: list[str] = []
    for row in value:
        if isinstance(row, dict):
            if keys:
                lines.append(" | ".join(_cell(row.get(k, "")) for k in keys))
            else:
                lines.append(" | ".join(_cell(v) for v in row.values()))
        else:
            lines.append(str(row))
    return "\n".join(lines)


def context_to_form_fields(profile_id: str, ctx: dict[str, Any]) -> dict[str, str]:
    spec = load_spec(profile_id)
    list_keys = LIST_KEYS.get(profile_id, {})
    form = empty_form(profile_id)
    for slot in spec.get("slots") or []:
        ident = str(slot.get("id") or "")
        val = ctx.get(ident)
        if val in (None, "", []):
            continue
        if ident in list_keys:
            form[ident] = _list_to_lines(val, list_keys[ident])
        elif str(slot.get("type")) == "list" or ident.startswith("attendees"):
            if isinstance(val, list) and val and not isinstance(val[0], dict):
                if ident.startswith("attendees"):
                    form[ident] = ", ".join(str(x) for x in val)
                else:
                    form[ident] = "\n".join(str(x).strip() for x in val if str(x).strip())
            else:
                form[ident] = _list_to_lines(val)
        else:
            form[ident] = str(val)
    return form


def form_fields_to_context(profile_id: str, form: dict[str, str]) -> dict[str, Any]:
    spec = load_spec(profile_id)
    list_keys = LIST_KEYS.get(profile_id, {})
    ctx: dict[str, Any] = {}
    for slot in spec.get("slots") or []:
        ident = str(slot.get("id") or "")
        raw = str(form.get(ident) or form.get(slot.get("label") or "") or "").strip()
        if not raw:
            continue
        if ident in list_keys:
            keys = list_keys[ident]
            rows: list[dict[str, str]] = []
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = [p.strip() for p in line.split("|")]
                rows.append({keys[i]: (parts[i] if i < len(parts) else "") for i in range(len(keys))})
            ctx[ident] = rows
        elif str(slot.get("type")) == "list" or ident.startswith("attendees"):
            if ident.startswith("attendees") and "\n" not in raw:
                ctx[ident] = [x.strip() for x in raw.split(",") if x.strip()]
            elif "\n" in raw:
                ctx[ident] = [x.strip().lstrip("-•* ").strip() for x in raw.splitlines() if x.strip()]
            else:
                ctx[ident] = [x.strip() for x in raw.split(",") if x.strip()]
        else:
            ctx[ident] = raw
    return ctx


def _merge_ctx(base: dict[str, Any], overlay: dict[str, Any], profile_id: str = "") -> dict[str, Any]:
    merged = dict(base)
    short_keys = {
        "team_name",
        "problem_statement_id",
        "theme",
        "demo_url",
        "process_name",
        "prepared_by",
        "doc_code",
        "area_path",
    }
    for key, value in overlay.items():
        if key.startswith("_") or value in (None, "", []):
            continue
        prev = merged.get(key)
        if isinstance(prev, list) and isinstance(value, list):
            if profile_id == "sample_ppt" and key in {"what_breaks", "what_you_built"}:
                if len(prev) >= 2 and (len(value) < 2 or (len(value) == 1 and len(str(value[0])) > 180)):
                    continue
            merged[key] = value if len(value) >= len(prev) else prev
            continue
        if (
            profile_id == "sample_ppt"
            and key in short_keys
            and isinstance(prev, str)
            and prev.strip()
            and isinstance(value, str)
            and len(value) > max(40, len(prev) * 2)
        ):
            continue
        merged[key] = value
    return merged


def _normalize_extracted_keys(profile_id: str, answers: dict[str, Any]) -> dict[str, Any]:
    spec = load_spec(profile_id)
    id_by_id = {str(s.get("id") or ""): str(s.get("id") or "") for s in spec.get("slots") or []}
    id_by_label = {
        snake(str(s.get("label") or s.get("id") or "")): str(s.get("id") or "")
        for s in spec.get("slots") or []
    }
    out: dict[str, Any] = {}
    for key, value in (answers or {}).items():
        if value in (None, "", []) or str(key).startswith("_"):
            continue
        ident = id_by_id.get(str(key)) or id_by_id.get(snake(str(key))) or id_by_label.get(snake(str(key)))
        if ident:
            out[ident] = value
    return out


def extract_form(profile_id: str, notes: str) -> dict[str, Any]:
    """Notes → review-form strings, same path as TemplateHub-Agent Smart-fill."""
    if profile_id not in _BY_ID:
        raise KeyError(f"Unknown profile: {profile_id}")
    text = (notes or "").strip()
    heuristic = extract_for_template(profile_id, text) if text else {}
    meta: dict[str, Any] = {"mode": "heuristic", "profile_id": profile_id}
    merged = dict(heuristic)
    kb_pack: dict[str, Any] | None = None
    if profile_id == "brd":
        try:
            from app.office.agent.brd_kb import retrieve_brd_kb

            kb_pack = retrieve_brd_kb(text, merged)
            if kb_pack.get("hit"):
                meta["kb"] = {
                    "source": kb_pack.get("source"),
                    "s3_key": kb_pack.get("s3_key"),
                    "process": kb_pack.get("process"),
                    "scope_code": kb_pack.get("scope_code"),
                }
        except Exception as exc:
            log.warning("BRD KB retrieve failed: %s", exc)
    if text:
        try:
            from app.services.bedrock_service import get_bedrock

            spec = load_spec(profile_id)
            schema = {
                s["id"]: {
                    "label": s.get("label"),
                    "type": s.get("type"),
                    "help": s.get("help") or s.get("question"),
                }
                for s in spec.get("slots") or []
            }
            if profile_id == "sample_ppt":
                system = (
                    "Extract hackathon PPT fields from the notes for Sample_ppt.pptx / change_request.json.\n"
                    "what_breaks = slide 2 section 'What breaks today, and where exactly does it break?' "
                    "— 3-4 bullets about the current pain (delay, who feels it, cost). "
                    "Never put solution/product behaviour here.\n"
                    "what_you_built = slide 2 section 'What you built, in plain language.' — exactly 3-4 bullets:\n"
                    "1) what the product does  2) what the user can do with it  "
                    "3) when/where they use it  4) optional screenshot note.\n"
                    "Do not copy what_breaks bullets into what_you_built or the other way around.\n"
                    "team_name is a short team name, not members. theme is a short track name, not the pitch. "
                    "pitch is one sentence. Leave optional keys empty unless mentioned. Never invent facts."
                )
            elif profile_id == "brd":
                system = (
                    "Extract a Business Requirements Document from the notes. "
                    "A SE37 knowledge-base excerpt may be provided — use it only to fill gaps "
                    "the notes do not already state. Do not invent systems, approvals, or fields "
                    "that are not in the notes or the KB excerpt. "
                    "Return process_name, prepared_by, document_date (ISO), area_path, overview, and items. "
                    "items must be the end-to-end process FLOW: one Epic, then Features in process order, "
                    "then User Stories. Each item is "
                    "{type, name, description, acceptance, flow} where flow is 3-6 short process steps. "
                    "Use process codes exactly as stated (e.g. 55.10.005). "
                    "Type must be Epic, Feature, or User Story."
                )
            else:
                system = (
                    f"Extract fields for the '{spec.get('name')}' Office template from the notes. "
                    "Notes may be unstructured: emails, bullets, meeting scribbles, or labeled fields. "
                    "Infer every schema key you can from meaning; headings do not need to match labels. "
                    "Return JSON with only the schema keys. Never invent facts. "
                    "Use ISO dates (YYYY-MM-DD). List/row slots must be arrays of objects "
                    "with the provided column keys. Bullet slots may be arrays of short strings."
                )
            payload: dict[str, Any] = {
                "schema": schema,
                "row_keys": LIST_KEYS.get(profile_id, {}),
                "notes": text[:8000],
            }
            if profile_id == "brd" and kb_pack and kb_pack.get("passages"):
                payload["knowledge_base"] = {
                    "process": kb_pack.get("process"),
                    "source_file": kb_pack.get("source_file"),
                    "excerpt": str(kb_pack.get("passages") or "")[:7000],
                    "rule": "Fill missing BRD fields from this excerpt. Do not contradict the notes.",
                }
            llm_ctx = get_bedrock().json_invoke(
                json.dumps(payload),
                system=system,
            )
            if isinstance(llm_ctx, dict):
                answers = llm_ctx.get("answers") if isinstance(llm_ctx.get("answers"), dict) else llm_ctx
                answers = _normalize_extracted_keys(profile_id, answers if isinstance(answers, dict) else {})
                merged = _merge_ctx(heuristic, answers, profile_id)
                if profile_id == "sample_ppt":
                    merged = separate_ppt_sections(sanitize_ppt_context(merged))
                if profile_id == "brd":
                    merged = polish_brd_context(merged)
                if profile_id == "bfl":
                    bp = str(merged.get("business_process") or "")
                    funcs = merged.get("functions") or []
                    if isinstance(funcs, str):
                        funcs = _pipe_rows(funcs, LIST_KEYS["bfl"]["functions"], min_parts=2) or [
                            {"process": ln.strip()} for ln in funcs.splitlines() if ln.strip()
                        ]
                    if isinstance(funcs, list):
                        merged["functions"] = [
                            polish_function_row(r if isinstance(r, dict) else str(r), i + 1, bp)
                            for i, r in enumerate(funcs)
                            if r not in (None, "", [])
                        ]
                meta["mode"] = "llm+heuristic"
        except Exception as exc:
            log.warning("Profile LLM extract fell back to heuristics: %s", exc)
            meta["mode"] = "heuristic_fallback"
            meta["error"] = str(exc)[:240]
    if profile_id == "sample_ppt":
        merged = separate_ppt_sections(sanitize_ppt_context(merged))
    if profile_id == "brd":
        merged = polish_brd_context(merged)
        try:
            from app.office.agent.brd_kb import enrich_brd_from_kb

            merged, kb_meta = enrich_brd_from_kb(merged, text, pack=kb_pack)
            if kb_meta.get("used"):
                merged = polish_brd_context(merged)
            meta["kb"] = {**(meta.get("kb") or {}), **kb_meta}
        except Exception as exc:
            log.warning("BRD KB enrich failed: %s", exc)
    form = context_to_form_fields(profile_id, merged)
    missing = [
        str(slot.get("label") or slot.get("id"))
        for slot in load_spec(profile_id).get("slots") or []
        if slot.get("required", True) and not str(form.get(slot.get("id")) or "").strip()
    ]
    return {
        "profile_id": profile_id,
        "filled_fields": form,
        "context": merged,
        "missing_fields": missing,
        "meta": meta,
        "field_config": field_config(profile_id),
        "format_help": FORMAT_HELP.get(profile_id, ""),
        "sample_notes": SAMPLE_NOTES.get(profile_id, ""),
    }


def annotate_template(tmpl: dict[str, Any]) -> dict[str, Any]:
    profile_id = match_profile(
        filename=str(tmpl.get("original_filename") or tmpl.get("s3_key") or ""),
        name=str(tmpl.get("name") or ""),
        template_id=str(tmpl.get("id") or ""),
    )
    if not profile_id:
        tmpl["profile_id"] = None
        tmpl["guided"] = False
        return tmpl
    spec = load_spec(profile_id)
    catalog = _BY_ID[profile_id]
    tmpl["profile_id"] = profile_id
    tmpl["guided"] = True
    tmpl["name"] = catalog["name"]
    tmpl["description"] = spec.get("description") or tmpl.get("description")
    tmpl["field_config"] = field_config(profile_id)
    tmpl["placeholders"] = [f["label"] for f in tmpl["field_config"]]
    tmpl["context_questions"] = [f["question"] for f in tmpl["field_config"]]
    tmpl["format_help"] = FORMAT_HELP.get(profile_id, "")
    tmpl["sample_notes"] = SAMPLE_NOTES.get(profile_id, "")
    tmpl["sample_file"] = catalog["sample_file"]
    tmpl["entry_mode"] = "form" if uses_form_entry(profile_id) else "chat"
    tmpl["profile_score"] = score_profile(
        profile_id,
        filename=str(tmpl.get("original_filename") or ""),
        name=str(tmpl.get("name") or ""),
        template_id=str(tmpl.get("id") or ""),
    )
    return tmpl


def pick_guided(templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One S3 template per known profile, preferring Sample files over seed Template files."""
    annotated = [annotate_template(dict(t)) for t in templates]
    best: dict[str, dict[str, Any]] = {}
    for tmpl in annotated:
        profile_id = tmpl.get("profile_id")
        if not profile_id:
            continue
        prev = best.get(profile_id)
        score = int(tmpl.get("profile_score") or 0)
        if prev is None or score > int(prev.get("profile_score") or 0):
            best[profile_id] = tmpl
    ordered = [best[p["id"]] for p in FORM_TEMPLATES if p["id"] in best]
    chosen = {row["id"] for row in ordered}
    extras: list[dict[str, Any]] = []
    for tmpl in annotated:
        if tmpl.get("id") in chosen:
            continue
        extra = dict(tmpl)
        extra["guided"] = False
        filename = str(extra.get("original_filename") or extra.get("s3_key") or "")
        if filename:
            extra["name"] = re.sub(r"[_-]+", " ", Path(filename).stem).strip() or extra.get("name")
        extras.append(extra)
    extras.sort(key=lambda t: (t.get("name") or "").lower())
    return ordered + extras


def answers_for_fill(template: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    """Turn review-form strings into filler context when this is a known S3 profile."""
    profile_id = template.get("profile_id") or match_profile(
        filename=str(template.get("original_filename") or template.get("s3_key") or ""),
        name=str(template.get("name") or ""),
        template_id=str(template.get("id") or ""),
    )
    if not profile_id:
        return dict(answers or {})
    str_form: dict[str, str] = {}
    structured: dict[str, Any] = {}
    for key, value in (answers or {}).items():
        if value in (None, ""):
            continue
        if isinstance(value, (list, dict)):
            structured[str(key)] = value
            continue
        str_form[str(key)] = value if isinstance(value, str) else str(value)
    ctx = form_fields_to_context(profile_id, str_form)
    for key, value in structured.items():
        ctx[key] = value
        ctx.setdefault(snake(key), value)
    for key, value in str_form.items():
        ctx.setdefault(key, value)
        ctx.setdefault(snake(key), value)
    return ctx
