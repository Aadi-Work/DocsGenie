"""
Adapter: TemplateHub-Agent engine + known-layout fillers.

Existing placeholder/label fill remains the fallback. This layer only adds
structural detection and layout-aware writes when they succeed.
"""

from __future__ import annotations

import logging
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.utils.file_utils import file_ext, snake

log = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_PROFILE_MATCHERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"mom[\s._-]*(sample|template)|minutes\s+of\s+meeting|meeting\s+minutes", re.I), "mom"),
    (re.compile(r"bfl[\s._-]*sample|business\s+function", re.I), "bfl"),
    (re.compile(r"poc[\s._-]*list|proof\s+of\s+concept", re.I), "poc"),
    (re.compile(r"brd[\s._-]*sample|business\s+requirements?", re.I), "brd"),
    (re.compile(r"sample[\s._-]*ppt|hackathon|salesforce[\s._-]*ppt", re.I), "sample_ppt"),
]

_ALIASES = {
    "date": "meeting_date",
    "meeting_date": "meeting_date",
    "purpose": "purpose",
    "venue": "venue",
    "prepared_by": "prepared_by",
    "attendees": "attendees_ymsli",
    "attendees_ymsli": "attendees_ymsli",
    "attendees_ymesg": "attendees_ymesg",
    "summary": "summary_items",
    "meeting_summary": "summary_items",
    "summary_items": "summary_items",
    "actions": "action_items",
    "action_items": "action_items",
    "action_plan": "action_items",
    "project": "project_name",
    "project_name": "project_name",
    "workstream": "workstream",
    "business_process": "business_process",
    "functions": "functions",
    "pocs": "pocs",
    "items": "items",
    "process_name": "process_name",
    "overview": "overview",
    "doc_code": "doc_code",
    "team": "team_name",
    "team_name": "team_name",
    "theme": "theme",
    "ps_id": "problem_statement_id",
    "problem_statement_id": "problem_statement_id",
    "members": "team_members",
    "team_members": "team_members",
    "pitch": "pitch",
    "what_breaks": "what_breaks",
    "what_you_built": "what_you_built",
}


def match_profile(filename: str) -> str | None:
    name = Path(filename or "").name
    try:
        from app.office.profiles import filler_kind, match_profile as match_s3_profile

        kind = filler_kind(match_s3_profile(filename=name))
        if kind:
            return kind
    except Exception:
        pass
    for pattern, kind in _PROFILE_MATCHERS:
        if pattern.search(name):
            return kind
    return None


def detect_engine_fields(filename: str, data: bytes) -> list[dict[str, Any]]:
    """Structural fields from the TemplateHub-Agent parser/spec builder."""
    ext = file_ext(filename)
    if ext not in {".docx", ".xlsx", ".pptx", ".xlsm", ".dotx", ".potx"}:
        return []
    src = _stage(filename, data)
    try:
        from engine.parsers import parse_template
        from engine.semantic.llm import NullLLM
        from engine.spec import RESERVED_SERIAL_NUMBER_KEY, SpecBuilder

        ir = parse_template(str(src))
        spec = SpecBuilder(llm=NullLLM()).build(ir)
        fields: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(ident: str, label: str, field_type: str = "string", required: bool = True) -> None:
            key = snake(ident)
            if not key or key in seen:
                return
            seen.add(key)
            nice = (label or ident).strip() or key
            fields.append(
                {
                    "id": key,
                    "label": nice,
                    "question": f"What is the {nice}?",
                    "required": required,
                    "field_type": field_type,
                    "source": "engine",
                }
            )

        for role, field in spec.fields.items():
            if not field.editable:
                continue
            add(field.label or role, field.label or role, field.value_format or "string", bool(field.critical))
        for role, table in spec.tables.items():
            add(role, table.section or role.replace("_", " "), "list", False)
            for col, meta in (table.columns or {}).items():
                if str(col).startswith("__") or col == RESERVED_SERIAL_NUMBER_KEY:
                    continue
                header = ""
                if isinstance(meta, dict):
                    header = str(meta.get("header") or meta.get("header_text") or col)
                else:
                    header = str(col)
                add(header or col, header or col, "string", False)
        return fields
    except Exception:
        log.exception("Engine field detection failed for %s", filename)
        return []
    finally:
        _cleanup(src.parent)


def smart_fill(filename: str, data: bytes, answers: dict[str, Any]) -> bytes | None:
    """Return filled bytes, or None so the caller can use placeholder fill."""
    if not answers or _preview_answers(answers):
        return None
    ext = file_ext(filename)
    if ext not in {".docx", ".xlsx", ".pptx", ".xlsm", ".dotx", ".potx"}:
        return None
    src = _stage(filename, data)
    dest = src.with_name(f"filled{src.suffix}")
    try:
        kind = match_profile(filename)
        if kind:
            try:
                if _fill_profile(kind, src, dest, answers):
                    return dest.read_bytes()
            except Exception:
                log.exception("Known-layout filler failed for %s", filename)
                if kind == "sample_ppt":
                    raise
            # BFL Sample.xlsx reports a million empty rows. Generic engine fill 500s.
            # Sample_ppt uses a dedicated pipeline; do not fall through to placeholder fill.
            if kind in {"bfl", "sample_ppt"}:
                return None
            return None
        try:
            filled = _fill_engine(src, dest, answers)
            if filled:
                return filled
        except Exception:
            log.exception("Engine fill failed for %s", filename)
        return None
    finally:
        _cleanup(src.parent)


