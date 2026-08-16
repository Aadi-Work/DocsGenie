from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException

OFFICE_EXT = {".docx", ".pptx", ".xlsx"}
ALLOWED_PREFIXES = (
    "template/",
    "templates/",
    "documents/",
    "document/",
    "metadata/",
    "kb/",
)
KIND_BY_EXT = {".docx": "word", ".pptx": "powerpoint", ".xlsx": "excel"}
CONTENT_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".html": "text/html",
    ".pdf": "application/pdf",
    ".json": "application/json",
}


class AppError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        super().__init__(detail)

    def http(self) -> HTTPException:
        return HTTPException(self.status_code, str(self))


def snake(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")


def dedupe_answers(answers: dict | None) -> dict[str, str]:
    """Keep one display entry per field. Prefer a human label over snake_case id."""
    ranked: dict[str, tuple[int, str, str]] = {}
    for key, value in (answers or {}).items():
        if value in (None, ""):
            continue
        ident = snake(str(key))
        if not ident:
            continue
        text = value if isinstance(value, str) else str(value)
        label = str(key)
        score = 2 if " " in label else (0 if "_" in label else 1)
        prev = ranked.get(ident)
        if prev is None or score > prev[0]:
            ranked[ident] = (score, title_from_ident(label) if "_" in label and " " not in label else label, text)
    return {label: text for _, label, text in ranked.values()}


def title_from_ident(value: str) -> str:
    raw = (value or "").replace("_", " ").replace("-", " ").strip()
    return " ".join(w.capitalize() if w.islower() else w for w in raw.split()) or "Template"


def safe_filename(name: str) -> str:
    name = Path(name or "file.bin").name.replace("\\", "_").replace("/", "_")
    if name in {".", "..", ""} or ".." in name:
        raise AppError(400, "Invalid file name")
    return name


def file_ext(name: str) -> str:
    return Path(name or "").suffix.lower()


def require_office(name: str) -> str:
    ext = file_ext(name)
    if ext not in OFFICE_EXT:
        raise AppError(400, "Only .docx, .pptx, and .xlsx files are supported")
    return ext


def kind_for_filename(name: str) -> str:
    return KIND_BY_EXT.get(file_ext(name), "word")


def content_type_for(name: str) -> str:
    return CONTENT_TYPES.get(file_ext(name), "application/octet-stream")


def normalize_prefix(prefix: str) -> str:
    p = (prefix or "").lstrip("/")
    if p and not p.endswith("/"):
        p += "/"
    return p


def _allowed_key(key: str) -> bool:
    lower = (key or "").lower()
    prefixes = list(ALLOWED_PREFIXES)
    try:
        from app.config import get_settings

        settings = get_settings()
        for raw in (
            settings.s3_templates_prefix,
            settings.s3_documents_prefix,
            settings.s3_previews_prefix,
            settings.s3_metadata_prefix,
            getattr(settings, "s3_kb_prefix", "KB/"),
        ):
            prefix = normalize_prefix(raw).lower()
            if prefix:
                prefixes.append(prefix)
                prefixes.append(prefix.split("/", 1)[0] + "/")
    except Exception:
        pass
    return any(lower.startswith(prefix) for prefix in prefixes)


def validate_key(key: str) -> str:
    key = (key or "").replace("\\", "/").lstrip("/")
    if not key or ".." in key:
        raise AppError(400, "Invalid S3 object key")
    if "/" not in key:
        raise AppError(400, "Invalid S3 object key")
    if not _allowed_key(key):
        raise AppError(403, "S3 key is outside the allowed prefixes")
    return key


def logical_template_id(key: str) -> str:
    key = (key or "").replace("\\", "/").lstrip("/")
    if not key or ".." in key:
        raise AppError(400, "Invalid file or S3 object key")
    parts = [p for p in key.split("/") if p]
    for i, part in enumerate(parts):
        if re.match(r"^v\d", part, re.I) and i > 0:
            return parts[i - 1]
    stem = Path(parts[-1]).stem if parts else "template"
    ident = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-").lower()
    return ident or "template"


def pretty_template_name(key: str, filename: str = "") -> str:
    name = Path(filename or key).stem
    return re.sub(r"[_-]+", " ", name).strip() or "Template"


def qkey(key: str) -> str:
    return quote(key, safe="")


def download_url(filename: str, key: str) -> str:
    return f"/api/files/{quote(filename)}?s3_key={qkey(key)}"


def preview_url(filename: str, key: str) -> str:
    return f"/api/preview/{quote(filename)}?s3_key={qkey(key)}"
