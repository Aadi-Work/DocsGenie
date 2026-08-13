from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class TemplateCategory(str, Enum):
    functional = "functional"
    technical = "technical"


class OutputFormat(str, Enum):
    docx = "docx"
    xlsx = "xlsx"
    pptx = "pptx"


class AccessLevel(str, Enum):
    read = "read"
    write = "write"
    authorize = "authorize"


class TemplateVersion(BaseModel):
    version: str
    status: Literal["draft", "approved", "deprecated"] = "approved"
    changelog: str = ""
    created_at: str
    created_by: str = "template-admin"
    # Snapshot used for compare / restore of template definition
    description: Optional[str] = None
    placeholders: list[str] = Field(default_factory=list)
    content_outline: list[str] = Field(default_factory=list)
    context_questions: list[str] = Field(default_factory=list)


class VersionCompareChange(BaseModel):
    field: str
    change: Literal["added", "removed", "changed", "unchanged"]
    before: Any = None
    after: Any = None


class VersionCompareResponse(BaseModel):
    template_id: str
    template_name: str
    from_version: str
    to_version: str
    changes: list[VersionCompareChange] = Field(default_factory=list)
    summary: str = ""


class CreateVersionRequest(BaseModel):
    version: str
    changelog: str
    status: Literal["draft", "approved", "deprecated"] = "draft"
    created_by: str = "consultant"
    description: Optional[str] = None
    placeholders: Optional[list[str]] = None
    content_outline: Optional[list[str]] = None
    context_questions: Optional[list[str]] = None
    promote_to_current: bool = True


class TemplateMeta(BaseModel):
    id: str
    name: str
    category: TemplateCategory
    description: str
    tags: list[str] = Field(default_factory=list)
    output_format: OutputFormat
    placeholders: list[str] = Field(default_factory=list)
    context_questions: list[str] = Field(default_factory=list)
    versions: list[TemplateVersion]
    required_access: AccessLevel = AccessLevel.read
    usage_count: int = 0
    last_used_at: Optional[str] = None
    content_outline: list[str] = Field(default_factory=list)


class UserInfo(BaseModel):
    username: str
    display_name: str
    role: str
    access: dict[str, AccessLevel] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    meta: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    username: str = "consultant"
    graph_token: Optional[str] = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    stage: str
    template: Optional[TemplateMeta] = None
    questions: list[str] = Field(default_factory=list)
    answers: dict[str, str] = Field(default_factory=dict)
    download_url: Optional[str] = None
    search_results: list[TemplateMeta] = Field(default_factory=list)
    messages: list[ChatMessage] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str
    username: str = "consultant"
    limit: int = 5


class GenerateRequest(BaseModel):
    template_id: str
    answers: dict[str, str]
    username: str = "consultant"
    output_format: Optional[OutputFormat] = None


class GenerateResponse(BaseModel):
    template_id: str
    version: str
    filename: str
    download_url: str
    filled_fields: dict[str, str]


class AnalyticsSummary(BaseModel):
    most_used: list[TemplateMeta]
    stale: list[TemplateMeta]
    total_templates: int
    total_versions: int


class UploadAnalyzeResponse(BaseModel):
    detected_doc_type: str
    summary: str
    selection_reason: str
    confidence: float
    template: TemplateMeta
    filled_fields: dict[str, str]
    missing_fields: list[str] = Field(default_factory=list)
    preview: str = ""
    filename: Optional[str] = None
    download_url: Optional[str] = None
    auto_generated: bool = False
    llm_provider: str = "gemini"
    template_source: str = "local"


class TemplateSourceItem(BaseModel):
    id: str
    name: str
    source: Literal["local", "onedrive"]
    output_format: Optional[str] = None
    description: str = ""
    onedrive_item_id: Optional[str] = None
