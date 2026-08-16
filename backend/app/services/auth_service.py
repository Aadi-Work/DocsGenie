from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Optional

import jwt
from jwt import InvalidTokenError

from app.config import ROOT, get_settings
from app.utils.file_utils import AppError

log = logging.getLogger(__name__)

ROLE_ADMIN = "admin"
ROLE_EMPLOYEE = "employee"
VALID_ROLES = {ROLE_ADMIN, ROLE_EMPLOYEE}


@dataclass
class AuthUser:
    id: str
    email: str
    name: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


class AuthService:
    def __init__(self) -> None:
        settings = get_settings()
        self.secret = settings.jwt_secret_value
        self.algorithm = settings.jwt_algorithm or "HS256"
        self.ttl = int(settings.jwt_ttl_seconds or 60 * 60 * 24 * 7)
        self.users_path = ROOT / "storage" / "users.json"
        self.users_path.parent.mkdir(parents=True, exist_ok=True)
        self._users: dict[str, dict[str, Any]] = {}
        self._load()
        self._seed()

    def _load(self) -> None:
        if not self.users_path.exists():
            return
        try:
            payload = json.loads(self.users_path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                self._users = {str(u["email"]).lower(): u for u in payload if u.get("email")}
        except Exception:
            log.warning("Could not read users file; starting with seeded accounts")

    def _save(self) -> None:
        self.users_path.write_text(json.dumps(list(self._users.values()), indent=2), encoding="utf-8")

    def _hash(self, password: str, salt: Optional[str] = None) -> str:
        salt = salt or secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000).hex()
        return f"pbkdf2_sha256${salt}${digest}"

    def _verify(self, password: str, stored: str) -> bool:
        try:
            algo, salt, _digest = stored.split("$", 2)
        except ValueError:
            return False
        if algo != "pbkdf2_sha256":
            return False
        return hmac.compare_digest(self._hash(password, salt=salt), stored)

    def _seed(self) -> None:
        demos = [
            ("admin@ymsli.com", "demo123", "System Admin", ROLE_ADMIN),
            ("employee@ymsli.com", "demo123", "YMSLI Employee", ROLE_EMPLOYEE),
            ("consultant@ymsli.com", "demo123", "Aaditva Admin", ROLE_ADMIN),
            ("joiner@ymsli.com", "demo123", "Alice", ROLE_EMPLOYEE),
        ]
        changed = False
        for email, password, name, role in demos:
            key = email.lower()
            if key not in self._users:
                self._users[key] = {
                    "id": secrets.token_hex(8),
                    "email": key,
                    "name": name,
                    "role": role,
                    "password_hash": self._hash(password),
                }
                changed = True
            else:
                self._users[key]["role"] = role
                self._users[key]["name"] = name
        if changed:
            self._save()

    def user_from_row(self, row: dict[str, Any]) -> AuthUser:
        role = row.get("role") if row.get("role") in VALID_ROLES else ROLE_EMPLOYEE
        return AuthUser(id=str(row["id"]), email=str(row["email"]), name=str(row.get("name") or row["email"]), role=role)

    def user_dict(self, user: AuthUser) -> dict[str, Any]:
        return {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "is_admin": user.is_admin,
        }

    def issue_token(self, user: AuthUser) -> str:
        now = int(time.time())
        return jwt.encode(
            {
                "sub": user.id,
                "email": user.email,
                "name": user.name,
                "role": user.role,
                "iat": now,
                "exp": now + self.ttl,
            },
            self.secret,
            algorithm=self.algorithm,
        )

    def decode_token(self, token: str) -> AuthUser:
        try:
            payload = jwt.decode(token, self.secret, algorithms=[self.algorithm])
        except InvalidTokenError as exc:
            raise AppError(401, "Invalid or expired token") from exc
        role = str(payload.get("role") or ROLE_EMPLOYEE)
        if role not in VALID_ROLES:
            role = ROLE_EMPLOYEE
        return AuthUser(
            id=str(payload.get("sub") or ""),
            email=str(payload.get("email") or ""),
            name=str(payload.get("name") or payload.get("email") or ""),
            role=role,
        )

    def register(self, email: str, password: str, name: str, role: str = ROLE_EMPLOYEE) -> tuple[AuthUser, str]:
        email = email.strip().lower()
        name = (name or "").strip() or email.split("@")[0]
        if not email or "@" not in email:
            raise AppError(400, "Valid email is required")
        if len(password) < 6:
            raise AppError(400, "Password must be at least 6 characters")
        if role not in VALID_ROLES:
            role = ROLE_EMPLOYEE
        if email in self._users:
            raise AppError(409, "Email already registered")
        row = {
            "id": secrets.token_hex(8),
            "email": email,
            "name": name,
            "role": role,
            "password_hash": self._hash(password),
        }
        self._users[email] = row
        self._save()
        user = self.user_from_row(row)
        return user, self.issue_token(user)

    def login(self, email: str, password: str) -> tuple[AuthUser, str]:
        email = (email or "").strip().lower()
        row = self._users.get(email)
        if not row or not self._verify(password, str(row.get("password_hash") or "")):
            raise AppError(401, "Invalid email or password")
        user = self.user_from_row(row)
        return user, self.issue_token(user)


@lru_cache
def get_auth() -> AuthService:
    return AuthService()
