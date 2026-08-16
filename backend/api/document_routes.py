from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response

from app.api.auth_routes import get_current_user, require_admin
from app.config import get_settings
from app.models.schemas import GenerateRequest
from app.services.auth_service import AuthUser
from app.services.bedrock_service import get_bedrock
from app.services.document_service import get_documents
from app.services.preview_service import get_preview
from app.services.s3_service import get_s3
from app.services.template_service import get_templates
from app.utils.extract import extract_plain_upload
from app.utils.file_utils import AppError, content_type_for, download_url, preview_url, safe_filename

router = APIRouter(prefix="/api", tags=["documents"])


def _load_bytes(filename: str, s3_key: Optional[str]) -> tuple[str, bytes]:
    s3 = get_s3()
    if s3_key:
        name, data = s3.get_object_with_name(s3_key)
        return name or filename, data
    safe = safe_filename(filename)
    if safe.startswith("blank_"):
        ident = safe[len("blank_") :].rsplit(".", 1)[0]
        tmpl, name, data = get_templates().fetch_bytes(ident)
        _ = tmpl
        return name, data
    try:
        key = get_documents().find_by_filename(safe)
        return s3.get_object_with_name(key)
    except AppError:
        tmpl, name, data = get_templates().fetch_bytes(safe.rsplit(".", 1)[0])
        _ = tmpl
        return name, data


@router.get("/generated-documents")
def list_generated(user: AuthUser = Depends(get_current_user)):
    try:
        docs = get_documents().list_generated(
            generated_by=None if user.is_admin else user.email,
            limit=50,
        )
    except AppError:
        docs = []
    return {"documents": docs}


@router.get("/files/{filename}")
def download_file(
    filename: str,
    s3_key: Optional[str] = Query(None),
    disposition: str = Query("inline"),
    user: AuthUser = Depends(get_current_user),
):
    _ = user
    try:
        name, data = _load_bytes(filename, s3_key)
    except AppError as exc:
        raise exc.http() from exc
    disp = "attachment" if disposition == "attachment" else "inline"
    return Response(
        content=data,
        media_type=content_type_for(name),
        headers={"Content-Disposition": f'{disp}; filename="{safe_filename(name)}"'},
    )


@router.get("/preview/{filename}")
def preview_file(
    filename: str,
    s3_key: Optional[str] = Query(None),
    user: AuthUser = Depends(get_current_user),
):
    _ = user
    try:
        name, data = _load_bytes(filename, s3_key)
        html = get_preview().render_html(name, data)
    except AppError as exc:
        raise exc.http() from exc
    return Response(content=html.encode("utf-8"), media_type="text/html; charset=utf-8")


@router.post("/parse")
async def parse_file(file: UploadFile = File(...), user: AuthUser = Depends(get_current_user)):
    _ = user
    settings = get_settings()
    raw = await file.read()
    if not raw:
        raise AppError(400, "Empty file").http()
    if len(raw) > settings.max_upload_bytes:
        raise AppError(400, "File too large (max 12MB)").http()
    text = extract_plain_upload(file.filename or "upload.txt", raw)
    return {"filename": file.filename, "char_count": len(text), "text": text}


@router.post("/generate")
def generate(req: GenerateRequest, user: AuthUser = Depends(get_current_user)):
    try:
        tmpl = get_templates().get(req.template_id)
        result = get_documents().generate(
            template_key=tmpl["s3_key"],
            answers=req.answers,
            generated_by=user.email,
            template_id=tmpl["id"],
            template_name=tmpl["name"],
            template_version=str(tmpl.get("current_version") or ""),
        )
    except AppError as exc:
        raise exc.http() from exc
    filename = result["filename"]
    return {
        "template_id": tmpl["id"],
        "version": tmpl.get("current_version") or "1.0",
        "filename": filename,
        "download_url": download_url(filename, result["s3_key"]),
        "preview_url": preview_url(filename, result["s3_key"]),
        "filled_fields": result["filled_fields"],
        "fill_mode": result["fill_mode"],
        "s3_key": result["s3_key"],
        "s3_uri": result["s3_uri"],
        "success": True,
        "document_name": filename,
    }


