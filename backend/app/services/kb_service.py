"""Load the SE37 BRD knowledge-base summary from S3 `KB/` (local fallback)."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import ROOT, get_settings
from app.utils.file_utils import AppError, normalize_prefix

log = logging.getLogger(__name__)

SUMMARY_NAME = "YNS_FO_GT_SE37_Knowledge_Base.json"
CACHE_DIR = ROOT / "storage" / "kb"


def _local_candidates() -> list[Path]:
    home = Path.home()
    return [
        CACHE_DIR / SUMMARY_NAME,
        home / "Downloads" / SUMMARY_NAME,
        Path(r"C:\Users\VE00YN245\Downloads") / SUMMARY_NAME,
    ]


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as exc:
        log.warning("Could not read KB JSON %s: %s", path, exc)
        return None


def _looks_like_summary(name: str) -> bool:
    low = (name or "").lower()
    return low.endswith(".json") and (
        "knowledge_base" in low or "knowledge-base" in low or "se37" in low
    )


def _cache_locally(data: dict[str, Any]) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / SUMMARY_NAME).write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        log.warning("Could not cache KB summary locally: %s", exc)


def _upload_to_s3(raw: bytes, key: str) -> None:
    from app.services.s3_service import get_s3

    get_s3().upload_object(key, raw, content_type="application/json")
    log.info("Uploaded BRD KB summary to s3://%s/%s", get_settings().bucket, key)


def _load_from_s3() -> tuple[dict[str, Any], str] | None:
    from app.services.s3_service import get_s3

    settings = get_settings()
    prefix = normalize_prefix(settings.s3_kb_prefix or "KB/")
    s3 = get_s3()
    try:
        objects = s3.list_objects(prefix, limit=200)
    except AppError as exc:
        log.warning("Could not list S3 KB prefix %s: %s", prefix, exc)
        return None
    json_objs = [obj for obj in objects if str(obj.get("name") or "").lower().endswith(".json")]
    if not json_objs:
        return None
    preferred = [obj for obj in json_objs if _looks_like_summary(str(obj.get("name") or ""))]
    chosen = sorted(preferred or json_objs, key=lambda o: int(o.get("size") or 0), reverse=True)[0]
    key = str(chosen["key"])
    raw = s3.get_object(key)
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        return None
    return data, key


@lru_cache
def load_kb_summary() -> dict[str, Any]:
    """Return the SE37 summary JSON. Prefer S3 `KB/`, then local cache/Downloads.

    If the summary exists locally but not in the bucket, it is uploaded to `KB/`
    so later fills read from S3.
    """
    pack = {
        "ok": False,
        "source": "",
        "s3_key": "",
        "data": {},
        "error": "",
    }
    try:
        loaded = _load_from_s3()
        if loaded:
            data, key = loaded
            _cache_locally(data)
            pack.update(ok=True, source="s3", s3_key=key, data=data)
            return pack
    except Exception as exc:
        log.warning("S3 KB summary load failed: %s", exc)
        pack["error"] = str(exc)[:240]

    for path in _local_candidates():
        data = _read_json_file(path)
        if not data:
            continue
        key = f"{normalize_prefix(get_settings().s3_kb_prefix or 'KB/')}{SUMMARY_NAME}"
        try:
            _upload_to_s3(path.read_bytes(), key)
            pack.update(ok=True, source="s3", s3_key=key, data=data)
        except Exception as exc:
            log.warning("KB summary upload to S3 skipped: %s", exc)
            pack.update(ok=True, source="local", s3_key="", data=data, error=str(exc)[:240])
        if path.resolve() != (CACHE_DIR / SUMMARY_NAME).resolve():
            _cache_locally(data)
        return pack

    pack["error"] = pack["error"] or "BRD knowledge-base summary not found on S3 KB/ or local disk"
    return pack


def kb_ready() -> bool:
    return bool((load_kb_summary() or {}).get("ok"))
