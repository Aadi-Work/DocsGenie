"""Run-preserving PowerPoint text writes, adapted from PPTMAKER TextExtractor."""

from __future__ import annotations

from collections.abc import Iterator


def iter_text_frames(presentation) -> Iterator:
    """Yield every text frame on slides, including table cells and notes."""
    for slide in presentation.slides:
        yield from _shape_frames(slide.shapes)
        if getattr(slide, "has_notes_slide", False):
            notes_slide = slide.notes_slide
            frame = getattr(notes_slide, "notes_text_frame", None)
            if frame is not None:
                yield frame


def _shape_frames(shapes) -> Iterator:
    for shape in shapes:
        if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
            yield shape.text_frame
        if getattr(shape, "has_table", False) and shape.has_table:
            for row in shape.table.rows:
                for cell in row.cells:
                    yield cell.text_frame
        nested = getattr(shape, "shapes", None)
        if nested is not None:
            yield from _shape_frames(nested)


def set_paragraph_text(paragraph, text: str) -> None:
    """Write into run 0 so the template font/size/color survive."""
    if paragraph.runs:
        paragraph.runs[0].text = text
        for run in paragraph.runs[1:]:
            run.text = ""
        return
    paragraph.text = text