@router.post("/compose")
async def compose(
    prompt: str = Form(""),
    text: str = Form(""),
    template_source: str = Form("s3"),
    auto_generate: str = Form("true"),
    template_id: Optional[str] = Form(None),
    s3_key: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    template_file: Optional[UploadFile] = File(None),
    user: AuthUser = Depends(get_current_user),
):
    _ = template_source
    settings = get_settings()
    notes = "\n".join(p for p in [prompt, text] if p).strip()
    if file is not None:
        raw = await file.read()
        if len(raw) > settings.max_upload_bytes:
            raise AppError(400, "File too large (max 12MB)").http()
        notes = (notes + "\n" + extract_plain_upload(file.filename or "upload.txt", raw)).strip()
    try:
        if s3_key:
            tmpl = get_templates().resolve(s3_key)
        elif template_id:
            tmpl = get_templates().resolve(template_id)
        else:
            tmpl = _match_template(notes)
        tmpl = get_templates().get(tmpl["id"])
        analysis = {}
        if not tmpl.get("profile_id"):
            analysis = get_templates().analyze_template(tmpl["id"])
        fields = tmpl.get("field_config") or analysis.get("field_config") or []
        kb_meta: dict = {}
        if tmpl.get("profile_id") and notes.strip():
            from app.office.profiles import extract_form

            extracted = extract_form(str(tmpl["profile_id"]), notes)
            filled = extracted.get("filled_fields") or {}
            missing = extracted.get("missing_fields") or []
            fields = extracted.get("field_config") or fields
            kb_meta = (extracted.get("meta") or {}).get("kb") or {}
        else:
            filled = _extract_answers(notes, fields) if notes.strip() else {}
            missing = [f["label"] for f in fields if f.get("required", True) and not (filled.get(f["id"]) or filled.get(f["label"]))]
        kb_note = ""
        if kb_meta.get("used") and kb_meta.get("process"):
            filled_names = ", ".join(kb_meta.get("filled") or [])
            kb_note = (
                f"Missing BRD fields filled from S3 KB ({kb_meta.get('process')}"
                f"{': ' + filled_names if filled_names else ''})."
            )
        result = {
            "detected_doc_type": tmpl["name"],
            "summary": analysis.get("summary") or tmpl.get("description") or "",
            "selection_reason": (
                kb_note
                or (
                    f"Using S3 file {tmpl.get('original_filename') or tmpl.get('s3_key')}"
                    if tmpl.get("profile_id")
                    else "Selected from S3 templates"
                )
            ),
            "confidence": 0.8,
            "template": tmpl,
            "filled_fields": filled,
            "missing_fields": missing,
            "preview": analysis.get("preview_text") or "",
            "auto_generated": False,
            "llm_provider": "bedrock",
            "template_source": "s3",
            "template_version": tmpl.get("current_version"),
            "fill_mode": None,
            "s3_key": tmpl.get("s3_key"),
            "filename": None,
            "download_url": None,
            "kb": kb_meta or None,
        }
        if auto_generate.lower() != "false":
            generated = get_documents().generate(
                template_key=tmpl["s3_key"],
                answers=filled,
                generated_by=user.email,
                template_id=tmpl["id"],
                template_name=tmpl["name"],
                template_version=str(tmpl.get("current_version") or ""),
            )
            result.update(
                {
                    "auto_generated": True,
                    "filename": generated["filename"],
                    "download_url": download_url(generated["filename"], generated["s3_key"]),
                    "preview_url": preview_url(generated["filename"], generated["s3_key"]),
                    "s3_key": generated["s3_key"],
                    "fill_mode": generated["fill_mode"],
                    "filled_fields": generated["filled_fields"] or filled,
                }
            )
        return result
    except AppError as exc:
        raise exc.http() from exc


