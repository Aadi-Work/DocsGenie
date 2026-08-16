"""
Hierarchical Document Parser

Parses PowerPoint textboxes and tables into hierarchical document trees.
Identifies sections, paragraphs, bullets, numbered lists, and other semantic elements.
"""

import re
from dataclasses import dataclass, field
from typing import Any, List, Optional, Dict, Tuple
from uuid import uuid4

try:
    from pptx.text.text import TextFrame
    from pptx.table import Table
    from pptx.oxml.xmlchemy import OxmlElement
except ImportError:  # pragma: no cover - dependency may be absent in some environments
    TextFrame = Any
    Table = Any
    OxmlElement = Any

from src.ppt_ai.document_tree import (
    DocumentNode,
    DocumentTree,
    ElementType,
    TextFormatting,
    Alignment,
    TableSemantics,
)
from src.ppt_ai.format_preservation import FormatExtractor


@dataclass
class SectionContentBlock:
    """Structured block extracted from a section body."""
    text: str
    is_heading: bool = False
    is_bullet: bool = False
    level: int = 0
    parent_index: Optional[int] = None
    children: List[int] = field(default_factory=list)
    index: int = 0


def analyze_section_content(content: str) -> List[SectionContentBlock]:
    """Parse section content into structured blocks with bullet hierarchy.

    The parser understands plain paragraphs, bullet points, and nested bullets
    that should be treated as child items of the nearest parent section or list item.
    """
    blocks: List[SectionContentBlock] = []
    current_section: Optional[SectionContentBlock] = None
    list_stack: List[SectionContentBlock] = []

    for raw_line in content.replace("\r\n", "\n").split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            continue

        # Headings are compact labels whose text is terminated by a colon.
        is_heading = (
            len(stripped) < 60
            and not stripped.startswith(("-", "•", "*"))
            and stripped.endswith(":")
        )

        if is_heading:
            block = SectionContentBlock(
                text=stripped.rstrip(":"),
                is_heading=True,
                is_bullet=False,
                level=-1,
                index=len(blocks),
            )
            blocks.append(block)
            current_section = block
            list_stack = []
            continue

        bullet_match = re.match(
            r"^(?P<indent>\s*)(?P<marker>[•\-*]|\d+\.|[a-zA-Z]\.|[ivxlcdm]+\.)(?:\s+)(?P<text>.+)$",
            line,
        )

        if bullet_match:
            indent = len(bullet_match.group("indent"))
            level = max(indent // 2, 0)
            text = bullet_match.group("text").strip()
            block = SectionContentBlock(
                text=text,
                is_heading=False,
                is_bullet=True,
                level=level,
                index=len(blocks),
            )

            while list_stack and list_stack[-1].level >= level:
                list_stack.pop()

            parent = list_stack[-1] if list_stack else current_section
            if parent is not None:
                block.parent_index = parent.index
                parent.children.append(block.index)

            list_stack.append(block)
            blocks.append(block)
            continue

        block = SectionContentBlock(
            text=stripped,
            is_heading=False,
            is_bullet=False,
            level=0,
            index=len(blocks),
        )

        if current_section is not None:
            block.parent_index = current_section.index
            current_section.children.append(block.index)
            list_stack = []

        blocks.append(block)

    return blocks


class DocumentParser:
    """Parses PowerPoint text frames into hierarchical document trees."""
    
    # Common bullet patterns
    BULLET_PATTERNS = [
        r"^[•\-\*]\s+",  # Bullet symbols
        r"^[\d]+\.\s+",  # Numbered lists
        r"^[a-zA-Z]\.\s+",  # Letter lists
        r"^[ivxlcdm]+\.\s+",  # Roman numerals
    ]
    
    # Heading indicators
    HEADING_PATTERNS = [
        r"^(#{1,6})\s+(.+)$",  # Markdown-style
        r"^(\w+[:\s]*)\s+$",  # Followed by colon
    ]
    
    # Section keywords that often indicate new sections
    SECTION_KEYWORDS = [
        "current status", "overview", "summary", "risks", "issues",
        "action items", "next steps", "timeline", "budget", "resources",
        "team", "dependencies", "assumptions", "constraints", "scope",
    ]
    
    def __init__(self, slide_index: int, shape_id: int, shape_name: str):
        self.slide_index = slide_index
        self.shape_id = shape_id
        self.shape_name = shape_name
        self.node_counter = 0
    
    def _generate_node_id(self) -> str:
        """Generate a unique node ID."""
        self.node_counter += 1
        return f"{self.shape_name}_{self.node_counter}_{uuid4().hex[:8]}"
    
    def _is_heading(self, text: str, level: int = 0, formatting: Optional[TextFormatting] = None) -> Tuple[bool, int]:
        """Detect if text is a heading."""
        text = text.strip()
        
        # Check markdown-style headings
        for pattern in self.HEADING_PATTERNS:
            match = re.match(pattern, text)
            if match:
                if pattern.startswith("^#"):
                    return True, len(match.group(1))
                else:
                    return True, 1
        
        # Check if text is a known section keyword (and is relatively short)
        if len(text) < 50:
            for keyword in self.SECTION_KEYWORDS:
                if text.lower().strip().startswith(keyword):
                    return True, 1
        
        # A short, emphatic paragraph is a reliable section signal.  Do not
        # treat every level-0 paragraph as a heading: that was the source of
        # sections swallowing their following body paragraphs.
        if formatting and level == 0 and formatting.bold and len(text) <= 80:
            # Bold, compact labels smaller than a normal slide heading are
            # subheadings.  This uses extracted typography rather than only
            # literal section names.
            heading_level = 2 if formatting.font_size and formatting.font_size <= 14 else 1
            return True, heading_level
        
        return False, 0
    
    def _detect_bullet_format(self, text: str) -> Optional[str]:
        """Detect and extract bullet format."""
        for pattern in self.BULLET_PATTERNS:
            match = re.match(pattern, text)
            if match:
                return match.group(0).strip()
        return None
    
    def _extract_bullet_text(self, text: str) -> str:
        """Extract text content after bullet marker."""
        for pattern in self.BULLET_PATTERNS:
            text = re.sub(pattern, "", text)
        return text.strip()
    
    def parse_text_frame(self, text_frame: TextFrame) -> DocumentTree:
        """Parse a text frame into a hierarchical document tree.

        This method is defensive: any unexpected errors during parsing will
        be caught and an empty `DocumentTree` will be returned to allow the
        rest of the pipeline to continue.  Warnings are printed so issues
        can be investigated.
        """
        tree = DocumentTree(
            slide_index=self.slide_index,
            shape_id=self.shape_id,
            shape_name=self.shape_name,
        )

        extractor = FormatExtractor()
        try:
            # A stable synthetic root lets several top-level sections coexist and
            # makes tree traversal independent of PowerPoint paragraph positions.
            root_id = self._generate_node_id()
            tree.add_node(DocumentNode(
                element_id=root_id,
                element_type=ElementType.SLIDE,
                text=self.shape_name,
                slide_index=self.slide_index,
                shape_id=self.shape_id,
                shape_name=self.shape_name,
                component_type="text_frame",
            ))

            # Track current section for grouping
            current_section_id: Optional[str] = None
            section_stack: List[Tuple[str, int]] = []  # (node_id, heading_level)

            for para_idx, paragraph in enumerate(text_frame.paragraphs):
                text = paragraph.text.strip()

                if not text:
                    continue

                # Extract formatting
                formatting = extractor.extract_paragraph_formatting(paragraph)
                level = paragraph.level if paragraph.level is not None else 0

                # Check if this is a heading
                is_heading, heading_level = self._is_heading(text, level, formatting)

                # Create node for this paragraph
                node_id = self._generate_node_id()

                if is_heading:
                    # Close deeper levels in stack
                    while section_stack and section_stack[-1][1] >= heading_level:
                        section_stack.pop()

                    # Create section node
                    node = DocumentNode(
                        element_id=node_id,
                        element_type=ElementType.SECTION,
                        text=text,
                        formatting=formatting,
                        slide_index=self.slide_index,
                        shape_id=self.shape_id,
                        shape_name=self.shape_name,
                        index_in_parent=para_idx,
                        is_heading=True,
                        heading_level=heading_level,
                    )

                    # Always attach top-level headings (level==1) to the synthetic
                    # root so they become siblings under the shape.  Only nested
                    # headings (level>1) are attached beneath the most recent
                    # section in the stack.
                    if heading_level == 1:
                        node.parent_id = root_id
                        tree.get_node(root_id).children_ids.append(node_id)
                    elif section_stack:
                        node.parent_id = section_stack[-1][0]
                        parent_node = tree.get_node(section_stack[-1][0])
                        if parent_node:
                            parent_node.children_ids.append(node_id)
                    else:
                        node.parent_id = root_id
                        tree.get_node(root_id).children_ids.append(node_id)

                    tree.add_node(node)
                    section_stack.append((node_id, heading_level))
                    current_section_id = node_id

                else:
                    # Check for bullet/numbered list.  Treat paragraphs with an
                    # explicit python-pptx `level` > 0 as native bullets too — some
                    # templates use indentation without glyphs.
                    bullet_format = self._detect_bullet_format(text)
                    is_native_bullet = extractor.is_bullet_paragraph(paragraph) or (level > 0)

                    if bullet_format or is_native_bullet:
                        # This is a list item
                        bullet_text = self._extract_bullet_text(text) if bullet_format else text
                        element_type = ElementType.BULLET_ITEM
                        parent_element_type = ElementType.BULLET_LIST

                    else:
                        # Regular paragraph
                        bullet_text = text
                        element_type = ElementType.PARAGRAPH
                        parent_element_type = None

                    # Create node
                    node = DocumentNode(
                        element_id=node_id,
                        element_type=element_type,
                        text=bullet_text,
                        formatting=formatting,
                        slide_index=self.slide_index,
                        shape_id=self.shape_id,
                        shape_name=self.shape_name,
                        index_in_parent=para_idx,
                        depth=level,
                    )

                    # Attach to current section (or synthetic root)
                    if current_section_id:
                        node.parent_id = current_section_id
                        parent_node = tree.get_node(current_section_id)
                        if parent_node:
                            parent_node.children_ids.append(node_id)
                    else:
                        node.parent_id = root_id
                        tree.get_node(root_id).children_ids.append(node_id)

                    tree.add_node(node)
            # end for paragraphs
        except Exception as e:
            print(f"[PARSER ERROR] shape={self.shape_name} slide={self.slide_index} error={e!r}")
            # Return the (possibly empty) tree rather than None so callers
            # don't need to handle a missing object.
            return tree

        return tree

    def parse_table(self, table: Table, table_name: str = "Table") -> TableSemantics:
        """Parse a table to understand its semantic structure."""
        print("\n" + "=" * 60)
        print(f"[TABLE CONTENT DEBUG]")
        print(f"Slide      : {self.slide_index}")
        print(f"Table name : {self.shape_name}")
        print(f"Shape ID   : {self.shape_id}")
        print(f"Rows       : {len(table.rows)}")
        print(f"Columns    : {len(table.columns)}")

        for r_idx, row in enumerate(table.rows):
            values = []

            for c_idx, cell in enumerate(row.cells):
                values.append(cell.text.strip())

            print(f"Row {r_idx}: {values}")

        print("=" * 60)        
        semantics = TableSemantics(
            table_id=self._generate_node_id(),
            slide_index=self.slide_index,
            shape_id=self.shape_id,
            shape_name=self.shape_name,
        )

        semantics.num_rows = len(table.rows)
        semantics.num_cols = len(table.columns) if table.rows else 0
        print(f"\n===== TABLE CONTENT =====")
        print(f"Slide: {self.slide_index}")
        print(f"Shape: {self.shape_name}")
        print(f"Rows: {semantics.num_rows}")
        print(f"Columns: {semantics.num_cols}")

        for row_idx, row in enumerate(table.rows):
            values = [cell.text.strip() for cell in row.cells]
            print(f"Row {row_idx}: {values}")

        print("=========================\n")

        # Extract headers (assume first row)
        if semantics.num_rows > 0:
            for col_idx, cell in enumerate(table.rows[0].cells):
                header_text = cell.text.strip()
                semantics.column_headers.append(header_text)

        # Extract row headers (assume first column)
        if semantics.num_rows > 1:
            for row_idx, row in enumerate(table.rows):
                if row_idx == 0:
                    continue
                header_text = row.cells[0].text.strip()
                semantics.row_headers.append(header_text)

        # Extract cell data
        for row_idx, row in enumerate(table.rows):
            for col_idx, cell in enumerate(row.cells):
                cell_value = cell.text.strip()

                # Get row and column headers
                row_header = (
                    semantics.row_headers[row_idx - 1]
                    if row_idx > 0 and row_idx - 1 < len(semantics.row_headers)
                    else ""
                )
                col_header = (
                    semantics.column_headers[col_idx]
                    if col_idx < len(semantics.column_headers)
                    else ""
                )

                semantics.cell_data[(row_idx, col_idx)] = {
                    "value": cell_value,
                    "row_header": row_header,
                    "col_header": col_header,
                    "row_index": row_idx,
                    "col_index": col_idx,
                }

        return semantics


class DocumentTreeBuilder:
    """High-level builder for creating document trees from presentation shapes."""
    
    @staticmethod
    def build_from_text_frame(
        slide_index: int,
        shape_id: int,
        shape_name: str,
        text_frame: TextFrame,
    ) -> DocumentTree:
        """Build a document tree from a text frame."""
        parser = DocumentParser(slide_index, shape_id, shape_name)
        return parser.parse_text_frame(text_frame)
    
    @staticmethod
    def build_from_table(
        slide_index: int,
        shape_id: int,
        shape_name: str,
        table: Table,
    ) -> TableSemantics:
        """Build table semantics from a table."""
        parser = DocumentParser(slide_index, shape_id, shape_name)
        return parser.parse_table(table, shape_name)

    @staticmethod
    def build_table_tree(slide_index: int, shape_id: int, shape_name: str, table: Table) -> DocumentTree:
        """Represent every table cell as an addressable semantic node."""
        tree = DocumentTree(slide_index=slide_index, shape_id=shape_id, shape_name=shape_name)
        # Template-based decks reuse the same shape name/id on every slide, so
        # the slide index must be part of the ID or cross-slide lookups (which
        # search all trees by node ID) collide and edit the wrong slide.
        table_id = f"slide{slide_index}_{shape_name}_table_{shape_id}"
        table_node = DocumentNode(
            element_id=table_id, element_type=ElementType.TABLE, text=shape_name,
            slide_index=slide_index, shape_id=shape_id, shape_name=shape_name,
            component_type="table",
        )
        tree.add_node(table_node)
        for row_index, row in enumerate(table.rows):
            row_id = f"{table_id}_row_{row_index}"
            row_node = DocumentNode(
                element_id=row_id, element_type=ElementType.TABLE_ROW, parent_id=table_id,
                slide_index=slide_index, shape_id=shape_id, shape_name=shape_name,
                index_in_parent=row_index, table_row_index=row_index,
            )
            table_node.children_ids.append(row_id)
            tree.add_node(row_node)
            for col_index, cell in enumerate(row.cells):
                cell_id = f"{row_id}_cell_{col_index}"
                cell_node = DocumentNode(
                    element_id=cell_id, element_type=ElementType.TABLE_CELL, parent_id=row_id,
                    text=cell.text, slide_index=slide_index, shape_id=shape_id, shape_name=shape_name,
                    index_in_parent=col_index, table_row_index=row_index, table_column_index=col_index,
                    table_header_row=row_index == 0, table_header_col=col_index == 0,
                    component_type="table_cell",
                )
                row_node.children_ids.append(cell_id)
                tree.add_node(cell_node)
        return tree
    
    @staticmethod
    def find_section(tree: DocumentTree, section_name: str) -> Optional[DocumentNode]:
        """Find a section by name (partial match)."""
        section_name_lower = section_name.lower()
        for node in tree.find_by_type(ElementType.SECTION):
            if section_name_lower in node.text.lower():
                return node
        return None
    
    @staticmethod
    def find_or_create_section(
        tree: DocumentTree,
        section_name: str,
        heading_level: int = 1,
    ) -> DocumentNode:
        """Find or create a section with the given name."""
        # Try to find existing
        section = DocumentTreeBuilder.find_section(tree, section_name)
        if section:
            return section
        
        # Create new section
        node_id = f"{tree.shape_name}_section_{uuid4().hex[:8]}"
        node = DocumentNode(
            element_id=node_id,
            element_type=ElementType.SECTION,
            text=section_name,
            formatting=TextFormatting(),
            slide_index=tree.slide_index,
            shape_id=tree.shape_id,
            shape_name=tree.shape_name,
            is_heading=True,
            heading_level=heading_level,
        )
        
        # Add to tree
        tree.add_node(node)
        
        # Set as root if no root exists
        if tree.root_id is None:
            tree.root_id = node_id
        
        return node
    
    @staticmethod
    def add_bullet_to_section(
        tree: DocumentTree,
        section_id: str,
        bullet_text: str,
        formatting: Optional[TextFormatting] = None,
    ) -> Optional[DocumentNode]:
        """Add a bullet point to a section."""
        section = tree.get_node(section_id)
        if not section:
            return None
        
        node_id = f"{section.shape_name}_bullet_{uuid4().hex[:8]}"
        node = DocumentNode(
            element_id=node_id,
            element_type=ElementType.BULLET_ITEM,
            parent_id=section_id,
            text=bullet_text,
            formatting=formatting or TextFormatting(),
            slide_index=section.slide_index,
            shape_id=section.shape_id,
            shape_name=section.shape_name,
            index_in_parent=len(section.children_ids),
        )
        
        section.children_ids.append(node_id)
        tree.add_node(node)
        
        return node
