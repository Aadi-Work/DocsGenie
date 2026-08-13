from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from app.deps import get_acl, get_agent, get_catalog, get_generator, get_ingest, get_retriever, get_versions
from app.api.auth_routes import get_current_user
from app.services.auth import AuthUser
from app.models.schemas import (
    ChatRequest,
    ChatResponse,
    GenerateRequest,
    GenerateResponse,
    SearchRequest,
    AccessLevel,
    UploadAnalyzeResponse,
    CreateVersionRequest,
)
from app.config import get_settings
from app.llm.factory import get_llm
from app.services.document_parser import DocumentParseError, extract_text_from_bytes

router = APIRouter(prefix="/api")


@router.get("/health")
def health():
    settings = get_settings()
    provider = settings.llm_provider.lower().strip().strip('"').strip("'")
    llm_ready = True
    if provider == "gemini":
        llm_ready = bool(settings.gemini_api_key)
    return {
        "status": "ok",
        "service": "ymsli-template-hub",
        "llm_provider": provider,
        "llm_ready": llm_ready or provider == "mock",
        "gemini_model": settings.gemini_model if provider == "gemini" else None,
    }


@router.get("/users")
def list_users():
    acl = get_acl()
    return {
        "users": [
            {
                "username": u.username,
                "display_name": u.display_name,
                "role": u.role,
            }
            for u in [
                acl.get_user("consultant"),
                acl.get_user("approver"),
                acl.get_user("joiner"),
            ]
        ]
    }


@router.get("/templates")
def list_templates(username: str = Query("consultant")):
    catalog = get_catalog()
    acl = get_acl()
    templates = acl.filter_templates(username, catalog.list_templates())
    return {"templates": [t.model_dump() for t in templates]}


@router.get("/templates/{template_id}")
def get_template(template_id: str, username: str = Query("consultant")):
    catalog = get_catalog()
    acl = get_acl()
    tmpl = catalog.get(template_id)
    if not tmpl:
        raise HTTPException(404, "Template not found")
    if not acl.can_access(username, tmpl, AccessLevel.read):
        raise HTTPException(403, "Access denied")
    latest = catalog.latest_version(tmpl)
    return {"template": tmpl.model_dump(), "latest_version": latest.model_dump()}


@router.get("/templates/{template_id}/versions")
def template_versions(
    template_id: str,
    user: AuthUser = Depends(get_current_user),
):
    catalog = get_catalog()
    acl = get_acl()
    versions = get_versions()
    username = user.role
    tmpl = catalog.get(template_id)
    if not tmpl:
        raise HTTPException(404, "Template not found")
    if not acl.can_access(username, tmpl, AccessLevel.read):
        raise HTTPException(403, "Access denied")
    latest = catalog.latest_version(tmpl)
    detailed = []
    for v in sorted(tmpl.versions, key=lambda x: x.created_at, reverse=True):
        snap = versions._resolve_snapshot(tmpl, v)
        detailed.append({**v.model_dump(), "snapshot": snap, "is_latest": v.version == latest.version})
    return {
        "template_id": template_id,
        "template_name": tmpl.name,
        "latest_version": latest.version,
        "versions": detailed,
    }


@router.get("/templates/{template_id}/versions/compare")
def compare_template_versions(
    template_id: str,
    from_version: str = Query(..., alias="from"),
    to_version: str = Query(..., alias="to"),
    user: AuthUser = Depends(get_current_user),
):
    catalog = get_catalog()
    acl = get_acl()
    username = user.role
    tmpl = catalog.get(template_id)
    if not tmpl:
        raise HTTPException(404, "Template not found")
    if not acl.can_access(username, tmpl, AccessLevel.read):
        raise HTTPException(403, "Access denied")
    try:
        return get_versions().compare(template_id, from_version, to_version).model_dump()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/templates/{template_id}/versions")
