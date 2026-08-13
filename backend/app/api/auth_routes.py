from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.deps import get_auth
from app.services.auth import AuthError, AuthUser

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=6)
    name: str = ""
    role: str = "consultant"


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


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
    except AuthError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


def get_optional_user(
    authorization: Optional[str] = Header(default=None),
    x_access_token: Optional[str] = Header(default=None, alias="X-Access-Token"),
) -> Optional[AuthUser]:
    token = x_access_token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        return get_auth().decode_token(token)
    except AuthError:
        return None


@router.post("/register", response_model=AuthResponse)
def register(req: RegisterRequest):
    auth = get_auth()
    try:
        user, token = auth.register(req.email, req.password, req.name, req.role)
    except AuthError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"access_token": token, "token_type": "bearer", "user": auth.user_dict(user)}


@router.post("/login", response_model=AuthResponse)
def login(req: LoginRequest):
    auth = get_auth()
    try:
        user, token = auth.login(req.email, req.password)
    except AuthError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"access_token": token, "token_type": "bearer", "user": auth.user_dict(user)}


@router.get("/me")
def me(user: AuthUser = Depends(get_current_user)):
    return {"user": get_auth().user_dict(user)}
