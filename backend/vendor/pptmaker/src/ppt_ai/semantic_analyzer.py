"""Map presentation components to business concepts without shape references."""

from __future__ import annotations

from .document_tree import DocumentTree, ElementType


class SemanticAnalyzer:
    STATUS_VALUES = {"on track", "at risk", "off track", "paused", "delayed", "in progress", "completed"}
    KEYWORDS = {
        "risk": "risk", "owner": "owner", "milestone": "milestone",
        "timeline": "timeline", "project": "project", "status": "status",
    }

    def analyze_tree(self, tree: DocumentTree) -> DocumentTree:
        for node in tree.nodes.values():
            text = node.text.strip().lower()
            if text in self.STATUS_VALUES or node.component_type == "status_badge":
                node.semantic_role = "status"
            elif node.element_type == ElementType.SECTION:
                node.semantic_role = next((role for word, role in self.KEYWORDS.items() if word in text), "section")
            elif node.element_type == ElementType.TABLE_CELL and node.table_header_row:
                node.semantic_role = next((role for word, role in self.KEYWORDS.items() if word in text), "table_header")
        return tree
