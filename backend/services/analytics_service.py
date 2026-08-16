"""S3 inventory analytics for the admin dashboard."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.s3_service import get_s3
from app.services.template_service import get_templates
from app.utils.file_utils import OFFICE_EXT, AppError, file_ext

_STAMP = re.compile(r"_\d{8}_\d{6}$")
_KIND_LABEL = {
    "xlsx": "Excel",
    "xls": "Excel",
    "xlsm": "Excel",
    "docx": "Word",
    "doc": "Word",
    "pptx": "PowerPoint",
    "ppt": "PowerPoint",
    "json": "Metadata",
    "html": "Preview",
    "pdf": "PDF",
}
_AREA_LABEL = {
    "templates": "Templates",
    "generated": "Generated documents",
    "previews": "Previews",
    "metadata": "Metadata",
    "other": "Other",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _classify(key: str) -> str:
    path = (key or "").replace("\\", "/").lower()
    if "preview" in path.split("/")[:3] or "/previews/" in path:
        return "previews"
    if path.startswith("metadata/") or "/metadata/" in path:
        return "metadata"
    if path.startswith("document"):
        return "generated"
    if path.startswith("template"):
        return "templates"
    return "other"


def _kind(name: str) -> str:
    ext = file_ext(name).lstrip(".")
    if ext in {"xlsx", "xls", "xlsm"}:
        return "xlsx"
    if ext in {"docx", "doc"}:
        return "docx"
    if ext in {"pptx", "ppt"}:
        return "pptx"
    return ext or "other"


def _stem(name: str) -> str:
    return _STAMP.sub("", Path(name or "").stem).strip().lower()


def _list_prefix(prefix: str, limit: int = 800) -> list[dict[str, Any]]:
    try:
        return get_s3().list_objects(prefix, limit=limit)
    except AppError:
        return []


class AnalyticsService:
    def snapshot(self) -> dict[str, Any]:
        s3 = get_s3()
        prefixes = []
        for raw in (
            s3.templates_prefix,
            "template/",
            "Template/",
            s3.documents_prefix,
            "documents/",
            "Documents/",
            s3.previews_prefix,
            s3.metadata_prefix,
        ):
            prefix = (raw or "").replace("\\", "/")
            if prefix and prefix not in prefixes:
                prefixes.append(prefix)

        by_key: dict[str, dict[str, Any]] = {}
        for prefix in prefixes:
            for item in _list_prefix(prefix):
                key = str(item.get("key") or "")
                if key:
                    by_key[key] = item
        objects = list(by_key.values())

        templates: list[dict[str, Any]] = []
        try:
            svc = get_templates()
            office = [
                obj
                for obj in objects
                if _classify(obj["key"]) == "templates" and file_ext(obj.get("name") or "") in OFFICE_EXT
            ]
            grouped = svc._group(office)
            for ident, items in grouped.items():
                meta = svc._load_metadata(ident)
                templates.append(svc.to_public(ident, items, meta))
        except Exception:
            templates = []

        generated = [o for o in objects if _classify(o["key"]) == "generated"]
        gen_stems: dict[str, int] = defaultdict(int)
        for obj in generated:
            stem = _stem(obj.get("name") or "")
            if stem:
                gen_stems[stem] += 1

        most_used: list[dict[str, Any]] = []
        for tmpl in templates:
            filename = str(tmpl.get("original_filename") or tmpl.get("name") or "")
            stem = _stem(filename)
            generated_count = gen_stems.get(stem, 0)
            if not generated_count:
                name_stem = _stem(str(tmpl.get("name") or ""))
                generated_count = gen_stems.get(name_stem, 0)
            usage = int(tmpl.get("usage_count") or 0)
            versions = tmpl.get("versions") or []
            version_bytes = 0
            for ver in versions:
                key = str(ver.get("s3_key") or "")
                if key in by_key:
                    version_bytes += int(by_key[key].get("size") or 0)
            most_used.append(
                {
                    "id": tmpl.get("id"),
                    "name": tmpl.get("name") or filename,
                    "format": str(tmpl.get("output_format") or _kind(filename)),
                    "usage_count": usage,
                    "generated_count": generated_count,
                    "score": usage + generated_count,
                    "size": int(tmpl.get("size") or 0),
                    "storage_bytes": version_bytes or int(tmpl.get("size") or 0),
                    "versions": len(versions),
                    "s3_key": tmpl.get("s3_key"),
                }
            )
        most_used.sort(key=lambda r: (r["score"], r["storage_bytes"]), reverse=True)

        area_map: dict[str, dict[str, int]] = defaultdict(lambda: {"count": 0, "bytes": 0})
        kind_map: dict[str, dict[str, int]] = defaultdict(lambda: {"count": 0, "bytes": 0})
        for obj in objects:
            size = int(obj.get("size") or 0)
            area = _classify(obj["key"])
            area_map[area]["count"] += 1
            area_map[area]["bytes"] += size
            kind = _kind(obj.get("name") or obj["key"])
            kind_map[kind]["count"] += 1
            kind_map[kind]["bytes"] += size

        largest = sorted(objects, key=lambda o: int(o.get("size") or 0), reverse=True)[:10]
        recent = sorted(objects, key=lambda o: str(o.get("last_modified") or ""), reverse=True)[:10]

        days: list[dict[str, Any]] = []
        today = _now().date()
        by_day: dict[str, dict[str, int]] = defaultdict(lambda: {"templates": 0, "generated": 0, "other": 0})
        for obj in objects:
            ts = _parse_ts(obj.get("last_modified"))
            if not ts:
                continue
            day = ts.date().isoformat()
            area = _classify(obj["key"])
            bucket = "templates" if area == "templates" else ("generated" if area == "generated" else "other")
            by_day[day][bucket] += 1
        for offset in range(13, -1, -1):
            day = (today - timedelta(days=offset)).isoformat()
            row = by_day.get(day, {"templates": 0, "generated": 0, "other": 0})
            days.append(
                {
                    "day": day,
                    "templates": row["templates"],
                    "generated": row["generated"],
                    "other": row["other"],
                    "count": row["templates"] + row["generated"] + row["other"],
                }
            )

        total_bytes = sum(int(o.get("size") or 0) for o in objects)
        office = [o for o in objects if _kind(o.get("name") or "") in {"xlsx", "docx", "pptx"}]

        return {
            "bucket": s3.bucket,
            "generated_at": _now().isoformat(),
            "totals": {
                "objects": len(objects),
                "bytes": total_bytes,
                "office_files": len(office),
                "templates": len(templates),
                "generated_documents": len(generated),
                "versions": sum(len(t.get("versions") or []) for t in templates),
            },
            "by_area": [
                {
                    "id": key,
                    "label": _AREA_LABEL.get(key, key.title()),
                    "count": stats["count"],
                    "bytes": stats["bytes"],
                }
                for key, stats in sorted(area_map.items(), key=lambda kv: kv[1]["bytes"], reverse=True)
            ],
            "by_kind": [
                {
                    "id": key,
                    "label": _KIND_LABEL.get(key, key.upper() or "Other"),
                    "count": stats["count"],
                    "bytes": stats["bytes"],
                }
                for key, stats in sorted(kind_map.items(), key=lambda kv: kv[1]["bytes"], reverse=True)
            ],
            "most_used": most_used[:10],
            "largest": [_file_row(o) for o in largest],
            "recent": [_file_row(o) for o in recent],
            "activity": days,
        }


def _file_row(obj: dict[str, Any]) -> dict[str, Any]:
    key = str(obj.get("key") or "")
    return {
        "name": obj.get("name") or key.rsplit("/", 1)[-1],
        "s3_key": key,
        "size": int(obj.get("size") or 0),
        "last_modified": obj.get("last_modified"),
        "area": _classify(key),
        "kind": _kind(obj.get("name") or key),
    }


@lru_cache
def get_analytics() -> AnalyticsService:
    return AnalyticsService()
