"""Tree-only change execution.

This module is deliberately independent of ``python-pptx``.  A plan changes
DocumentNode objects; the renderer is the sole layer allowed to materialize
those changes in a presentation file.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from .change_plan import OperationType, UpdateOperation
from .document_parser import analyze_section_content
from .document_tree import DocumentNode, DocumentTree, ElementType


class TreePlanner:
    """Applies validated update operations to semantic document trees."""

    def __init__(self, trees: dict[int, dict[str, DocumentTree]]):
        self.trees = trees

    def find_tree(self, slide: int, shape_name: str) -> Optional[DocumentTree]:
        return self.trees.get(slide, {}).get(shape_name)

    def apply(self, operation: UpdateOperation) -> None:
        target = self._find_node(operation.target_id)
        if target is None:
            raise ValueError(f"Document node not found: {operation.target_id}")
        tree, node = target

        if operation.operation_type == OperationType.UPDATE_TEXT:
            node.text = str(operation.new_value)
        elif operation.operation_type == OperationType.DELETE_TEXT:
            tree.remove_subtree(node.element_id)
        elif operation.operation_type == OperationType.APPEND_TEXT:
            node.text += str(operation.new_value)
        elif operation.operation_type == OperationType.INSERT_TEXT:
            self._append_child(tree, node, str(operation.new_value), ElementType.PARAGRAPH)
        elif operation.operation_type == OperationType.UPDATE_TABLE_CELL:
            node.text = str(operation.new_value)
        elif operation.operation_type == OperationType.UPDATE_FORMATTING:
            for name, value in dict(operation.new_value or {}).items():
                if hasattr(node.formatting, name):
                    setattr(node.formatting, name, value)
        else:
            raise ValueError(f"Unsupported tree operation: {operation.operation_type.value}")
        # Rendering must only touch nodes changed by this operation.  Rewriting
        # an entire table resets cell runs and can make unrelated content
        # overlap or lose its original formatting.
        if "__changed__" not in node.semantic_tags:
            node.semantic_tags.append("__changed__")
        for tag in operation.tags:
            if tag not in node.semantic_tags:
                node.semantic_tags.append(tag)
        if operation.operation_type in {OperationType.INSERT_TEXT, OperationType.DELETE_TEXT}:
            if "__structural__" not in node.semantic_tags:
                node.semantic_tags.append("__structural__")
        if "replace_whole_shape" in operation.tags:
            # The anchor described the shape's full text, not one paragraph
            # within it, so collapse every sibling paragraph into this node.
            if "__structural__" not in node.semantic_tags:
                node.semantic_tags.append("__structural__")
            sibling_ids = [
                other.element_id for other in tree.nodes.values()
                if other.element_id != node.element_id
                and other.element_type in {ElementType.SECTION, ElementType.PARAGRAPH, ElementType.BULLET_ITEM}
            ]
            for sibling_id in sibling_ids:
                tree.remove_subtree(sibling_id)
        tree.version += 1

    def replace_section_body(self, section_id: str, new_text: str) -> None:
        target = self._find_node(section_id)
        if target is None:
            raise ValueError(f"Section not found: {section_id}")
        tree, node = target
        if node.element_type == ElementType.SECTION:
            # The usual case: a heading with its own body beneath it in the
            # same tree. Keep the heading, replace only its children.
            section = node
        else:
            # A flat list with no heading of its own -- every bullet/paragraph
            # sits directly under the shape's root. Matching one item still
            # means "replace the whole list", so retarget to its parent.
            section = tree.get_parent(node.element_id)
            if section is None:
                raise ValueError(f"Node has no parent to replace its body: {section_id}")
        old_body = [tree.get_node(child_id) for child_id in section.children_ids]
        old_body = [n for n in old_body if n is not None]
        # A section heading is not the body style.  Preserve the existing
        # body's font and bullet convention for replacement content.
        body_style = old_body[0].formatting.copy() if old_body else section.formatting.copy()
        default_type = old_body[0].element_type if old_body else ElementType.PARAGRAPH
        for child_id in list(section.children_ids):
            tree.remove_subtree(child_id)
        blocks = analyze_section_content(new_text)
        print("\n========== PARSED BLOCKS ==========")
        for block in blocks:
            print(
                f"text={repr(block.text)}, "
                f"is_bullet={block.is_bullet}, "
                f"level={block.level}, "
                f"parent={block.parent_index}"
            )
        print("===================================\n")
        created_nodes = {}

        for block in blocks:

            # determine parent
            if block.parent_index is None:
                parent = section
            else:
                parent = created_nodes[block.parent_index]

            element_type = (
                ElementType.BULLET_ITEM
                if block.is_bullet
                else ElementType.PARAGRAPH
            )
            print(
                f"Creating node -> "
                f"text={repr(block.text)}, "
                f"default_type={default_type}, "
                f"is_bullet={block.is_bullet}, "
                f"element_type={element_type}"
            )
            node = self._append_child(
                tree,
                parent,
                block.text,
                element_type,
                indentation_level=block.level if block.is_bullet else 0,
                formatting=body_style,
            )

            created_nodes[block.index] = node
        tree.version += 1
        for tag in ("__changed__", "__structural__"):
            if tag not in section.semantic_tags:
                section.semantic_tags.append(tag)

    def _find_node(self, node_id: str) -> Optional[tuple[DocumentTree, DocumentNode]]:
        for slide_trees in self.trees.values():
            for tree in slide_trees.values():
                node = tree.get_node(node_id)
                if node:
                    return tree, node
        return None

    @staticmethod
    def _append_child(
        tree: DocumentTree, parent: DocumentNode, text: str,
        element_type: ElementType, indentation_level: int = 0, formatting=None,
    ) -> DocumentNode:
        # Keep the parent section's body separate from nested sections.  A
        # new paragraph for "Overview" must appear before child sections
        # (for example, "Alarms Insights/Tasks"), not after the entire
        # Overview subtree.
        insert_at = len(parent.children_ids)
        resolved_formatting = formatting
        if parent.element_type == ElementType.SECTION:
            for index, child_id in enumerate(parent.children_ids):
                child = tree.get_node(child_id)
                if child is None:
                    continue
                if child.element_type == ElementType.SECTION:
                    insert_at = index
                    break
                # Section headings have a different format from their body.
                # Reuse the first body item's formatting for the new content.
                if resolved_formatting is None and child.element_type in {
                    ElementType.PARAGRAPH,
                    ElementType.BULLET_ITEM,
                }:
                    resolved_formatting = child.formatting

        node = DocumentNode(
            element_id=f"node_{uuid4().hex}", element_type=element_type,
            parent_id=parent.element_id, text=text,
            formatting=(resolved_formatting or parent.formatting).copy(), slide_index=parent.slide_index,
            shape_id=parent.shape_id, shape_name=parent.shape_name,
            index_in_parent=insert_at, depth=parent.depth + 1,
        )
        node.formatting.indentation_level = indentation_level
        parent.children_ids.insert(insert_at, node.element_id)
        for index, child_id in enumerate(parent.children_ids):
            child = tree.get_node(child_id)
            if child is not None:
                child.index_in_parent = index
        tree.add_node(node)
        return node
