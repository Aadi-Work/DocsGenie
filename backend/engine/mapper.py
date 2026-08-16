"""
Field / slot mapping.

    TemplateSpec (what the template wants)  +  CanonicalSource (what the source proves)
                              -> FillPlan (what will be written, and why)

The plan is data. Nothing is written here. Every instruction carries value,
confidence, evidence and a decision, so the whole run is reviewable before a
single byte of the document changes.

Confidence policy (configurable):
    >= 0.95   fill
    0.85-0.95 fill + audit flag
    0.70-0.85 hold for human review
    <  0.70   do not fill
Critical roles get a higher bar.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional

from .ir import ValueFormat
from .logging_config import get_logger
from .normalize import coerce
from .semantic.classifier import SemanticClassifier, text_similarity
from .semantic.llm import BaseLLM, NullLLM, VALUE_SELECTION_SYSTEM
from .semantic.roles import DEFAULT_REGISTRY, ITEM_FIELD_SYNONYMS, RoleRegistry
from .source.canonical import CanonicalSource, Collection
from .spec import FieldSpec, TableSpec, TemplateSpec, RESERVED_SERIAL_NUMBER_KEY

log = get_logger("mapper")


class Decision(str, Enum):
    FILL = "fill"
    FILL_AUDIT = "fill_with_audit"
    REVIEW = "needs_review"
    SKIP_LOW_CONFIDENCE = "skipped_low_confidence"
    SKIP_NO_EVIDENCE = "skipped_no_evidence"
    SKIP_NOT_FOUND = "skipped_not_found"
    SKIP_PROTECTED = "skipped_protected"
    SKIP_TYPE_MISMATCH = "skipped_type_mismatch"
    BLOCKED_BY_VALIDATION = "blocked_by_validation"


WRITE_DECISIONS = {Decision.FILL, Decision.FILL_AUDIT}


def _geometric_mean(values) -> float:
    vals = [max(1e-6, float(v)) for v in values if v is not None]
    if not vals:
        return 0.0
    prod = 1.0
    for v in vals:
        prod *= v
    return prod ** (1.0 / len(vals))


@dataclass
class Policy:
    auto_fill: float = 0.95
    audit_fill: float = 0.85
    review: float = 0.70
    critical_bonus: float = 0.05          # critical roles need this much more
    fill_at_review: bool = False          # True = also write REVIEW-band values
    require_evidence: bool = True
    max_rows_per_table: int = 500

    def decide(self, confidence: float, critical: bool, has_evidence: bool) -> Decision:
        if self.require_evidence and not has_evidence:
            return Decision.SKIP_NO_EVIDENCE
        bump = self.critical_bonus if critical else 0.0
        if confidence >= min(0.99, self.auto_fill + bump):
            return Decision.FILL
        if confidence >= self.audit_fill + bump:
            return Decision.FILL_AUDIT
        if confidence >= self.review + bump:
            return Decision.FILL_AUDIT if self.fill_at_review else Decision.REVIEW
        return Decision.SKIP_LOW_CONFIDENCE


@dataclass
class FillInstruction:
    kind: str                              # "field" | "table"
    role: str
    target: Dict[str, Any]                 # location dict from the spec
    node_id: str
    value: Any = None
    rows: Optional[List[Dict[str, Any]]] = None
    value_format: str = ValueFormat.TEXT.value
    confidence: float = 0.0
    evidence: str = ""
    decision: str = Decision.SKIP_NOT_FOUND.value
    label: str = ""
    notes: List[str] = field(default_factory=list)
    source_extractor: str = ""

    @property
    def writable(self) -> bool:
        return self.decision in {d.value for d in WRITE_DECISIONS}

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["writable"] = self.writable
        return d


@dataclass
class FillPlan:
    template_id: str
    doc_type: str
    instructions: List[FillInstruction] = field(default_factory=list)
    policy: Dict[str, Any] = field(default_factory=dict)
    summary: Dict[str, int] = field(default_factory=dict)

    def writable(self) -> List[FillInstruction]:
        return [i for i in self.instructions if i.writable]

    def by_decision(self, decision: Decision) -> List[FillInstruction]:
        return [i for i in self.instructions if i.decision == decision.value]

    def recompute_summary(self) -> None:
        s: Dict[str, int] = {}
        for i in self.instructions:
            s[i.decision] = s.get(i.decision, 0) + 1
        self.summary = s

    def to_dict(self) -> Dict[str, Any]:
        return {"template_id": self.template_id, "doc_type": self.doc_type,
                "policy": self.policy, "summary": self.summary,
                "instructions": [i.to_dict() for i in self.instructions]}

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


class FieldMapper:
    def __init__(self, policy: Optional[Policy] = None,
                 registry: RoleRegistry = DEFAULT_REGISTRY,
                 llm: Optional[BaseLLM] = None,
                 fuzzy_role_match: float = 0.72):
        self.policy = policy or Policy()
        self.registry = registry
        self.classifier = SemanticClassifier(registry)
        self.llm = llm or NullLLM()
        self.fuzzy_role_match = fuzzy_role_match

    # ------------------------------------------------------------------
    def map(self, spec: TemplateSpec, source: CanonicalSource) -> FillPlan:
        plan = FillPlan(template_id=spec.template_id, doc_type=spec.doc_type,
                        policy=asdict(self.policy))

        for role, fs in spec.fields.items():
            ins = self._map_field(role, fs, source)
            plan.instructions.append(ins)
            log.debug("field '%s' -> %s (confidence=%.2f)", role, ins.decision, ins.confidence)

        for role, ts in spec.tables.items():
            ins = self._map_table(role, ts, source)
            plan.instructions.append(ins)
            log.debug("table '%s' -> %s (%d row(s), confidence=%.2f)",
                     role, ins.decision, len(ins.rows or []), ins.confidence)

        plan.recompute_summary()
        log.info("Mapping complete: %s", plan.summary)
        return plan

    # ------------------------------------------------------------------
    def _map_field(self, role: str, fs: FieldSpec, source: CanonicalSource) -> FillInstruction:
        ins = FillInstruction(kind="field", role=role, target=fs.location,
                              node_id=fs.node_id, value_format=fs.value_format,
                              label=fs.label)
        if not fs.editable:
            ins.decision = Decision.SKIP_PROTECTED.value
            ins.notes.append("target is not editable")
            return ins

        fact, match_conf, how = self._find_fact(role, fs, source)
        if fact is None:
            ins.decision = Decision.SKIP_NOT_FOUND.value
            ins.notes.append("no supporting fact in source")
            return ins

        value, ok = coerce(fact.value, fs.value_format)
        if not ok and fs.value_format in (ValueFormat.DATE.value, ValueFormat.TIME.value,
                                          ValueFormat.NUMBER.value, ValueFormat.CURRENCY.value):
            ins.value = fact.value
            ins.confidence = round(fact.confidence * 0.5, 3)
            ins.evidence = fact.evidence
            ins.decision = Decision.SKIP_TYPE_MISMATCH.value
            ins.notes.append(f"'{fact.value}' does not parse as {fs.value_format}")
            return ins

        # three independent confidences: does the template slot mean what we think,
        # does the source say what we think, and do the two refer to each other?
        conf = min(0.99, _geometric_mean([fs.confidence, fact.confidence, match_conf]))
        ins.value = value
        ins.confidence = round(conf, 3)
        ins.evidence = fact.evidence
        ins.source_extractor = fact.extractor
        ins.notes.append(how)
        ins.decision = self.policy.decide(conf, fs.critical, bool(fact.evidence)).value
        return ins

    def _find_fact(self, role: str, fs: FieldSpec, source: CanonicalSource):
        """exact role -> fuzzy role -> label similarity -> LLM. Never a guess."""
        fact = source.get(role)
        if fact is not None:
            return fact, 1.0, "exact role match"

        # a collection can legitimately fill a single free-text region
        # ("Decisions" as a paragraph rather than a table)
        coll = source.get_collection(role)
        if coll is not None and coll.items:
            from .source.canonical import Fact
            text = self._flatten_collection(coll.items, multiline=fs.multiline)
            if text:
                return (Fact(text, coll.confidence * 0.95, coll.evidence, coll.extractor),
                        0.95, "collection flattened into a text region")

        # same idea, but fuzzily - "x_attendees_ymsli" (an open-world role
        # minted from a label the taxonomy doesn't recognise) should still
        # find the source's generic "attendees" collection via name overlap,
        # not just an exact role-name match.
        best_coll, best_coll_score, best_coll_role = None, 0.0, None
        for src_role, c in source.collections.items():
            if not c.items:
                continue
            s = max(text_similarity(role.replace("_", " "), src_role.replace("_", " ")),
                   text_similarity(fs.label, src_role.replace("_", " ")))
            if s > best_coll_score:
                best_coll, best_coll_score, best_coll_role = c, s, src_role
        if best_coll is not None and best_coll_score >= self.fuzzy_role_match:
            from .source.canonical import Fact
            text = self._flatten_collection(best_coll.items, multiline=fs.multiline)
            if text:
                # A qualified template role ("x_attendees_ymsli") matching
                # only a GENERIC, unqualified source collection ("attendees")
                # is a genuine guess - the source never said which group
                # those names belong to, so filling every qualified variant
                # with the identical full list would silently look like
                # confirmed, split data when it's actually just one
                # undifferentiated list duplicated everywhere it might fit.
                # That's exactly the kind of thing that should go to a
                # human, not auto-fill.
                is_qualified_guess = role.startswith("x_") and not best_coll_role.startswith("x_")
                penalty = 0.55 if is_qualified_guess else 1.0
                conf = best_coll.confidence * 0.85 * best_coll_score * penalty
                how = (f"fuzzy match to source collection '{best_coll_role}' "
                      f"({best_coll_score:.2f}), flattened into a text region")
                if is_qualified_guess:
                    how += " - source doesn't distinguish this group, treat as unverified"
                return Fact(text, conf, best_coll.evidence, best_coll.extractor), \
                    0.85 * best_coll_score * penalty, how

        best, best_score, best_role = None, 0.0, None
        for src_role, f in source.fields.items():
            s = max(text_similarity(role.replace("_", " "), src_role.replace("_", " ")),
                    text_similarity(fs.label, src_role.replace("_", " ")))
            if s > best_score:
                best, best_score, best_role = f, s, src_role
        if best is not None and best_score >= self.fuzzy_role_match:
            return best, 0.90 * best_score, f"fuzzy match to source role '{best_role}' ({best_score:.2f})"

        if self.llm.available():
            payload = {
                "template_field": {"role": role, "label": fs.label,
                                   "section": fs.section, "format": fs.value_format},
                "available_source_facts": {
                    r: {"value": f.value, "evidence": f.evidence}
                    for r, f in list(source.fields.items())[:60]},
            }
            ans = self.llm.json_or({}, VALUE_SELECTION_SYSTEM, json.dumps(payload, default=str))
            val = (ans or {}).get("value")
            ev = (ans or {}).get("evidence") or ""
            if val not in (None, "", []) and ev:
                from .source.canonical import Fact
                return Fact(val, float(ans.get("confidence", 0.7)), ev, "llm"), 0.85, "llm value selection"
        return None, 0.0, "not found"

    # ------------------------------------------------------------------
    def _map_table(self, role: str, ts: TableSpec, source: CanonicalSource) -> FillInstruction:
        target = {
            **ts.location,
            "header_row": ts.header_row,
            "template_row": ts.template_row,
            "existing_data_rows": ts.existing_data_rows,
            "fixed_rows": ts.fixed_rows,
            "last_row": ts.last_row,
            "columns": {f: c["location_hint"] for f, c in ts.columns.items()
                        if c.get("editable", True)},
        }
        ins = FillInstruction(kind="table", role=role, target=target,
                              node_id=ts.node_id, label=ts.section or role)
        if not ts.editable:
            ins.decision = Decision.SKIP_PROTECTED.value
            return ins

        coll, match_conf, how = self._find_collection(role, ts, source)
        if coll is None or not coll.items:
            ins.decision = Decision.SKIP_NOT_FOUND.value
            ins.notes.append("no supporting collection in source")
            return ins

        writable_cols = {f: c for f, c in ts.columns.items() if c.get("editable", True)}
        rows: List[Dict[str, Any]] = []
        dropped_fields: set = set()
        placed = offered = 0
        for item in coll.items[: self.policy.max_rows_per_table]:
            row: Dict[str, Any] = {}
            for fname, raw in item.items():
                offered += 1
                target = fname if fname in writable_cols else self._nearest_column(fname, writable_cols)
                if target is None:
                    dropped_fields.add(fname)
                    continue
                value, ok = coerce(raw, writable_cols[target].get("format", "text"))
                row[target] = value if ok else raw
                placed += 1
            if row:
                rows.append(row)

        if not rows:
            ins.decision = Decision.SKIP_NOT_FOUND.value
            ins.notes.append("collection found but no column overlap")
            return ins

        if RESERVED_SERIAL_NUMBER_KEY in writable_cols:
            for i, row in enumerate(rows, start=1):
                row[RESERVED_SERIAL_NUMBER_KEY] = i
            ins.notes.append(f"auto-numbered serial column 1..{len(rows)}")

        # coverage = how much of the source data found a home, NOT how many
        # template columns got filled. A template column the source cannot
        # speak to is not a mapping error - it is simply missing information.
        coverage = placed / max(1, offered)
        columns_used = len({k for r in rows for k in r}) / max(1, len(writable_cols))
        conf = min(0.99, _geometric_mean([ts.confidence, coll.confidence, match_conf])
                   * (0.85 + 0.15 * coverage))
        ins.rows = rows
        ins.confidence = round(conf, 3)
        ins.evidence = coll.evidence
        ins.source_extractor = coll.extractor
        ins.notes.append(f"{how}; {len(rows)} rows; source data placed {coverage:.0%}; "
                         f"template columns used {columns_used:.0%}")
        if dropped_fields:
            ins.notes.append("source fields with no column in this template: "
                             + ", ".join(sorted(dropped_fields)))
        ins.decision = self.policy.decide(conf, self.registry.is_critical(role),
                                          bool(coll.evidence)).value
        return ins

    def _find_collection(self, role: str, ts: TableSpec, source: CanonicalSource):
        coll = source.get_collection(role)
        if coll is not None:
            return coll, 1.0, "exact role match"
        base_role = role.split("__")[0]
        if base_role != role and source.get_collection(base_role):
            return source.get_collection(base_role), 0.95, f"matched base role '{base_role}'"

        best, best_score, best_role = None, 0.0, None
        template_fields = set(ts.columns.keys())
        for src_role, c in source.collections.items():
            name_sim = max(text_similarity(role.replace("_", " "), src_role.replace("_", " ")),
                           text_similarity(ts.section or "", src_role.replace("_", " ")))
            src_fields = set()
            for it in c.items[:10]:
                src_fields |= set(it.keys())
            overlap = len(template_fields & src_fields) / max(1, len(template_fields))
            score = 0.5 * name_sim + 0.5 * overlap
            if score > best_score:
                best, best_score, best_role = c, score, src_role
        if best is not None and best_score >= 0.5:
            return best, min(1.0, 0.85 + 0.15 * best_score), \
                   f"structural match to source collection '{best_role}' ({best_score:.2f})"

        # Graceful degradation: the source may only have given a single
        # free-text paragraph ("discussion": "The team reviewed...") rather
        # than a bulleted list. A table expecting a collection can still
        # take that as one row rather than staying empty - a person reading
        # the source would treat one paragraph as "one point" too.
        singular_role = role[:-1] if role.endswith("s") else None
        for candidate_role in filter(None, [role, singular_role, "discussion", "summary"]):
            fact = source.get(candidate_role)
            if fact is not None and fact.supported():
                primary_field = next(iter(ts.columns.keys()), "text")
                wrapped = Collection(items=[{primary_field: fact.value}],
                                    confidence=fact.confidence * 0.85,
                                    evidence=fact.evidence, extractor=fact.extractor)
                return wrapped, 0.80, f"single fact '{candidate_role}' wrapped as one row"
        return None, 0.0, "not found"

    @staticmethod
    @staticmethod
    def _flatten_collection(items: List[Dict[str, Any]], multiline: bool = True) -> str:
        """
        A collection has no table of its own here, so it's rendered as text.

        Whether that reads as a bulleted list or a comma-separated line is
        decided by the CONTENT, not just the target cell's own formatting -
        a cell can have wrap_text=True defensively (in case a long value
        ever lands there) without that being a signal the author actually
        wants a bulleted list. A handful of short items ("Ayushi Jain",
        "Rahul Mehta") reads naturally as one comma-separated line, the way
        a person would actually type it by hand; several full-sentence
        items ("Reviewed vendor delivery timelines...") read far better one
        per line. `multiline=False` still means what it always meant - a
        genuinely single-line target - and overrides content length either
        way, since bullets with embedded newlines in an unwrapped single
        cell would render broken regardless of what the items look like.
        """
        def item_text(it: Dict[str, Any]) -> str:
            if not isinstance(it, dict):
                return str(it)
            primary = next((it[k] for k in ("text", "task", "item", "name", "description",
                                            "point") if it.get(k)), None)
            if primary is None:
                primary = "; ".join(f"{k}: {v}" for k, v in it.items())
            return str(primary)

        def item_with_extras(it: Dict[str, Any], primary: str) -> str:
            if not isinstance(it, dict):
                return primary
            extras = [f"{k}: {v}" for k, v in it.items()
                     if v and v != primary and k in ("owner", "due_date", "status")]
            return primary + (f" ({', '.join(extras)})" if extras else "")

        primaries = [item_text(it) for it in items]
        # Short, name-like items (a handful of short strings, no item over
        # ~40 chars) read fine inline regardless of what the target cell's
        # formatting allows; only genuinely long, sentence-like content
        # gets the bulleted treatment, and only when the target can hold it.
        content_wants_bullets = any(len(p) > 40 for p in primaries) or len(primaries) > 6
        use_bullets = multiline and content_wants_bullets

        if not use_bullets:
            return ", ".join(primaries)
        lines = [item_with_extras(it, p) for it, p in zip(items, primaries)]
        return "\n".join(f"• {l}" for l in lines)

    def _nearest_column(self, field_name: str, columns: Dict[str, Any]) -> Optional[str]:
        best, best_score = None, 0.0
        for cname, cfg in columns.items():
            s = max(text_similarity(field_name, cname),
                    text_similarity(field_name, cfg.get("header", "")))
            for syn in ITEM_FIELD_SYNONYMS.get(cname, []):
                s = max(s, text_similarity(field_name, syn))
            if s > best_score:
                best, best_score = cname, s
        return best if best_score >= 0.7 else None


def build_plan(spec: TemplateSpec, source: CanonicalSource, policy: Optional[Policy] = None,
               llm: Optional[BaseLLM] = None) -> FillPlan:
    return FieldMapper(policy=policy, llm=llm).map(spec, source)
