from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from app.services.bedrock_service import get_bedrock
from app.services.s3_service import get_s3
from app.utils.extract import extract_office
from app.utils.file_utils import (
    AppError,
    content_type_for,
    file_ext,
    kind_for_filename,
    logical_template_id,
    pretty_template_name,
    require_office,
    safe_filename,
    validate_key,
)

log = logging.getLogger(__name__)

AUDIT: list[dict[str, Any]] = []


class TemplateService:
    def discovery_prefixes(self) -> list[str]:
        s3 = get_s3()
        prefixes = [s3.templates_prefix]
        for extra in ("template/", "Template/"):
            if extra not in prefixes:
                prefixes.append(extra)
        return prefixes

    def list_office_objects(self) -> list[dict[str, Any]]:
        s3 = get_s3()
        found: dict[str, dict[str, Any]] = {}
        for prefix in self.discovery_prefixes():
            try:
                items = s3.list_objects(prefix, limit=500)
            except AppError:
                continue
            for item in items:
                ext = file_ext(item["name"])
                if ext not in {".docx", ".pptx", ".xlsx"}:
                    continue
                found[item["key"]] = item
        return sorted(found.values(), key=lambda i: (i.get("name") or "").lower())

    def _group(self, objects: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        """One list row per template. Root copies like template/BFL Sample.xlsx
        are merged into the versioned object template/tmpl-bfl/v1.0/..."""
        versioned: dict[str, list[dict[str, Any]]] = {}
        loose: list[dict[str, Any]] = []
        for item in objects:
            try:
                ident = logical_template_id(item["key"])
            except AppError:
                continue
            if _is_versioned_key(item["key"]):
                versioned.setdefault(ident, []).append(item)
            else:
                loose.append(item)

        by_filename: dict[str, str] = {}
        for ident, items in versioned.items():
            for item in items:
                by_filename.setdefault((item.get("name") or "").lower(), ident)

        grouped = {ident: list(items) for ident, items in versioned.items()}
        for item in loose:
            name = (item.get("name") or "").lower()
            ident = by_filename.get(name) or logical_template_id(item["key"])
            grouped.setdefault(ident, []).append(item)
            by_filename.setdefault(name, ident)

        for items in grouped.values():
            items.sort(
                key=lambda i: (
                    _is_versioned_key(i["key"]),
                    _version_tuple(_version_from_key(i["key"])),
                    i.get("last_modified") or "",
                ),
                reverse=True,
            )
        return grouped

    def _metadata_key(self, template_id: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", template_id).strip("-") or "template"
        return f"{get_s3().metadata_prefix}{safe}.json"

    def _load_metadata(self, template_id: str) -> dict[str, Any]:
        s3 = get_s3()
        key = self._metadata_key(template_id)
        try:
            raw = s3.get_object(key)
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            mapped = getattr(exc, "status_code", None)
            if mapped != 404:
                log.warning("Could not load metadata for %s: %s", template_id, exc)
            return {}

    def _save_metadata(self, template_id: str, payload: dict[str, Any], user: str = "") -> None:
        s3 = get_s3()
        payload = {k: payload[k] for k in _META_KEYS if k in payload}
        payload["id"] = template_id
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload["updated_by"] = user
        s3.upload_object(
            self._metadata_key(template_id),
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            content_type="application/json",
        )

    def _version_entry_from_item(self, item: dict[str, Any], latest_key: str, meta: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": _version_from_key(item["key"]),
            "status": "published",
            "changelog": "",
            "created_at": item.get("last_modified") or "",
            "created_by": "",
            "modified_at": item.get("last_modified") or "",
            "s3_key": item["key"],
            "is_latest": item["key"] == latest_key,
            "is_active": item["key"] == latest_key,
            "template_name": meta.get("name") or pretty_template_name(item["key"], item.get("name") or ""),
        }

    def _hydrate_items(
        self,
        template_id: str,
        items: list[dict[str, Any]],
        meta: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        meta = meta if meta is not None else self._load_metadata(template_id)
        known = {item["key"] for item in items}
        for ver in meta.get("versions") or []:
            if not isinstance(ver, dict):
                continue
            key = ver.get("s3_key")
            if not key or key in known:
                continue
            items.append(
                {
                    "key": key,
                    "name": str(key).rsplit("/", 1)[-1],
                    "size": 0,
                    "last_modified": ver.get("modified_at") or ver.get("created_at"),
                }
            )
            known.add(key)
        items.sort(
            key=lambda i: (
                _is_versioned_key(i["key"]),
                _version_tuple(_version_from_key(i["key"])),
                i.get("last_modified") or "",
            ),
            reverse=True,
        )
        return items

    def _merge_versions(
        self,
        meta: dict[str, Any],
        items: list[dict[str, Any]],
        latest_key: str,
    ) -> list[dict[str, Any]]:
        by_ver: dict[str, dict[str, Any]] = {}
        has_versioned = any(_is_versioned_key(item["key"]) for item in items)
        for item in items:
            if has_versioned and not _is_versioned_key(item["key"]):
                continue
            entry = self._version_entry_from_item(item, latest_key, meta)
            by_ver[str(entry["version"])] = entry
        for mv in meta.get("versions") or []:
            if not isinstance(mv, dict):
                continue
            ver = str(mv.get("version") or "")
            if not ver:
                continue
            base = by_ver.get(ver, {})
            overlay = {k: mv[k] for k in mv if mv[k] not in (None, "")}
            by_ver[ver] = {**base, **overlay}
        versions = list(by_ver.values())
        versions.sort(key=lambda v: _version_tuple(str(v.get("version") or "")), reverse=True)
        highest = str(versions[0].get("version") or "1.0") if versions else "1.0"
        pinned = str(meta.get("active_version") or "")
        if pinned and pinned in by_ver:
            current = pinned
        else:
            current = highest
        for ver in versions:
            is_current = str(ver.get("version") or "") == current
            ver["is_latest"] = is_current
            ver["is_active"] = is_current
            if is_current:
                ver["status"] = "published"
            else:
                ver["status"] = ver.get("status") if ver.get("status") not in (None, "", "published") else "archived"
        return versions

    def to_public(self, template_id: str, items: list[dict[str, Any]], meta: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        meta = meta if meta is not None else self._load_metadata(template_id)
        unique: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for item in items:
            name = (item.get("name") or "").lower()
            if name in seen_names and not _is_versioned_key(item["key"]):
                continue
            seen_names.add(name)
            unique.append(item)
        items = unique or items
        items = self._hydrate_items(template_id, items, meta)
        if not items:
            raise AppError(404, "Template file not found in S3")
        versions = self._merge_versions(meta, items, items[0]["key"])
        current = next((str(v.get("version")) for v in versions if v.get("is_active")), None) or (
            str(versions[0]["version"]) if versions else "1.0"
        )
        current_key = next((str(v.get("s3_key")) for v in versions if str(v.get("version")) == current and v.get("s3_key")), "")
        latest = next((i for i in items if i["key"] == current_key), None) or items[0]
        filename = (current_key.rsplit("/", 1)[-1] if current_key else latest["name"])
        ext = file_ext(filename).lstrip(".")
        return {
            "id": template_id,
            "name": meta.get("name") or pretty_template_name(latest["key"], filename),
            "key": current_key or latest["key"],
            "s3_key": current_key or latest["key"],
            "s3_uri": f"s3://{get_s3().bucket}/{current_key or latest['key']}",
            "type": ext,
            "output_format": ext,
            "size": latest.get("size") or 0,
            "last_modified": latest.get("last_modified"),
            "modified_at": latest.get("last_modified"),
            "created_at": versions[-1]["created_at"] if versions else latest.get("last_modified"),
            "category": kind_for_filename(filename),
            "description": meta.get("description") or f"Office template stored in S3 ({filename})",
            "tags": meta.get("tags") or [ext, kind_for_filename(filename)],
            "placeholders": meta.get("placeholders") or [],
            "context_questions": meta.get("context_questions") or [],
            "content_outline": meta.get("content_outline") or [],
            "field_config": meta.get("field_config") or [],
            "current_version": current,
            "current_status": "published",
            "original_filename": filename,
            "source": "s3",
            "versions": versions,
            "usage_count": int(meta.get("usage_count") or 0),
            "created_by": meta.get("created_by"),
        }

    def list_templates(self) -> list[dict[str, Any]]:
        grouped = self._group(self.list_office_objects())
        out = []
        for ident, items in grouped.items():
            meta = self._load_metadata(ident)
            items = self._hydrate_items(ident, items, meta)
            out.append(self.to_public(ident, items, meta))
        out.sort(key=lambda t: (t.get("name") or "").lower())
        return out

    def list_sources(self) -> list[dict[str, Any]]:
        from app.office.profiles import pick_guided, uses_form_entry

        templates = pick_guided(self.list_templates())
        return [
            {
                "id": t["id"],
                "name": t["name"],
                "source": "s3",
                "output_format": t.get("output_format"),
                "description": t.get("description") or "",
                "s3_key": t.get("s3_key"),
                "current_version": t.get("current_version"),
                "status": t.get("current_status"),
                "profile_id": t.get("profile_id"),
                "guided": bool(t.get("guided")),
                "original_filename": t.get("original_filename"),
                "sample_file": t.get("sample_file"),
                "format_help": t.get("format_help") or "",
                "sample_notes": t.get("sample_notes") or "",
                "field_config": t.get("field_config") or [],
                "entry_mode": "form" if uses_form_entry(t.get("profile_id")) else "chat",
            }
            for t in templates
        ]

    def resolve(self, template_id_or_key: str) -> dict[str, Any]:
        value = (template_id_or_key or "").strip()
        if not value:
            raise AppError(400, "Template id is required")
        templates = self.list_templates()
        for tmpl in templates:
            if tmpl["id"] == value or tmpl.get("s3_key") == value or tmpl.get("key") == value:
                return tmpl
        if "/" in value:
            key = validate_key(value)
            ident = logical_template_id(key)
            items = [i for i in self.list_office_objects() if i["key"] == key or logical_template_id(i["key"]) == ident]
            if items:
                return self.to_public(ident, items)
        raise AppError(404, "Template not found in S3")

    def get(self, template_id: str) -> dict[str, Any]:
        tmpl = self.resolve(template_id)
        return self._ensure_fields(tmpl)

    def _ensure_fields(self, tmpl: dict[str, Any]) -> dict[str, Any]:
        from app.office.profiles import annotate_template

        tmpl = annotate_template(tmpl)
        if tmpl.get("profile_id") and tmpl.get("field_config"):
            return tmpl
        if tmpl.get("field_config") or tmpl.get("placeholders"):
            return tmpl
        key = tmpl.get("s3_key")
        if not key:
            return tmpl
        try:
            name, data = get_s3().get_object_with_name(str(key))
            detected = extract_office(name, data)
        except Exception:
            log.exception("Could not extract placeholders from %s", key)
            return tmpl
        tmpl["placeholders"] = list(detected.get("placeholders") or [])
        tmpl["field_config"] = list(detected.get("field_config") or [])
        tmpl["context_questions"] = list(detected.get("context_questions") or [])
        if detected.get("content_outline"):
            tmpl["content_outline"] = detected["content_outline"]
        return tmpl

    def search(self, query: str, *, scope: str = "templates", limit: int = 25) -> list[dict[str, Any]]:
        q = (query or "").strip().lower()
        pool = self.list_office_objects() if scope != "documents" else get_s3().list_objects(get_s3().documents_prefix, limit=400)
        if not q:
            return pool[:limit]
        tokens = [t for t in re.split(r"[^\w.-]+", q) if t]
        scored: list[tuple[float, dict[str, Any]]] = []
        for item in pool:
            hay = f"{item.get('key','')} {item.get('name','')}".lower()
            score = 0.0
            if q in hay:
                score += 8
            score += sum(3 for tok in tokens if tok in hay)
            if score:
                scored.append((score, item))
        scored.sort(key=lambda x: -x[0])
        return [item for _, item in scored[:limit]]

    def fetch_bytes(self, template_id_or_key: str) -> tuple[dict[str, Any], str, bytes]:
        tmpl = self.resolve(template_id_or_key)
        name, data = get_s3().get_object_with_name(tmpl["s3_key"])
        return tmpl, name, data

    def analyze_bytes(self, filename: str, data: bytes) -> dict[str, Any]:
        require_office(filename)
        detected = extract_office(filename, data)
        fields = list(detected.get("field_config") or [])
        try:
            refined = get_bedrock().json_invoke(
                json.dumps(
                    {
                        "filename": filename,
                        "detected_fields": fields,
                        "outline": detected.get("content_outline"),
                        "excerpt": detected.get("preview_text"),
                    },
                    ensure_ascii=False,
                ),
                system=(
                    "You analyze an Office template for YMSLI Template Hub. "
                    "Return JSON: {\"summary\":\"...\",\"fields\":[{\"id\":\"...\",\"label\":\"...\",\"question\":\"...\",\"required\":true}]}. "
                    "Questions must come from this template's labels, placeholders, and sections. "
                    "Do not invent unrelated fields. Prefer 4 to 10 required fields."
                ),
            )
            if refined.get("fields"):
                fields = [
                    {
                        "id": str(f.get("id") or f.get("label") or f"field_{i}").strip(),
                        "label": str(f.get("label") or f.get("id") or f"Field {i}"),
                        "question": str(f.get("question") or f"What is the {f.get('label') or f.get('id')}?"),
                        "required": bool(f.get("required", True)),
                        "field_type": "string",
                        "source": "bedrock",
                    }
                    for i, f in enumerate(refined["fields"], start=1)
                    if isinstance(f, dict)
                ]
            summary = str(refined.get("summary") or "")
        except AppError:
            summary = ""
        if not fields:
            fields = [
                {
                    "id": "document_title",
                    "label": "Document title",
                    "question": "What is the title of this document?",
                    "required": True,
                    "field_type": "string",
                    "source": "fallback",
                }
            ]
        return {
            "filename": filename,
            "output_format": file_ext(filename).lstrip("."),
            "placeholders": [f["label"] for f in fields],
            "field_config": fields,
            "context_questions": [f["question"] for f in fields],
            "content_outline": detected.get("content_outline") or [],
            "tables": detected.get("tables") or 0,
            "preview_text": detected.get("preview_text") or "",
            "summary": summary or detected.get("preview_text", "")[:240],
            "message": "Template analyzed from S3/upload content.",
        }

    def analyze_template(self, template_id_or_key: str) -> dict[str, Any]:
        tmpl, name, data = self.fetch_bytes(template_id_or_key)
        analysis = self.analyze_bytes(name, data)
        analysis["template"] = tmpl
        return analysis

    def upload_template(
        self,
        *,
        filename: str,
        data: bytes,
        name: str,
        description: str = "",
        changelog: str = "Initial upload",
        placeholders: list[str] | None = None,
        questions: list[str] | None = None,
        outline: list[str] | None = None,
        field_config: list[dict[str, Any]] | None = None,
        user: str = "",
        template_id: str | None = None,
    ) -> dict[str, Any]:
        filename = safe_filename(filename)
        require_office(filename)
        ident = template_id or logical_template_id(filename)
        existing = None
        try:
            existing = self.resolve(ident)
        except AppError:
            existing = None
        previous = _highest_version((existing or {}).get("versions") or [], (existing or {}).get("current_version"))
        version = _next_version(previous)
        kind = kind_for_filename(filename)
        key = f"{get_s3().templates_prefix}{kind}/{ident}/v{version}/{filename}"
        get_s3().upload_object(key, data, content_type=content_type_for(filename), metadata={"template_id": ident, "version": version})
        now = datetime.now(timezone.utc).isoformat()
        versions = [dict(v) for v in ((existing or {}).get("versions") or [])]
        entry = {
            "version": version,
            "status": "published",
            "changelog": changelog,
            "previous_version": previous,
            "created_at": now,
            "created_by": user,
            "modified_at": now,
            "s3_key": key,
            "is_latest": True,
            "is_active": True,
            "template_name": name or pretty_template_name(key, filename),
        }
        for ver in versions:
            ver["is_latest"] = False
            ver["is_active"] = False
            if (ver.get("status") or "published") == "published":
                ver["status"] = "archived"
        versions.insert(0, entry)
        payload = {
            "id": ident,
            "name": name or pretty_template_name(key, filename),
            "description": description or (existing or {}).get("description") or "",
            "placeholders": placeholders or [],
            "context_questions": questions or [],
            "content_outline": outline or [],
            "field_config": field_config or [],
            "current_version": version,
            "active_version": version,
            "versions": versions,
            "created_by": user or (existing or {}).get("created_by"),
            "tags": (existing or {}).get("tags") or [],
            "usage_count": int((existing or {}).get("usage_count") or 0),
        }
        self._save_metadata(ident, payload, user=user)
        self.audit(ident, "upload", user, f"Saved v{version} to {key}")
        return self._template_after_save(ident, payload, versions, key, filename, data, entry)

    def save_new_version(
        self,
        template_id: str,
        *,
        changelog: str,
        description: str = "",
        placeholders: list[str] | None = None,
        questions: list[str] | None = None,
        outline: list[str] | None = None,
        field_config: list[dict[str, Any]] | None = None,
        user: str = "",
        filename: str | None = None,
        data: bytes | None = None,
    ) -> dict[str, Any]:
        """Always create a new immutable version. If no file is provided, copy the current S3 object."""
        current = self.resolve(template_id)
        if not data:
            source_key = current.get("s3_key")
            if not source_key:
                raise AppError(400, "Template file not found in S3")
            filename, data = get_s3().get_object_with_name(str(source_key))
        return self.upload_template(
            filename=filename or current.get("original_filename") or "template.docx",
            data=data,
            name=current["name"],
            description=description if description != "" else (current.get("description") or ""),
            changelog=changelog,
            placeholders=placeholders if placeholders is not None else (current.get("placeholders") or []),
            questions=questions if questions is not None else (current.get("context_questions") or []),
            outline=outline if outline is not None else (current.get("content_outline") or []),
            field_config=field_config if field_config is not None else (current.get("field_config") or []),
            user=user,
            template_id=current["id"],
        )

    def _template_after_save(
        self,
        ident: str,
        payload: dict[str, Any],
        versions: list[dict[str, Any]],
        key: str,
        filename: str,
        data: bytes,
        entry: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            found = self.resolve(ident)
            found_vers = {str(v.get("version")) for v in (found.get("versions") or [])}
            if str(entry["version"]) in found_vers:
                return found
        except AppError:
            log.warning("Saved v%s for %s but listing did not include it yet", entry["version"], ident)
        items = []
        for ver in versions:
            s3_key = ver.get("s3_key")
            if not s3_key:
                continue
            items.append(
                {
                    "key": s3_key,
                    "name": str(s3_key).rsplit("/", 1)[-1],
                    "size": len(data) if s3_key == key else 0,
                    "last_modified": ver.get("modified_at") or ver.get("created_at"),
                }
            )
        if not items:
            items = [{"key": key, "name": filename, "size": len(data), "last_modified": entry.get("created_at")}]
        return self.to_public(ident, items, payload)

    def activate(self, template_id: str, version: str, user: str) -> dict[str, Any]:
        tmpl = self.resolve(template_id)
        versions = [dict(v) for v in (tmpl.get("versions") or [])]
        match = next((v for v in versions if str(v.get("version")) == str(version)), None)
        if not match or not match.get("s3_key"):
            raise AppError(404, "Version not found")
        for ver in versions:
            is_current = str(ver.get("version")) == str(version)
            ver["is_latest"] = is_current
            ver["is_active"] = is_current
            ver["status"] = "published" if is_current else "archived"
        payload = {
            "id": tmpl["id"],
            "name": tmpl.get("name"),
            "description": tmpl.get("description") or "",
            "placeholders": tmpl.get("placeholders") or [],
            "context_questions": tmpl.get("context_questions") or [],
            "content_outline": tmpl.get("content_outline") or [],
            "field_config": tmpl.get("field_config") or [],
            "current_version": str(version),
            "active_version": str(version),
            "versions": versions,
            "created_by": tmpl.get("created_by"),
            "tags": tmpl.get("tags") or [],
            "usage_count": int(tmpl.get("usage_count") or 0),
        }
        self._save_metadata(tmpl["id"], payload, user=user)
        self.audit(tmpl["id"], "activate", user, f"Set v{version} as the active template file")
        return self.to_public(
            tmpl["id"],
            self._hydrate_items(
                tmpl["id"],
                [
                    {
                        "key": v["s3_key"],
                        "name": str(v["s3_key"]).rsplit("/", 1)[-1],
                        "size": 0,
                        "last_modified": v.get("modified_at"),
                    }
                    for v in versions
                    if v.get("s3_key")
                ],
                payload,
            ),
            payload,
        )

    def restore(self, template_id: str, source_version: str, changelog: str, user: str) -> dict[str, Any]:
        tmpl = self.resolve(template_id)
        match = next((v for v in tmpl.get("versions") or [] if str(v.get("version")) == str(source_version)), None)
        if not match or not match.get("s3_key"):
            raise AppError(404, "Version not found in S3")
        filename, data = get_s3().get_object_with_name(match["s3_key"])
        return self.upload_template(
            filename=filename,
            data=data,
            name=tmpl["name"],
            description=tmpl.get("description") or "",
            changelog=changelog or f"Restored from v{source_version}",
            placeholders=tmpl.get("placeholders") or [],
            questions=tmpl.get("context_questions") or [],
            outline=tmpl.get("content_outline") or [],
            field_config=tmpl.get("field_config") or [],
            user=user,
            template_id=template_id,
        )

    def compare(self, template_id: str, from_ver: str, to_ver: str) -> dict[str, Any]:
        tmpl = self.resolve(template_id)
        versions = {str(v.get("version")): v for v in tmpl.get("versions") or []}
        left = versions.get(str(from_ver))
        right = versions.get(str(to_ver))
        if not left or not right:
            raise AppError(404, "One of the versions was not found")
        changes = []
        if left.get("s3_key") != right.get("s3_key"):
            changes.append(
                {
                    "field": "s3_key",
                    "change": "updated",
                    "before": left.get("s3_key"),
                    "after": right.get("s3_key"),
                    "lines": [
                        {"type": "removed", "text": str(left.get("s3_key") or "")},
                        {"type": "added", "text": str(right.get("s3_key") or "")},
                    ],
                }
            )
        if (left.get("changelog") or "") != (right.get("changelog") or ""):
            changes.append(
                {
                    "field": "changelog",
                    "change": "updated",
                    "before": left.get("changelog"),
                    "after": right.get("changelog"),
                    "lines": [
                        {"type": "removed", "text": str(left.get("changelog") or "")},
                        {"type": "added", "text": str(right.get("changelog") or "")},
                    ],
                }
            )
        unified = [line for change in changes for line in change["lines"]]
        return {
            "template_id": template_id,
            "template_name": tmpl["name"],
            "from_version": from_ver,
            "to_version": to_ver,
            "summary": f"{len(changes)} difference(s) between v{from_ver} and v{to_ver}.",
            "changes": changes,
            "unified_diff": unified,
        }

    def audit(self, template_id: str, action: str, user: str, detail: str = "") -> None:
        AUDIT.insert(
            0,
            {
                "template_id": template_id,
                "action": action,
                "user": user,
                "detail": detail,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        del AUDIT[200:]

    def list_audit(self, template_id: Optional[str] = None) -> list[dict[str, Any]]:
        if not template_id:
            return list(AUDIT)
        return [e for e in AUDIT if e.get("template_id") == template_id]


_META_KEYS = (
    "id",
    "name",
    "description",
    "placeholders",
    "context_questions",
    "content_outline",
    "field_config",
    "current_version",
    "active_version",
    "versions",
    "created_by",
    "tags",
    "usage_count",
)


def _is_versioned_key(key: str) -> bool:
    return bool(re.search(r"/v\d", (key or "").replace("\\", "/"), re.I))


def _version_from_key(key: str) -> str:
    match = re.search(r"/v(\d+(?:\.\d+)*)/", key.replace("\\", "/"), re.I)
    return match.group(1) if match else "1.0"


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.match(r"^v?(\d+(?:\.\d+)*)$", str(value or "").strip(), re.I)
    if not match:
        return (0,)
    return tuple(int(part) for part in match.group(1).split("."))


def _highest_version(versions: list[dict[str, Any]], current: Optional[str] = None) -> Optional[str]:
    best: Optional[str] = None
    best_tuple: tuple[int, ...] = (-1,)
    candidates = [current] if current else []
    candidates.extend(str(v.get("version")) for v in versions if v.get("version"))
    for raw in candidates:
        if not raw:
            continue
        parsed = _version_tuple(str(raw))
        if parsed > best_tuple:
            best_tuple = parsed
            best = str(raw).lstrip("vV")
    return best


def _next_version(current: Optional[str]) -> str:
    if not current:
        return "1.0"
    match = re.match(r"^(\d+)(?:\.(\d+))?$", str(current).lstrip("vV"))
    if not match:
        return "1.0"
    major = int(match.group(1))
    minor = int(match.group(2) or 0) + 1
    return f"{major}.{minor}"


@lru_cache
def get_templates() -> TemplateService:
    return TemplateService()
