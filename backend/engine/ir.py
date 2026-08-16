"""
Universal Template IR (Intermediate Representation).

Every Office file (xlsx / docx / pptx) is parsed into the SAME graph shape.
The semantic layer is common; only `location` is format-specific.

    Document
      └── Section        (sheet / heading-section / slide)
           ├── Node      (label | value_region | static | image)
           └── TableIR   (header row + template row + columns)

Nothing in this module imports openpyxl / python-docx / python-pptx.
That is deliberate: the IR is the contract between parsers and everything else.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class DocType(str, Enum):
    XLSX = "xlsx"
    DOCX = "docx"
    PPTX = "pptx"


class NodeType(str, Enum):
    LABEL = "label"                # "Meeting Date:"  -> describes a neighbour
    VALUE_REGION = "value_region"  # the fillable target
    STATIC = "static"              # title / legal text / branding -> never touch
    TABLE = "table"                # handled by TableIR
    IMAGE = "image"
    SECTION_HEADER = "section_header"


class ValueFormat(str, Enum):
    TEXT = "text"
    DATE = "date"
    TIME = "time"
    NUMBER = "number"
    CURRENCY = "currency"
    PERCENT = "percent"
    BOOL = "bool"
    LIST = "list"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------
# Location: format-specific addressing, kept opaque to the semantic layer
# --------------------------------------------------------------------------
@dataclass
class Location:
    """
    Physical address of a node.

    xlsx : {sheet, cell, range}          e.g. sheet="MOM", cell="D6", range="D6:F6"
    docx : {kind: paragraph|table_cell, para_index, table_index, row, col}
    pptx : {slide_index, shape_id, shape_name, placeholder_idx, row, col}
    """
    doc_type: DocType
    parts: Dict[str, Any] = field(default_factory=dict)

    def key(self) -> str:
        if self.doc_type == DocType.XLSX:
            return f"{self.parts.get('sheet')}!{self.parts.get('range') or self.parts.get('cell')}"
        if self.doc_type == DocType.DOCX:
            if self.parts.get("kind") == "table_cell":
                return f"table[{self.parts.get('table_index')}]!r{self.parts.get('row')}c{self.parts.get('col')}"
            return f"paragraph[{self.parts.get('para_index')}]"
        return f"slide[{self.parts.get('slide_index')}]!shape[{self.parts.get('shape_id')}]"

    def to_dict(self) -> Dict[str, Any]:
        return {"doc_type": self.doc_type.value, **self.parts}


@dataclass
class StyleSignals:
    """Formatting facts the rule engine uses as *signals* (never as truth alone)."""
    bold: bool = False
    italic: bool = False
    underline: bool = False
    font_size: Optional[float] = None
    font_name: Optional[str] = None
    filled: bool = False
    bordered: bool = False
    merged: bool = False
    number_format: Optional[str] = None
    alignment: Optional[str] = None
    locked: bool = False
    indent: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Node:
    """A single addressable unit of the template."""
    node_id: str
    type: NodeType
    location: Location
    text: str = ""                       # literal text found in the template
    label: Optional[str] = None          # label governing this node
    label_node_id: Optional[str] = None
    semantic_role: Optional[str] = None  # e.g. "meeting_date" (filled by semantic engine)
    role_confidence: float = 0.0
    value_format: ValueFormat = ValueFormat.UNKNOWN
    editable: bool = True
    is_empty: bool = True
    has_formula: bool = False
    placeholder: Optional[str] = None    # {{...}} found literally in the template
    section: Optional[str] = None        # enclosing section title
    style: StyleSignals = field(default_factory=StyleSignals)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        d["value_format"] = self.value_format.value
        d["location"] = self.location.to_dict()
        return d


@dataclass
class ColumnIR:
    index: int                     # 0-based position inside the table
    header_text: str
    location_hint: Dict[str, Any]  # xlsx: {"column": "B"} / docx+pptx: {"col": 1}
    semantic_role: Optional[str] = None
    role_confidence: float = 0.0
    value_format: ValueFormat = ValueFormat.UNKNOWN
    editable: bool = True
    has_formula: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["value_format"] = self.value_format.value
        return d


@dataclass
class TableIR:
    """A repeating collection. Rows are cloned from `template_row`, never invented."""
    node_id: str
    location: Location
    columns: List[ColumnIR]
    header_row: int                     # absolute index in the host document
    template_row: int                   # the styled row that gets cloned
    existing_data_rows: int = 0
    semantic_role: Optional[str] = None  # e.g. "action_items"
    role_confidence: float = 0.0
    section: Optional[str] = None
    editable: bool = True
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "location": self.location.to_dict(),
            "columns": [c.to_dict() for c in self.columns],
            "header_row": self.header_row,
            "template_row": self.template_row,
            "existing_data_rows": self.existing_data_rows,
            "semantic_role": self.semantic_role,
            "role_confidence": self.role_confidence,
            "section": self.section,
            "editable": self.editable,
            "meta": self.meta,
        }


@dataclass
class Section:
    section_id: str
    title: str
    location: Location
    node_ids: List[str] = field(default_factory=list)
    table_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "section_id": self.section_id,
            "title": self.title,
            "location": self.location.to_dict(),
            "node_ids": self.node_ids,
            "table_ids": self.table_ids,
        }


@dataclass
class TemplateIR:
    """The whole template, format-agnostic."""
    doc_type: DocType
    source_path: str
    nodes: List[Node] = field(default_factory=list)
    tables: List[TableIR] = field(default_factory=list)
    sections: List[Section] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    # ---- lookups -------------------------------------------------------
    def node(self, node_id: str) -> Optional[Node]:
        return next((n for n in self.nodes if n.node_id == node_id), None)

    def table(self, node_id: str) -> Optional[TableIR]:
        return next((t for t in self.tables if t.node_id == node_id), None)

    def value_regions(self) -> List[Node]:
        return [n for n in self.nodes if n.type == NodeType.VALUE_REGION and n.editable]

    def labels(self) -> List[Node]:
        return [n for n in self.nodes if n.type == NodeType.LABEL]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "doc_type": self.doc_type.value,
            "source_path": self.source_path,
            "meta": self.meta,
            "sections": [s.to_dict() for s in self.sections],
            "nodes": [n.to_dict() for n in self.nodes],
            "tables": [t.to_dict() for t in self.tables],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)
