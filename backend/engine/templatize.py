"""
Auto-templatize.

    Any template  ->  parse  ->  spec (the engine's own understanding)
                              ->  write {{role}} into every detected slot
                              ->  a placeholder-annotated version of the SAME template

This is the answer to "do I have to hand-type {{placeholders}} into my
templates myself?" - no. The engine can already detect a fillable field from
structure alone (a label next to a blank or merged cell, a header row above
a blank band) without any placeholder ever being present - that's exactly
how the very first demo template works. Placeholders are an optional
authoring convention some templates already use; this module lets you go
the *other* direction - point the engine at a plain, unmarked form and get
back a copy with its own understanding written into it as visible tokens,
which is also just a good way to sanity-check what the engine actually
found before trusting it with real data.

Nothing here is a substitute for fixing genuine structural ambiguity in a
template (two sections that visually run together with no clear boundary,
an orphaned label with no adjacent target) - that class of problem is a
judgment call about what the template *should* mean, and still needs a
person to decide. `engine.diagnose` surfaces exactly that class of issue
so it can be found and fixed once, by hand, the same way it was fixed for
the MOM template earlier in this conversation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .ir import TemplateIR
from .logging_config import get_logger
from .mapper import Decision, FillInstruction, FillPlan
from .parsers import parse_template
from .renderers import render_plan
from .renderers.base import RenderResult
from .spec import SpecBuilder, TemplateSpec

log = get_logger("templatize")


@dataclass
class TemplatizeResult:
    output_path: str
    spec: TemplateSpec
    render: RenderResult
    fields_annotated: List[str] = field(default_factory=list)
    tables_annotated: List[str] = field(default_factory=list)
    skipped: List[Dict[str, Any]] = field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        return {
            "output": self.output_path,
            "fields_annotated": len(self.fields_annotated),
            "tables_annotated": len(self.tables_annotated),
            "skipped": len(self.skipped),
        }

    def render_text(self) -> str:
        lines = [f"Templatized -> {self.output_path}", ""]
        if self.fields_annotated:
            lines.append(f"Fields ({len(self.fields_annotated)}):")
            for r in self.fields_annotated:
                lines.append(f"  {{{{{r}}}}}")
        if self.tables_annotated:
            lines.append(f"\nTables ({len(self.tables_annotated)}):")
            for r in self.tables_annotated:
                lines.append(f"  {r} (row of {{{{item_field}}}} tokens)")
        if self.skipped:
            lines.append(f"\nSkipped ({len(self.skipped)}) - protected or not editable:")
            for s in self.skipped:
                lines.append(f"  {s['node_id']}: {s['reason']}")
        return "\n".join(lines)


def templatize(template_path: str, output_path: str,
               spec: Optional[TemplateSpec] = None,
               ir: Optional[TemplateIR] = None) -> TemplatizeResult:
    """
    Write {{role}} into every field the engine detects, and a row of
    {{item_field}} tokens into every detected table's template row. The
    original file is never modified (same copy-first guarantee every
    renderer already gives); the output is a new file.
    """
    ir = ir or parse_template(template_path)
    spec = spec or SpecBuilder().build(ir)

    plan = FillPlan(template_id=spec.template_id, doc_type=spec.doc_type)
    fields_annotated: List[str] = []
    tables_annotated: List[str] = []
    skipped: List[Dict[str, Any]] = []

    for role, fs in spec.fields.items():
        if not fs.editable:
            skipped.append({"node_id": fs.node_id, "reason": "not editable"})
            continue
        plan.instructions.append(FillInstruction(
            kind="field", role=role, target=fs.location, node_id=fs.node_id,
            value="{{" + role + "}}", value_format=fs.value_format,
            confidence=1.0, evidence="templatize", source_extractor="templatize",
            decision=Decision.FILL.value, label=fs.label,
        ))
        fields_annotated.append(role)

    for role, ts in spec.tables.items():
        if not ts.editable:
            skipped.append({"node_id": ts.node_id, "reason": "not editable"})
            continue
        writable_cols = {f: c for f, c in ts.columns.items() if c.get("editable", True)}
        if not writable_cols:
            skipped.append({"node_id": ts.node_id, "reason": "no editable columns"})
            continue
        row = {fname: "{{" + fname + "}}" for fname in writable_cols}
        target = {
            "sheet": ts.location.get("sheet"), "range": ts.location.get("range"),
            "kind": ts.location.get("kind"), "table_index": ts.location.get("table_index"),
            "slide_index": ts.location.get("slide_index"), "shape_id": ts.location.get("shape_id"),
            "header_row": ts.header_row, "template_row": ts.template_row,
            "existing_data_rows": ts.existing_data_rows, "fixed_rows": ts.fixed_rows,
            "last_row": ts.last_row,
            "columns": {f: c["location_hint"] for f, c in writable_cols.items()},
        }
        plan.instructions.append(FillInstruction(
            kind="table", role=role, target=target, node_id=ts.node_id,
            rows=[row], confidence=1.0, evidence="templatize", source_extractor="templatize",
            decision=Decision.FILL.value, label=ts.section or role,
        ))
        tables_annotated.append(role)

    for u in spec.unresolved:
        skipped.append({"node_id": u.get("node_id", "?"), "reason": u.get("reason", "unresolved")})

    plan.recompute_summary()
    log.info("Templatizing %s: %d field(s), %d table(s), %d skipped",
            template_path, len(fields_annotated), len(tables_annotated), len(skipped))

    render = render_plan(template_path, output_path, plan, clear_unresolved=False)
    return TemplatizeResult(output_path=render.output_path, spec=spec, render=render,
                            fields_annotated=fields_annotated,
                            tables_annotated=tables_annotated, skipped=skipped)
