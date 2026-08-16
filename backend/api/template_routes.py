from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from fastapi.responses import Response

from app.api.auth_routes import get_current_user, require_admin
from app.config import get_settings
from app.models.schemas import ActivateVersionRequest, RestoreVersionRequest, SearchRequest
from app.services.analytics_service import get_analytics
from app.services.auth_service import AuthUser
from app.services.preview_service import get_preview
from app.services.s3_service import get_s3
from app.services.template_service import get_templates
from app.utils.file_utils import AppError

router = APIRouter(prefix="/api", tags=["templates"])


@router.get("/s3/health")
def s3_health(user: AuthUser = Depends(get_current_user)):
    _ = user
    health = get_s3().health()
    return {"ok": health.get("ok"), "bucket": health.get("bucket"), "error": health.get("error")}


@router.post("/s3/search")
def s3_search(req: SearchRequest, user: AuthUser = Depends(get_current_user)):
    _ = user
    items = get_templates().search(req.query, scope=req.scope, limit=req.limit)
    return {"items": items, "count": len(items)}


@router.get("/template-sources")
def template_sources(user: AuthUser = Depends(get_current_user)):
    return {"templates": get_templates().list_sources(), "user": user.email}


@router.get("/templates")
def list_templates(user: AuthUser = Depends(get_current_user)):
    _ = user
    return {"templates": get_templates().list_templates()}


@router.get("/templates/{template_id:path}/versions/compare")
def compare_versions(
    template_id: str,
    from_: str = Query(alias="from"),
    to: str = Query(...),
    user: AuthUser = Depends(get_current_user),
):
    _ = user
    try:
        return get_templates().compare(template_id, from_, to)
    except AppError as exc:
        raise exc.http() from exc


@router.get("/templates/{template_id:path}/versions")
def template_versions(template_id: str, user: AuthUser = Depends(get_current_user)):
    _ = user
    try:
        tmpl = get_templates().get(template_id)
    except AppError as exc:
        raise exc.http() from exc
    return {
        "template_id": tmpl["id"],
        "template_name": tmpl["name"],
        "latest_version": tmpl.get("current_version"),
        "versions": tmpl.get("versions") or [],
    }


@router.post("/templates/{template_id:path}/versions/activate")
def activate_version(
    template_id: str,
    req: ActivateVersionRequest,
    user: AuthUser = Depends(require_admin),
):
    try:
        tmpl = get_templates().activate(template_id, req.version, user.email)
    except AppError as exc:
        raise exc.http() from exc
    return {"template": tmpl}


@router.post("/templates/{template_id:path}/versions/restore")
def restore_version(
    template_id: str,
    req: RestoreVersionRequest,
    user: AuthUser = Depends(require_admin),
):
    try:
        tmpl = get_templates().restore(template_id, req.source_version, req.changelog, user.email)
    except AppError as exc:
        raise exc.http() from exc
    return {"template": tmpl}


@router.get("/templates/{template_id:path}/preview")
def preview_template(template_id: str, user: AuthUser = Depends(get_current_user)):
    _ = user
    try:
        tmpl, name, data = get_templates().fetch_bytes(template_id)
        _ = tmpl
        html = get_preview().render_html(name, data)
    except AppError as exc:
        raise exc.http() from exc
    return Response(content=html.encode("utf-8"), media_type="text/html; charset=utf-8")


@router.get("/templates/{template_id:path}")
def get_template(template_id: str, user: AuthUser = Depends(get_current_user)):
    _ = user
    try:
        tmpl = get_templates().get(template_id)
    except AppError as exc:
        raise exc.http() from exc
    latest = next((v for v in (tmpl.get("versions") or []) if v.get("is_active") or v.get("is_latest")), None) or (tmpl.get("versions") or [{}])[0]
    return {
        "template": tmpl,
        "latest_version": {"version": latest.get("version") or tmpl.get("current_version"), "status": latest.get("status") or "published"},
    }


@router.get("/admin/analytics")
def s3_analytics(user: AuthUser = Depends(require_admin)):
    _ = user
    try:
        return get_analytics().snapshot()
    except AppError as exc:
        raise exc.http() from exc


@router.get("/audit")
def list_audit(template_id: Optional[str] = None, user: AuthUser = Depends(require_admin)):
    _ = user
    return {"events": get_templates().list_audit(template_id)}


@router.post("/admin/templates/analyze")
async def admin_analyze(file: UploadFile = File(...), user: AuthUser = Depends(require_admin)):
    _ = user
    settings = get_settings()
    raw = await file.read()
    if not raw:
        raise AppError(400, "Empty file").http()
    if len(raw) > settings.max_upload_bytes:
        raise AppError(400, "File too large (max 12MB)").http()
    try:
        return get_templates().analyze_bytes(file.filename or "template.docx", raw)
    except AppError as exc:
        raise exc.http() from exc


@router.post("/admin/templates/upload")
async def admin_upload(
    file: UploadFile = File(...),
    name: str = Form(""),
    description: str = Form(""),
    changelog: str = Form("Initial upload"),
    placeholders_json: str = Form("[]"),
    questions_json: str = Form("[]"),
    outline_json: str = Form("[]"),
    field_config_json: str = Form("[]"),
    user: AuthUser = Depends(require_admin),
):
    settings = get_settings()
    raw = await file.read()
    if not raw:
        raise AppError(400, "Empty file").http()
    if len(raw) > settings.max_upload_bytes:
        raise AppError(400, "File too large (max 12MB)").http()
    try:
        tmpl = get_templates().upload_template(
            filename=file.filename or "template.docx",
            data=raw,
            name=name,
            description=description,
            changelog=changelog,
            placeholders=json.loads(placeholders_json or "[]"),
            questions=json.loads(questions_json or "[]"),
            outline=json.loads(outline_json or "[]"),
            field_config=json.loads(field_config_json or "[]"),
            user=user.email,
        )
    except AppError as exc:
        raise exc.http() from exc
    return {"template": tmpl, "s3": {"key": tmpl.get("s3_key"), "uri": tmpl.get("s3_uri")}, "saved": True}


@router.post("/admin/templates/{template_id:path}/save")
async def admin_save(
    template_id: str,
    changelog: str = Form(...),
    description: str = Form(""),
    placeholders_json: str = Form("[]"),
    questions_json: str = Form("[]"),
    outline_json: str = Form("[]"),
    field_config_json: str = Form("[]"),
    file: Optional[UploadFile] = File(None),
    user: AuthUser = Depends(require_admin),
):
    try:
        raw = None
        upload_name = None
        if file is not None and (file.filename or "").strip():
            raw = await file.read()
            if not raw:
                raise AppError(400, "Empty file")
            upload_name = file.filename
        tmpl = get_templates().save_new_version(
            template_id,
            changelog=changelog,
            description=description,
            placeholders=json.loads(placeholders_json or "[]"),
            questions=json.loads(questions_json or "[]"),
            outline=json.loads(outline_json or "[]"),
            field_config=json.loads(field_config_json or "[]"),
            user=user.email,
            filename=upload_name,
            data=raw,
        )
    except AppError as exc:
        raise exc.http() from exc
    return {"template": tmpl, "saved": True, "s3": {"key": tmpl.get("s3_key"), "uri": tmpl.get("s3_uri")}}