def create_template_version(
    template_id: str,
    req: CreateVersionRequest,
    user: AuthUser = Depends(get_current_user),
):
    catalog = get_catalog()
    acl = get_acl()
    username = user.role
    tmpl = catalog.get(template_id)
    if not tmpl:
        raise HTTPException(404, "Template not found")
    if not acl.can_access(username, tmpl, AccessLevel.write):
        raise HTTPException(403, "Write access required to create versions")
    try:
        updated = get_versions().create_version(
            template_id,
            version=req.version,
            changelog=req.changelog,
            created_by=req.created_by or user.email,
            status=req.status,
            description=req.description,
            placeholders=req.placeholders,
            content_outline=req.content_outline,
            context_questions=req.context_questions,
            promote_to_current=req.promote_to_current,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"template": updated.model_dump()}


@router.post("/search")
def search(req: SearchRequest):
    retriever = get_retriever()
    acl = get_acl()
    hits = retriever.search(req.query, limit=req.limit)
    results = []
    for tmpl, score in hits:
        if acl.can_access(req.username, tmpl, AccessLevel.read):
            results.append({"score": score, "template": tmpl.model_dump()})
    return {"results": results}


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    agent = get_agent()
    return await agent.handle(req.message, req.session_id, req.username, graph_token=req.graph_token)


@router.post("/parse")
async def parse_file(
    file: UploadFile = File(...),
    user: AuthUser = Depends(get_current_user),
):
    """Extract plain text from an uploaded document for frontend preview."""
    _ = user
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file")
    if len(raw) > 12 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 12MB)")
    try:
        text = extract_text_from_bytes(file.filename or "upload.txt", raw, max_chars=50000)
    except DocumentParseError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "filename": file.filename or "upload.txt",
        "char_count": len(text),
        "text": text,
    }


@router.post("/upload/analyze", response_model=UploadAnalyzeResponse)
async def upload_analyze(
    file: UploadFile = File(...),
    username: str = Form("consultant"),
    user_hint: str = Form(""),
    template_id: Optional[str] = Form(None),
    auto_generate: bool = Form(True),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file")
    if len(raw) > 12 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 12MB)")

    ingest = get_ingest()
    try:
        result = await ingest.compose(
            prompt=user_hint,
            file_name=file.filename or "upload.txt",
            file_data=raw,
            template_id=template_id or None,
            username=username,
            auto_generate=auto_generate,
        )
    except DocumentParseError as exc:
        raise HTTPException(400, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Upload analysis failed: {exc}") from exc

    settings = get_settings()
    uploads = Path(settings.storage_path) / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or "upload.bin").name
    (uploads / safe_name).write_bytes(raw)

    return UploadAnalyzeResponse(
        detected_doc_type=result.detected_doc_type,
        summary=result.summary,
        selection_reason=result.selection_reason,
        confidence=result.confidence,
        template=result.selected_template,
        filled_fields=result.filled_fields,
        missing_fields=result.missing_fields,
        preview=result.extracted_text_preview,
        filename=result.filename,
        download_url=result.download_url,
        auto_generated=result.auto_generated,
        llm_provider=get_settings().llm_provider,
        template_source="local",
    )