@router.post("/admin/templates/preview")
async def admin_preview(
    file: Optional[UploadFile] = File(None),
    template_id: Optional[str] = Form(None),
    notes: str = Form(""),
    answers_json: str = Form("{}"),
    user: AuthUser = Depends(require_admin),
):
    settings = get_settings()
    answers = json.loads(answers_json or "{}")
    try:
        if file is not None:
            raw = await file.read()
            if len(raw) > settings.max_upload_bytes:
                raise AppError(400, "File too large (max 12MB)")
            from app.services.document_service import fill_office

            filename = file.filename or "template.docx"
            filled = fill_office(filename, raw, answers)
            from datetime import datetime, timezone

            out_name = f"preview_{datetime.now(timezone.utc).strftime('%H%M%S')}_{filename}"
            key = f"{get_s3().previews_prefix}{out_name}"
            get_s3().upload_object(key, filled, metadata={"generated_by": user.email, "template_name": notes or filename})
            return {
                "filename": out_name,
                "download_url": download_url(out_name, key),
                "preview_url": preview_url(out_name, key),
                "fill_mode": "placeholders",
                "filled_fields": answers,
                "preview": True,
                "s3_key": key,
                "message": "Preview generated from uploaded file",
            }
        if not template_id:
            raise AppError(400, "template_id or file is required")
        tmpl = get_templates().get(template_id)
        generated = get_documents().generate(
            template_key=tmpl["s3_key"],
            answers=answers,
            generated_by=user.email,
            template_id=tmpl["id"],
            template_name=tmpl["name"],
            template_version=str(tmpl.get("current_version") or ""),
        )
        return {
            "filename": generated["filename"],
            "download_url": download_url(generated["filename"], generated["s3_key"]),
            "preview_url": preview_url(generated["filename"], generated["s3_key"]),
            "fill_mode": generated["fill_mode"],
            "filled_fields": generated["filled_fields"] or answers,
            "preview": True,
            "s3_key": generated["s3_key"],
            "message": "Preview generated from S3 template",
        }
    except AppError as exc:
        raise exc.http() from exc


def _match_template(notes: str) -> dict:
    templates = get_templates().list_templates()
    if not templates:
        raise AppError(404, "No templates found in S3")
    if not notes.strip():
        return templates[0]
    payload = {
        "user_text": notes[:4000],
        "templates": [{"id": t["id"], "name": t["name"], "filename": t.get("original_filename"), "type": t.get("type")} for t in templates],
    }
    pick = get_bedrock().json_invoke(
        json.dumps(payload),
        system=(
            "Select the S3 Office template that matches the user's document need. "
            "If none fit, matched=false. Return JSON "
            '{"matched":true,"template_id":"...","reason":"..."}.'
        ),
    )
    ident = str(pick.get("template_id") or "").strip()
    match = next((t for t in templates if t["id"] == ident or t.get("s3_key") == ident), None)
    if not match or pick.get("matched") is False:
        raise AppError(404, "Could not identify a matching S3 template")
    return match


def _extract_answers(notes: str, fields: list[dict]) -> dict[str, str]:
    if not notes.strip() or not fields:
        return {}
    data = get_bedrock().json_invoke(
        json.dumps({"notes": notes[:8000], "fields": fields}),
        system=(
            "Extract answers for the given template fields from the notes. "
            "Notes may be unstructured (email, bullets, loose sentences) or labeled. "
            "Infer values from meaning; headings do not need to match field labels. "
            "Return JSON {\"answers\":{\"field_id\":\"value\"}}. "
            "Use empty string when unknown. Do not invent facts."
        ),
    )
    answers = data.get("answers") if isinstance(data.get("answers"), dict) else {}
    out: dict[str, str] = {}
    for field in fields:
        ident = str(field.get("id") or "")
        label = str(field.get("label") or ident)
        value = str(answers.get(ident) or answers.get(label) or "").strip()
        if value:
            out[ident] = value
    return out
