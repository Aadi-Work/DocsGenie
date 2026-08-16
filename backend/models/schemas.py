from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=6)
    name: str = ""
    role: str = "employee"


class ChatRequest(BaseModel):
    message: str = ""
    session_id: Optional[str] = None
    template_id: Optional[str] = None
    attachment_text: Optional[str] = None
    attachment_name: Optional[str] = None


class GenerateRequest(BaseModel):
    template_id: str
    answers: dict[str, str] = Field(default_factory=dict)


class AiStartRequest(BaseModel):
    template_key: str


class AiAnswerRequest(BaseModel):
    session_id: str
    answer: str


class AiGenerateRequest(BaseModel):
    session_id: str
    answers: dict[str, str] = Field(default_factory=dict)


class SearchRequest(BaseModel):
    query: str = ""
    scope: str = "templates"
    limit: int = 25


class CreateTemplateRequest(BaseModel):
    name: str
    description: str = ""
    s3_key: str = ""


class RestoreVersionRequest(BaseModel):
    source_version: str
    changelog: str = ""
    publish: bool = True


class ActivateVersionRequest(BaseModel):
    version: str
