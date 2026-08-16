from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from threading import Lock
from typing import Any, Optional, Protocol
import uuid


@dataclass
class GenerationSession:
    session_id: str
    template_key: str = ""
    template_id: str = ""
    template_name: str = ""
    template_type: str = ""
    template_version: str = ""
    required_fields: list[dict[str, Any]] = field(default_factory=list)
    collected_fields: dict[str, str] = field(default_factory=dict)
    pending_questions: list[str] = field(default_factory=list)
    answers: dict[str, str] = field(default_factory=dict)
    generation_status: str = "idle"
    username: str = ""
    field_index: int = 0
    messages: list[dict[str, str]] = field(default_factory=list)
    generated_key: Optional[str] = None
    generated_filename: Optional[str] = None

    def current_field(self) -> Optional[dict[str, Any]]:
        pending = self.missing_fields()
        return pending[0] if pending else None

    def missing_fields(self) -> list[dict[str, Any]]:
        missing = []
        for item in self.required_fields:
            if not item.get("required", True):
                continue
            ident = str(item.get("id") or item.get("label") or "")
            val = (self.answers.get(ident) or self.answers.get(str(item.get("label") or "")) or "").strip()
            if not val:
                missing.append(item)
        return missing


class SessionStore(Protocol):
    def get(self, session_id: str) -> Optional[GenerationSession]: ...
    def save(self, session: GenerationSession) -> None: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._data: dict[str, GenerationSession] = {}
        self._lock = Lock()

    def get(self, session_id: str) -> Optional[GenerationSession]:
        with self._lock:
            return self._data.get(session_id)

    def save(self, session: GenerationSession) -> None:
        with self._lock:
            self._data[session.session_id] = session


def new_session_id() -> str:
    return uuid.uuid4().hex


@lru_cache
def get_sessions() -> InMemorySessionStore:
    return InMemorySessionStore()
