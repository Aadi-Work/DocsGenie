"""Infer semantic layout regions from extracted geometry, not raw coordinates."""

from __future__ import annotations

from .document_tree import DocumentTree


class LayoutAnalyzer:
    def analyze_tree(self, tree: DocumentTree, slide_width: int, slide_height: int) -> DocumentTree:
        for node in tree.nodes.values():
            top = node.geometry.get("top", 0)
            left = node.geometry.get("left", 0)
            if top < slide_height * .16:
                node.layout_region = "header"
            elif top > slide_height * .85:
                node.layout_region = "footer"
            elif left > slide_width * .7:
                node.layout_region = "sidebar"
            else:
                node.layout_region = "content"
        return tree
