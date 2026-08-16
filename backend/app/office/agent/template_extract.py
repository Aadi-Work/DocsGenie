"""Heuristic extractors for PS-08 templates (MOM / BFL / POC / BRD)."""

from __future__ import annotations

import re
from typing import Any

from app.office.agent.freeform_extract import _to_iso_date, extract_mom

LIST_KEYS: dict[str, dict[str, list[str]]] = {
    "mom": {
        "action_items": ["action", "owner", "due_date", "status", "remarks"],
    },
    "bfl": {
        "functions": [
            "process",
            "description",
            "input",
            "output",
            "department",
            "frequency",
            "manual_auto",
            "type",
            "module",
            "fit_gap",
            "steps",
        ],
    },
    "poc_list": {
        "pocs": [
            "id",
            "cycle",
            "subprocess",
            "title",
            "description",
            "prereq",
            "steps",
            "decision",
            "azure_ids",
        ],
    },
    "brd": {
        "items": ["type", "name", "description", "acceptance", "flow"],
    },
}


def _kv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        raw = line.strip().lstrip("-•* ").strip()
        if not raw or raw.startswith("#"):
            continue
        m = re.match(r"^(.{1,48}?)\s*[:\-=]\s+(.+)$", raw)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if re.search(r"https?://", key, re.I):
            continue
        key_n = re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")
        if key_n:
            out[key_n] = val.strip()
    return out


def _pipe_rows(text: str, keys: list[str], min_parts: int = 2) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        line = line.strip().lstrip("-•* ").strip()
        if not line or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < min_parts:
            continue
        head = " ".join(parts).lower()
        if parts[0].lower() in {"id", "process", "type", "task", "action", "item"} and (
            "owner" in head or "description" in head or "cycle" in head or "name" in head
        ):
            continue
        row = {keys[i]: (parts[i] if i < len(parts) else "") for i in range(len(keys))}
        rows.append(row)
    return rows


def _section_blob(text: str, *headings: str, stop: tuple[str, ...] = ()) -> str:
    names = "|".join(re.escape(h) for h in headings)
    if not names:
        return ""
    m = re.search(
        rf"(?im)^(?:#+\s*)?(?:{names})\s*:?\s*$",
        text,
    )
    if m:
        rest = text[m.end() :]
        stops = [h for h in stop if h and h.lower() not in {x.lower() for x in headings}]
        nxt = None
        if stops:
            stop_pat = "|".join(re.escape(h) for h in stops)
            # Match a heading on its own line *or* "Demo URL: https://…"
            nxt = re.search(rf"(?im)^(?:#+\s*)?(?:{stop_pat})\s*:?(?:\s+\S.*)?$", rest)
        else:
            # Whole-line "Label:" only — never treat a body sentence as a heading.
            nxt = re.search(r"(?im)^(?:#+\s*)?[A-Z][A-Za-z0-9 /_,.&-]{1,60}:\s*$", rest)
        if nxt:
            return rest[: nxt.start()].strip()
        return rest.strip()
    m = re.search(rf"(?is)(?:{names})\s*[:\-]\s*(.+?)(?:\n\s*\n|$)", text)
    return m.group(1).strip() if m else ""


def extract_bfl(notes: str) -> dict[str, Any]:
    from app.office.agent.bfl_intelligence import summarize_bfl

    return summarize_bfl(notes)


def extract_poc(notes: str) -> dict[str, Any]:
    text = notes.strip()
    kv = _kv(text)
    out: dict[str, Any] = {}

    for src, dest in (
        ("project_name", "project_name"),
        ("project", "project_name"),
        ("workstream", "workstream"),
        ("updated_by", "updated_by"),
        ("prepared_by", "updated_by"),
    ):
        if src in kv and dest not in out:
            out[dest] = kv[src]

    m = re.search(
        r"(?:POC list|POCs?)\s+for\s+([^/\n]+)(?:\s*/\s*([^\n.]+))?",
        text,
        re.I,
    )
    if m:
        out.setdefault("project_name", m.group(1).strip())
        if m.group(2):
            out.setdefault("workstream", m.group(2).strip())

    blob = _section_blob(text, "POCs", "POC List", "Scenarios", "Rows") or text
    rows = _pipe_rows(blob, LIST_KEYS["poc_list"]["pocs"], min_parts=3)
    if rows:
        out["pocs"] = rows
    out.setdefault("updated_by", "YMSLI")
    return out


