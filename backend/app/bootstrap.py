"""Seed database and rebuild vector index."""

from __future__ import annotations

from pathlib import Path

from app.config import get_settings
from app.deps import get_catalog, get_retriever


def main() -> None:
    settings = get_settings()
    Path(settings.storage_path).mkdir(parents=True, exist_ok=True)
    Path(settings.storage_path, "generated").mkdir(parents=True, exist_ok=True)
    Path(settings.chroma_path).mkdir(parents=True, exist_ok=True)

    catalog = get_catalog()
    count = catalog.seed_from_json(force=True)
    indexed = get_retriever().reindex()
    print(f"Seeded {count} templates; indexed {indexed} documents.")
    for t in catalog.list_templates():
        latest = catalog.latest_version(t)
        print(f" - {t.id}: {t.name} (v{latest.version}, {t.output_format.value})")


if __name__ == "__main__":
    main()
