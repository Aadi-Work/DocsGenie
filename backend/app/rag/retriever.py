from __future__ import annotations

import math
import re
from collections import Counter
from typing import Optional

from app.models.schemas import TemplateMeta
from app.services.catalog import CatalogService


TOKEN_RE = re.compile(r"[a-z0-9_]+", re.I)


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text)]


class HybridRetriever:
    """
    Lightweight semantic+keyword retriever.
    Uses Chroma when available; always falls back to TF-IDF-like scoring.
    """

    def __init__(self, catalog: CatalogService):
        self.catalog = catalog
        self._collection = None
        self._init_chroma()

    def _init_chroma(self) -> None:
        try:
            import chromadb
            from app.config import get_settings

            settings = get_settings()
            client = chromadb.PersistentClient(path=settings.chroma_path)
            self._collection = client.get_or_create_collection(
                name="ymsli_templates",
                metadata={"hnsw:space": "cosine"},
            )
        except Exception:
            self._collection = None

    def _doc_text(self, t: TemplateMeta) -> str:
        latest = self.catalog.latest_version(t)
        return " ".join(
            [
                t.name,
                t.description,
                t.category.value,
                " ".join(t.tags),
                " ".join(t.content_outline),
                " ".join(t.placeholders),
                latest.changelog,
                latest.version,
            ]
        )

    def reindex(self) -> int:
        templates = self.catalog.list_templates()
        if self._collection is None:
            return len(templates)
        ids = [t.id for t in templates]
        documents = [self._doc_text(t) for t in templates]
        metadatas = [
            {
                "name": t.name,
                "category": t.category.value,
                "format": t.output_format.value,
            }
            for t in templates
        ]
        # recreate for idempotent seed
        try:
            existing = self._collection.get()
            if existing and existing.get("ids"):
                self._collection.delete(ids=existing["ids"])
        except Exception:
            pass
        if ids:
            self._collection.add(ids=ids, documents=documents, metadatas=metadatas)
        return len(ids)

    def _keyword_score(self, query: str, template: TemplateMeta) -> float:
        q_tokens = tokenize(query)
        if not q_tokens:
            return 0.0
        doc_tokens = tokenize(self._doc_text(template))
        if not doc_tokens:
            return 0.0
        q_counts = Counter(q_tokens)
        d_counts = Counter(doc_tokens)
        overlap = sum(min(q_counts[t], d_counts[t]) for t in q_counts)
        # boost exact phrase / tag hits
        text = self._doc_text(template).lower()
        phrase_boost = sum(2.0 for t in q_tokens if t in text)
        tf = overlap / math.sqrt(len(doc_tokens))
        return tf + phrase_boost

    def search(self, query: str, limit: int = 5) -> list[tuple[TemplateMeta, float]]:
        templates = {t.id: t for t in self.catalog.list_templates()}
        scored: dict[str, float] = {}

        # keyword channel
        for t in templates.values():
            scored[t.id] = self._keyword_score(query, t)

        # chroma channel
        if self._collection is not None and query.strip():
            try:
                result = self._collection.query(query_texts=[query], n_results=min(limit, len(templates) or 1))
                ids = (result.get("ids") or [[]])[0]
                distances = (result.get("distances") or [[]])[0]
                for tid, dist in zip(ids, distances):
                    # chroma cosine distance → similarity
                    sim = 1.0 - float(dist) if dist is not None else 0.0
                    scored[tid] = scored.get(tid, 0.0) + max(sim, 0.0) * 3.0
            except Exception:
                pass

        ranked = sorted(scored.items(), key=lambda x: x[1], reverse=True)
        out: list[tuple[TemplateMeta, float]] = []
        for tid, score in ranked:
            if score <= 0:
                continue
            tmpl = templates.get(tid)
            if tmpl:
                out.append((tmpl, score))
            if len(out) >= limit:
                break
        if not out:
            # soft fallback: return most used
            fallback = sorted(templates.values(), key=lambda t: t.usage_count, reverse=True)[:limit]
            return [(t, 0.1) for t in fallback]
        return out

    def best_match(self, query: str) -> Optional[TemplateMeta]:
        hits = self.search(query, limit=1)
        return hits[0][0] if hits else None
