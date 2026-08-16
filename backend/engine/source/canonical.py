"""
Canonical source model.

The source document is never mapped straight onto the template. It is first
reduced to this evidence-bearing structure:

    Summary/MOM/PDF/transcript -> CanonicalSource -> (mapping) -> Template

Every fact carries the verbatim text it came from. A fact with no evidence
is not a fact - it never reaches the renderer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class Fact:
    value: Any
    confidence: float = 0.0
    evidence: str = ""
    extractor: str = "rules"          # rules | llm | user
    raw: Optional[str] = None         # pre-normalisation text

    def supported(self) -> bool:
        return self.value not in (None, "", []) and bool(self.evidence)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Collection:
    items: List[Dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    evidence: str = ""
    extractor: str = "rules"

    def supported(self) -> bool:
        return bool(self.items) and bool(self.evidence)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CanonicalSource:
    fields: Dict[str, Fact] = field(default_factory=dict)
    collections: Dict[str, Collection] = field(default_factory=dict)
    text: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    # -- access ---------------------------------------------------------
    def get(self, role: str) -> Optional[Fact]:
        return self.fields.get(role)

    def get_collection(self, role: str) -> Optional[Collection]:
        return self.collections.get(role)

    def put(self, role: str, fact: Fact) -> None:
        """Higher-confidence facts win; ties keep the first (earlier = more prominent)."""
        cur = self.fields.get(role)
        if cur is None or fact.confidence > cur.confidence:
            self.fields[role] = fact

    def put_collection(self, role: str, coll: Collection) -> None:
        cur = self.collections.get(role)
        if cur is None or len(coll.items) > len(cur.items) or coll.confidence > cur.confidence:
            self.collections[role] = coll

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fields": {k: v.to_dict() for k, v in self.fields.items()},
            "collections": {k: v.to_dict() for k, v in self.collections.items()},
            "meta": self.meta,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_dict(cls, d: Dict[str, Any], text: str = "") -> "CanonicalSource":
        cs = cls(text=text, meta=d.get("meta", {}))
        for role, f in (d.get("fields") or {}).items():
            if isinstance(f, dict):
                cs.fields[role] = Fact(value=f.get("value"),
                                       confidence=float(f.get("confidence", 0.0)),
                                       evidence=f.get("evidence", ""),
                                       extractor=f.get("extractor", "llm"))
        for role, c in (d.get("collections") or {}).items():
            if isinstance(c, dict):
                cs.collections[role] = Collection(items=c.get("items", []),
                                                  confidence=float(c.get("confidence", 0.0)),
                                                  evidence=c.get("evidence", ""),
                                                  extractor=c.get("extractor", "llm"))
        return cs
