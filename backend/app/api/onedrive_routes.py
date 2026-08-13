from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from app.config import get_settings
from app.models.onedrive import (
    AuthConfigResponse,
    OneDriveCommitRequest,
    OneDriveRestoreRequest,
    OneDriveSearchRequest,
    OneDriveUploadRequest,
)
from app.services.onedrive import GraphError, OneDriveService, decode_content_base64


router = APIRouter(prefix="/api/onedrive", tags=["onedrive"])


@lru_cache
def get_onedrive() -> OneDriveService:
    return OneDriveService()


def _token(authorization: Optional[str], x_graph_token: Optional[str]) -> str:
    settings = get_settings()
    if settings.graph_mode.lower() == "mock":
        return x_graph_token or "mock-token"
    raw = x_graph_token or authorization or ""
    if raw.lower().startswith("bearer "):
        raw = raw.split(" ", 1)[1]
    if not raw:
        raise HTTPException(401, "Microsoft Graph access token required. Sign in with OneDrive.")
    return raw


def _user_hint(x_user: Optional[str], fallback: str = "demo.user@ymsli.com") -> str:
    return x_user or fallback


class MeResponse(BaseModel):
    mode: str
    user: dict


@router.get("/auth-config", response_model=AuthConfigResponse)
def auth_config():
    return get_onedrive().auth_config()


@router.get("/me", response_model=MeResponse)
async def me(
    authorization: Optional[str] = Header(default=None),
    x_graph_token: Optional[str] = Header(default=None, alias="X-Graph-Token"),
):
    od = get_onedrive()
    token = _token(authorization, x_graph_token)
    try:
        user = await od.get_me(token)
    except GraphError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"mode": get_settings().graph_mode, "user": user}


@router.get("/files")
async def list_files(
    folder: str = Query(""),
    authorization: Optional[str] = Header(default=None),
    x_graph_token: Optional[str] = Header(default=None, alias="X-Graph-Token"),
    x_user: Optional[str] = Header(default=None, alias="X-User"),
):
    od = get_onedrive()
    token = _token(authorization, x_graph_token)
    hint = _user_hint(x_user)
    try:
        items = await od.list_folder(token, folder=folder, username_hint=hint)
        user = await od.get_me(token)
    except GraphError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {
        "folder": folder or "/",
        "items": [i.model_dump() for i in items],
        "mode": get_settings().graph_mode,
        "user": user,
    }


@router.post("/search")
async def search_files(
    req: OneDriveSearchRequest,
    authorization: Optional[str] = Header(default=None),
    x_graph_token: Optional[str] = Header(default=None, alias="X-Graph-Token"),
    x_user: Optional[str] = Header(default=None, alias="X-User"),
):
    od = get_onedrive()
    token = _token(authorization, x_graph_token)
    hint = _user_hint(x_user)
    try:
        items = await od.search(token, req.query, username_hint=hint)
    except GraphError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"query": req.query, "items": [i.model_dump() for i in items]}


@router.get("/files/{item_id}/access")
async def file_access(
    item_id: str,
    authorization: Optional[str] = Header(default=None),
    x_graph_token: Optional[str] = Header(default=None, alias="X-Graph-Token"),
    x_user: Optional[str] = Header(default=None, alias="X-User"),
):
    od = get_onedrive()
    token = _token(authorization, x_graph_token)
    hint = _user_hint(x_user)
    try:
        report = await od.get_access(token, item_id, username_hint=hint)
    except GraphError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return report.model_dump()


@router.get("/files/{item_id}/versions")
async def file_versions(
    item_id: str,
    authorization: Optional[str] = Header(default=None),
    x_graph_token: Optional[str] = Header(default=None, alias="X-Graph-Token"),
    x_user: Optional[str] = Header(default=None, alias="X-User"),
):
    od = get_onedrive()
    token = _token(authorization, x_graph_token)
    hint = _user_hint(x_user)
    try:
        return await od.version_timeline(token, item_id, username_hint=hint)
    except GraphError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/files/{item_id}/content")
