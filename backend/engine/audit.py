"""
Audit log.

Every generated file gets a companion record answering, per field:
what was written, where, from which sentence, with what confidence, and who
decided (rules / LLM / human). This is what makes the system defensible in an
enterprise setting - and what makes debugging a bad output a two-minute job.
"""

from __future__ import annotations

import json
import os
import platform
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from .mapper import FillPlan
from .qa.structural import QAReport
from .renderers.base import RenderResult
from .spec import TemplateSpec
from .validation import ValidationReport


@dataclass
class AuditEntry:
    field: str
    target: str
    value: Any
    status: str
    confidence: float = 0.0
    evidence: str = ""
    source: str = ""
    decided_by: str = ""
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AuditLog:
    job_id: str
    template_path: str
    source_path: str
    output_path: str
    template_id: str = ""
    created_at: float = field(default_factory=time.time)
    entries: List[AuditEntry] = field(default_factory=list)
    validation: Dict[str, Any] = field(default_factory=dict)
    qa: Dict[str, Any] = field(default_factory=dict)
    visual_qa: Dict[str, Any] = field(default_factory=dict)
    summary: Dict[str, Any] = field(default_factory=dict)
    environment: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["entries"] = [e.to_dict() for e in self.entries]
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def save(self, path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.to_json())
        return path

    # -- human-readable ---------------------------------------------------
    def render_text(self) -> str:
        lines = [f"Audit {self.job_id}",
                 f"  template : {os.path.basename(self.template_path)} ({self.template_id})",
                 f"  source   : {os.path.basename(self.source_path)}",
                 f"  output   : {os.path.basename(self.output_path)}", ""]
        width = max([len(e.field) for e in self.entries] + [12])
        for e in self.entries:
            mark = {"filled": "✓", "fill_with_audit": "✓", "needs_review": "?",
                    }.get(e.status, "·")
            val = str(e.value)
            if len(val) > 46:
                val = val[:43] + "..."
            lines.append(f"  {mark} {e.field.ljust(width)}  {val}")
            lines.append(f"    {' ' * width}  → {e.target}  "
                         f"[{e.status}, conf {e.confidence:.2f}, {e.decided_by or 'rules'}]")
            if e.evidence:
                ev = e.evidence.replace("\n", " ")
                lines.append(f"    {' ' * width}  “{ev[:90]}”")
        lines.append("")
        for k, v in self.summary.items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)


def build_audit(plan: FillPlan, spec: TemplateSpec, render: RenderResult,
                template_path: str, source_path: str,
                validation: Optional[ValidationReport] = None,
                qa: Optional[QAReport] = None,
                visual: Optional[QAReport] = None,
                job_id: Optional[str] = None) -> AuditLog:
    log = AuditLog(job_id=job_id or uuid.uuid4().hex[:12],
                   template_path=template_path, source_path=source_path,
                   output_path=render.output_path, template_id=spec.template_id)

    written = {r.node_id: r for r in render.records}
    for ins in plan.instructions:
        rec = written.get(ins.node_id)
        status = ins.decision
        if rec is not None:
            status = {"written": "filled", "skipped": "skipped_by_renderer",
                      "failed": "render_failed"}.get(rec.status, rec.status)
        fs = spec.fields.get(ins.role) or spec.tables.get(ins.role)
        target = (rec.target if rec else _target_str(ins.target))
        value = ins.value if ins.kind == "field" else f"{len(ins.rows or [])} row(s)"
        log.entries.append(AuditEntry(
            field=ins.role, target=target, value=value, status=status,
            confidence=ins.confidence, evidence=ins.evidence,
            source=ins.source_extractor, decided_by=getattr(fs, "decided_by", ""),
            notes=list(ins.notes) + ([rec.message] if rec and rec.message else []),
        ))

    log.validation = validation.to_dict() if validation else {}
    log.qa = qa.to_dict() if qa else {}
    log.visual_qa = visual.to_dict() if visual else {}
    log.summary = {
        "fields_in_spec": len(spec.fields),
        "tables_in_spec": len(spec.tables),
        "planned": len(plan.instructions),
        "written": render.written,
        "rows_added": render.rows_added,
        "failed": render.failed,
        "needs_review": len([i for i in plan.instructions if i.decision == "needs_review"]),
        "skipped_no_evidence": len([i for i in plan.instructions
                                    if i.decision == "skipped_no_evidence"]),
        "not_found_in_source": len([i for i in plan.instructions
                                    if i.decision == "skipped_not_found"]),
        "blocked_by_validation": len([i for i in plan.instructions
                                      if i.decision == "blocked_by_validation"]),
        "validation_ok": validation.ok if validation else None,
        "structural_qa_ok": qa.ok if qa else None,
        "visual_qa_ok": visual.ok if visual else None,
    }
    log.environment = {"python": platform.python_version(), "platform": platform.platform()}
    return log


def _target_str(target: Dict[str, Any]) -> str:
    if "sheet" in target:
        return f"{target['sheet']}!{target.get('range') or target.get('cell')}"
    if "slide_index" in target:
        return f"slide[{target['slide_index']}]/shape[{target.get('shape_id')}]"
    if target.get("kind") == "table_cell" or "table_index" in target:
        return f"table[{target.get('table_index')}]!r{target.get('row')}c{target.get('col')}"
    if target.get("kind") == "table" or ("header_row" in target and "para_index" not in target):
        return f"table[{target.get('table_index')}]"
    return f"paragraph[{target.get('para_index')}]" if "para_index" in target else str(target)
