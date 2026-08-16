"""Resolve a plain, slide-grouped request into the low-level change_request format.

Today's ``change_request.json`` requires the author to already know whether a
change targets free text (``anchor``) or a table cell (``entity`` + ``field``).
This module instead lets a request describe a slide the way a person would --
its heading/content in plain text, and its table contents as key/value pairs
-- in one entry:

    {"slide": 1, "heading": "New Generated DOC"}
    {"slide": 8, "table": {"33": "Updated: ..."}}

and figures out on its own how to resolve each one, reusing the same
``SemanticSchemaMatcher`` the rest of the pipeline already relies on so the
resolution logic isn't duplicated.

Each slide entry may combine:
  - ``heading``: plain new text, whole -- replaces the slide's title
    placeholder, no label needed.
  - ``content``: ``{label: new_value}`` -- plain text for anything else on the
    slide (subheadings, captions, body copy). ``label`` is the text (or a
    recognisable snippet of it) as it appears today.
  - ``table``: ``{key: value}`` -- a table row's one data column, keyed by
    whatever identifies that row (an ID, a name). Works for the common
    "key | value" two-column table shape; the matching column is found
    automatically. For a row with more than one column to update, nest it
    instead: ``{key: {column: value, column2: value2}}``.
  - ``sections``: ``{heading: {"points": [...]} | {"content": "..."}}`` --
    replaces everything *underneath* a heading (its bullets/paragraphs) as a
    unit, without touching any other heading's body on the slide or anywhere
    else in the deck. Give either a list of ``points`` (one bullet each) or
    free-form ``content`` text.
  - ``changes``: escape hatch for anything ambiguous; same ``{label, value}``
    form, resolved as either free text or a table row, whichever fits best.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .document_tree import ElementType
from .integrated_updater import PresentationUpdatePipeline
from .schema_matcher import SemanticSchemaMatcher, similarity


def build_change_request(simple_request: Dict[str, Any], presentation_path: str) -> Dict[str, Any]:
    """Turn a slide-grouped simple request into the ``{"changes": [...]}``
    form ``update_presentation_end_to_end`` already understands.
    """
    pipeline = PresentationUpdatePipeline(presentation_path)
    pipeline.parse_presentation()
    matcher = SemanticSchemaMatcher(pipeline.document_trees, pipeline.table_schemas)

    changes = []
    for slide_entry in simple_request.get("slides", []):
        slide_no = int(slide_entry["slide"])

        if "heading" in slide_entry:
            anchor_text = _find_heading_anchor(pipeline, slide_no)
            if anchor_text is None:
                raise ValueError(
                    f"Slide {slide_no} has no title placeholder or top-level heading to replace. "
                    "Use 'content' with a recognisable label instead."
                )
            changes.append({"anchor": anchor_text, "value": slide_entry["heading"], "slide": slide_no})

        for label, value in (slide_entry.get("content") or {}).items():
            changes.append({"anchor": str(label), "value": value, "slide": slide_no})

        table_value = slide_entry.get("table")
        if isinstance(table_value, list):
            # A full grid, fitted into the slide's table by position (row 0
            # given -> the table's first data row) and by column name --
            # unlike the keyed form, this can also overwrite the key column
            # itself, and doesn't require rows to have distinct keys.
            changes.extend(_fill_table_rows(pipeline, table_value, slide_no, str(slide_entry.get("near") or "")))
            table_value = None

        for label, value in (table_value or {}).items():
            if isinstance(value, dict):
                # A row in a table with more than two columns -- one column
                # can't be auto-guessed, so each field is named explicitly:
                # {"table": {"<row>": {"<column>": "<value>", ...}}}.
                for field, field_value in value.items():
                    target = matcher.resolve_table(str(label), str(field), slide_no)
                    if target is None:
                        raise ValueError(
                            f"Could not find a table cell matching row '{label}' and "
                            f"column '{field}' on slide {slide_no}."
                        )
                    changes.append({
                        "entity": target.row_label,
                        "field": target.field_label,
                        "value": field_value,
                        "slide": slide_no,
                    })
                continue
            resolved = _resolve_table_label(matcher, pipeline, str(label), slide_no)
            resolved["value"] = value
            resolved["slide"] = slide_no
            changes.append(resolved)

        for label, section_change in (slide_entry.get("sections") or {}).items():
            heading_text = _find_section_heading(pipeline, str(label), slide_no)
            if heading_text is None:
                raise ValueError(
                    f"Could not find a section heading matching '{label}' on slide {slide_no}. "
                    "A section's body must live in the same text box as its heading."
                )
            changes.append({
                "anchor": heading_text,
                "value": _section_value(section_change),
                "slide": slide_no,
                "mode": "section",
            })

        for item in slide_entry.get("changes", []):
            label = str(item["label"])
            resolved = _resolve_label(matcher, pipeline, label, slide_no)
            resolved["value"] = item["value"]
            resolved["slide"] = slide_no
            changes.append(resolved)

    return {
        "description": simple_request.get("description", ""),
        "presentation": simple_request.get("presentation", presentation_path),
        "output": simple_request.get("output"),
        "changes": changes,
    }


def _find_heading_anchor(pipeline: PresentationUpdatePipeline, slide_no: int) -> Optional[str]:
    """Return the slide's current title text, whole, so it can be used as an
    exact anchor (the whole-shape replace then swaps every paragraph in it).
    """
    slide = pipeline.prs.slides[slide_no - 1]
    for shape in slide.shapes:
        if getattr(shape, "is_placeholder", False):
            ph_type = str(getattr(shape.placeholder_format, "type", "")).upper()
            if "TITLE" in ph_type and getattr(shape, "has_text_frame", False):
                text = shape.text_frame.text.strip()
                if text:
                    return text

    # No placeholder metadata (common in decks exported from Google Slides) --
    # fall back to whichever shape holds the first top-level heading on this
    # slide, and return that shape's FULL text so the whole box gets
    # replaced, not just the heading's own paragraph.
    heading_shape_id = None
    for tree in pipeline.document_trees.get(slide_no, {}).values():
        for node in tree.nodes.values():
            if node.element_type == ElementType.SECTION and getattr(node, "heading_level", None) == 1:
                heading_shape_id = node.shape_id
                break
        if heading_shape_id is not None:
            break

    if heading_shape_id is not None:
        for shape in slide.shapes:
            if shape.shape_id == heading_shape_id and getattr(shape, "has_text_frame", False):
                text = shape.text_frame.text.strip()
                if text:
                    return text
    return None


def _find_section_heading(pipeline: PresentationUpdatePipeline, label: str, slide_no: int) -> Optional[str]:
    """Find the text on this slide that most likely names the section to
    replace -- a heading with its own body beneath it (``ElementType.SECTION``),
    or, when a shape has no heading of its own (a flat bullet list sitting
    directly under the shape's root, as some templates use), any one of its
    paragraphs/bullets -- matching that shape's whole body still gets
    replaced, via ``TreePlanner.replace_section_body``'s parent fallback.
    """
    best_score = 0.0
    best_text: Optional[str] = None
    for tree in pipeline.document_trees.get(slide_no, {}).values():
        for node in tree.nodes.values():
            if node.element_type not in {ElementType.SECTION, ElementType.PARAGRAPH, ElementType.BULLET_ITEM}:
                continue
            score = similarity(label, node.text)
            if score > best_score:
                best_score = score
                best_text = node.text
    return best_text if best_score >= SemanticSchemaMatcher.MIN_TEXT_SCORE else None


def _section_value(section_change: Any) -> str:
    """Turn a section's ``points``/``content`` shorthand into the plain text
    ``replace_section_body`` expects (it re-splits this into bullets and
    paragraphs itself via ``analyze_section_content``).
    """
    if isinstance(section_change, str):
        return section_change
    if isinstance(section_change, dict):
        if "points" in section_change:
            return "\n".join(f"- {point}" for point in section_change["points"])
        if "content" in section_change:
            return str(section_change["content"])
    raise ValueError(
        "A section change needs either a 'points' list (one bullet each) "
        "or a 'content' string."
    )


def _match_column(schema, label: str) -> Optional[int]:
    best_score, best_idx = 0.0, None
    for idx, header in enumerate(schema.column_headers):
        score = similarity(label, header)
        if score > best_score:
            best_score, best_idx = score, idx
    return best_idx if best_score >= SemanticSchemaMatcher.MIN_TABLE_SCORE else None


def _nearby_caption(pipeline: PresentationUpdatePipeline, slide_no: int, shape_id: int) -> str:
    """The nearest text sitting above a shape -- the caption a person looking
    at the slide would actually read to tell tables apart, since PowerPoint's
    internal shape names ("Table 7") are invisible without opening the
    Selection Pane.
    """
    slide = pipeline.prs.slides[slide_no - 1]
    target = next((s for s in slide.shapes if s.shape_id == shape_id), None)
    if target is None:
        return ""
    best_gap, best_text = None, ""
    for shape in slide.shapes:
        if shape.shape_id == shape_id or getattr(shape, "has_table", False):
            continue
        if not getattr(shape, "has_text_frame", False):
            continue
        text = shape.text_frame.text.strip()
        if not text:
            continue
        gap = target.top - shape.top
        if gap <= 0:
            continue
        if best_gap is None or gap < best_gap:
            best_gap, best_text = gap, text.splitlines()[0]
    return best_text


def _fill_table_rows(
    pipeline: PresentationUpdatePipeline, rows: list, slide_no: int, near_hint: str = ""
) -> list:
    """Fit a list of ``{column: value}`` row dicts into the slide's table,
    matched by position -- row 0 given becomes the table's first data row,
    row 1 the second, and so on -- with each column matched by name. If the
    slide has several tables, the one whose columns best match the given
    column names is picked -- unless two or more tables tie (identical or
    near-identical headers), in which case guessing would risk silently
    overwriting the wrong one, so a ``near`` hint (text visible just above
    the intended table, e.g. a caption or section title) is required instead.
    """
    candidates = [
        (shape_name, schema)
        for (schema_slide, shape_name), schema in pipeline.table_schemas.items()
        if schema_slide == slide_no
    ]
    if not candidates:
        raise ValueError(f"No table found on slide {slide_no}.")

    def caption_of(shape_name: str) -> str:
        shape_id = pipeline.document_trees[slide_no][shape_name].shape_id
        return _nearby_caption(pipeline, slide_no, shape_id)

    # Columns decide the candidate pool first -- a caption never overrides an
    # actual column mismatch (two tables can share one caption, like a
    # "before"/"after" pair placed side by side under the same heading).
    # "near" only breaks ties *within* the best-matching group.
    given_columns = {str(col) for row in rows for col in row.keys()}

    def table_score(schema) -> float:
        return sum(
            max((similarity(col, header) for header in schema.column_headers), default=0.0)
            for col in given_columns
        )

    scored = [(name, s, table_score(s)) for name, s in candidates]
    top_score = max(score for _, _, score in scored)
    tied = [(name, s) for name, s, score in scored if score >= top_score - 1e-9]

    if top_score < len(given_columns) * SemanticSchemaMatcher.MIN_TABLE_SCORE:
        raise ValueError(
            f"Slide {slide_no} has {len(candidates)} tables and none clearly match the "
            f"given columns ({sorted(given_columns)}). Name columns as they appear in the target table."
        )

    if len(tied) == 1:
        shape_name, schema = tied[0]
    elif near_hint:
        shape_name, schema = max(tied, key=lambda item: similarity(near_hint, caption_of(item[0])))
        if similarity(near_hint, caption_of(shape_name)) < SemanticSchemaMatcher.MIN_TEXT_SCORE:
            options = ", ".join(f"'{caption_of(name)}'" for name, _ in tied)
            raise ValueError(
                f"No equally-matching table on slide {slide_no} sits near text matching "
                f"'{near_hint}'. Options here: {options}."
            )
    else:
        options = ", ".join(f"'{caption_of(name)}'" for name, _ in tied)
        raise ValueError(
            f"Slide {slide_no} has {len(tied)} tables with equally-matching columns. Add a "
            f"'near' hint naming the text just above the one you mean -- options here: {options}."
        )
    tree = pipeline.document_trees[slide_no][shape_name]

    data_row_indices = sorted({
        node.table_row_index for node in tree.nodes.values()
        if node.element_type == ElementType.TABLE_CELL and node.table_row_index != schema.header_row_index
    })
    if len(rows) > len(data_row_indices):
        raise ValueError(
            f"Slide {slide_no}'s table only has {len(data_row_indices)} data rows, "
            f"but {len(rows)} rows were given. Adding or removing rows isn't supported yet."
        )

    changes = []
    for row_data, row_idx in zip(rows, data_row_indices):
        for col_label, value in row_data.items():
            col_idx = _match_column(schema, str(col_label))
            if col_idx is None:
                raise ValueError(f"Column '{col_label}' not found on slide {slide_no}'s table.")
            cell_node = next(
                (
                    node for node in tree.nodes.values()
                    if node.element_type == ElementType.TABLE_CELL
                    and node.table_row_index == row_idx and node.table_column_index == col_idx
                ),
                None,
            )
            if cell_node is None:
                continue
            changes.append({"cell_id": cell_node.element_id, "value": value, "slide": slide_no})
    return changes


def _best_table_match(matcher: SemanticSchemaMatcher, pipeline: PresentationUpdatePipeline, label: str, slide_no: int):
    """Find the table row this label most likely names, and the one data
    column it should update. Only tables with a single data column (the
    common "key | value" shape) can be resolved without an explicit field --
    anything wider is genuinely ambiguous from a label alone.
    """
    best_score = 0.0
    best_target = None
    for (schema_slide, _shape_name), schema in pipeline.table_schemas.items():
        if schema_slide != slide_no or len(schema.column_headers) != 2:
            continue
        for row_header in schema.row_headers:
            score = similarity(label, row_header)
            if score <= best_score:
                continue
            header_col = schema.header_col_index if schema.header_col_index in (0, 1) else 0
            data_col = 1 - header_col
            target = matcher.resolve_table(row_header, schema.column_headers[data_col], slide_no)
            if target is not None:
                best_score = score
                best_target = target
    return best_target, best_score


def _resolve_header_row_cell(pipeline: PresentationUpdatePipeline, label: str, slide_no: int):
    """Fall back for small "key | value" tables that have no real header row
    -- every row, including row 0, is its own property (e.g. "Project Name"
    paired with "YNS-FO-GT" right next to it). ``TableSchemaBuilder`` always
    treats row 0 as column headers, so a key sitting in row 0 is invisible to
    ``resolve_table``/``row_headers``; this looks it up directly by table
    coordinates instead.
    """
    best_score = 0.0
    best_cell_id = None
    for (schema_slide, shape_name), schema in pipeline.table_schemas.items():
        if schema_slide != slide_no or len(schema.column_headers) != 2:
            continue
        tree = pipeline.document_trees.get(slide_no, {}).get(shape_name)
        if tree is None:
            continue
        for col_idx, header_text in enumerate(schema.column_headers):
            score = similarity(label, header_text)
            if score <= best_score:
                continue
            value_col = 1 - col_idx
            cell_node = next(
                (
                    node for node in tree.nodes.values()
                    if node.element_type == ElementType.TABLE_CELL
                    and node.table_row_index == schema.header_row_index
                    and node.table_column_index == value_col
                ),
                None,
            )
            if cell_node is not None:
                best_score = score
                best_cell_id = cell_node.element_id
    return best_cell_id, best_score


def _resolve_table_label(matcher: SemanticSchemaMatcher, pipeline: PresentationUpdatePipeline, label: str, slide_no: int) -> Dict[str, Any]:
    target, row_score = _best_table_match(matcher, pipeline, label, slide_no)
    cell_id, header_score = _resolve_header_row_cell(pipeline, label, slide_no)

    if cell_id is not None and header_score >= row_score:
        return {"cell_id": cell_id}
    if target is not None:
        return {"entity": target.row_label, "field": target.field_label}

    raise ValueError(
        f"Could not find a table row matching '{label}' on slide {slide_no}. "
        "If the table has more than two columns, use the detailed "
        "{'entity': ..., 'field': ...} form in change_request.json instead."
    )


def _resolve_label(matcher: SemanticSchemaMatcher, pipeline: PresentationUpdatePipeline, label: str, slide_no: int) -> Dict[str, Any]:
    text_target = matcher.resolve_text(label, slide=slide_no)
    text_score = text_target.score if text_target else 0.0

    table_target, table_score = _best_table_match(matcher, pipeline, label, slide_no)

    if table_target is not None and table_score >= text_score:
        return {"entity": table_target.row_label, "field": table_target.field_label}
    if text_target is not None:
        return {"anchor": label}

    raise ValueError(
        f"Could not find anything matching '{label}' on slide {slide_no}. "
        "If this is a row in a table with more than two columns, use the "
        "detailed {'entity': ..., 'field': ...} form in change_request.json instead."
    )