def _fill_profile(kind: str, src: Path, dest: Path, answers: dict[str, Any]) -> bool:
    ctx = _context(answers)
    if kind == "sample_ppt":
        from app.office.profiles import answers_for_fill

        ctx = {**ctx, **answers_for_fill({"profile_id": "sample_ppt"}, answers)}
    needed = {
        "mom": ("meeting_date", "purpose", "prepared_by", "venue", "summary_items", "action_items", "attendees_ymsli", "attendees_ymesg"),
        "bfl": ("project_name", "workstream", "business_process", "functions"),
        "poc": ("project_name", "workstream", "pocs", "updated_by"),
        "brd": ("process_name", "prepared_by", "items", "overview", "doc_code"),
        "sample_ppt": ("team_name", "theme", "problem_statement_id", "pitch", "what_breaks", "what_you_built", "team_members"),
    }
    if not any(_usable(ctx.get(key)) for key in needed.get(kind, ())):
        return False
    from app.office.sample_fillers import (
        fill_bfl_sample,
        fill_brd_sample,
        fill_mom_sample,
        fill_poc_sample,
        fill_sample_ppt,
    )

    spec = _FillerSpec(id=src.stem)
    if kind == "mom":
        fill_mom_sample(spec, src, ctx, dest)
    elif kind == "bfl":
        fill_bfl_sample(spec, src, ctx, dest)
    elif kind == "poc":
        fill_poc_sample(spec, src, ctx, dest)
    elif kind == "brd":
        fill_brd_sample(spec, src, ctx, dest)
    elif kind == "sample_ppt":
        fill_sample_ppt(spec, src, ctx, dest)
    else:
        return False
    return dest.exists() and dest.stat().st_size > 0


def _fill_engine(src: Path, dest: Path, answers: dict[str, Any]) -> bytes | None:
    from engine.pipeline import EngineConfig, TemplateEngine
    from engine.semantic.llm import NullLLM

    cache = BACKEND_ROOT / "storage" / ".ote_cache" / "specs"
    cache.mkdir(parents=True, exist_ok=True)
    engine = TemplateEngine(
        EngineConfig(
            llm_provider="null",
            run_visual_qa=False,
            use_llm_for_source=False,
            use_llm_for_mapping=False,
            spec_cache_dir=str(cache),
            output_dir=str(src.parent),
        ),
        llm=NullLLM(),
    )
    spec = engine.analyze_template(str(src), use_cache=True, save_spec=True)
    result = engine.fill(
        str(src),
        source_text=_source_text(answers),
        output_path=str(dest),
        use_cache=True,
        save_artifacts=False,
        spec=spec,
        overrides=_overrides(spec, answers),
    )
    written = result.render.written if result.render else 0
    if written > 0 and dest.exists():
        return dest.read_bytes()
    return None


def _preview_answers(answers: dict[str, Any]) -> bool:
    values = [v for v in (answers or {}).values() if v not in (None, "")]
    if not values:
        return True
    return all(
        isinstance(v, str) and v.strip().startswith("[") and v.strip().endswith("]")
        for v in values
    )


def _usable(value: Any) -> bool:
    if value in (None, "", [], {}):
        return False
    if isinstance(value, str) and value.strip().startswith("[") and value.strip().endswith("]"):
        return False
    return True


def _context(answers: dict[str, Any]) -> dict[str, Any]:
    ctx: dict[str, Any] = {}
    for key, value in (answers or {}).items():
        if value in (None, ""):
            continue
        ctx[key] = value
        ctx[snake(str(key))] = value
    for src, dest in _ALIASES.items():
        if src in ctx and dest not in ctx:
            ctx[dest] = ctx[src]
    return ctx


def _source_text(answers: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, value in (answers or {}).items():
        if value in (None, ""):
            continue
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                if isinstance(item, dict):
                    lines.append("- " + " | ".join(str(v) for v in item.values() if v not in (None, "")))
                else:
                    lines.append(f"- {item}")
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)


def _overrides(spec: Any, answers: dict[str, Any]) -> dict[str, Any]:
    ctx = _context(answers)
    out: dict[str, Any] = {}
    fields = getattr(spec, "fields", {}) or {}
    tables = getattr(spec, "tables", {}) or {}
    for role, field in fields.items():
        for candidate in (role, getattr(field, "label", None), snake(role), snake(getattr(field, "label", "") or "")):
            if candidate and candidate in ctx:
                out[role] = ctx[candidate]
                break
    for role in tables:
        for candidate in (role, snake(role)):
            if candidate in ctx:
                out[role] = ctx[candidate]
                break
    for key, value in ctx.items():
        out.setdefault(key, value)
    return out


def _stage(filename: str, data: bytes) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="ymsli-office-"))
    name = Path(filename or "template.bin").name
    src = tmp / name
    if not src.suffix:
        src = tmp / f"template{file_ext(filename) or '.bin'}"
    src.write_bytes(data)
    return src


def _cleanup(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)


@dataclass
class _FillerSpec:
    id: str = ""
    filler: str | None = None
