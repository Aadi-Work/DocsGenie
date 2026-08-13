from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.config import get_settings


def _utcnow_ts() -> int:
    return int(time.time())


@dataclass
class AuthUser:
    id: str
    email: str
    name: str
    role: str  # consultant | approver | joiner


class AuthError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class AuthService:
    """Email/password auth with salted password hashes and signed JWT-like tokens."""

    def __init__(self) -> None:
        settings = get_settings()
        self.db_path = Path(settings.database_path)
        self.secret = (settings.jwt_secret or "ymsli-dev-secret-change-me").encode("utf-8")
        self.token_ttl = int(settings.jwt_ttl_seconds or 60 * 60 * 24 * 7)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._seed_demo_users()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.commit()

    def _hash_password(self, password: str, salt: Optional[str] = None) -> str:
        salt = salt or secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            120_000,
        ).hex()
        return f"pbkdf2_sha256${salt}${digest}"

    def _verify_password(self, password: str, stored: str) -> bool:
        try:
            algo, salt, digest = stored.split("$", 2)
        except ValueError:
            return False
        if algo != "pbkdf2_sha256":
            return False
        check = self._hash_password(password, salt=salt)
        return hmac.compare_digest(check, stored)

    def _b64url(self, data: bytes) -> str:
        import base64

        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    def _b64url_decode(self, data: str) -> bytes:
        import base64

        pad = "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(data + pad)

    def issue_token(self, user: AuthUser) -> str:
        header = self._b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload = self._b64url(
            json.dumps(
                {
                    "sub": user.id,
                    "email": user.email,
                    "name": user.name,
                    "role": user.role,
                    "iat": _utcnow_ts(),
                    "exp": _utcnow_ts() + self.token_ttl,
                }
            ).encode()
        )
        signing_input = f"{header}.{payload}".encode()
        sig = hmac.new(self.secret, signing_input, hashlib.sha256).digest()
        return f"{header}.{payload}.{self._b64url(sig)}"

    def decode_token(self, token: str) -> AuthUser:
        try:
            header_b64, payload_b64, sig_b64 = token.split(".")
        except ValueError as exc:
            raise AuthError("Invalid token", 401) from exc
        signing_input = f"{header_b64}.{payload_b64}".encode()
        expected = hmac.new(self.secret, signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, self._b64url_decode(sig_b64)):
            raise AuthError("Invalid token signature", 401)
        try:
            payload = json.loads(self._b64url_decode(payload_b64))
        except Exception as exc:
            raise AuthError("Invalid token payload", 401) from exc
        if int(payload.get("exp", 0)) < _utcnow_ts():
            raise AuthError("Token expired", 401)
        return AuthUser(
            id=str(payload["sub"]),
            email=str(payload["email"]),
            name=str(payload.get("name") or payload["email"]),
            role=str(payload.get("role") or "consultant"),
        )

    def _row_to_user(self, row: sqlite3.Row) -> AuthUser:
        return AuthUser(id=row["id"], email=row["email"], name=row["name"], role=row["role"])

    def register(self, email: str, password: str, name: str, role: str = "consultant") -> tuple[AuthUser, str]:
        email = email.strip().lower()
        name = name.strip() or email.split("@")[0]
        if not email or "@" not in email:
            raise AuthError("Valid email is required")
        if len(password) < 6:
            raise AuthError("Password must be at least 6 characters")
        if role not in {"consultant", "approver", "joiner"}:
            role = "consultant"
        user_id = secrets.token_hex(8)
        password_hash = self._hash_password(password)
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO users (id, email, name, role, password_hash, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, email, name, role, password_hash, _utcnow_ts()),
                )
                conn.commit()
        except sqlite3.IntegrityError as exc:
            raise AuthError("Email already registered", 409) from exc
        user = AuthUser(id=user_id, email=email, name=name, role=role)
        return user, self.issue_token(user)

    def login(self, email: str, password: str) -> tuple[AuthUser, str]:
        email = email.strip().lower()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not row or not self._verify_password(password, row["password_hash"]):
            raise AuthError("Invalid email or password", 401)
        user = self._row_to_user(row)
        return user, self.issue_token(user)

    def get_by_email(self, email: str) -> Optional[AuthUser]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
            ).fetchone()
        return self._row_to_user(row) if row else None

    def _seed_demo_users(self) -> None:
        demos = [
            ("consultant@ymsli.com", "demo123", "Aaditva Consultant", "consultant"),
            ("approver@ymsli.com", "demo123", "Template Approver", "approver"),
            ("joiner@ymsli.com", "demo123", "New Joiner", "joiner"),
        ]
        for email, password, name, role in demos:
            if self.get_by_email(email):
                continue
            try:
                self.register(email, password, name, role)
            except AuthError:
                pass

    def user_dict(self, user: AuthUser) -> dict[str, Any]:
        return {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role,
        }
