"""
Source -> CanonicalSource.

Rules find the obvious ("Date: 12 August 2026", pipe tables, bullet lists under
"Decisions"). The LLM finds the rest, but only under a prompt that forbids
invention and demands a verbatim quote per fact.

Hard rule enforced here, not merely requested of the model:
    a fact whose evidence string does not appear in the source text is dropped.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

from ..normalize import coerce, parse_date
from ..ir import ValueFormat
from ..semantic.classifier import SemanticClassifier, normalize as norm_text, text_similarity
from ..semantic.llm import BaseLLM, NullLLM, SOURCE_EXTRACTION_SYSTEM
from ..semantic.roles import DEFAULT_REGISTRY, ITEM_FIELD_SYNONYMS, RoleRegistry
from .canonical import CanonicalSource, Collection, Fact
from .readers import read_source

KV_RE = re.compile(r"^\s*[-*•]?\s*\**([A-Za-z][A-Za-z0-9 /&().'\-]{1,48}?)\**\s*[:\-–]\s+(.+?)\s*$")
HEADING_RE = re.compile(r"^\s*(?:#{1,6}\s*|\d+[.)]\s*)?\**([A-Za-z][A-Za-z0-9 /&()'\-]{1,60}?)\**\s*:?\s*$")
BULLET_RE = re.compile(r"^\s*(?:[-*•·]|\d+[.)])\s+(.+?)\s*$")
PIPE_RE = re.compile(r"^\s*\|?(.+\|.+?)\|?\s*$")
SEP_ROW_RE = re.compile(r"^[\s|:\-—+]+$")
LIST_SPLIT_RE = re.compile(r"\s*[,;]\s*|\s+and\s+")
# A parenthetical qualifier - "(YMSLI)", "(Vendor)" - marks a genuinely
# distinct variant of a role, as opposed to plain descriptive phrasing.
QUALIFIER_RE = re.compile(r"\(([A-Za-z0-9][A-Za-z0-9 &/\-]{0,24})\)")

# --------------------------------------------------------------------------
# Narrative-prose patterns. These are best-effort heuristics, not language
# understanding - genuinely free-form text ("the meeting covered various
# operational concerns") is exactly the case the LLM layer exists for. What
# follows only catches phrasing common enough in meeting notes to be worth a
# dedicated pattern: a date anywhere in the text, "met at/in <place>", a
# "prepared these notes" attribution, "From <group>, X and Y attended", and
# "<Name> will <task> by <date>" action items.
_DATE_ANYWHERE_RE = re.compile(
    r"\b(?:\d{4}-\d{1,2}-\d{1,2}"
    r"|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\.?,?\s+\d{4}"
    r"|[A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})\b")
_LOCATION_RE = re.compile(
    r"\b(?:met|meeting|held|conducted|took place)\b(?:\s+\S+){0,4}?\s+(?:at|in)\s+"
    r"([A-Z][^.,;\n]{2,45}?)(?=[.,;]|\s+(?:to|on|for|and)\b|$)", re.S)
_PURPOSE_RE = re.compile(
    r"(?:to\s+discuss|meeting\s+was\s+(?:called|held)(?:\s+\S+){0,2}?\s+to"
    r"|purpose\s+(?:of\s+(?:the|this)\s+meeting\s+)?(?:was|is)\s+to)\s+"
    r"([a-z][^.;]{5,90}?)(?=[.;]|$)", re.I | re.S)
_PREPARED_BY_RE = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\s+(?:prepared|drafted|wrote|recorded|took)\s+"
    r"(?:these\s+|this\s+|the\s+)?(?:notes|minutes)\b", re.S)
_ATTENDEE_GROUP_RE = re.compile(
    r"\bfrom\s+([A-Za-z0-9][\w &/\-]{1,24}?)(?:\s+side)?[,:]?\s+"
    r"([A-Z][\w .,'&\-]+?)\s+(?:attended|joined|were\s+present|participated|joined\s+the\s+call)\b",
    re.I | re.S)
_ACTION_ITEM_RE = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\s+(?:will|needs\s+to|is\s+to|should|must)\s+"
    r"([a-z][^.;]{5,150}?)\s+by\s+"
    r"(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9})"
    r"(?:,?\s*(?:marked|status(?:ed)?\s+as)?\s*(in progress|open|closed|done|pending|completed))?",
    re.I | re.S)


class SourceExtractor:
    def __init__(self, registry: RoleRegistry = DEFAULT_REGISTRY,
                 llm: Optional[BaseLLM] = None, use_llm: bool = True):
        self.registry = registry
        self.classifier = SemanticClassifier(registry)
        self.llm = llm or NullLLM()
        self.use_llm = use_llm

    # ------------------------------------------------------------------
    def extract_file(self, path: str) -> CanonicalSource:
        return self.extract_text(read_source(path), origin=path)

    def extract_text(self, text: str, origin: str = "") -> CanonicalSource:
        cs = CanonicalSource(text=text, meta={"origin": origin, "chars": len(text)})
        self._rules_pass(text, cs)
        self._narrative_pass(text, cs)
        if self.use_llm and self.llm.available():
            self._llm_pass(text, cs)
        cs.meta["extractors"] = sorted({f.extractor for f in cs.fields.values()} |
                                       {c.extractor for c in cs.collections.values()})
        return cs

    # ------------------------------------------------------------------
    # rules
    # ------------------------------------------------------------------
    def _rules_pass(self, text: str, cs: CanonicalSource) -> None:
        lines = text.splitlines()
        current_heading = ""
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            if not stripped:
                i += 1
                continue

            # ---- pipe / markdown table -> collection
            if "|" in stripped and not KV_RE.match(stripped):
                block, consumed = self._collect_table(lines, i)
                if block:
                    self._table_to_collection(block, current_heading, cs)
                    i += consumed
                    continue

            # ---- key: value (may continue across several wrapped lines)
            m = KV_RE.match(stripped)
            if m:
                key, val = m.group(1).strip(), m.group(2).strip()
                if val and len(key.split()) <= 8:
                    continuation, consumed = self._collect_continuation(lines, i + 1)
                    full_val = " ".join([val] + continuation) if continuation else val
                    evidence = " ".join([stripped] + continuation) if continuation else stripped
                    self._absorb_kv(key, full_val, evidence, cs)
                    i += 1 + consumed
                    continue

            # ---- heading followed by bullets -> collection, or prose -> narrative fact
            h = HEADING_RE.match(stripped)
            if h and (stripped.endswith(":") or stripped.startswith("#")
                      or len(stripped.split()) <= 6):
                heading = h.group(1).strip()
                current_heading = heading
                bullets, consumed = self._collect_bullets(lines, i + 1)
                if bullets:
                    self._bullets_to_collection(heading, bullets, cs)
                    i += 1 + consumed
                    continue
                prose, consumed = self._collect_prose(lines, i + 1)
                if prose:
                    self._prose_to_fact(heading, prose, cs)
                    i += 1 + consumed
                    continue
            i += 1

    def _absorb_kv(self, key: str, val: str, evidence: str, cs: CanonicalSource) -> None:
        scores = self.classifier.label_scores(key)
        if not scores:
            return
        role, score, matched_synonym = scores[0]
        if score < 0.70:
            return
        rd = self.registry.get(role)

        # A parenthetical qualifier - "Attendees (YMSLI)" vs "Attendees
        # (YMESG)" - marks a genuinely distinct group, not just incidental
        # phrasing ("Meeting Attendees" has no such marker and should still
        # map to the plain "attendees" role). Minting a qualified role for
        # it, using the same slugify scheme the template side uses for its
        # own open-world roles, means a template placeholder named the same
        # way lines up with this exactly, with no fuzzy step needed.
        qualifier = QUALIFIER_RE.search(key)
        if qualifier:
            qualified = self.registry.mint_unknown(key, is_collection=bool(rd and rd.is_collection))
            role, rd = qualified.role, qualified

        if rd and rd.is_collection:
            parts = [p.strip() for p in LIST_SPLIT_RE.split(val) if p.strip()]
            if len(parts) >= 2 or role in ("attendees", "absentees") or role.startswith("x_"):
                field_name = "name" if (role in ("attendees", "absentees") or role.startswith("x_")) else (
                    rd.item_fields[0] if rd.item_fields else "text")
                cs.put_collection(role, Collection(
                    items=[{field_name: p} for p in parts],
                    confidence=round(min(0.93, 0.55 + 0.4 * score), 3),
                    evidence=evidence, extractor="rules"))
                return

        fmt = rd.value_format if rd else ValueFormat.TEXT
        coerced, ok = coerce(val, fmt)
        conf = min(0.95, 0.55 + 0.4 * score) * (1.0 if ok else 0.75)
        cs.put(role, Fact(value=coerced, confidence=round(conf, 3),
                          evidence=evidence, extractor="rules", raw=val))

    def _collect_continuation(self, lines: List[str], start: int) -> Tuple[List[str], int]:
        """
        A hand-written "Key: value" line is very often hard-wrapped across
        several physical lines in a real document - the value doesn't stop
        just because the editor inserted a line break. Keep absorbing lines
        as continuation as long as they don't themselves look like a new
        key:value pair, a heading, a bullet, or a table row - any of those
        is a real boundary, a mid-sentence line break is not.
        """
        out, i = [], start
        while i < len(lines):
            s = lines[i].strip()
            if not s:
                break
            if KV_RE.match(s) or HEADING_RE.match(s) or BULLET_RE.match(s) or "|" in s:
                break
            out.append(s)
            i += 1
            if len(out) > 30:
                break
        return out, (i - start)

    def _collect_bullets(self, lines: List[str], start: int) -> Tuple[List[str], int]:
        out, i = [], start
        while i < len(lines):
            s = lines[i].strip()
            if not s:
                if out:
                    break
                i += 1
                continue
            m = BULLET_RE.match(s)
            if not m:
                break
            out.append(m.group(1).strip())
            i += 1
        return out, (i - start)

    def _collect_prose(self, lines: List[str], start: int) -> Tuple[str, int]:
        """Body text under a heading, stopping at the next heading, bullet or table."""
        out, i, blanks = [], start, 0
        while i < len(lines):
            s = lines[i].strip()
            if not s:
                blanks += 1
                if out and blanks >= 2:
                    break
                i += 1
                continue
            if s.startswith("#") or BULLET_RE.match(s) or "|" in s:
                break
            if KV_RE.match(s) and not out:
                break
            blanks = 0
            out.append(s)
            i += 1
            if len(out) > 60:
                break
        text = " ".join(out).strip()
        return (text if len(text) >= 40 else ""), (i - start)

    def _prose_to_fact(self, heading: str, prose: str, cs: CanonicalSource) -> None:
        scores = self.classifier.label_scores(heading)
        if not scores or scores[0][1] < 0.72:
            return
        role, score, _ = scores[0]
        if self.registry.is_collection(role):
            return
        cs.put(role, Fact(value=prose,
                          confidence=round(min(0.92, 0.55 + 0.4 * score), 3),
                          evidence=prose[:300], extractor="rules"))

    def _collect_table(self, lines: List[str], start: int) -> Tuple[List[List[str]], int]:
        rows, i = [], start
        while i < len(lines):
            s = lines[i].strip()
            if "|" not in s:
                break
            if SEP_ROW_RE.match(s):
                i += 1
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) < 2:
                break
            rows.append(cells)
            i += 1
        if len(rows) < 2:
            return [], max(1, i - start)
        return rows, (i - start)

    def _table_to_collection(self, rows: List[List[str]], heading: str, cs: CanonicalSource) -> None:
        header = rows[0]
        mapped: Dict[int, str] = {}
        for idx, h in enumerate(header):
            fname, score = self.classifier.classify_column(h)
            if fname and fname not in mapped.values():
                mapped[idx] = fname
        if len(mapped) < 2:
            return

        items = []
        for row in rows[1:]:
            item = {}
            for idx, fname in mapped.items():
                if idx < len(row) and row[idx]:
                    item[fname] = row[idx]
            if item:
                items.append(item)
        if not items:
            return

        role = self._collection_role(heading, list(mapped.values()))
        cs.put_collection(role, Collection(
            items=items, confidence=0.88,
            evidence=" | ".join(header) + f"  (+{len(items)} rows)",
            extractor="rules"))

    def _bullets_to_collection(self, heading: str, bullets: List[str], cs: CanonicalSource) -> None:
        scores = self.classifier.label_scores(heading)
        role = None
        for r, s, _ in scores:
            rd = self.registry.get(r)
            if rd and rd.is_collection and s >= 0.70:
                role = r
                break
        if role is None:
            if scores and scores[0][1] >= 0.75 and not self.registry.is_collection(scores[0][0]):
                # narrative block: keep as a single multi-line fact
                cs.put(scores[0][0], Fact(
                    value="\n".join(f"• {b}" for b in bullets),
                    confidence=round(min(0.9, 0.5 + 0.4 * scores[0][1]), 3),
                    evidence=f"{heading}: " + "; ".join(bullets[:3])[:300],
                    extractor="rules"))
            return

        rd = self.registry.get(role)
        items = [self._parse_bullet_item(b, rd.item_fields if rd else []) for b in bullets]
        cs.put_collection(role, Collection(
            items=[i for i in items if i], confidence=0.82,
            evidence=f"{heading}: " + "; ".join(bullets[:3])[:300], extractor="rules"))

    def _parse_bullet_item(self, bullet: str, item_fields: List[str]) -> Dict[str, str]:
        """
        Handles the common shorthand forms without inventing structure:
            "Fix the API — Ayushi — 20 Aug"
            "Ayushi: fix the API (due 20 Aug)"
            "Fix the API [Owner: Ayushi, Due: 20 Aug]"
        """
        text = bullet.strip()
        item: Dict[str, str] = {}

        for m in re.finditer(r"(?:\(|\[)?\s*(owner|assignee|responsible|due|by|status|priority)"
                             r"\s*[:=]\s*([^\)\],;]+)", text, re.I):
            key, val = m.group(1).lower(), m.group(2).strip()
            fname = {"owner": "owner", "assignee": "owner", "responsible": "owner",
                     "due": "due_date", "by": "due_date", "status": "status",
                     "priority": "priority"}[key]
            item[fname] = val
            text = text.replace(m.group(0), " ")

        parts = [p.strip() for p in re.split(r"\s+[—–|]\s+|\s+--\s+", text) if p.strip()]
        if len(parts) >= 2:
            main = parts[0]
            for p in parts[1:]:
                d = parse_date(p)
                if d and "due_date" not in item:
                    item["due_date"] = d.isoformat()
                elif len(p.split()) <= 4 and "owner" not in item:
                    item["owner"] = p
                elif "status" not in item and p.lower() in ("open", "closed", "in progress",
                                                            "pending", "done", "wip"):
                    item["status"] = p
            text = main
        text = re.sub(r"\s{2,}", " ", text).strip(" -–—:")

        primary = "task"
        if item_fields:
            primary = next((f for f in ("task", "item", "name", "description", "text")
                            if f in item_fields), item_fields[0])
        if text:
            item[primary] = text
        item.pop("", None)
        return item

    def _collection_role(self, heading: str, fields: List[str]) -> str:
        if heading:
            for r, s, _ in self.classifier.label_scores(heading):
                if self.registry.is_collection(r) and s >= 0.65:
                    return r
        fs = set(fields)
        if {"owner", "task"} <= fs or {"task", "due_date"} <= fs:
            return "action_items"
        if {"name"} <= fs and len(fs) <= 4:
            return "attendees"
        if {"description", "quantity"} <= fs or {"unit_price", "amount"} <= fs:
            return "line_items"
        return self.registry.mint_unknown(heading or "items", is_collection=True).role

    # ------------------------------------------------------------------
    # narrative prose (best-effort heuristics, not language understanding)
    # ------------------------------------------------------------------
    def _narrative_pass(self, text: str, cs: CanonicalSource) -> None:
        """
        Structured rules (_rules_pass) look for explicit "Key: value" lines,
        headings, bullets, and tables. Genuinely free-form prose - a person
        just typing up what happened - has none of that. This pass adds a
        handful of common narrative phrasings on top, each contributing only
        when the structured pass found nothing for that role (evidenced
        facts from an explicit "Date:" line always outrank a pattern match
        in prose, via the same confidence comparison every other source
        already goes through).

        This is NOT a substitute for the LLM layer. It catches specific,
        common phrasings - it does not understand meaning. A meeting summary
        that doesn't happen to use any of these constructions will still
        come through empty on the fields below, same as before.
        """
        if not cs.get("meeting_date"):
            m = _DATE_ANYWHERE_RE.search(text)
            if m:
                d = parse_date(m.group(0))
                if d:
                    cs.put("meeting_date", Fact(d, 0.65, m.group(0), "narrative"))

        if not cs.get("location"):
            m = _LOCATION_RE.search(text)
            if m:
                loc = re.sub(r"\s+", " ", m.group(1)).strip()
                cs.put("location", Fact(loc, 0.60, m.group(0), "narrative"))

        if not cs.get("purpose"):
            m = _PURPOSE_RE.search(text)
            if m:
                purpose = re.sub(r"\s+", " ", m.group(1)).strip()
                cs.put("purpose", Fact(purpose, 0.55, m.group(0), "narrative"))

        if not cs.get("recorder"):
            m = _PREPARED_BY_RE.search(text)
            if m:
                cs.put("recorder", Fact(m.group(1).strip(), 0.70, m.group(0), "narrative"))

        self._narrative_attendee_groups(text, cs)
        self._narrative_action_items(text, cs)

    def _narrative_attendee_groups(self, text: str, cs: CanonicalSource) -> None:
        """'From YMSLI, Ayushi Jain, Rahul Mehta and Sana Qureshi attended.'"""
        for m in _ATTENDEE_GROUP_RE.finditer(text):
            qualifier, names_blob = m.group(1).strip(), m.group(2).strip()
            names = [n.strip() for n in LIST_SPLIT_RE.split(names_blob) if n.strip()]
            names = [n for n in names if re.match(r"^[A-Z][\w.'\-]*(\s+[A-Z][\w.'\-]*)*$", n)]
            if not names:
                continue
            role = "attendees"
            if QUALIFIER_RE.search(f"({qualifier})") or len(qualifier.split()) <= 3:
                # Named group ("YMSLI") reads the same way a parenthetical
                # qualifier does elsewhere - keep distinct groups distinct.
                candidate_key = f"Attendees ({qualifier})"
                scores = self.classifier.label_scores(candidate_key)
                if scores and scores[0][1] >= 0.7:
                    qualified = self.registry.mint_unknown(candidate_key, is_collection=True)
                    role = qualified.role
            existing = cs.get_collection(role)
            if existing and len(existing.items) >= len(names):
                continue
            cs.put_collection(role, Collection(
                items=[{"name": n} for n in names], confidence=0.60,
                evidence=m.group(0), extractor="narrative"))

    def _narrative_action_items(self, text: str, cs: CanonicalSource) -> None:
        """'Rahul Mehta will update the SLA document... by 20th August.'"""
        items = []
        evidences = []
        meeting_date = cs.get("meeting_date")
        default_year = meeting_date.value.year if meeting_date and hasattr(meeting_date.value, "year") else None
        for m in _ACTION_ITEM_RE.finditer(text):
            owner, task, due_raw, status = m.group(1), m.group(2), m.group(3), m.group(4)
            due = parse_date(due_raw, default_year=default_year)
            item = {"task": re.sub(r"\s+", " ", task).strip(),
                   "owner": re.sub(r"\s+", " ", owner).strip()}
            if due:
                item["due_date"] = due.isoformat()
            if status:
                item["status"] = status.strip().title()
            items.append(item)
            evidences.append(m.group(0))
        if not items:
            return
        existing = cs.get_collection("action_items")
        if existing and len(existing.items) >= len(items):
            return
        cs.put_collection("action_items", Collection(
            items=items, confidence=0.60, evidence="; ".join(evidences[:2]),
            extractor="narrative"))

    # ------------------------------------------------------------------
    # LLM (additive; never overwrites a well-evidenced rule fact)
    # ------------------------------------------------------------------
    def _llm_pass(self, text: str, cs: CanonicalSource) -> None:
        known = [rd.role for rd in self.registry.all() if not rd.role.startswith("x_")]
        payload = {
            "known_roles": known,
            "already_extracted": sorted(cs.fields.keys()),
            "item_field_vocabulary": sorted(ITEM_FIELD_SYNONYMS.keys()),
            "source_text": text[:60000],
        }
        data = self.llm.json_or(None, SOURCE_EXTRACTION_SYSTEM, json.dumps(payload), max_tokens=4000)
        if not isinstance(data, dict):
            return

        haystack = norm_text(text)
        for role, f in (data.get("fields") or {}).items():
            if not isinstance(f, dict):
                continue
            value, ev = f.get("value"), (f.get("evidence") or "").strip()
            if value in (None, "", []) or not ev:
                continue
            if not self._evidence_present(ev, haystack):
                continue                                    # unsupported -> dropped
            rd = self.registry.get(role)
            fmt = rd.value_format if rd else ValueFormat.TEXT
            coerced, ok = coerce(value, fmt)
            conf = float(f.get("confidence", 0.7)) * (1.0 if ok else 0.8)
            existing = cs.fields.get(role)
            if existing and existing.confidence >= conf:
                continue
            cs.put(role, Fact(coerced, round(min(conf, 0.97), 3), ev, "llm", raw=str(value)))

        for role, c in (data.get("collections") or {}).items():
            if not isinstance(c, dict):
                continue
            items = [i for i in (c.get("items") or []) if isinstance(i, dict) and i]
            ev = (c.get("evidence") or "").strip()
            if not items or not ev or not self._evidence_present(ev, haystack):
                continue
            existing = cs.collections.get(role)
            if existing and len(existing.items) >= len(items) and existing.confidence >= float(c.get("confidence", 0.7)):
                continue
            cs.put_collection(role, Collection(items, round(min(float(c.get("confidence", 0.75)), 0.97), 3),
                                               ev, "llm"))

    @staticmethod
    def _evidence_present(evidence: str, haystack_norm: str, threshold: float = 0.82) -> bool:
        ev = norm_text(evidence)
        if len(ev) < 8:
            return False
        if ev in haystack_norm:
            return True
        # tolerate whitespace/punctuation drift in the quote
        window = len(ev)
        step = max(1, window // 4)
        for i in range(0, max(1, len(haystack_norm) - window + 1), step):
            if text_similarity(ev, haystack_norm[i:i + window]) >= threshold:
                return True
        return False


def extract_source(path: str, llm: Optional[BaseLLM] = None, **kw) -> CanonicalSource:
    return SourceExtractor(llm=llm, **kw).extract_file(path)
