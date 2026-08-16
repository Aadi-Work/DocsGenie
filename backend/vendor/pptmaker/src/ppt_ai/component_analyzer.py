"""Classify presentation components from extracted tree/style/geometry data."""

from __future__ import annotations

from .document_tree import DocumentNode, DocumentTree, ElementType


class ComponentAnalyzer:
    """Deterministic baseline classifier; a vision model can enrich it later."""

    def analyze_tree(self, tree: DocumentTree, slide_height: int = 0) -> DocumentTree:
        for node in tree.nodes.values():
            node.component_type = self.classify(node, slide_height)
            if node.component_type in {"heading", "sub_heading"}:
                node.semantic_role = node.semantic_role or "section_heading"
        return tree

    @staticmethod
    def classify(node: DocumentNode, slide_height: int = 0) -> str:
        if node.element_type == ElementType.TABLE:
            return "table"
        if node.element_type == ElementType.IMAGE:
            return "image"
        if node.element_type == ElementType.CHART:
            return "chart"
        if node.element_type == ElementType.TIMELINE:
            return "timeline"
        if node.element_type == ElementType.STATUS_BADGE:
            return "status_badge"
        if node.element_type == ElementType.BULLET_ITEM:
            return "bullet"
        if node.element_type == ElementType.SECTION:
            return "heading" if node.heading_level <= 1 else "sub_heading"
        if node.element_type == ElementType.TITLE:
            return "title"
        if node.element_type == ElementType.FOOTER:
            return "footer"
        if slide_height and node.geometry.get("top", 0) > slide_height * .85:
            return "footer"
        return "paragraph"
