"""
Core data models used throughout PPT-AI.

These models are intentionally independent of python-pptx so that
higher level modules (relationship engine, semantic engine, AI, updater)
operate on a clean intermediate representation.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Any


# ============================================================
# Shape Roles
# ============================================================

class ShapeRole(Enum):
    UNKNOWN = "UNKNOWN"

    TITLE = "TITLE"
    SUBTITLE = "SUBTITLE"

    HEADER = "HEADER"
    FOOTER = "FOOTER"

    TEXT = "TEXT"

    TABLE = "TABLE"

    IMAGE = "IMAGE"

    CHART = "CHART"

    STATUS_BADGE = "STATUS_BADGE"

    LEGEND = "LEGEND"


# ============================================================
# Geometry
# ============================================================

@dataclass(slots=True)
class Geometry:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def center_x(self) -> float:
        return self.left + self.width / 2

    @property
    def center_y(self) -> float:
        return self.top + self.height / 2

    @property
    def area(self) -> int:
        return self.width * self.height


# ============================================================
# Style
# ============================================================

@dataclass(slots=True)
class FillStyle:
    color: Optional[str] = None
    transparency: Optional[float] = None


@dataclass(slots=True)
class LineStyle:
    color: Optional[str] = None
    width: Optional[float] = None


@dataclass(slots=True)
class FontStyle:
    name: Optional[str] = None
    size: Optional[float] = None
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    underline: Optional[bool] = None
    color: Optional[str] = None


# ============================================================
# Text
# ============================================================

@dataclass(slots=True)
class RunNode:
    text: str
    style: FontStyle


@dataclass(slots=True)
class ParagraphNode:
    text: str
    alignment: Optional[str]
    level: int
    runs: List[RunNode] = field(default_factory=list)


# ============================================================
# Table
# ============================================================

@dataclass(slots=True)
class TableCell:
    row: int
    col: int
    text: str


@dataclass(slots=True)
class TableNode:
    rows: int
    cols: int
    cells: List[TableCell] = field(default_factory=list)


# ============================================================
# Relationships
# ============================================================

class RelationshipType(Enum):

    NEAREST = "nearest"

    LEFT_OF = "left_of"

    RIGHT_OF = "right_of"

    ABOVE = "above"

    BELOW = "below"

    INSIDE = "inside"

    CONTAINS = "contains"

    OVERLAPS = "overlaps"

    ALIGNED_LEFT = "aligned_left"

    ALIGNED_RIGHT = "aligned_right"

    ALIGNED_TOP = "aligned_top"

    ALIGNED_BOTTOM = "aligned_bottom"

    SAME_ROW = "same_row"

    SAME_COLUMN = "same_column"

    BELONGS_TO = "belongs_to"

    OWNS = "owns"

    EXPLAINS = "explains"


@dataclass(slots=True)
class Relationship:

    source: int

    target: int

    relation: RelationshipType

    confidence: float = 1.0

    metadata: dict = field(default_factory=dict)


# ============================================================
# Shape
# ============================================================

@dataclass(slots=True)
class ShapeNode:

    id: int

    name: str

    shape_type: str

    role: ShapeRole

    geometry: Geometry

    paragraphs: List[ParagraphNode] = field(default_factory=list)

    table: Optional[TableNode] = None

    fill: Optional[FillStyle] = None

    line: Optional[LineStyle] = None

    relationships: List[Relationship] = field(default_factory=list)

    # NEW
    document_tree: Any = None

    # NEW
    table_semantics: Any = None

    metadata: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n".join(
            p.text
            for p in self.paragraphs
            if p.text.strip()
        ).strip()

# ============================================================
# Slide
# ============================================================

@dataclass(slots=True)
class SlideNode:

    slide_number: int

    shapes: List[ShapeNode] = field(default_factory=list)

    metadata: dict = field(default_factory=dict)


# ============================================================
# Presentation
# ============================================================

@dataclass(slots=True)
class PresentationNode:

    slides: List[SlideNode] = field(default_factory=list)

    metadata: dict = field(default_factory=dict)