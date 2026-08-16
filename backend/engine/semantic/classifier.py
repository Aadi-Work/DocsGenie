"""
Rule-based semantic classification.

Principle from the design doc: never identify a field from its label alone.
Every classification fuses several independent signals:

    label text  +  number format  +  section context  +  layout position
                +  merge state    +  emptiness        +  column neighbours

The output is always a *ranked candidate list* with a score, never a single
hard answer. The LLM layer (semantic/llm.py) only arbitrates when the rules
are ambiguous, and the validation gate has the final word.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from ..ir import Node, TableIR, ValueFormat
from .roles import ITEM_FIELD_SYNONYMS, RoleRegistry, DEFAULT_REGISTRY

_STOP = {"the", "of", "a", "an", "for", "to", "in", "on", "by", "and", "is", "no", "s"}
_PUNCT_RE = re.compile(r"[^\w\s%/&.-]+")
_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    t = (text or "").lower().replace("_", " ").replace("\u00a0", " ")
    t = _PUNCT_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip(" :*-.")
    return t.strip()


def tokens(text: str) -> List[str]:
    return [w for w in normalize(text).split() if w not in _STOP]


def text_similarity(a: str, b: str) -> float:
    """Token-overlap (Jaccard-ish, recall-weighted) blended with character ratio."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = set(tokens(a)), set(tokens(b))
    overlap = 0.0
    if ta and tb:
        inter = len(ta & tb)
        overlap = inter / max(1, min(len(ta), len(tb)))
        if ta == tb:
            overlap = 1.0
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    containment = 1.0 if (na in nb or nb in na) and min(len(na), len(nb)) >= 4 else 0.0
    return max(0.85 * overlap + 0.15 * ratio, ratio * 0.9, containment * 0.88)


# --------------------------------------------------------------------------
@dataclass
class RoleCandidate:
    role: str
    score: float
    signals: Dict[str, float]
    reason: str = ""

    def to_dict(self) -> dict:
        return {"role": self.role, "score": round(self.score, 4),
                "signals": {k: round(v, 3) for k, v in self.signals.items()},
                "reason": self.reason}


FORMAT_COMPAT = {
    (ValueFormat.DATE, ValueFormat.DATE): 1.0,
    (ValueFormat.TIME, ValueFormat.TIME): 1.0,
    (ValueFormat.NUMBER, ValueFormat.NUMBER): 1.0,
    (ValueFormat.CURRENCY, ValueFormat.CURRENCY): 1.0,
    (ValueFormat.CURRENCY, ValueFormat.NUMBER): 0.7,
    (ValueFormat.NUMBER, ValueFormat.CURRENCY): 0.7,
}


def format_compatibility(expected: ValueFormat, observed: ValueFormat) -> float:
    if observed in (ValueFormat.UNKNOWN, None) or expected in (ValueFormat.UNKNOWN, None):
        return 0.5                      # neutral - no evidence either way
    if expected == observed:
        return 1.0
    if (expected, observed) in FORMAT_COMPAT:
        return FORMAT_COMPAT[(expected, observed)]
    if expected == ValueFormat.TEXT:
        return 0.45
    return 0.1                          # date role in a currency cell: strong veto


