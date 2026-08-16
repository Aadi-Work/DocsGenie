"""
Document Tree Module

Represents PowerPoint content as a hierarchical document structure.
Each element (section, paragraph, bullet, table, image) is a node in the tree.
This enables semantic queries and updates like "replace everything under Current Status".
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any
from datetime import datetime


class ElementType(Enum):
    """Types of elements in the document tree."""
    SECTION = "section"
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    BULLET_LIST = "bullet_list"
    BULLET_ITEM = "bullet_item"
    NUMBERED_LIST = "numbered_list"
    NUMBERED_ITEM = "numbered_item"
    TABLE = "table"
    TABLE_ROW = "table_row"
    TABLE_CELL = "table_cell"
    IMAGE = "image"
    CAPTION = "caption"
    NOTE = "note"
    RUN = "run"  # Smallest unit of text with consistent formatting
    SLIDE = "slide"
    TITLE = "title"
    SUBTITLE = "subtitle"
    FOOTER = "footer"
    STATUS_BADGE = "status_badge"
    CHART = "chart"
    TIMELINE = "timeline"


class TextStyle(Enum):
    """Text styling options."""
    BOLD = "bold"
    ITALIC = "italic"
    UNDERLINE = "underline"
    STRIKETHROUGH = "strikethrough"
    SUPERSCRIPT = "superscript"
    SUBSCRIPT = "subscript"


class Alignment(Enum):
    """Text alignment options."""
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    JUSTIFY = "justify"


@dataclass
class TextFormatting:
    """Preserves detailed text formatting information."""
    font_name: str = "Calibri"
    font_size: int = 11
    bold: bool = False
    italic: bool = False
    underline: bool = False
    color_rgb: Optional[tuple] = None  # (R, G, B)
    color_hex: Optional[str] = None
    background_color_rgb: Optional[tuple] = None
    alignment: Alignment = Alignment.LEFT
    line_spacing: float = 1.0
    indentation_level: int = 0
    indent_left: float = 0.0  # in EMUs or points
    indent_right: float = 0.0
    indent_first_line: float = 0.0
    bullet_format: Optional[str] = None  # e.g., "-", "•", "1.", "a)"
    hyperlink: Optional[str] = None
    styles: set = field(default_factory=set)  # Additional styles from TextStyle enum

    def copy(self) -> "TextFormatting":
        """Create a copy of this formatting."""
        return TextFormatting(
            font_name=self.font_name,
            font_size=self.font_size,
            bold=self.bold,
            italic=self.italic,
            underline=self.underline,
            color_rgb=self.color_rgb,
            color_hex=self.color_hex,
            background_color_rgb=self.background_color_rgb,
            alignment=self.alignment,
            line_spacing=self.line_spacing,
            indentation_level=self.indentation_level,
            indent_left=self.indent_left,
            indent_right=self.indent_right,
            indent_first_line=self.indent_first_line,
            bullet_format=self.bullet_format,
            hyperlink=self.hyperlink,
            styles=self.styles.copy(),
        )


@dataclass
class DocumentNode:
    """A node in the hierarchical document tree."""
    element_id: str
    element_type: ElementType
    parent_id: Optional[str] = None
    children_ids: List[str] = field(default_factory=list)
    
    # Content
    text: str = ""
    formatting: TextFormatting = field(default_factory=TextFormatting)
    
    # Metadata
    slide_index: int = 0
    shape_id: int = 0
    shape_name: str = ""
    
    # Position information
    index_in_parent: int = 0  # Order among siblings
    depth: int = 0  # Nesting level
    
    # For tables: semantic meaning
    table_header_row: bool = False
    table_header_col: bool = False
    table_column_index: int = -1
    table_row_index: int = -1
    table_cell_headers: Dict[str, Any] = field(default_factory=dict)  # Maps headers to values
    
    # For images
    image_path: Optional[str] = None
    image_caption: str = ""
    image_width: int = 0
    image_height: int = 0
    image_alt_text: str = ""
    
    # Timestamps
    created_at: datetime = field(default_factory=datetime.now)
    last_modified: datetime = field(default_factory=datetime.now)
    
    # Semantic attributes
    is_heading: bool = False
    heading_level: int = 0  # 1=H1, 2=H2, etc.
    semantic_role: str = ""  # e.g., "project_name", "status", "owner"
    
    # References and relationships
    references: List[str] = field(default_factory=list)  # IDs of referenced nodes
    semantic_tags: List[str] = field(default_factory=list)  # e.g., ["important", "todo", "deprecated"]

    # The component and layout analyzers enrich these without coupling the
    # document model to python-pptx.
    component_type: str = ""
    layout_region: str = "content"
    style_name: str = ""
    geometry: Dict[str, int] = field(default_factory=dict)


# Named nodes keep the public model expressive while retaining one indexed
# storage type for efficient graph/query operations.  They are intentionally
# data-only and have no python-pptx dependency.
class _TypedNode(DocumentNode):
    node_type: ElementType = ElementType.PARAGRAPH

    def __init__(self, element_id: str, **kwargs: Any):
        super().__init__(element_id=element_id, element_type=self.node_type, **kwargs)


class SlideNode(_TypedNode): node_type = ElementType.SLIDE
class SectionNode(_TypedNode): node_type = ElementType.SECTION
class ParagraphNode(_TypedNode): node_type = ElementType.PARAGRAPH
class BulletListNode(_TypedNode): node_type = ElementType.BULLET_LIST
class BulletNode(_TypedNode): node_type = ElementType.BULLET_ITEM
class TableNode(_TypedNode): node_type = ElementType.TABLE
class TableRowNode(_TypedNode): node_type = ElementType.TABLE_ROW
class TableCellNode(_TypedNode): node_type = ElementType.TABLE_CELL
class ImageNode(_TypedNode): node_type = ElementType.IMAGE
class ChartNode(_TypedNode): node_type = ElementType.CHART
class TimelineNode(_TypedNode): node_type = ElementType.TIMELINE
class StatusBadgeNode(_TypedNode): node_type = ElementType.STATUS_BADGE
class FooterNode(_TypedNode): node_type = ElementType.FOOTER


@dataclass
class DocumentTree:
    """Hierarchical representation of presentation content."""
    slide_index: int
    shape_id: int
    shape_name: str
    root_id: Optional[str] = None
    
    # All nodes indexed by ID for fast lookup
    nodes: Dict[str, DocumentNode] = field(default_factory=dict)
    
    # Metadata
    created_at: datetime = field(default_factory=datetime.now)
    version: int = 1
    
    def add_node(self, node: DocumentNode) -> None:
        """Add a node to the tree."""
        self.nodes[node.element_id] = node
        if self.root_id is None and node.parent_id is None:
            self.root_id = node.element_id

    def remove_subtree(self, node_id: str) -> None:
        """Remove a node and all of its descendants, preserving tree links."""
        node = self.get_node(node_id)
        if node is None:
            return
        for child_id in list(node.children_ids):
            self.remove_subtree(child_id)
        if node.parent_id:
            parent = self.get_parent(node_id)
            if parent and node_id in parent.children_ids:
                parent.children_ids.remove(node_id)
        self.nodes.pop(node_id, None)
        if self.root_id == node_id:
            self.root_id = None

    def descendants(self, node_id: str) -> List[DocumentNode]:
        """Return descendants in document order."""
        result: List[DocumentNode] = []
        for child in self.get_children(node_id):
            result.append(child)
            result.extend(self.descendants(child.element_id))
        return result
    
    def get_node(self, node_id: str) -> Optional[DocumentNode]:
        """Retrieve a node by ID."""
        return self.nodes.get(node_id)
    
    def get_children(self, node_id: str) -> List[DocumentNode]:
        """Get all direct children of a node."""
        node = self.get_node(node_id)
        if not node:
            return []
        return [self.nodes[cid] for cid in node.children_ids if cid in self.nodes]
    
    def get_parent(self, node_id: str) -> Optional[DocumentNode]:
        """Get the parent of a node."""
        node = self.get_node(node_id)
        if not node or not node.parent_id:
            return None
        return self.nodes.get(node.parent_id)
    
    def get_siblings(self, node_id: str) -> List[DocumentNode]:
        """Get all sibling nodes."""
        node = self.get_node(node_id)
        if not node or not node.parent_id:
            return []
        parent = self.get_parent(node_id)
        if not parent:
            return []
        return [self.nodes[cid] for cid in parent.children_ids if cid != node_id]
    
    def find_by_text(self, text: str, partial: bool = True) -> List[DocumentNode]:
        """Find nodes by text content."""
        results = []
        search_text = text.lower() if partial else text
        
        for node in self.nodes.values():
            node_text = node.text.lower() if partial else node.text
            if search_text in node_text:
                results.append(node)
        
        return results
    
    def find_by_type(self, element_type: ElementType) -> List[DocumentNode]:
        """Find all nodes of a specific type."""
        return [node for node in self.nodes.values() if node.element_type == element_type]
    
    def find_by_semantic_role(self, role: str) -> List[DocumentNode]:
        """Find nodes with a specific semantic role."""
        return [node for node in self.nodes.values() if node.semantic_role == role]
    
    def get_section_content(self, section_id: str) -> str:
        """Get all text content under a section."""
        section = self.get_node(section_id)
        if not section:
            return ""
        
        def collect_text(node_id: str) -> str:
            node = self.get_node(node_id)
            if not node:
                return ""
            
            text = node.text
            for child_id in node.children_ids:
                child_text = collect_text(child_id)
                if child_text:
                    text += "\n" + child_text
            
            return text
        
        return collect_text(section_id)
    
    def get_path_to_root(self, node_id: str) -> List[DocumentNode]:
        """Get the path from a node to the root (bottom-up)."""
        path = []
        current_id = node_id
        
        while current_id:
            node = self.get_node(current_id)
            if not node:
                break
            path.append(node)
            current_id = node.parent_id
        
        return path
    
    def get_subtree(self, node_id: str) -> "DocumentTree":
        """Extract a subtree rooted at a specific node."""
        subtree = DocumentTree(
            slide_index=self.slide_index,
            shape_id=self.shape_id,
            shape_name=self.shape_name,
            root_id=node_id,
        )
        
        def collect_nodes(nid: str) -> None:
            node = self.get_node(nid)
            if node:
                # Create a copy
                new_node = DocumentNode(
                    element_id=node.element_id,
                    element_type=node.element_type,
                    parent_id=node.parent_id,
                    children_ids=node.children_ids.copy(),
                    text=node.text,
                    formatting=node.formatting.copy(),
                    slide_index=node.slide_index,
                    shape_id=node.shape_id,
                    shape_name=node.shape_name,
                    index_in_parent=node.index_in_parent,
                    depth=node.depth,
                )
                subtree.add_node(new_node)
                
                for child_id in node.children_ids:
                    collect_nodes(child_id)
        
        collect_nodes(node_id)
        return subtree


@dataclass
class TableSemantics:
    """Semantic understanding of table structure and content."""
    table_id: str
    slide_index: int
    shape_id: int
    shape_name: str
    
    # Table structure
    num_rows: int = 0
    num_cols: int = 0
    header_row_index: int = 0
    header_col_index: int = -1
    
    # Header information
    column_headers: List[str] = field(default_factory=list)
    row_headers: List[str] = field(default_factory=list)
    
    # Cell mapping: (row, col) -> semantic meaning
    cell_semantics: Dict[tuple, str] = field(default_factory=dict)
    
    # Data access
    # Access cell by (row, col) -> (row_header, col_header, value)
    cell_data: Dict[tuple, Dict[str, Any]] = field(default_factory=dict)
    
    def get_cell_by_headers(self, row_header: str, col_header: str) -> Optional[Any]:
        """Find a cell by row and column header values."""
        for (row, col), data in self.cell_data.items():
            if data.get("row_header") == row_header and data.get("col_header") == col_header:
                return data.get("value")
        return None
    
    def set_cell_by_headers(self, row_header: str, col_header: str, value: Any) -> bool:
        """Update a cell by row and column header values."""
        for (row, col), data in self.cell_data.items():
            if data.get("row_header") == row_header and data.get("col_header") == col_header:
                data["value"] = value
                return True
        return False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "table_id": self.table_id,
            "slide_index": self.slide_index,
            "shape_id": self.shape_id,
            "shape_name": self.shape_name,
            "num_rows": self.num_rows,
            "num_cols": self.num_cols,
            "column_headers": self.column_headers,
            "row_headers": self.row_headers,
        }