def extract_brd(notes: str) -> dict[str, Any]:
    text = notes.strip()
    kv = _kv(text)
    out: dict[str, Any] = {}

    mapping = (
        ("doc_code", "doc_code"),
        ("document_code", "doc_code"),
        ("title", "doc_code"),
        ("process_name", "process_name"),
        ("process", "process_name"),
        ("prepared_by", "prepared_by"),
        ("document_date", "document_date"),
        ("date", "document_date"),
        ("area_path", "area_path"),
        ("overview", "overview"),
        ("description", "overview"),
    )
    for src, dest in mapping:
        if src in kv and dest not in out:
            val = kv[src]
            if dest == "document_date":
                val = _to_iso_date(val) or val
            out[dest] = val

    m = re.search(
        r"(?:BRD|business requirements?(?:\s+document)?)\s+for\s+([^\n.]+)",
        text,
        re.I,
    )
    if m:
        out.setdefault("process_name", m.group(1).strip())

    if "overview" not in out:
        m = re.search(
            r"(?:overview|process description)\s*[:\-]\s*(.+?)(?:\n\s*\n|Items|Epics|$)",
            text,
            re.I | re.S,
        )
        if m:
            out["overview"] = re.sub(r"\s+", " ", m.group(1)).strip()

    blob = _section_blob(text, "Items", "Epics", "Features", "User Stories", "Rows") or ""
    if not blob:
        m = re.search(r"(?im)^items\s*:?\s*$", text)
        blob = text[m.end() :].strip() if m else text
    rows = _pipe_rows(blob, LIST_KEYS["brd"]["items"], min_parts=2)
    cleaned = []
    for row in rows:
        typ = str(row.get("type", "")).strip().lower()
        if typ in {"epic", "feature", "user story", "user_story", "story"} or (
            "epic" in typ or "feature" in typ or "story" in typ
        ):
            cleaned.append(row)
    if cleaned:
        out["items"] = cleaned
    elif rows:
        out["items"] = rows

    out.setdefault("doc_code", "SE52: Business Requirement Document")
    out.setdefault("prepared_by", "YMSLI")
    out.setdefault("area_path", "YNS-FnO-ERP")
    if "document_date" not in out:
        from datetime import datetime

        out["document_date"] = datetime.now().date().isoformat()
    elif out.get("document_date"):
        iso = _to_iso_date(str(out["document_date"]))
        if iso:
            out["document_date"] = iso
    return out


def extract_for_template(template_id: str, notes: str) -> dict[str, Any]:
    if template_id == "bfl":
        return extract_bfl(notes)
    if template_id == "poc_list":
        return extract_poc(notes)
    if template_id == "brd":
        return extract_brd(notes)
    if template_id == "mom":
        return extract_mom(notes)
    if template_id == "sample_ppt":
        return extract_sample_ppt(notes)
    return _kv(notes)


_PPT_ALIASES = {
    "team_name": "team_name",
    "our_team": "team_name",
    "problem_statement_id": "problem_statement_id",
    "problem_statement": "problem_statement_id",
    "ps_id": "problem_statement_id",
    "problem_id": "problem_statement_id",
    "theme": "theme",
    "hackathon_theme": "theme",
    "team_members": "team_members",
    "members": "team_members",
    "team_member_details": "team_members",
    "people": "team_members",
    "pitch": "pitch",
    "one_sentence_pitch": "pitch",
    "one_liner": "pitch",
    "tagline": "pitch",
    "demo_url": "demo_url",
    "demo_link": "demo_url",
    "recording": "demo_url",
    "demo_moment": "demo_moment",
    "hardest_input": "hardest_input",
    "user_outcome": "user_outcome",
    "ai_decision": "ai_decision",
    "why_not_rules": "why_not_rules",
    "ai_stack": "ai_stack",
    "input_source": "input_source",
    "processing": "processing",
    "ai_layer": "ai_layer",
    "output_user": "output_user",
    "flow_summary": "flow_summary",
    "today_limitation": "today_limitation",
    "win_dimension": "win_dimension",
    "why_not_clone": "why_not_clone",
    "hours_saved": "hours_saved",
    "faster_response": "faster_response",
    "reach": "reach",
    "next_need": "next_need",
    "what_you_need_next": "next_need",
}

_PPT_LIST_KEYS = (
    "what_breaks",
    "what_you_built",
    "tools_used",
    "stack",
    "demo_status",
    "technical_gap",
)


