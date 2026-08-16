"""Turn an Excel update template and MOM notes into semantic PPT changes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional


ENTITY_HEADERS = {"entity", "row", "row_match", "project", "item", "initiative", "application", "component"}
FIELD_HEADERS = {"field", "column", "column_name", "metric", "attribute", "target_field", "ppt_field"}
VALUE_HEADERS = {"value", "new_value", "update", "mom_value", "summary", "notes", "status"}
KEYWORD_HEADERS = {"keywords", "keyword", "mom_keywords", "section", "topic"}
SLIDE_HEADERS = {"slide", "slide_no", "slide_number", "ppt_slide"}
SHAPE_HEADERS = {"shape", "shape_name", "table", "table_name"}
ACTION_HEADERS = {"action", "mode", "replace_mode"}


@dataclass
class TemplateRow:
    sheet: str
    row_number: int
    values: Dict[str, Any]


def enrich_change_request_from_template_excel(change_request: Dict[str, Any], request_path: str) -> Dict[str, Any]:
    """Expand ``template_excel`` + MOM fields into regular semantic changes.

    The updater already knows how to place ``{entity, field, value}`` changes
    inside the scanned deck. This function only translates a friendlier user
    input format into that established contract.
    """
    if not change_request.get("template_excel"):
        return change_request

    workbook_path = _resolve_request_file(str(change_request["template_excel"]), request_path)
    mom_summary = _load_mom_summary(change_request, request_path)
    generated_changes = build_changes_from_excel_mom(workbook_path, mom_summary)

    merged = dict(change_request)
    existing_changes = merged.get("changes", merged.get("updates", merged.get("requests", [])))
    if isinstance(existing_changes, (str, dict)):
        existing_changes = [existing_changes]
    merged["changes"] = list(existing_changes or []) + generated_changes
    merged["excel_mom_generated_changes"] = generated_changes
    return merged


def build_changes_from_excel_mom(template_excel: str | Path, mom_summary: str = "") -> List[Dict[str, Any]]:
    rows = read_template_rows(template_excel)
    notes = parse_mom_summary(mom_summary)
    changes: List[Dict[str, Any]] = []

    for row in rows:
        entity = _first_value(row.values, ENTITY_HEADERS)
        field = _first_value(row.values, FIELD_HEADERS)
        if not entity or not field:
            continue

        explicit_value = _first_value(row.values, VALUE_HEADERS)
        keywords = _keywords_for_row(row, entity, field)
        inferred = infer_value_from_mom(notes, entity, field, keywords)
        value = explicit_value or inferred
        if not value:
            continue

        change: Dict[str, Any] = {
            "entity": entity,
            "field": field,
            "value": value,
            "source": "template_excel+mom_summary",
            "source_sheet": row.sheet,
            "source_row": row.row_number,
        }
        slide = _first_value(row.values, SLIDE_HEADERS)
        shape_name = _first_value(row.values, SHAPE_HEADERS)
        action = _first_value(row.values, ACTION_HEADERS)
        if slide:
            change["slide"] = slide
        if shape_name:
            change["shape_name"] = shape_name
        if action:
            change["action"] = action
        changes.append(change)

    return changes


def read_template_rows(template_excel: str | Path) -> List[TemplateRow]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError(
            "Excel template input needs openpyxl. Run: pip install -r requirements.txt"
        ) from exc

    workbook = load_workbook(template_excel, data_only=True)
    rows: List[TemplateRow] = []
    for worksheet in workbook.worksheets:
        raw_rows = list(worksheet.iter_rows(values_only=True))
        header_index = _find_header_row(raw_rows)
        if header_index is None:
            continue
        headers = [_normalise_header(cell) for cell in raw_rows[header_index]]
        for offset, raw in enumerate(raw_rows[header_index + 1 :], start=header_index + 2):
            values = {
                header: _clean_cell(value)
                for header, value in zip(headers, raw)
                if header and _clean_cell(value)
            }
            if values:
                rows.append(TemplateRow(worksheet.title, offset, values))
    return rows


def parse_mom_summary(text: str) -> Dict[str, List[str]]:
    sections: Dict[str, List[str]] = {"all": []}
    current = "all"
    for raw_line in (text or "").splitlines():
        line = raw_line.strip(" \t-*#")
        if not line:
            continue
        heading = re.match(r"^([A-Za-z0-9][A-Za-z0-9 /&()._-]{1,80}):$", line)
        if heading:
            current = _normalise_key(heading.group(1))
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
        sections["all"].append(line)
    return sections


def infer_value_from_mom(notes: Dict[str, List[str]], entity: str, field: str, keywords: Iterable[str]) -> str:
    haystacks = _candidate_lines(notes, keywords)
    entity_key = _normalise_key(entity)
    field_key = _normalise_key(field)

    best_line = ""
    best_score = 0
    for line in haystacks:
        line_key = _normalise_key(line)
        score = _token_overlap(entity_key, line_key) * 3 + _token_overlap(field_key, line_key) * 2
        if any(_normalise_key(keyword) in line_key for keyword in keywords):
            score += 2
        if score > best_score:
            best_score = score
            best_line = line

    if best_score <= 0:
        return ""

    labelled = re.search(
        rf"{re.escape(field)}\s*(?:=|:|-|is|to)\s*(.+)$",
        best_line,
        flags=re.IGNORECASE,
    )
    if labelled:
        return _trim_value(labelled.group(1))
    status = re.search(r"\b(on track|at risk|off track|blocked|delayed|complete|completed|green|amber|red)\b", best_line, re.I)
    if status and "status" in field_key:
        return status.group(1).title()
    return _trim_value(best_line)


def _resolve_request_file(value: str, request_path: str) -> Path:
    raw = Path(value).expanduser()
    candidates = [raw] if raw.is_absolute() else [Path.cwd() / raw, Path(request_path).resolve().parent / raw]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"Excel template not found: {value}")


def _load_mom_summary(change_request: Dict[str, Any], request_path: str) -> str:
    if change_request.get("mom_summary") is not None:
        return str(change_request.get("mom_summary") or "")
    if change_request.get("mom_summary_path"):
        path = _resolve_request_file(str(change_request["mom_summary_path"]), request_path)
        return path.read_text(encoding="utf-8")
    return ""


def _find_header_row(rows: List[tuple[Any, ...]]) -> Optional[int]:
    known = ENTITY_HEADERS | FIELD_HEADERS | VALUE_HEADERS | KEYWORD_HEADERS | SLIDE_HEADERS | SHAPE_HEADERS
    for index, row in enumerate(rows[:10]):
        headers = {_normalise_header(cell) for cell in row if cell is not None}
        if len(headers & known) >= 2:
            return index
    return 0 if rows else None


def _first_value(values: Dict[str, Any], headers: set[str]) -> str:
    for key, value in values.items():
        if key in headers:
            return str(value).strip()
    return ""


def _keywords_for_row(row: TemplateRow, entity: str, field: str) -> List[str]:
    raw = _first_value(row.values, KEYWORD_HEADERS)
    keywords = [entity, field, row.sheet]
    if raw:
        keywords.extend(part.strip() for part in re.split(r"[,;|]", raw) if part.strip())
    return keywords


def _candidate_lines(notes: Dict[str, List[str]], keywords: Iterable[str]) -> List[str]:
    lines = list(notes.get("all", []))
    for keyword in keywords:
        lines.extend(notes.get(_normalise_key(keyword), []))
    return lines


def _normalise_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _normalise_key(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()


def _clean_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _token_overlap(needle: str, haystack: str) -> int:
    tokens = {token for token in needle.split() if len(token) > 1}
    return len(tokens & set(haystack.split()))


def _trim_value(value: str) -> str:
    return re.split(r"\s+(?:owner|next steps?|due|eta)\s*[:=-]", value.strip(), maxsplit=1, flags=re.I)[0].strip(" .;-")
