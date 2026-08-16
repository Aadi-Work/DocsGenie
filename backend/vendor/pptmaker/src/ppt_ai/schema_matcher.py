"""Resolve a user's intent to stable nodes in the presentation AST.

This module deliberately has no python-pptx writes.  It is the boundary
between forgiving user requests and the deterministic document tree used by
the update engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import Any, Iterable, Optional

from .document_tree import DocumentNode, DocumentTree, ElementType


def normalise(value: Any) -> str:
    """Make labels comparable despite case, punctuation, and line wrapping."""
    value = re.sub(r"[^\w\s]", " ", str(value or "").casefold())
    return " ".join(value.split())


def similarity(query: Any, candidate: Any) -> float:
    """A compact, explainable fuzzy score in the range 0..1."""
    left, right = normalise(query), normalise(candidate)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        return 0.92
    left_words, right_words = set(left.split()), set(right.split())
    overlap = len(left_words & right_words) / max(len(left_words), 1)
    # A shared generic word (for example, "Project Status" and "Current
    # Status") is not enough evidence to edit content.  Character similarity
    # remains useful for one-word typos, while multi-word labels need strong
    # word agreement.
    if len(left_words) > 1 and len(right_words) > 1:
        return overlap
    return max(overlap, SequenceMatcher(None, left, right).ratio())


@dataclass(frozen=True)
class ResolvedTarget:
    slide: int
    shape_name: str
    node: DocumentNode
    score: float
    kind: str
    row_label: str = ""
    field_label: str = ""


class SemanticSchemaMatcher:
    """Find table cells and text nodes using semantic and spatial context."""

    MIN_TABLE_SCORE = 0.55
    MIN_TEXT_SCORE = 0.58

    def __init__(self, trees: dict[int, dict[str, DocumentTree]], schemas: dict[tuple[int, str], Any]):
        self.trees = trees
        self.schemas = schemas

    def resolve_table(
        self, entity: str, field: str, slide: Optional[int] = None, shape_name: str = ""
    ) -> Optional[ResolvedTarget]:
        """Find a table cell by its row entity and column field across the deck."""
        candidates: list[ResolvedTarget] = []
        for (slide_no, name), schema in self.schemas.items():
            if slide is not None and slide_no != slide:
                continue
            tree = self.trees.get(slide_no, {}).get(name)
            if tree is None:
                continue
            for row_label in schema.row_headers:
                row_score = similarity(entity, row_label)
                if row_score < self.MIN_TABLE_SCORE:
                    continue
                for field_label in schema.column_headers:
                    col_score = similarity(field, field_label)
                    if col_score < self.MIN_TABLE_SCORE:
                        continue
                    coordinates = schema.semantic_map.get((row_label, field_label))
                    if coordinates is None:
                        continue
                    row_idx, col_idx = schema.cell_roots.get(coordinates, coordinates)
                    node = self._table_node(tree, row_idx, col_idx)
                    if node is None:
                        continue
                    score = (row_score + col_score) / 2
                    if shape_name:
                        score += 0.08 * similarity(shape_name, name)
                    candidates.append(ResolvedTarget(slide_no, name, node, min(score, 1.0), "table", row_label, field_label))
        return max(candidates, key=lambda item: item.score, default=None)

    def resolve_text(
        self, anchor: str, slide: Optional[int] = None, shape_name: str = "", prefer_body: bool = False
    ) -> Optional[ResolvedTarget]:
        """Resolve text by its words, favouring nodes in a requested shape/slide.

        Paragraphs within the same shape are the AST equivalent of visual
        proximity, so an anchor match naturally reaches child content without
        any exposed paragraph index.
        """
        candidates: list[ResolvedTarget] = []
        for slide_no, slide_trees in self.trees.items():
            if slide is not None and slide_no != slide:
                continue
            for name, tree in slide_trees.items():
                if any(n.element_type == ElementType.TABLE for n in tree.nodes.values()):
                    continue
                text_nodes = [
                    node for node in tree.nodes.values()
                    if node.element_type in {ElementType.SECTION, ElementType.PARAGRAPH, ElementType.BULLET_ITEM}
                ]
                for node in text_nodes:
                    score = similarity(anchor, node.text)
                    if score < self.MIN_TEXT_SCORE:
                        continue
                    if shape_name:
                        score += 0.08 * similarity(shape_name, name)
                    if prefer_body and node.element_type != ElementType.SECTION:
                        score += 0.03
                    candidates.append(ResolvedTarget(slide_no, name, node, min(score, 1.0), "text"))

                # A title or caption is sometimes split across several
                # paragraphs (soft line breaks, an empty spacer line). If the
                # anchor describes the whole shape's text better than any one
                # paragraph inside it, replace the whole shape instead of the
                # single best-matching line.
                if len(text_nodes) > 1:
                    ordered = sorted(text_nodes, key=lambda n: n.index_in_parent)
                    combined = " ".join(n.text for n in ordered if n.text)
                    whole_score = similarity(anchor, combined)
                    best_single = max((similarity(anchor, n.text) for n in ordered), default=0.0)
                    if whole_score >= self.MIN_TEXT_SCORE and whole_score > best_single + 0.05:
                        score = whole_score
                        if shape_name:
                            score += 0.08 * similarity(shape_name, name)
                        candidates.append(
                            ResolvedTarget(slide_no, name, ordered[0], min(score, 1.0), "text_whole_shape")
                        )
        return max(candidates, key=lambda item: item.score, default=None)

    @staticmethod
    def _table_node(tree: DocumentTree, row: int, column: int) -> Optional[DocumentNode]:
        return next((node for node in tree.nodes.values() if node.element_type == ElementType.TABLE_CELL
                     and node.table_row_index == row and node.table_column_index == column), None)
