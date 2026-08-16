"""
template.spec.json - the machine contract.

    Template.xlsx -> parse -> IR -> classify -> template.spec.json

The spec is the *only* thing the renderer needs. It is inspectable, diffable,
cacheable and hand-correctable: a human can fix one wrong mapping and every
future run of that template family is right. Nothing in it is hardcoded per
template - it is generated.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

from .ir import DocType, Node, NodeType, TableIR, TemplateIR, ValueFormat
from .semantic.classifier import SemanticClassifier, RoleCandidate
from .semantic.llm import BaseLLM, NullLLM, ROLE_ARBITRATION_SYSTEM
from .semantic.roles import DEFAULT_REGISTRY, RoleRegistry, looks_like_serial_number_header

# A reserved column key (not a real semantic role) marking an auto-numbered
# serial-number column. Never collides with a real item field name since it
# can't be produced by slugify() or any registered role.
RESERVED_SERIAL_NUMBER_KEY = "__serial_no__"

# A parenthetical qualifier - "Attendees(YMSLI)" vs "Attendees(YMESG)" - marks
# a genuinely distinct variant of a role. Matches the same signal used on the
# source-extraction side (engine/source/extractor.py: QUALIFIER_RE) so a
# template field and a source fact named the same way land on the identical
# role without any fuzzy matching needed.
QUALIFIER_RE = re.compile(r"\(([A-Za-z0-9][A-Za-z0-9 &/\-]{0,24})\)")

SPEC_VERSION = "1.0"


@dataclass
class FieldSpec:
    role: str
    node_id: str
    label: str
    location: Dict[str, Any]
    value_format: str = ValueFormat.TEXT.value
    editable: bool = True
    confidence: float = 0.0
    critical: bool = False
    section: Optional[str] = None
    multiline: bool = False
    alternatives: List[Dict[str, Any]] = field(default_factory=list)
    decided_by: str = "rules"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TableSpec:
    role: str
    node_id: str
    location: Dict[str, Any]
    header_row: int
    template_row: int
    existing_data_rows: int
    columns: Dict[str, Dict[str, Any]]     # item_field -> {header, location_hint, format}
    unmapped_columns: List[Dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    section: Optional[str] = None
    editable: bool = True
    fixed_rows: bool = False               # pptx tables cannot grow safely
    last_row: Optional[int] = None         # last row of the band as originally detected
    decided_by: str = "rules"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TemplateSpec:
    template_id: str
    doc_type: str
    source_path: str
    spec_version: str = SPEC_VERSION
    created_at: float = field(default_factory=time.time)
    fields: Dict[str, FieldSpec] = field(default_factory=dict)
    tables: Dict[str, TableSpec] = field(default_factory=dict)
    protected: List[Dict[str, Any]] = field(default_factory=list)
    unresolved: List[Dict[str, Any]] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "template_id": self.template_id,
            "spec_version": self.spec_version,
            "doc_type": self.doc_type,
            "source_path": self.source_path,
            "created_at": self.created_at,
            "fields": {k: v.to_dict() for k, v in self.fields.items()},
            "tables": {k: v.to_dict() for k, v in self.tables.items()},
            "protected": self.protected,
            "unresolved": self.unresolved,
            "stats": self.stats,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def save(self, path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.to_json())
        return path

    @classmethod
    def load(cls, path: str) -> "TemplateSpec":
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        spec = cls(template_id=d["template_id"], doc_type=d["doc_type"],
                   source_path=d.get("source_path", ""),
                   spec_version=d.get("spec_version", SPEC_VERSION),
                   created_at=d.get("created_at", time.time()))
        spec.fields = {k: FieldSpec(**v) for k, v in d.get("fields", {}).items()}
        spec.tables = {k: TableSpec(**v) for k, v in d.get("tables", {}).items()}
        spec.protected = d.get("protected", [])
        spec.unresolved = d.get("unresolved", [])
        spec.stats = d.get("stats", {})
        return spec


def template_fingerprint(ir: TemplateIR) -> str:
    """
    Structure hash - identifies a template *family*, not a file. Two files
    saved from the same master with different data hash the same, so the
    spec (and any human correction to it) can be reused.
    """
    parts = [ir.doc_type.value]
    for n in sorted(ir.nodes, key=lambda x: x.node_id):
        if n.type in (NodeType.LABEL, NodeType.STATIC, NodeType.SECTION_HEADER):
            parts.append(f"{n.location.key()}|{n.type.value}|{(n.text or '')[:40]}")
    for t in sorted(ir.tables, key=lambda x: x.node_id):
        parts.append(f"{t.location.key()}|" + ",".join(c.header_text for c in t.columns))
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:16]


class SpecBuilder:
    """IR + semantics -> TemplateSpec. Rules first, LLM only to break ties."""

    def __init__(self, registry: RoleRegistry = DEFAULT_REGISTRY,
                 llm: Optional[BaseLLM] = None,
                 accept_threshold: float = 0.62,
                 ambiguous_margin: float = 0.08,
                 allow_open_world: bool = True):
        self.registry = registry
        self.classifier = SemanticClassifier(registry)
        self.llm = llm or NullLLM()
        self.accept_threshold = accept_threshold
        self.ambiguous_margin = ambiguous_margin
        self.allow_open_world = allow_open_world

    # ------------------------------------------------------------------
    def build(self, ir: TemplateIR) -> TemplateSpec:
        spec = TemplateSpec(template_id=template_fingerprint(ir),
                            doc_type=ir.doc_type.value,
                            source_path=ir.source_path)

        # ---- protected regions: recorded explicitly so the renderer can refuse
        for n in ir.nodes:
            if not n.editable or n.type in (NodeType.STATIC, NodeType.IMAGE,
                                            NodeType.SECTION_HEADER, NodeType.LABEL):
                spec.protected.append({
                    "node_id": n.node_id, "reason": self._protect_reason(n),
                    "location": n.location.to_dict(), "text": (n.text or "")[:120],
                })

        # ---- repeating collections FIRST: a real table claiming a collection
        # role (e.g. action_items) should always win that role outright, so a
        # loose heading like "Action Plan" right above it can't also claim it
        # as a single free-text paragraph and duplicate the same content.
        for t in ir.tables:
            ts = self._build_table_spec(t)
            if ts is None:
                spec.unresolved.append({
                    "node_id": t.node_id, "kind": "table",
                    "headers": [c.header_text for c in t.columns],
                    "reason": "no collection role matched",
                })
                continue
            if ts.role in spec.tables and spec.tables[ts.role].confidence >= ts.confidence:
                ts.role = f"{ts.role}__{len(spec.tables)}"
            spec.tables[ts.role] = ts
        table_claimed_roles = set(spec.tables.keys()) | {r.split("__")[0] for r in spec.tables}

        # ---- single-value fields
        taken: Dict[str, FieldSpec] = {}
        for node in ir.value_regions():
            cands = [c for c in self.classifier.classify_node(node)
                     if not (self.registry.is_collection(c.role) and c.role in table_claimed_roles)]
            label_text = (node.label or node.text or "")
            qualifier = QUALIFIER_RE.search(label_text)
            if qualifier and cands and self.registry.is_collection(cands[0].role):
                # "Attendees(YMSLI)" fuzzily matches the generic "attendees"
                # collection role, but this is one cell, not a table - and
                # the qualifier says it's meant to be its own distinct
                # variant anyway (paired with "Attendees(YMESG)" elsewhere).
                # Mint that variant directly rather than letting both
                # collide into the same generic role.
                qualified = self.registry.mint_unknown(label_text.strip(), is_collection=False)
                role, conf, decided_by, alts = qualified.role, 0.75, "qualified_variant", cands
            else:
                role, conf, decided_by, alts = self._decide(node, cands)
            if role is None:
                spec.unresolved.append({
                    "node_id": node.node_id, "label": node.label or node.text,
                    "location": node.location.to_dict(),
                    "candidates": [c.to_dict() for c in cands[:3]],
                    "reason": "no candidate above threshold",
                })
                continue

            fs = FieldSpec(
                role=role, node_id=node.node_id, label=(node.label or node.text or "").strip(),
                location=node.location.to_dict(),
                value_format=(node.value_format if node.value_format != ValueFormat.UNKNOWN
                              else self.registry.expected_format(role)).value,
                editable=node.editable, confidence=round(conf, 4),
                critical=self.registry.is_critical(role), section=node.section,
                multiline=bool(node.meta.get("multiline_capable")),
                alternatives=[c.to_dict() for c in cands[1:3]], decided_by=decided_by,
            )
            # collision: two regions claim one role -> keep the stronger, demote the other
            prev = taken.get(role)
            if prev is None:
                taken[role] = fs
            elif fs.confidence > prev.confidence:
                self._demote(spec, prev, role)
                taken[role] = fs
            else:
                self._demote(spec, fs, role)

        spec.fields = taken

        spec.stats = {
            "nodes": len(ir.nodes),
            "value_regions": len(ir.value_regions()),
            "fields_mapped": len(spec.fields),
            "tables_found": len(ir.tables),
            "tables_mapped": len(spec.tables),
            "protected_regions": len(spec.protected),
            "unresolved": len(spec.unresolved),
            "llm": self.llm.name,
        }
        return spec

    # ------------------------------------------------------------------
    def _decide(self, node: Node, cands: List[RoleCandidate]):
        if not cands:
            return self._open_world(node, 0.0)
        top = cands[0]
        runner = cands[1].score if len(cands) > 1 else 0.0
        ambiguous = (top.score - runner) < self.ambiguous_margin and len(cands) > 1

        if top.score >= self.accept_threshold and not ambiguous:
            return top.role, top.score, "rules", cands

        if self.llm.available() and (ambiguous or top.score >= 0.40):
            payload = {
                "field": {"label": node.label or node.text, "section": node.section,
                          "format": node.value_format.value,
                          "merged": node.style.merged, "empty": node.is_empty},
                "candidates": [{"role": c.role, "rule_score": round(c.score, 3),
                                "why": c.reason} for c in cands],
                "allow_new": False,
            }
            ans = self.llm.json_or({}, ROLE_ARBITRATION_SYSTEM, json.dumps(payload))
            role = (ans or {}).get("role")
            if role and any(c.role == role for c in cands):
                conf = float((ans or {}).get("confidence", 0.7))
                blended = 0.5 * conf + 0.5 * next(c.score for c in cands if c.role == role)
                if blended >= self.accept_threshold:
                    return role, blended, "llm", cands

        if top.score >= self.accept_threshold:
            return top.role, top.score * 0.9, "rules_ambiguous", cands
        return self._open_world(node, top.score)

    def _open_world(self, node: Node, base: float):
        """
        An unrecognised but clearly labelled input still deserves a slot.
        A blank cell with a label qualifies, and so does an explicit
        {{placeholder}} - the placeholder IS the "this needs filling" signal,
        even though such a node's is_empty is False (it literally contains
        the placeholder text, which isn't the same as being unfillable).
        """
        label = (node.label or "").strip()
        fillable = node.is_empty or bool(node.placeholder)
        if not (self.allow_open_world and label and fillable and node.editable):
            return None, 0.0, "none", []
        rd = self.registry.mint_unknown(label, is_collection=False)
        return rd.role, max(base, 0.55), "open_world", []

    def _demote(self, spec: TemplateSpec, fs: FieldSpec, role: str) -> None:
        spec.unresolved.append({
            "node_id": fs.node_id, "label": fs.label, "location": fs.location,
            "reason": f"duplicate claim on role '{role}' (kept the higher-confidence region)",
            "confidence": fs.confidence,
        })

    def _protect_reason(self, n: Node) -> str:
        if n.has_formula:
            return "formula"
        if n.style.locked:
            return "sheet protection"
        if n.type == NodeType.IMAGE:
            return "image/branding"
        if n.type == NodeType.LABEL:
            return "label text"
        if n.type == NodeType.SECTION_HEADER:
            return "section header"
        return "static content"

    def _build_table_spec(self, t: TableIR) -> Optional[TableSpec]:
        cands = self.classifier.classify_table(t)
        if cands and cands[0].score >= 0.35:
            role, conf, decided_by = cands[0].role, cands[0].score, "rules"
        elif self.allow_open_world:
            title = t.section or " ".join(c.header_text for c in t.columns[:3])
            rd = self.registry.mint_unknown(title, is_collection=True)
            role, conf, decided_by = rd.role, 0.5, "open_world"
        else:
            return None

        rd = self.registry.get(role)
        allowed = rd.item_fields if rd and rd.item_fields else None
        columns: Dict[str, Dict[str, Any]] = {}

        # Score every column against every candidate field first, THEN assign
        # greedily by score across the whole table - not column-by-column in
        # left-to-right order. A left-to-right assignment lets a column with
        # a weak, coincidental match ("Closure Date" ~ "closure status") claim
        # a role before a later column with an exact match ("Status" ~
        # "status") ever gets a turn; scoring everything first and assigning
        # strongest-match-first fixes that regardless of column order.
        candidates: List[Tuple[float, ColumnIR, str]] = []
        for col in t.columns:
            fname, score = self.classifier.classify_column(col.header_text, allowed)
            if fname is None and allowed is None:
                # Only widen the search when this role has no declared
                # item_fields at all (an open-world/unknown collection) - a
                # role WITH a defined field list should never accept a
                # match outside that list, or "Sl.No." can wrongly claim an
                # unrelated field like "item" that this table never asked for.
                fname, score = self.classifier.classify_column(col.header_text, None)
            if fname:
                candidates.append((score, col, fname))
        candidates.sort(key=lambda x: -x[0])

        assigned_cols: set = set()
        for score, col, fname in candidates:
            if fname in columns or col.index in assigned_cols:
                continue
            fmt = col.value_format
            if fmt == ValueFormat.UNKNOWN:
                # docx/pptx tables carry no number-format signal at all;
                # a column named "due_date" is still known to want a date.
                fmt = self.registry.expected_format(fname)
            columns[fname] = {
                "header": col.header_text,
                "location_hint": col.location_hint,
                "format": fmt.value,
                "editable": col.editable and not col.has_formula,
                "confidence": round(score, 3),
            }
            assigned_cols.add(col.index)

        unmapped: List[Dict[str, Any]] = []
        for col in t.columns:
            if col.index in assigned_cols:
                continue
            info = {"header": col.header_text, "location_hint": col.location_hint,
                    "editable": col.editable and not col.has_formula,
                    "has_formula": col.has_formula}
            if info["editable"] and looks_like_serial_number_header(col.header_text) \
                    and RESERVED_SERIAL_NUMBER_KEY not in columns:
                # A serial-number column isn't sourced from data at all - the
                # mapper auto-generates 1, 2, 3... for however many rows end
                # up written, the same way a person filling this in by hand
                # would just number the rows as they go.
                columns[RESERVED_SERIAL_NUMBER_KEY] = {
                    "header": col.header_text, "location_hint": col.location_hint,
                    "format": ValueFormat.NUMBER.value, "editable": True,
                    "confidence": 0.9, "auto_generated": True,
                }
                assigned_cols.add(col.index)
                continue
            unmapped.append(info)
        if not columns:
            return None

        return TableSpec(
            role=role, node_id=t.node_id, location=t.location.to_dict(),
            header_row=t.header_row, template_row=t.template_row,
            existing_data_rows=t.existing_data_rows, columns=columns,
            unmapped_columns=unmapped, confidence=round(conf, 4),
            section=t.section, editable=t.editable,
            fixed_rows=bool(t.meta.get("fixed_rows")),
            last_row=t.meta.get("last_row"), decided_by=decided_by,
        )


def build_spec(ir: TemplateIR, llm: Optional[BaseLLM] = None, **kw) -> TemplateSpec:
    return SpecBuilder(llm=llm, **kw).build(ir)
