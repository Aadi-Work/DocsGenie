"""The only adapter from the semantic document tree to python-pptx."""

from __future__ import annotations

from .document_tree import DocumentNode, DocumentTree, ElementType
from .format_preservation import FormatApplier

try:
    from pptx.oxml.xmlchemy import OxmlElement
except ImportError:  # pragma: no cover
    OxmlElement = None

try:
    from pptx.util import Emu, Pt
except ImportError:  # pragma: no cover
    Emu = Pt = None


class DocumentTreeRenderer:
    """Renders a changed tree to its original shape, preserving its geometry."""

    RENDERABLE_TEXT = {ElementType.SECTION, ElementType.PARAGRAPH, ElementType.BULLET_ITEM}

    def render_text_tree(self, shape_or_text_frame, tree: DocumentTree) -> None:
        shape = shape_or_text_frame if hasattr(shape_or_text_frame, "text_frame") else None
        text_frame = shape.text_frame if shape is not None else shape_or_text_frame
        changed = [node for node in tree.nodes.values() if "__changed__" in node.semantic_tags]
        if not changed:
            return
        structural = any("__structural__" in node.semantic_tags for node in changed)
        if not structural:
            self._render_changed_paragraphs(shape, text_frame, changed)
            return
        nodes = self._ordered_text_nodes(tree)
        paragraphs = list(text_frame.paragraphs)

        if not paragraphs:
            paragraphs.append(text_frame.add_paragraph())

        for index, node in enumerate(nodes):
            if index < len(paragraphs):
                paragraph = paragraphs[index]
            else:
                paragraph = text_frame.add_paragraph()
                paragraphs.append(paragraph)

            self._update_paragraph_text(paragraph, node.text)
            paragraph.level = node.formatting.indentation_level if node.element_type == ElementType.BULLET_ITEM else 0
            FormatApplier.apply_to_paragraph(paragraph, node.formatting)
            self._set_bullet(paragraph, node.element_type == ElementType.BULLET_ITEM)
            if shape is not None and "__status_badge__" in node.semantic_tags:
                self._apply_status_badge_style(shape, node.text)

        for paragraph in paragraphs[len(nodes):]:
            self._remove_paragraph(paragraph)

        if shape is not None:
            self._shrink_to_fit(shape, text_frame)

    def _shrink_to_fit(self, shape, text_frame) -> None:
        """Approximate PowerPoint's "shrink text on overflow".

        A rebuilt list can run longer than the placeholder text it replaced,
        but this template's boxes have a fixed-height background shape and
        no autofit configured, so overflowing text would spill past its
        border in any viewer -- not just PowerPoint, which is why this is
        computed here rather than left to a live autofit flag.
        """
        if Emu is None or Pt is None or not shape.height or not shape.width:
            return
        paragraphs = [p for p in text_frame.paragraphs if p.text.strip()]
        runs = [run for p in paragraphs for run in p.runs]
        sizes = [run.font.size.pt for run in runs if run.font.size]
        if not paragraphs or not sizes:
            return
        base_size = max(sizes)

        width_pt = max(Emu(shape.width).pt - 14.4, 10.0)  # ~0.1in margin each side
        available_pt = max(Emu(shape.height).pt - 7.2, 10.0)  # ~0.05in margin top/bottom
        avg_char_width_factor = 0.52
        line_spacing_factor = 1.25

        def estimated_height(font_size: float) -> float:
            chars_per_line = max(1, int(width_pt / (font_size * avg_char_width_factor)))
            total_lines = sum(max(1, -(-len(p.text) // chars_per_line)) for p in paragraphs)
            return total_lines * font_size * line_spacing_factor

        size = base_size
        while size > 8.0 and estimated_height(size) > available_pt:
            size -= 0.5

        if size < base_size:
            for run in runs:
                if run.font.size:
                    run.font.size = Pt(size)

    def _render_changed_paragraphs(self, shape, text_frame, nodes: list[DocumentNode]) -> None:
        """Patch ordinary text updates in place, retaining sibling layout."""
        paragraphs = list(text_frame.paragraphs)
        for node in nodes:
            if node.element_type not in self.RENDERABLE_TEXT:
                continue
            if 0 <= node.index_in_parent < len(paragraphs):
                paragraph = paragraphs[node.index_in_parent]
                self._update_paragraph_text(paragraph, node.text)
                if shape is not None and "__status_badge__" in node.semantic_tags:
                    self._apply_status_badge_style(shape, node.text)

    def _update_paragraph_text(self, paragraph, text: str) -> None:
        if not paragraph.runs:
            paragraph.add_run().text = text
            return

        # Do not split a replacement across the original runs.  A short value
        # such as "Delayed" was previously split as "D" / "elayed", which
        # wraps on separate lines in narrow table cells.
        runs = list(paragraph.runs)
        runs[0].text = text
        for run in runs[1:]:
            run.text = ""

    def _distribute_text_across_runs(self, runs, text: str, total_chars: int) -> None:
        if not text:
            for run in runs:
                run.text = ""
            return

        target_length = len(text)
        offset = 0

        for index, run in enumerate(runs):
            if index == len(runs) - 1:
                segment = text[offset:]
            else:
                proportion = (len(run.text or "") / total_chars) if total_chars else 1 / len(runs)
                length = int(round(proportion * target_length))
                length = max(0, min(length, target_length - offset))
                segment = text[offset:offset + length]

            run.text = segment
            offset += len(segment)

            if offset >= target_length:
                for later_run in runs[index + 1:]:
                    later_run.text = ""
                break

        if offset < target_length and runs:
            runs[-1].text += text[offset:]

    def _remove_paragraph(self, paragraph) -> None:
        p = paragraph._p
        parent = p.getparent()
        if parent is not None:
            parent.remove(p)

    def render_table_tree(self, table, tree: DocumentTree) -> None:
        for node in tree.nodes.values():
            if node.element_type == ElementType.TABLE_CELL and "__changed__" in node.semantic_tags:
                row, col = node.table_row_index, node.table_column_index
                if 0 <= row < len(table.rows) and 0 <= col < len(table.columns):
                    # Update the existing runs rather than assigning
                    # ``cell.text``; this retains fonts, margins, wrapping,
                    # paragraph spacing, and vertical alignment.
                    text_frame = table.cell(row, col).text_frame
                    paragraph = text_frame.paragraphs[0] if text_frame.paragraphs else text_frame.add_paragraph()
                    self._update_paragraph_text(paragraph, node.text)
                    for extra in list(text_frame.paragraphs[1:]):
                        self._remove_paragraph(extra)

    def _ordered_text_nodes(self, tree: DocumentTree) -> list[DocumentNode]:
        result: list[DocumentNode] = []

        def visit(node: DocumentNode) -> None:
            if node.element_type in self.RENDERABLE_TEXT:
                result.append(node)

            for child in tree.get_children(node.element_id):
                visit(child)

        if tree.root_id:
            visit(tree.get_node(tree.root_id))

        return result

    @staticmethod
    def _apply_status_badge_style(shape, status: str) -> None:
        """Keep a status badge's text and colour semantically consistent."""
        try:
            from pptx.dml.color import RGBColor
            palette = {
                "on track": RGBColor(0x70, 0xAD, 0x47),
                "at risk": RGBColor(0xFF, 0xC0, 0x00),
                "delayed": RGBColor(0xFF, 0xC0, 0x00),
                "off track": RGBColor(0xC0, 0x00, 0x00),
                "blocked": RGBColor(0xC0, 0x00, 0x00),
            }
            color = palette.get(status.casefold())
            if color is not None and getattr(shape, "fill", None) is not None:
                shape.fill.solid()
                shape.fill.fore_color.rgb = color
        except Exception:
            return

    @staticmethod
    def _set_bullet(paragraph, enabled: bool):
        if OxmlElement is None:
            return

        pPr = paragraph._p.get_or_add_pPr()
        try:
            # Remove only existing bullet-related children (buChar, buAutoNum, buBlip)
            for child in list(pPr):
                tag = child.tag.split("}")[-1]
                if tag in ("buChar", "buAutoNum", "buBlip"):
                    pPr.remove(child)

            if enabled:
                # Add a bullet character element under paragraph properties.
                bu = OxmlElement("a:buChar")
                bu.set("char", "•")
                pPr.append(bu)

                # Ensure the drawingml 'lvl' attribute reflects paragraph.level
                try:
                    level = int(getattr(paragraph, "level", 0) or 0)
                except Exception:
                    level = 0

                # DrawingML namespace for the 'lvl' attribute
                DML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
                pPr.set(f"{{{DML_NS}}}lvl", str(level))
        except Exception:
            # Avoid raising XML errors that would corrupt the file; silently
            # skip bullet adjustments if something unexpected occurs.
            return
