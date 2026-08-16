"""
The orchestrator.

    template ──▶ parse ──▶ IR ──▶ classify ──▶ spec.json ─┐
                                                          ├─▶ map ─▶ validate ─▶ render ─▶ QA ─▶ audit
    source   ──▶ read  ──▶ extract ──▶ canonical.json ─────┘

Every stage is independently callable and independently inspectable. The
pipeline just sequences them and keeps the artefacts.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .audit import AuditLog, build_audit
from .ir import TemplateIR
from .logging_config import get_logger
from .mapper import Decision, FieldMapper, FillPlan, Policy
from .parsers import parse_template
from .qa.structural import QAReport, structural_qa
from .qa.visual import visual_qa
from .renderers import render_plan
from .renderers.base import RenderResult
from .semantic.llm import BaseLLM, NullLLM, get_llm
from .source.canonical import CanonicalSource
from .source.extractor import SourceExtractor
from .spec import SpecBuilder, TemplateSpec, template_fingerprint
from .validation import ValidationGate, ValidationReport

log = get_logger("pipeline")


@dataclass
class EngineConfig:
    llm_provider: Optional[str] = None            # None -> env OTE_LLM_PROVIDER -> "null"
    llm_model: Optional[str] = None
    policy: Policy = field(default_factory=Policy)
    spec_cache_dir: str = "./.ote_cache/specs"
    output_dir: str = "./output"
    run_visual_qa: bool = False
    dry_run: bool = False
    use_llm_for_source: bool = True
    use_llm_for_mapping: bool = True


@dataclass
class FillResult:
    job_id: str
    output_path: Optional[str]
    spec: TemplateSpec
    source: CanonicalSource
    plan: FillPlan
    validation: ValidationReport
    render: Optional[RenderResult]
    qa: Optional[QAReport]
    visual: Optional[QAReport]
    audit: Optional[AuditLog]
    artifacts: Dict[str, str] = field(default_factory=dict)
    duration_s: float = 0.0

    @property
    def ok(self) -> bool:
        return bool(self.output_path) and self.validation.ok \
            and (self.qa is None or self.qa.ok) \
            and (self.visual is None or self.visual.ok)

    def review_queue(self) -> List[Dict[str, Any]]:
        """Everything a human should look at before this file is trusted."""
        out = []
        for ins in self.plan.instructions:
            if ins.decision in (Decision.REVIEW.value, Decision.FILL_AUDIT.value,
                                Decision.SKIP_TYPE_MISMATCH.value,
                                Decision.BLOCKED_BY_VALIDATION.value):
                out.append({"role": ins.role, "label": ins.label, "value": ins.value,
                            "confidence": ins.confidence, "decision": ins.decision,
                            "evidence": ins.evidence, "notes": ins.notes})
        for u in self.spec.unresolved:
            out.append({"role": None, "label": u.get("label"), "decision": "unmapped_region",
                        "notes": [u.get("reason", "")]})
        return out

    def summary(self) -> Dict[str, Any]:
        return {"job_id": self.job_id, "ok": self.ok, "output": self.output_path,
                "template_id": self.spec.template_id, "plan": self.plan.summary,
                "written": self.render.written if self.render else 0,
                "rows_added": self.render.rows_added if self.render else 0,
                "validation_ok": self.validation.ok,
                "qa_ok": self.qa.ok if self.qa else None,
                "review_items": len(self.review_queue()),
                "duration_s": round(self.duration_s, 2)}


class TemplateEngine:
    def __init__(self, config: Optional[EngineConfig] = None, llm: Optional[BaseLLM] = None):
        self.config = config or EngineConfig()
        self.llm = llm or get_llm(self.config.llm_provider, self.config.llm_model)
        log.info("Engine ready. llm_provider=%s available=%s output_dir=%s",
                self.llm.name, self.llm.available(), self.config.output_dir)

    # ------------------------------------------------------------------
    # stage 1: understand the template
    # ------------------------------------------------------------------
    def analyze_template(self, template_path: str, use_cache: bool = True,
                         save_spec: bool = True) -> TemplateSpec:
        log.info("Parsing template: %s", template_path)
        ir = parse_template(template_path)
        fingerprint = template_fingerprint(ir)
        cache_path = os.path.join(self.config.spec_cache_dir, f"{fingerprint}.spec.json")

        if use_cache and os.path.exists(cache_path):
            log.info("Spec cache hit for template_id=%s -> %s", fingerprint, cache_path)
            spec = TemplateSpec.load(cache_path)
            spec.stats["cache_hit"] = True
            return spec

        log.info("Building spec: %d nodes, %d tables found in %s",
                len(ir.nodes), len(ir.tables), os.path.basename(template_path))
        spec = SpecBuilder(llm=self.llm).build(ir)
        log.info("Spec built: %d field(s), %d table(s) mapped, %d unresolved",
                len(spec.fields), len(spec.tables), len(spec.unresolved))
        if save_spec:
            spec.save(cache_path)
            spec.stats["spec_path"] = cache_path
            log.debug("Saved spec cache -> %s", cache_path)
        return spec

    def parse(self, template_path: str) -> TemplateIR:
        return parse_template(template_path)

    # ------------------------------------------------------------------
    # stage 2: understand the source
    # ------------------------------------------------------------------
    def extract_source(self, source_path: Optional[str] = None,
                       source_text: Optional[str] = None) -> CanonicalSource:
        ex = SourceExtractor(llm=self.llm, use_llm=self.config.use_llm_for_source)
        if source_text is not None:
            return ex.extract_text(source_text, origin="<inline>")
        if not source_path:
            raise ValueError("Provide source_path or source_text")
        return ex.extract_file(source_path)

    # ------------------------------------------------------------------
    # stage 3: the whole flow
    # ------------------------------------------------------------------
    def fill(self, template_path: str, source_path: Optional[str] = None,
             source_text: Optional[str] = None, output_path: Optional[str] = None,
             use_cache: bool = True, save_artifacts: bool = True,
             spec: Optional[TemplateSpec] = None,
             overrides: Optional[Dict[str, Any]] = None) -> FillResult:
        t0 = time.time()
        job_id = uuid.uuid4().hex[:12]
        cfg = self.config
        log.info("=== fill() starting: job_id=%s template=%s source=%s dry_run=%s ===",
                job_id, template_path, source_path or "<inline text>", cfg.dry_run)

        if not os.path.exists(template_path):
            log.error("Template file does not exist: %s", template_path)
            raise FileNotFoundError(f"Template not found: {template_path}")
        if source_path and not os.path.exists(source_path):
            log.error("Source file does not exist: %s", source_path)
            raise FileNotFoundError(f"Source not found: {source_path}")

        ir = parse_template(template_path)
        spec = spec or self.analyze_template(template_path, use_cache=use_cache)

        log.info("Extracting source: %s", source_path or "<inline text>")
        source = self.extract_source(source_path, source_text)
        log.info("Source extracted: %d field(s), %d collection(s) found",
                len(source.fields), len(source.collections))
        if not source.fields and not source.collections:
            log.warning("No fields or collections were extracted from the source. "
                       "The output will likely be empty - check that the source file "
                       "actually contains the content you expect, and that its format "
                       "is supported (see engine/source/readers.py).")

        plan = FieldMapper(policy=cfg.policy,
                           llm=self.llm if cfg.use_llm_for_mapping else NullLLM()
                           ).map(spec, source)
        log.info("Fill plan built: %s", plan.summary)
        if overrides:
            log.info("Applying %d human override(s): %s", len(overrides), list(overrides))
            self._apply_overrides(plan, overrides)

        validation = ValidationGate(spec, ir, cfg.policy).run(plan)
        if not validation.ok:
            log.warning("Validation gate blocked %d instruction(s): %s",
                       len(validation.blocked),
                       [v.message for v in validation.errors()][:5])
        else:
            log.info("Validation passed for all %d writable instruction(s)", validation.checked)

        render = None
        qa = None
        visual = None
        out_path = None
        if cfg.dry_run:
            log.info("dry_run=True: plan built but nothing will be written to disk. "
                    "Set EngineConfig(dry_run=False) (the default) to actually produce a file.")
        else:
            base, ext = os.path.splitext(os.path.basename(template_path))
            out_path = output_path or os.path.join(cfg.output_dir, f"{base}.filled.{job_id}{ext}")
            log.info("Rendering to: %s", out_path)
            render = render_plan(template_path, out_path, plan)
            log.info("Rendered: %d written, %d failed, %d row(s) added",
                    render.written, render.failed, render.rows_added)
            if render.failed:
                for r in render.records:
                    if r.status == "failed":
                        log.error("Write failed for %s (%s): %s", r.node_id, r.role, r.message)

            qa = structural_qa(template_path, out_path)
            log.info("Structural QA: %s (%d issue(s))", "OK" if qa.ok else "FAILED",
                    len(qa.issues))
            if cfg.run_visual_qa:
                log.info("Running visual QA (renders via LibreOffice - can take a few seconds)...")
                visual = visual_qa(out_path, llm=self.llm)
                log.info("Visual QA: %s (%d issue(s))", "OK" if visual.ok else "FAILED",
                        len(visual.issues))

        audit = None
        artifacts: Dict[str, str] = {}
        if render is not None:
            audit = build_audit(plan, spec, render, template_path,
                                source_path or "<inline>", validation, qa, visual, job_id)
            if save_artifacts:
                d = os.path.join(cfg.output_dir, "artifacts", job_id)
                os.makedirs(d, exist_ok=True)
                artifacts["spec"] = spec.save(os.path.join(d, "template.spec.json"))
                artifacts["canonical"] = _write(os.path.join(d, "canonical.json"), source.to_json())
                artifacts["plan"] = _write(os.path.join(d, "fill_plan.json"), plan.to_json())
                artifacts["audit"] = audit.save(os.path.join(d, "audit.json"))
                artifacts["ir"] = _write(os.path.join(d, "template.ir.json"), ir.to_json())
                log.debug("Artifacts written under: %s", d)

        result = FillResult(job_id=job_id, output_path=out_path, spec=spec, source=source,
                            plan=plan, validation=validation, render=render, qa=qa,
                            visual=visual, audit=audit, artifacts=artifacts,
                            duration_s=time.time() - t0)

        if out_path and os.path.exists(out_path):
            log.info("=== fill() done in %.2fs: wrote %s ===", result.duration_s,
                    os.path.abspath(out_path))
        elif cfg.dry_run:
            log.info("=== fill() done in %.2fs: dry run, no file written ===", result.duration_s)
        else:
            log.error("=== fill() finished but no output file exists at %s - "
                     "check the errors above ===", out_path)
        return result

    # ------------------------------------------------------------------
    def _apply_overrides(self, plan: FillPlan, overrides: Dict[str, Any]) -> None:
        """
        Human-in-the-loop corrections: {"meeting_date": "2026-08-12"} or
        {"action_items": [{...}, ...]}. A human-supplied value is fully trusted
        and bypasses the confidence policy - but not the validation gate.
        """
        for ins in plan.instructions:
            if ins.role not in overrides:
                continue
            val = overrides[ins.role]
            if ins.kind == "table" and isinstance(val, list):
                ins.rows = val
            else:
                ins.value = val
            ins.confidence = 1.0
            ins.evidence = "human override"
            ins.source_extractor = "user"
            ins.decision = Decision.FILL.value
            ins.notes.append("value supplied by user")
        plan.recompute_summary()


def _write(path: str, text: str) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path