def _looks_like_member_list(value: str) -> bool:
    text = (value or "").strip()
    if "•" in text or ";" in text:
        return True
    return len([p for p in re.split(r"[,/]", text) if p.strip()]) >= 3


def sanitize_ppt_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """Keep PPT form fields on the right labels — identity fields stay short."""
    out = {k: v for k, v in (ctx or {}).items() if not str(k).startswith("_")}
    team = str(out.get("team_name") or "").strip()
    if team:
        team = team.splitlines()[0].strip()
        if re.match(r"^(members?|details|name|team)$", team, re.I) or _looks_like_member_list(team):
            if _looks_like_member_list(team) and not out.get("team_members"):
                out["team_members"] = team
            out.pop("team_name", None)
        else:
            out["team_name"] = team[:80]

    ps = str(out.get("problem_statement_id") or "").strip()
    match = re.search(r"PS[\s\-]?(\d{2,4})", ps, re.I)
    if match:
        out["problem_statement_id"] = f"PS-{match.group(1)}"
    elif ps and (len(ps) > 32 or "\n" in ps or (" " in ps.strip() and len(ps.split()) > 4)):
        out.pop("problem_statement_id", None)

    theme = str(out.get("theme") or "").strip()
    if theme:
        theme = theme.splitlines()[0].strip(" .")
        if len(theme) > 80 or re.search(r"\bfor\b.+\bso that\b", theme, re.I):
            out.setdefault("pitch", theme)
            out.pop("theme", None)
        else:
            out["theme"] = theme[:80]

    members = str(out.get("team_members") or "").strip()
    if members and re.match(r"^(members?|details)$", members, re.I):
        out.pop("team_members", None)

    pitch = str(out.get("pitch") or "").strip()
    if pitch:
        pitch = re.sub(r"\s+", " ", pitch).strip(" .")
        out["pitch"] = pitch[:280] if len(pitch) > 280 else pitch

    for key in _PPT_LIST_KEYS:
        val = out.get(key)
        if isinstance(val, str):
            lines = [ln.strip().lstrip("-•* ").strip() for ln in val.splitlines() if ln.strip()]
            if lines:
                out[key] = lines
        elif isinstance(val, list):
            cleaned: list[str] = []
            for item in val:
                if isinstance(item, dict):
                    text = " ".join(str(v).strip() for v in item.values() if str(v).strip())
                else:
                    text = str(item).strip().lstrip("-•* ").strip()
                if text:
                    cleaned.append(text)
            if cleaned:
                out[key] = cleaned
    return out


_PPT_SECTION_HEADINGS = {
    "what_breaks": (
        "What breaks today, and where exactly does it break?",
        "What breaks today",
        "What breaks",
    ),
    "what_you_built": (
        "What you built, in plain language.",
        "What you built",
        "What we built",
    ),
    "tools_used": ("Tools used, and for what", "Tools used"),
    "stack": ("Tech stack", "Front end"),
    "demo_status": ("What is demoable", "Built and demoable"),
    "technical_gap": ("Biggest technical gap", "Technical gap"),
}

_PPT_STOP_HEADINGS = tuple(
    heading for heads in _PPT_SECTION_HEADINGS.values() for heading in heads
) + (
    "Pitch",
    "Demo URL",
    "Demo moment",
    "Hardest input",
    "User outcome",
    "AI decision",
    "Team name",
    "Theme",
    "Problem statement ID",
)

_BREAKS_HINT = re.compile(
    r"\b(today|currently|right now|unsorted|hours before|shared inbox|delayed|hurts|"
    r"csat|sit[s]?\s+unsorted|feel it|costs roughly|figuring out what.?s urgent)\b",
    re.I,
)
_BUILT_HINT = re.compile(
    r"\b(reads|classifies|drafts|one click|we built|product screenshot|queue view|"
    r"approve,?\s*edit|within seconds|triage queue|insert (?:product )?screenshot)\b",
    re.I,
)


def _ppt_bullets(blob: str) -> list[str]:
    return [line.strip().lstrip("-•* ").strip() for line in (blob or "").splitlines() if line.strip()]


