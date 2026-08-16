from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from app.models.schemas import LoginRequest, RegisterRequest
from app.services.auth_service import (
    ROLE_ADMIN,
    ROLE_EMPLOYEE,
    VALID_ROLES,
    AuthUser,
    get_auth,
)
from app.utils.file_utils import AppError

router = APIRouter(prefix="/api/auth", tags=["auth"])


def get_current_user(
    authorization: Optional[str] = Header(default=None),
    x_access_token: Optional[str] = Header(default=None, alias="X-Access-Token"),
) -> AuthUser:
    token = x_access_token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(401, "Authentication required. Please sign in.")
    try:
        return get_auth().decode_token(token)
    except AppError as exc:
        raise exc.http() from exc


def require_admin(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    if not user.is_admin:
        raise HTTPException(403, "Admin access required")
    return user


@router.post("/register")
def register(req: RegisterRequest):
    auth = get_auth()
    role = ROLE_EMPLOYEE if req.role == ROLE_ADMIN else (req.role if req.role in VALID_ROLES else ROLE_EMPLOYEE)
    try:
        user, token = auth.register(req.email, req.password, req.name, role)
    except AppError as exc:
        raise exc.http() from exc
    return {"access_token": token, "token_type": "bearer", "user": auth.user_dict(user)}


@router.post("/login")
def login(req: LoginRequest):
    auth = get_auth()
    try:
        user, token = auth.login(req.email, req.password)
    except AppError as exc:
        raise exc.http() from exc
    return {"access_token": token, "token_type": "bearer", "user": auth.user_dict(user)}


@router.get("/me")
def me(user: AuthUser = Depends(get_current_user)):
    return {"user": get_auth().user_dict(user)}
