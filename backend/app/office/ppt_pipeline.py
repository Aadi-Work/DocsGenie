"""Fill Sample_ppt.pptx the same way the original PPT-AI simple_request pipeline did.

The original `input/simple_request.json` used the template's own placeholder
strings as labels. Content that lives in a two-column table is a table row;
everything else is a text anchor. Short labels like "Theme" must not match the
footer that merely *contains* those words.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path
from typing import Any

VENDOR_ROOT = Path(__file__).resolve().parents[2] / "vendor" / "pptmaker"
if str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))

# Exact labels from the original PPTMAKER simple_request.json / Sample_ppt.pptx.
FOOTER_LABEL = "Team name   |   Problem Statement ID   |   Theme"
PITCH_LABEL = "One sentence, max 20 words: for <who>, our solution <does what> so that <what changes>."
BREAKS_HEADING = "What breaks today, and where exactly does it break?"
BUILT_HEADING = "What you built, in plain language."
TOOLS_HEADING = "Tools used, and for what"
FLOW_LABEL = "Replace these four boxes with your real components — name the service or library in each."
TODAY_LABEL = "What exists today — a tool, a vendor, a spreadsheet, a person — and what it cannot do."
WIN_LABEL = "The one dimension you clearly win on: speed, accuracy, cost, coverage or effort."
CLONE_LABEL = "Domain logic, data, workflow fit — why a weekend clone would not match this."
NEXT_LABEL = (
    "One sentence: what you need to take this forward — a data owner, a pilot team, an AWS account, two more weeks."
)
AI_DECISION_LABEL = "Which decision, judgement or generation does the model actually make? Name the single step."
WHY_NOT_RULES_LABEL = "What could an if-else or a SQL query never have done here? Be specific."
AI_STACK_LABEL = "Service used, and prompt / RAG / agent / fine-tune. PII handling, grounding, who overrides it."


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(_text(item) for item in value if _text(item)).strip()
    return str(value).strip()


def _lines(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, dict):
                line = " | ".join(str(v).strip() for v in item.values() if str(v).strip())
                if line:
                    out.append(line)
            else:
                text = str(item).strip().lstrip("-•* ").strip()
                if text:
                    out.append(text)
        return out
    raw = str(value).strip()
    if "\n" in raw:
        return [line.strip().lstrip("-•* ").strip() for line in raw.splitlines() if line.strip()]
    return [raw] if raw else []


def _section_points(value: Any, limit: int = 4) -> list[str]:
    """Slide-2 sections in change_request.json are 3-4 dash bullets, not one blob."""
    lines = _lines(value)
    if len(lines) == 1 and len(lines[0]) > 140:
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", lines[0]) if s.strip()]
        if len(sents) >= 2:
            lines = sents
    return lines[:limit] if limit else lines


def _members(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    if "•" in text:
        return " • ".join(part.strip() for part in text.split("•") if part.strip())
    if "\n" in text:
        return " • ".join(line.strip().lstrip("-•* ").strip() for line in text.splitlines() if line.strip())
    if "," in text:
        return " • ".join(part.strip() for part in text.split(",") if part.strip())
    return text


def _prefixed(value: str, prefix: str) -> str:
    raw = _text(value)
    if not raw:
        return ""
    if raw.lower().startswith(prefix.lower()):
        return raw
    return f"{prefix}{raw}"


def _footer(ctx: dict[str, Any]) -> str:
    team = _text(ctx.get("team_name")) or "Team name"
    ps = _text(ctx.get("problem_statement_id")) or "Problem Statement ID"
    theme = _text(ctx.get("theme")) or "Theme"
    return f"{team}   |   {ps}   |   {theme}"


def build_simple_request(ctx: dict[str, Any]) -> dict[str, Any]:
    """Map employee-form answers onto the original simple_request.json shape."""
    footer = _footer(ctx)
    theme = _text(ctx.get("theme"))
    ps = _text(ctx.get("problem_statement_id"))
    members = _members(ctx.get("team_members"))

    slides: list[dict[str, Any]] = []

    slide1_content: dict[str, str] = {}
    if theme:
        slide1_content["Theme"] = _prefixed(theme, "Theme: ")
    if ps:
        slide1_content["Problem Statement ID"] = _prefixed(ps, "Problem Statement ID: ")
    if members:
        slide1_content["Team Members Details"] = members
    if slide1_content:
        slides.append({"slide": 1, "content": slide1_content})

    slide2: dict[str, Any] = {"slide": 2, "content": {FOOTER_LABEL: footer}}
    pitch = _text(ctx.get("pitch"))
    if pitch:
        slide2["content"][PITCH_LABEL] = pitch
    sections2: dict[str, Any] = {}
    breaks = _section_points(ctx.get("what_breaks"), 4)
    if breaks:
        sections2[BREAKS_HEADING] = {"points": breaks}
    built = _section_points(ctx.get("what_you_built"), 4)
    if built:
        sections2[BUILT_HEADING] = {"points": built}
    if sections2:
        slide2["sections"] = sections2
    slides.append(slide2)

    slide3_content: dict[str, str] = {FOOTER_LABEL: footer}
    for key, label in (
        ("demo_url", "Paste the full URL here"),
        ("demo_moment", "The moment the AI does its job — timestamp it."),
        ("hardest_input", "The hardest input you tested it against."),
        ("user_outcome", "What the user gets at the end, and how fast."),
    ):
        value = _text(ctx.get(key))
        if value:
            slide3_content[label] = value
    slides.append({"slide": 3, "content": slide3_content})

    slide4: dict[str, Any] = {"slide": 4, "content": {FOOTER_LABEL: footer}}
    for key, label in (
        ("ai_decision", AI_DECISION_LABEL),
        ("why_not_rules", WHY_NOT_RULES_LABEL),
        ("ai_stack", AI_STACK_LABEL),
    ):
        value = _text(ctx.get(key))
        if value:
            slide4["content"][label] = value
    tools = _lines(ctx.get("tools_used"))
    if tools:
        slide4["sections"] = {TOOLS_HEADING: {"points": tools}}
    slides.append(slide4)

    slide5: dict[str, Any] = {"slide": 5, "content": {FOOTER_LABEL: footer}}
    for key, label, prefix in (
        ("input_source", "INPUT / SOURCE", "INPUT / SOURCE — "),
        ("processing", "PROCESSING", "PROCESSING — "),
        ("ai_layer", "AI LAYER", "AI LAYER — "),
        ("output_user", "OUTPUT / USER", "OUTPUT / USER — "),
        ("flow_summary", FLOW_LABEL, ""),
    ):
        value = _text(ctx.get(key))
        if not value:
            continue
        slide5["content"][label] = _prefixed(value, prefix) if prefix else value
    sections5: dict[str, Any] = {}
    stack = _lines(ctx.get("stack"))
    if stack:
        sections5["Front end"] = {"points": stack}
    status = _lines(ctx.get("demo_status"))
    if status:
        sections5["Built and demoable"] = {"points": status}
    gap = _lines(ctx.get("technical_gap"))
    if gap:
        sections5["Biggest technical gap"] = {"points": gap}
    if sections5:
        slide5["sections"] = sections5
    slides.append(slide5)

    slide6_content: dict[str, str] = {FOOTER_LABEL: footer}
    for key, label in (
        ("today_limitation", TODAY_LABEL),
        ("win_dimension", WIN_LABEL),
        ("why_not_clone", CLONE_LABEL),
        ("hours_saved", "measured or estimated"),
        ("faster_response", "say which one, and how you know"),
        ("reach", "the reach if adopted"),
        ("next_need", NEXT_LABEL),
    ):
        value = _text(ctx.get(key))
        if value:
            slide6_content[label] = value
    slides.append({"slide": 6, "content": slide6_content})

    return {
        "description": _text(ctx.get("description")) or f"{_text(ctx.get('team_name')) or 'Hackathon'} deck fill",
        "slides": slides,
    }


def _content_score(label: str, candidate: str) -> float:
    """Prefer the placeholder itself over a longer string that merely contains it."""
    from src.ppt_ai.schema_matcher import normalise, similarity

    left, right = normalise(label), normalise(candidate)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if right.startswith(left) or left.startswith(right):
        return 0.97
    score = similarity(label, candidate)
    left_n, right_n = len(left.split()), len(right.split())
    if left_n <= 4 and right_n >= left_n * 3:
        score *= 0.35
    return score


def _resolve_content(pipeline: Any, matcher: Any, label: str, slide_no: int, value: str) -> dict[str, Any]:
    """Resolve a content label the way original simple_request.py does for `changes`.

    Table key|value rows win when the label names the row. Otherwise pick the
    shortest strong text match so "Theme" does not overwrite the footer.
    """
    from src.ppt_ai.document_tree import ElementType
    from src.ppt_ai.schema_matcher import SemanticSchemaMatcher
    from src.ppt_ai.simple_request import _resolve_header_row_cell, _best_table_match

    table_target, table_score = _best_table_match(matcher, pipeline, label, slide_no)
    cell_id, header_score = _resolve_header_row_cell(pipeline, label, slide_no)

    best_text = None
    best_text_score = 0.0
    for tree in pipeline.document_trees.get(slide_no, {}).values():
        if any(n.element_type == ElementType.TABLE for n in tree.nodes.values()):
            continue
        for node in tree.nodes.values():
            if node.element_type not in {ElementType.SECTION, ElementType.PARAGRAPH, ElementType.BULLET_ITEM}:
                continue
            score = _content_score(label, node.text)
            if score > best_text_score:
                best_text_score = score
                best_text = node.text

    table_best = max(table_score or 0.0, header_score or 0.0)
    if table_best >= SemanticSchemaMatcher.MIN_TABLE_SCORE and table_best + 0.02 >= best_text_score:
        if cell_id is not None and header_score >= (table_score or 0.0):
            return {"cell_id": cell_id, "value": value, "slide": slide_no}
        if table_target is not None:
            return {
                "entity": table_target.row_label,
                "field": table_target.field_label,
                "value": value,
                "slide": slide_no,
            }

    if best_text and best_text_score >= SemanticSchemaMatcher.MIN_TEXT_SCORE:
        return {"anchor": best_text, "value": value, "slide": slide_no}

    raise ValueError(f"Could not find '{label}' on slide {slide_no}.")


def _find_section_heading(pipeline: Any, label: str, slide_no: int) -> str | None:
    """Prefer the exact heading from change_request.json, not a similar body bullet."""
    from src.ppt_ai.document_tree import ElementType
    from src.ppt_ai.schema_matcher import SemanticSchemaMatcher, similarity

    want = (label or "").strip()
    best_exact = None
    best_section = None
    best_section_score = 0.0
    best_any = None
    best_any_score = 0.0
    for tree in pipeline.document_trees.get(slide_no, {}).values():
        for node in tree.nodes.values():
            text = (node.text or "").strip()
            if not text:
                continue
            if text == want or text.rstrip(".") == want.rstrip("."):
                if node.element_type == ElementType.SECTION:
                    return text
                best_exact = best_exact or text
                continue
            score = similarity(want, text)
            if node.element_type == ElementType.SECTION and score > best_section_score:
                best_section_score = score
                best_section = text
            elif node.element_type in {ElementType.PARAGRAPH, ElementType.BULLET_ITEM} and score > best_any_score:
                best_any_score = score
                best_any = text
    if best_exact:
        return best_exact
    if best_section and best_section_score >= SemanticSchemaMatcher.MIN_TEXT_SCORE:
        return best_section
    if best_any and best_any_score >= 0.92:
        return best_any
    return None


def apply_simple_request(source_pptx: Path, output_pptx: Path, simple_request: dict[str, Any]) -> Path:
    from src.ppt_ai.integrated_updater import PresentationUpdatePipeline
    from src.ppt_ai.schema_matcher import SemanticSchemaMatcher
    from src.ppt_ai.simple_request import _section_value

    pipeline = PresentationUpdatePipeline(str(source_pptx))
    pipeline.parse_presentation()
    pipeline.build_semantic_graph()
    matcher = SemanticSchemaMatcher(pipeline.document_trees, pipeline.table_schemas)

    changes: list[dict[str, Any]] = []
    errors: list[str] = []
    for slide_entry in simple_request.get("slides", []):
        slide_no = int(slide_entry["slide"])
        for label, value in (slide_entry.get("content") or {}).items():
            if value in (None, ""):
                continue
            try:
                changes.append(_resolve_content(pipeline, matcher, str(label), slide_no, str(value)))
            except ValueError as exc:
                errors.append(str(exc))
        for label, section_change in (slide_entry.get("sections") or {}).items():
            heading_text = _find_section_heading(pipeline, str(label), slide_no)
            if heading_text is None:
                errors.append(f"Could not find section '{label}' on slide {slide_no}.")
                continue
            changes.append(
                {
                    "anchor": heading_text,
                    "value": _section_value(section_change),
                    "slide": slide_no,
                    "mode": "section",
                }
            )

    if not changes:
        raise ValueError("No PPT placeholders could be matched. " + " ".join(errors[:4]))

    change_request = {
        "description": simple_request.get("description", ""),
        "presentation": str(source_pptx),
        "output": str(output_pptx),
        "changes": changes,
    }
    plan = pipeline.create_update_plan_from_json(change_request)
    pipeline.execute_plan(plan, dry_run=False)
    pipeline.validate_presentation()
    saved = pipeline.save_presentation(str(output_pptx))
    return Path(saved)


def fill_sample_ppt_file(template_path: Path, output_path: Path, context: dict[str, Any]) -> Path:
    from app.office.profiles import answers_for_fill, form_fields_to_context

    str_form = {
        str(k): (v if isinstance(v, str) else str(v))
        for k, v in (context or {}).items()
        if v not in (None, "", []) and not isinstance(v, (list, dict))
    }
    ctx = answers_for_fill({"profile_id": "sample_ppt"}, context) if context else {}
    if not any(ctx.get(k) for k in ("team_name", "theme", "pitch", "problem_statement_id")):
        ctx = {**form_fields_to_context("sample_ppt", str_form), **(context or {})}
    else:
        ctx = {**(context or {}), **ctx}

    simple_request = build_simple_request(ctx)
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / "Sample_ppt.pptx"
        staged.write_bytes(Path(template_path).read_bytes())
        dest = Path(tmp) / "filled.pptx"
        saved = apply_simple_request(staged, dest, simple_request)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(saved.read_bytes())
    return output_path