def separate_ppt_sections(ctx: dict[str, Any]) -> dict[str, Any]:
    """Keep slide-2 'what breaks' vs 'what you built' from leaking into each other."""
    out = dict(ctx)
    breaks = [str(x).strip() for x in (out.get("what_breaks") or []) if str(x).strip()] if isinstance(out.get("what_breaks"), list) else _ppt_bullets(str(out.get("what_breaks") or ""))
    built = [str(x).strip() for x in (out.get("what_you_built") or []) if str(x).strip()] if isinstance(out.get("what_you_built"), list) else _ppt_bullets(str(out.get("what_you_built") or ""))

    moved_built: list[str] = []
    kept_breaks: list[str] = []
    for line in breaks:
        if _BUILT_HINT.search(line) and not _BREAKS_HINT.search(line):
            moved_built.append(line)
        else:
            kept_breaks.append(line)
    moved_breaks: list[str] = []
    kept_built: list[str] = []
    for line in built:
        if _BREAKS_HINT.search(line) and not _BUILT_HINT.search(line):
            moved_breaks.append(line)
        else:
            kept_built.append(line)

    breaks = kept_breaks + [b for b in moved_breaks if b not in kept_breaks]
    built = kept_built + [b for b in moved_built if b not in kept_built]
    # Slide 2 in change_request.json uses four bullets each.
    if breaks:
        out["what_breaks"] = breaks[:4]
    if built:
        out["what_you_built"] = built[:4]
    return out


def extract_sample_ppt(notes: str) -> dict[str, Any]:
    text = notes.strip()
    kv = _kv(text)
    out: dict[str, Any] = {}
    for src, dest in _PPT_ALIASES.items():
        if src not in kv or dest in out:
            continue
        val = str(kv[src]).strip()
        if dest == "team_name" and (
            re.match(r"^(members?|details|name)$", val, re.I) or _looks_like_member_list(val)
        ):
            if _looks_like_member_list(val):
                out.setdefault("team_members", val)
            continue
        out[dest] = val
    for ident, headings in _PPT_SECTION_HEADINGS.items():
        blob = _section_blob(text, *headings, stop=_PPT_STOP_HEADINGS)
        if blob:
            out[ident] = _ppt_bullets(blob)

    if "problem_statement_id" not in out:
        m = re.search(r"\bPS[\s\-]?(\d{2,4})\b", text, re.I)
        if m:
            out["problem_statement_id"] = f"PS-{m.group(1)}"
    if "demo_url" not in out:
        m = re.search(r"https?://[^\s)>\]]+", text, re.I)
        if m:
            out["demo_url"] = m.group(0).rstrip(".,;")
    if "team_name" not in out:
        m = re.search(r"(?im)^team\s+name\s*[:\-]\s*([^\n]{2,60})\s*$", text)
        if m:
            out["team_name"] = m.group(1).strip(" .")
    if "theme" not in out:
        m = re.search(r"(?im)^(?:hackathon\s+)?theme\s*[:\-]\s*([^\n]{3,80})\s*$", text)
        if m:
            out["theme"] = m.group(1).strip(" .")
    if "team_members" not in out:
        m = re.search(r"(?im)^(?:team\s+members(?:\s+details)?|members)\s*[:\-]\s*([^\n]+)\s*$", text)
        if m:
            out["team_members"] = m.group(1).strip()
    if "pitch" not in out:
        m = re.search(
            r"(?i)\bfor\s+[^,]{3,80},\s+(?:our|we|the)\s+[^\n.]{10,160}",
            text,
        )
        if m:
            out["pitch"] = re.sub(r"\s+", " ", m.group(0)).strip(" .")
        else:
            for sent in re.split(r"(?<=[.!?])\s+", re.sub(r"\s*\n\s*", ". ", text)):
                sent = sent.strip(" .")
                if not (40 <= len(sent) <= 180):
                    continue
                if re.search(r"^(?:for\b|our solution\b)|(?:so that|we built|one[- ]sentence)", sent, re.I):
                    out["pitch"] = sent
                    break
    if "what_you_built" not in out:
        built = re.findall(r"(?:we built|what we built(?: is)?)\s+([^\n.]{10,180})", text, re.I)
        if built:
            out["what_you_built"] = [b.strip() for b in built]
    if "what_breaks" not in out:
        breaks = re.findall(
            r"((?:today|currently|right now)[^\n.]{10,160}|(?:sit|sits|sitting) unsorted[^\n.]{0,80})",
            text,
            re.I,
        )
        cleaned = [re.sub(r"\s+", " ", b).strip() for b in breaks if len(b.strip()) > 12]
        if cleaned:
            out["what_breaks"] = cleaned
    return separate_ppt_sections(sanitize_ppt_context(out))