@router.post("/compose", response_model=UploadAnalyzeResponse)
async def compose_document(
    prompt: str = Form(""),
    text: str = Form(""),
    template_id: Optional[str] = Form(None),
    template_source: str = Form("local"),
    onedrive_item_id: Optional[str] = Form(None),
    auto_generate: bool = Form(True),
    file: Optional[UploadFile] = File(None),
    x_graph_token: Optional[str] = Header(default=None, alias="X-Graph-Token"),
    user: AuthUser = Depends(get_current_user),
):
    """
    Simple compose API:
    - template from local library or OneDrive
    - data from pasted text and/or uploaded file
    - prompt can name the template if none selected
    """
    file_name = None
    file_data = None
    if file is not None and file.filename:
        file_data = await file.read()
        file_name = file.filename
        if file_data and len(file_data) > 12 * 1024 * 1024:
            raise HTTPException(400, "File too large (max 12MB)")

    ingest = get_ingest()
    try:
        result = await ingest.compose(
            prompt=prompt,
            text=text,
            file_name=file_name,
            file_data=file_data,
            template_id=template_id or None,
            template_source=template_source,
            onedrive_item_id=onedrive_item_id or None,
            onedrive_token=x_graph_token or "mock-token",
            username=user.role,
            auto_generate=auto_generate,
        )
    except DocumentParseError as exc:
        raise HTTPException(400, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Compose failed: {exc}") from exc

    return UploadAnalyzeResponse(
        detected_doc_type=result.detected_doc_type,
        summary=result.summary,
        selection_reason=result.selection_reason,
        confidence=result.confidence,
        template=result.selected_template,
        filled_fields=result.filled_fields,
        missing_fields=result.missing_fields,
        preview=result.extracted_text_preview,
        filename=result.filename,
        download_url=result.download_url,
        auto_generated=result.auto_generated,
        llm_provider=get_settings().llm_provider,
        template_source=template_source,
    )


@router.get("/template-sources")
async def template_sources(
    x_graph_token: Optional[str] = Header(default=None, alias="X-Graph-Token"),
    user: AuthUser = Depends(get_current_user),
):
    """Local templates + OneDrive template files in one list."""
    from app.services.onedrive import OneDriveService

    catalog = get_catalog()
    acl = get_acl()
    username = user.role
    local = acl.filter_templates(username, catalog.list_templates())
    items = [
        {
            "id": t.id,
            "name": t.name,
            "source": "local",
            "output_format": t.output_format.value,
            "description": t.description,
            "onedrive_item_id": None,
        }
        for t in local
    ]

    token = x_graph_token or "mock-token"
    hint = (
        "joiner@ymsli.com"
        if username == "joiner"
        else user.email if "@" in user.email else "demo.user@ymsli.com"
    )
    try:
        od = OneDriveService()
        drive_items = await od.list_folder(token, folder="Templates", username_hint=hint)
        for d in drive_items:
            if d.kind.value != "file":
                continue
            items.append(
                {
                    "id": f"od:{d.id}",
                    "name": d.name,
                    "source": "onedrive",
                    "output_format": Path(d.name).suffix.lstrip(".") or None,
                    "description": f"OneDrive · {d.path}",
                    "onedrive_item_id": d.id,
                }
            )
    except Exception:
        pass

    return {"templates": items, "user": {"email": user.email, "name": user.name, "role": user.role}}


@router.get("/llm/status")
def llm_status():
    settings = get_settings()
    provider = settings.llm_provider.lower().strip().strip('"').strip("'")
    active = type(get_llm()).__name__
    return {
        "configured_provider": provider,
        "active_class": active,
        "gemini_model": settings.gemini_model,
        "has_gemini_key": bool(settings.gemini_api_key),
    }


@router.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest):
    catalog = get_catalog()
    acl = get_acl()
    generator = get_generator()
    tmpl = catalog.get(req.template_id)
    if not tmpl:
        raise HTTPException(404, "Template not found")
    if not acl.can_access(req.username, tmpl, AccessLevel.write):
        raise HTTPException(403, "Write access required")
    filled = dict(req.answers)
    for ph in tmpl.placeholders:
        filled.setdefault(ph, f"[Pending: {ph}]")
    filename, _ = generator.generate(tmpl, filled, req.output_format)
    version = catalog.latest_version(tmpl).version
    catalog.record_usage(tmpl.id, "generate", req.username)
    return GenerateResponse(
        template_id=tmpl.id,
        version=version,
        filename=filename,
        download_url=f"/api/files/{filename}",
        filled_fields=filled,
    )


@router.get("/analytics")
def analytics():
    return get_catalog().analytics().model_dump()


@router.get("/files/{filename}")
def download_file(filename: str):
    settings = get_settings()
    path = Path(settings.storage_path) / "generated" / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "File not found")
    # prevent path traversal
    if path.resolve().parent != (Path(settings.storage_path) / "generated").resolve():
        raise HTTPException(400, "Invalid path")
    media = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
    return FileResponse(path, media_type=media.get(path.suffix, "application/octet-stream"), filename=filename)
