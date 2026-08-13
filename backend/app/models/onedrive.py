from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class FileAccessMode(str, Enum):
    none = "none"
    read = "read"
    write = "write"
    owner = "owner"


class DriveItemKind(str, Enum):
    file = "file"
    folder = "folder"


class DriveItemSummary(BaseModel):
    id: str
    name: str
    kind: DriveItemKind
    path: str = ""
    size: int = 0
    mime_type: Optional[str] = None
    web_url: Optional[str] = None
    last_modified: Optional[str] = None
    created_by: Optional[str] = None
    modified_by: Optional[str] = None
    etag: Optional[str] = None
    access: FileAccessMode = FileAccessMode.read
    can_read: bool = True
    can_write: bool = False
    version_count: int = 0


class DrivePermissionInfo(BaseModel):
    id: str
    roles: list[str] = Field(default_factory=list)
    granted_to: Optional[str] = None
    granted_to_type: Optional[str] = None  # user | group | link | site
    link_scope: Optional[str] = None


class FileAccessReport(BaseModel):
    item_id: str
    item_name: str
    current_user: str
    access: FileAccessMode
    can_read: bool
    can_write: bool
    permissions: list[DrivePermissionInfo] = Field(default_factory=list)
    rationale: str = ""


class DriveVersionEntry(BaseModel):
    """Native OneDrive / Graph version (like a GitHub commit snapshot)."""

    id: str
    last_modified: Optional[str] = None
    size: int = 0
    modified_by: Optional[str] = None
    source: Literal["onedrive", "hub"] = "onedrive"


class HubCommit(BaseModel):
    """App-level GitHub-style commit recorded when saving through Template Hub."""

    sha: str
    message: str
    author: str
    created_at: str
    item_id: str
    item_name: str
    graph_version_id: Optional[str] = None
    parent_sha: Optional[str] = None
    size: int = 0
    content_hash: Optional[str] = None


class VersionTimeline(BaseModel):
    item_id: str
    item_name: str
    access: FileAccessReport
    onedrive_versions: list[DriveVersionEntry] = Field(default_factory=list)
    hub_commits: list[HubCommit] = Field(default_factory=list)


class OneDriveListResponse(BaseModel):
    folder: str
    items: list[DriveItemSummary]
    mode: str
    user: Optional[dict[str, Any]] = None


class OneDriveSearchRequest(BaseModel):
    query: str
    folder: Optional[str] = None


class OneDriveUploadRequest(BaseModel):
    folder: str = ""
    filename: str
    commit_message: str = "Update via Template Hub"
    content_base64: Optional[str] = None
    local_generated_filename: Optional[str] = None


class OneDriveCommitRequest(BaseModel):
    item_id: str
    commit_message: str
    content_base64: Optional[str] = None
    local_generated_filename: Optional[str] = None


class OneDriveRestoreRequest(BaseModel):
    item_id: str
    version_id: str
    commit_message: str = "Restore previous version"


class AuthConfigResponse(BaseModel):
    mode: str
    client_id: str
    tenant_id: str
    redirect_uri: str
    scopes: list[str]
    authority: str