class SemanticClassifier:
    """Signal fusion. Weights are configurable; nothing here is template-specific."""

    WEIGHTS = {
        "label": 0.55,
        "format": 0.15,
        "section": 0.15,
        "layout": 0.10,
        "neighbour": 0.05,
    }

    def __init__(self, registry: RoleRegistry = DEFAULT_REGISTRY,
                 weights: Optional[Dict[str, float]] = None):
        self.registry = registry
        self.weights = {**self.WEIGHTS, **(weights or {})}

    # -- label -> role ---------------------------------------------------
    def label_scores(self, label: str) -> List[Tuple[str, float, str]]:
        out: List[Tuple[str, float, str]] = []
        for rd in self.registry.all():
            if rd.role.startswith("x_"):
                continue
            best, matched = 0.0, ""
            for syn in [rd.role.replace("_", " ")] + rd.synonyms:
                s = text_similarity(label, syn)
                if s > best:
                    best, matched = s, syn
            if best > 0.35:
                out.append((rd.role, best, matched))
        out.sort(key=lambda x: -x[1])
        return out

    # -- full multi-signal classification of one value region ------------
    def classify_node(self, node: Node, ir_sections: Optional[Sequence[str]] = None,
                      top_k: int = 5) -> List[RoleCandidate]:
        label = node.label or node.text or ""
        if not label.strip():
            return []

        cands: List[RoleCandidate] = []
        for role, lscore, matched in self.label_scores(label)[:12]:
            rd = self.registry.get(role)
            sig: Dict[str, float] = {"label": lscore}

            sig["format"] = format_compatibility(rd.value_format, node.value_format)

            # section context: "Date" inside "Meeting Information" beats a stray "Date"
            sec = normalize(node.section or "")
            sec_hit = 0.5
            if sec:
                sec_sim = max([text_similarity(sec, s) for s in ([rd.role.replace("_", " ")] + rd.synonyms)] or [0])
                head = role.split("_")[0]
                if head and head in sec:
                    sec_hit = 0.95
                else:
                    sec_hit = 0.5 + 0.5 * sec_sim
            sig["section"] = sec_hit

            # layout: an empty, merged, unlocked region is a strong "fill me" signal
            layout = 0.4
            if node.is_empty:
                layout += 0.3
            if node.style.merged:
                layout += 0.2
            if node.style.bordered or node.style.filled:
                layout += 0.1
            if node.has_formula or not node.editable:
                layout = 0.0
            sig["layout"] = min(layout, 1.0)

            # neighbour: label immediately adjacent (parser sets this) is worth a lot
            sig["neighbour"] = 1.0 if node.label_node_id else 0.5

            score = sum(self.weights[k] * v for k, v in sig.items())
            # a collection role has no business in a single-cell region
            if rd.is_collection and not node.meta.get("multiline_capable"):
                score *= 0.55
            cands.append(RoleCandidate(role, score, sig,
                                       reason=f"label≈'{matched}'"))

        cands.sort(key=lambda c: -c.score)
        return cands[:top_k]

    # -- table -> collection role ---------------------------------------
    def classify_table(self, table: TableIR, top_k: int = 3) -> List[RoleCandidate]:
        header_text = " ".join(c.header_text for c in table.columns)
        title = table.section or ""
        cands: List[RoleCandidate] = []

        for rd in self.registry.all():
            if not rd.is_collection or rd.role.startswith("x_"):
                continue
            title_score = max([text_similarity(title, s)
                               for s in [rd.role.replace("_", " ")] + rd.synonyms] or [0])
            # how many declared item_fields do the headers cover?
            covered = 0
            for f in rd.item_fields:
                syns = ITEM_FIELD_SYNONYMS.get(f, [f])
                if any(max(text_similarity(c.header_text, s) for s in syns) > 0.7
                       for c in table.columns):
                    covered += 1
            cover_score = covered / max(1, len(rd.item_fields)) if rd.item_fields else 0.0
            header_score = max([text_similarity(header_text, s) for s in rd.synonyms] or [0])

            score = 0.45 * title_score + 0.40 * cover_score + 0.15 * header_score
            if score > 0.25:
                cands.append(RoleCandidate(
                    rd.role, score,
                    {"title": title_score, "column_coverage": cover_score, "headers": header_score},
                    reason=f"{covered}/{max(1,len(rd.item_fields))} expected columns present"))

        cands.sort(key=lambda c: -c.score)
        return cands[:top_k]

    # -- column header -> item field -------------------------------------
    def classify_column(self, header: str, allowed: Optional[Sequence[str]] = None) -> Tuple[Optional[str], float]:
        pool = allowed or list(ITEM_FIELD_SYNONYMS.keys())
        best, best_score = None, 0.0
        for f in pool:
            for syn in [f.replace("_", " ")] + ITEM_FIELD_SYNONYMS.get(f, []):
                s = text_similarity(header, syn)
                if s > best_score:
                    best, best_score = f, s
        return (best, best_score) if best_score > 0.6 else (None, best_score)