async def download_file(
    item_id: str,
    authorization: Optional[str] = Header(default=None),
    x_graph_token: Optional[str] = Header(default=None, alias="X-Graph-Token"),
    x_user: Optional[str] = Header(default=None, alias="X-User"),
):
    od = get_onedrive()
    token = _token(authorization, x_graph_token)
    hint = _user_hint(x_user)
    try:
        name, content = await od.download(token, item_id, username_hint=hint)
    except GraphError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/upload")
async def upload_file(
    req: OneDriveUploadRequest,
    authorization: Optional[str] = Header(default=None),
    x_graph_token: Optional[str] = Header(default=None, alias="X-Graph-Token"),
    x_user: Optional[str] = Header(default=None, alias="X-User"),
):
    od = get_onedrive()
    token = _token(authorization, x_graph_token)
    hint = _user_hint(x_user)
    content = _resolve_bytes(req.content_base64, req.local_generated_filename)
    try:
        item, commit = await od.upload_bytes(
            token,
            filename=req.filename,
            content=content,
            folder=req.folder,
            commit_message=req.commit_message,
            author=hint,
            username_hint=hint,
        )
    except GraphError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"item": item.model_dump(), "commit": commit.model_dump()}


@router.post("/commit")
async def commit_file(
    req: OneDriveCommitRequest,
    authorization: Optional[str] = Header(default=None),
    x_graph_token: Optional[str] = Header(default=None, alias="X-Graph-Token"),
    x_user: Optional[str] = Header(default=None, alias="X-User"),
):
    od = get_onedrive()
    token = _token(authorization, x_graph_token)
    hint = _user_hint(x_user)
    content = _resolve_bytes(req.content_base64, req.local_generated_filename)
    try:
        commit = await od.commit_update(
            token,
            item_id=req.item_id,
            content=content,
            commit_message=req.commit_message,
            author=hint,
            username_hint=hint,
        )
    except GraphError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"commit": commit.model_dump()}


@router.post("/restore")
async def restore_version(
    req: OneDriveRestoreRequest,
    authorization: Optional[str] = Header(default=None),
    x_graph_token: Optional[str] = Header(default=None, alias="X-Graph-Token"),
    x_user: Optional[str] = Header(default=None, alias="X-User"),
):
    od = get_onedrive()
    token = _token(authorization, x_graph_token)
    hint = _user_hint(x_user)
    try:
        commit = await od.restore_version(
            token,
            item_id=req.item_id,
            version_id=req.version_id,
            author=hint,
            commit_message=req.commit_message,
            username_hint=hint,
        )
    except GraphError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"commit": commit.model_dump()}


class PushGeneratedRequest(BaseModel):
    local_filename: str
    remote_filename: Optional[str] = None
    folder: str = "Templates/Generated"
    commit_message: str = "feat: publish generated document from Template Hub"


@router.post("/push-generated")
async def push_generated(
    req: PushGeneratedRequest,
    authorization: Optional[str] = Header(default=None),
    x_graph_token: Optional[str] = Header(default=None, alias="X-Graph-Token"),
    x_user: Optional[str] = Header(default=None, alias="X-User"),
):
    """Upload a locally generated Template Hub file into OneDrive with a commit message."""
    od = get_onedrive()
    token = _token(authorization, x_graph_token)
    hint = _user_hint(x_user)
    content = _resolve_bytes(None, req.local_filename)
    filename = req.remote_filename or req.local_filename
    try:
        item, commit = await od.upload_bytes(
            token,
            filename=filename,
            content=content,
            folder=req.folder,
            commit_message=req.commit_message,
            author=hint,
            username_hint=hint,
        )
    except GraphError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"item": item.model_dump(), "commit": commit.model_dump()}


def _resolve_bytes(content_base64: Optional[str], local_generated_filename: Optional[str]) -> bytes:
    if content_base64:
        try:
            return decode_content_base64(content_base64)
        except Exception as exc:
            raise HTTPException(400, f"Invalid base64 content: {exc}") from exc
    if local_generated_filename:
        settings = get_settings()
        path = (Path(settings.storage_path) / "generated" / Path(local_generated_filename).name).resolve()
        root = (Path(settings.storage_path) / "generated").resolve()
        if path.parent != root or not path.exists():
            raise HTTPException(404, "Generated file not found")
        return path.read_bytes()
    raise HTTPException(400, "Provide content_base64 or local_generated_filename")
