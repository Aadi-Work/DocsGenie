from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings
from app.models.onedrive import (
    DriveItemKind,
    DriveItemSummary,
    DrivePermissionInfo,
    DriveVersionEntry,
    FileAccessMode,
    FileAccessReport,
    HubCommit,
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class GraphError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class OneDriveService:
    """
    Microsoft Graph OneDrive client.

    - live: uses the caller's Graph access token (delegated)
    - mock: in-memory/local demo drive with permissions + versions
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.db_path = Path(self.settings.database_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.mock_root = Path(self.settings.storage_path) / "onedrive_mock"
        self.mock_root.mkdir(parents=True, exist_ok=True)
        self._init_db()
        if self.settings.graph_mode.lower() == "mock":
            self._ensure_mock_seed()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS hub_commits (
                    sha TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    item_name TEXT NOT NULL,
                    message TEXT NOT NULL,
                    author TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    graph_version_id TEXT,
                    parent_sha TEXT,
                    size INTEGER DEFAULT 0,
                    content_hash TEXT
                );
                CREATE TABLE IF NOT EXISTS mock_drive_items (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    parent_id TEXT,
                    path TEXT NOT NULL,
                    size INTEGER DEFAULT 0,
                    mime_type TEXT,
                    content_path TEXT,
                    created_at TEXT,
                    modified_at TEXT,
                    created_by TEXT,
                    modified_by TEXT
                );
                CREATE TABLE IF NOT EXISTS mock_permissions (
                    id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    roles TEXT NOT NULL,
                    granted_to TEXT,
                    granted_to_type TEXT
                );
                CREATE TABLE IF NOT EXISTS mock_versions (
                    id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    content_path TEXT,
                    size INTEGER,
                    modified_at TEXT,
                    modified_by TEXT
                );
                """
            )

    # ── Auth config ──────────────────────────────────────────────

    def auth_config(self) -> dict[str, Any]:
        tenant = self.settings.azure_tenant_id or "common"
        return {
            "mode": self.settings.graph_mode.lower(),
            "client_id": self.settings.azure_client_id,
            "tenant_id": tenant,
            "redirect_uri": self.settings.azure_redirect_uri,
            "scopes": self.settings.graph_scope_list,
            "authority": f"https://login.microsoftonline.com/{tenant}",
        }

    # ── HTTP helpers ─────────────────────────────────────────────

    async def _graph(
        self,
        method: str,
        path: str,
        token: str,
        *,
        json_body: Any = None,
        content: bytes | None = None,
        headers: Optional[dict[str, str]] = None,
        params: Optional[dict[str, str]] = None,
    ) -> Any:
        url = path if path.startswith("http") else f"{self.settings.graph_base_url}{path}"
        hdrs = {"Authorization": f"Bearer {token}", **(headers or {})}
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.request(
                method,
                url,
                headers=hdrs,
                json=json_body,
                content=content,
                params=params,
            )
        if res.status_code >= 400:
            raise GraphError(f"Graph API error {res.status_code}: {res.text}", res.status_code)
        if res.status_code == 204 or not res.content:
            return None
        content_type = res.headers.get("content-type", "")
        if "application/json" in content_type:
            return res.json()
        return res.content

    async def get_me(self, token: str) -> dict[str, Any]:
        if self.settings.graph_mode.lower() == "mock":
            return {
                "id": "mock-user",
                "displayName": "Demo User",
                "mail": "demo.user@ymsli.com",
                "userPrincipalName": "demo.user@ymsli.com",
            }
        return await self._graph("GET", "/me", token)

    # ── List / search ────────────────────────────────────────────

    async def list_folder(
        self,
        token: str,
        folder: str = "",
        username_hint: str = "demo.user@ymsli.com",
    ) -> list[DriveItemSummary]:
        if self.settings.graph_mode.lower() == "mock":
            return self._mock_list(folder, username_hint)

        root = self.settings.onedrive_root_folder
        # Ensure hub folder exists
        await self._ensure_remote_folder(token, root)
        relative = folder.strip("/")
        if relative:
            path = f"/me/drive/root:/{root}/{relative}:/children"
        else:
            path = f"/me/drive/root:/{root}:/children"
        try:
            data = await self._graph("GET", path, token)
        except GraphError as exc:
            if exc.status_code == 404:
                return []
            raise
        items = []
        for raw in data.get("value", []):
            summary = self._map_item(raw, prefix=f"/{root}" + (f"/{relative}" if relative else ""))
            access = await self.get_access(token, summary.id, username_hint)
            summary.access = access.access
            summary.can_read = access.can_read
            summary.can_write = access.can_write
            items.append(summary)
        return items

    async def search(
        self,
        token: str,
        query: str,
        username_hint: str = "demo.user@ymsli.com",
    ) -> list[DriveItemSummary]:
        if self.settings.graph_mode.lower() == "mock":
            return [
                i
                for i in self._mock_list("", username_hint)
                if query.lower() in i.name.lower() or query.lower() in i.path.lower()
            ]

        q = query.replace("'", " ")
        data = await self._graph("GET", f"/me/drive/root/search(q='{q}')", token)
        items = []
        for raw in data.get("value", []):
            summary = self._map_item(raw)
            access = await self.get_access(token, summary.id, username_hint)
            summary.access = access.access
            summary.can_read = access.can_read
            summary.can_write = access.can_write
            if summary.can_read:
                items.append(summary)
        return items

    # ── Permissions ──────────────────────────────────────────────

    async def get_access(
        self,
        token: str,
        item_id: str,
        username_hint: str = "demo.user@ymsli.com",
    ) -> FileAccessReport:
        if self.settings.graph_mode.lower() == "mock":
            return self._mock_access(item_id, username_hint)

        me = await self.get_me(token)
        upn = (me.get("userPrincipalName") or me.get("mail") or username_hint or "").lower()
        display = me.get("displayName") or upn

        try:
            item = await self._graph("GET", f"/me/drive/items/{item_id}", token)
        except GraphError as exc:
            if exc.status_code in (403, 404):
                return FileAccessReport(
                    item_id=item_id,
                    item_name="",
                    current_user=display,
                    access=FileAccessMode.none,
                    can_read=False,
                    can_write=False,
                    rationale="Item not found or no access.",
                )
            raise

        try:
            perm_data = await self._graph("GET", f"/me/drive/items/{item_id}/permissions", token)
            permissions = [self._map_permission(p) for p in perm_data.get("value", [])]
        except GraphError:
            # Some accounts cannot list permissions; infer from ability to read item
            permissions = []

        access = self._infer_access(permissions, upn)
        if access == FileAccessMode.none and item:
            # User can read the item via Graph → at least read
            access = FileAccessMode.read

        # Probe write: if roles already say write/owner, trust that.
        can_write = access in (FileAccessMode.write, FileAccessMode.owner)
        can_read = access != FileAccessMode.none

        return FileAccessReport(
            item_id=item_id,
            item_name=item.get("name", ""),
            current_user=display,
            access=access,
            can_read=can_read,
            can_write=can_write,
            permissions=permissions,
            rationale=self._access_rationale(access, permissions),
        )

    def _infer_access(self, permissions: list[DrivePermissionInfo], upn: str) -> FileAccessMode:
        best = FileAccessMode.none
        rank = {
            FileAccessMode.none: 0,
            FileAccessMode.read: 1,
            FileAccessMode.write: 2,
            FileAccessMode.owner: 3,
        }
        for p in permissions:
            roles = {r.lower() for r in p.roles}
            granted = (p.granted_to or "").lower()
            # organization-wide / anonymous links still count for the signed-in user
            applies = (
                not granted
                or granted == upn
                or p.granted_to_type in ("link", "site")
                or "everyone" in granted
            )
            if not applies:
                continue
            if "owner" in roles:
                candidate = FileAccessMode.owner
            elif "write" in roles or "edit" in roles:
                candidate = FileAccessMode.write
            elif "read" in roles or "view" in roles:
                candidate = FileAccessMode.read
            else:
                candidate = FileAccessMode.read
            if rank[candidate] > rank[best]:
                best = candidate
        return best

    def _access_rationale(self, access: FileAccessMode, permissions: list[DrivePermissionInfo]) -> str:
        if access == FileAccessMode.owner:
            return "User has owner role on this OneDrive item."
        if access == FileAccessMode.write:
            return "User has write/edit permission — can update and commit new versions."
        if access == FileAccessMode.read:
            return "User has read-only permission — can view/download but not modify."
        if not permissions:
            return "No explicit permissions returned; access inferred from Graph reachability."
        return "No matching permission for the signed-in user."

    # ── Versions (GitHub-style) ──────────────────────────────────

    async def version_timeline(
        self,
        token: str,
        item_id: str,
        username_hint: str = "demo.user@ymsli.com",
    ) -> dict[str, Any]:
        access = await self.get_access(token, item_id, username_hint)
        if not access.can_read:
            raise GraphError("Read access required to view version history.", 403)

        if self.settings.graph_mode.lower() == "mock":
            od_versions = self._mock_versions(item_id)
            item_name = access.item_name
        else:
            item = await self._graph("GET", f"/me/drive/items/{item_id}", token)
            item_name = item.get("name", access.item_name)
            data = await self._graph("GET", f"/me/drive/items/{item_id}/versions", token)
            od_versions = [
                DriveVersionEntry(
                    id=v.get("id", ""),
                    last_modified=v.get("lastModifiedDateTime"),
                    size=int((v.get("size") or 0)),
                    modified_by=(
                        ((v.get("lastModifiedBy") or {}).get("user") or {}).get("displayName")
                    ),
                    source="onedrive",
                )
                for v in data.get("value", [])
            ]

        commits = self.list_hub_commits(item_id)
        return {
            "item_id": item_id,
            "item_name": item_name,
            "access": access.model_dump(),
            "onedrive_versions": [v.model_dump() for v in od_versions],
            "hub_commits": [c.model_dump() for c in commits],
        }

    def list_hub_commits(self, item_id: str) -> list[HubCommit]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM hub_commits WHERE item_id = ? ORDER BY created_at DESC",
                (item_id,),
            ).fetchall()
        return [HubCommit(**dict(r)) for r in rows]

    def _record_commit(
        self,
        *,
        item_id: str,
        item_name: str,
        message: str,
        author: str,
        size: int = 0,
        content_hash: Optional[str] = None,
        graph_version_id: Optional[str] = None,
    ) -> HubCommit:
        parent = None
        existing = self.list_hub_commits(item_id)
        if existing:
            parent = existing[0].sha
        sha = hashlib.sha1(f"{item_id}:{message}:{_utcnow()}:{uuid.uuid4()}".encode()).hexdigest()[:12]
        commit = HubCommit(
            sha=sha,
            message=message,
            author=author,
            created_at=_utcnow(),
            item_id=item_id,
            item_name=item_name,
            graph_version_id=graph_version_id,
            parent_sha=parent,
            size=size,
            content_hash=content_hash,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO hub_commits
                (sha, item_id, item_name, message, author, created_at, graph_version_id, parent_sha, size, content_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    commit.sha,
                    commit.item_id,
                    commit.item_name,
                    commit.message,
                    commit.author,
                    commit.created_at,
                    commit.graph_version_id,
                    commit.parent_sha,
                    commit.size,
                    commit.content_hash,
                ),
            )
            conn.commit()
        return commit

    async def restore_version(
        self,
        token: str,
        item_id: str,
        version_id: str,
        author: str,
        commit_message: str = "Restore previous version",
        username_hint: str = "demo.user@ymsli.com",
    ) -> HubCommit:
        access = await self.get_access(token, item_id, username_hint)
        if not access.can_write:
            raise GraphError("Write access required to restore a version.", 403)

        if self.settings.graph_mode.lower() == "mock":
            self._mock_restore(item_id, version_id, author)
            return self._record_commit(
                item_id=item_id,
                item_name=access.item_name,
                message=commit_message,
                author=author,
                graph_version_id=version_id,
            )

        await self._graph(
            "POST",
            f"/me/drive/items/{item_id}/versions/{version_id}/restoreVersion",
            token,
        )
        return self._record_commit(
            item_id=item_id,
            item_name=access.item_name,
            message=commit_message,
            author=author,
            graph_version_id=version_id,
        )

    # ── Download / upload / commit ───────────────────────────────

    async def download(self, token: str, item_id: str, username_hint: str = "demo.user@ymsli.com") -> tuple[str, bytes]:
        access = await self.get_access(token, item_id, username_hint)
        if not access.can_read:
            raise GraphError("Read access required to download.", 403)

        if self.settings.graph_mode.lower() == "mock":
            return self._mock_download(item_id)

        meta = await self._graph("GET", f"/me/drive/items/{item_id}", token)
        content = await self._graph("GET", f"/me/drive/items/{item_id}/content", token)
        if isinstance(content, dict):
            raise GraphError("Unexpected JSON when downloading content.")
        return meta.get("name", "download.bin"), content

    async def upload_bytes(
        self,
        token: str,
        *,
        filename: str,
        content: bytes,
        folder: str = "",
        commit_message: str = "Upload via Template Hub",
        author: str = "template-hub",
        username_hint: str = "demo.user@ymsli.com",
    ) -> tuple[DriveItemSummary, HubCommit]:
        if self.settings.graph_mode.lower() == "mock":
            item = self._mock_upload(filename, content, folder, author)
            # Enforce write on parent path via mock permissions of resulting item
            access = self._mock_access(item.id, username_hint)
            if not access.can_write:
                raise GraphError("Write access required to upload/update this file.", 403)
            commit = self._record_commit(
                item_id=item.id,
                item_name=item.name,
                message=commit_message,
                author=author,
                size=len(content),
                content_hash=hashlib.sha256(content).hexdigest()[:16],
            )
            item.can_write = True
            item.can_read = True
            item.access = access.access
            return item, commit

        root = self.settings.onedrive_root_folder
        await self._ensure_remote_folder(token, root)
        relative = "/".join(p for p in [root, folder.strip("/"), filename] if p)
        # Upload creates a new version automatically in OneDrive
        data = await self._graph(
            "PUT",
            f"/me/drive/root:/{relative}:/content",
            token,
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )
        item = self._map_item(data)
        access = await self.get_access(token, item.id, username_hint)
        if not access.can_write and access.access == FileAccessMode.none:
            raise GraphError("Write access required to upload/update this file.", 403)
        item.access = access.access
        item.can_read = access.can_read
        item.can_write = access.can_write
        commit = self._record_commit(
            item_id=item.id,
            item_name=item.name,
            message=commit_message,
            author=author,
            size=len(content),
            content_hash=hashlib.sha256(content).hexdigest()[:16],
        )
        return item, commit

    async def commit_update(
        self,
        token: str,
        *,
        item_id: str,
        content: bytes,
        commit_message: str,
        author: str,
        username_hint: str = "demo.user@ymsli.com",
    ) -> HubCommit:
        access = await self.get_access(token, item_id, username_hint)
        if not access.can_write:
            raise GraphError("Write access required to commit changes.", 403)

        if self.settings.graph_mode.lower() == "mock":
            self._mock_write_content(item_id, content, author)
            return self._record_commit(
                item_id=item_id,
                item_name=access.item_name,
                message=commit_message,
                author=author,
                size=len(content),
                content_hash=hashlib.sha256(content).hexdigest()[:16],
            )

        await self._graph(
            "PUT",
            f"/me/drive/items/{item_id}/content",
            token,
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )
        return self._record_commit(
            item_id=item_id,
            item_name=access.item_name,
            message=commit_message,
            author=author,
            size=len(content),
            content_hash=hashlib.sha256(content).hexdigest()[:16],
        )

    # ── Mapping helpers ──────────────────────────────────────────

    def _map_item(self, raw: dict[str, Any], prefix: str = "") -> DriveItemSummary:
        is_folder = "folder" in raw
        parent_path = ((raw.get("parentReference") or {}).get("path") or "").replace("/drive/root:", "")
        path = f"{parent_path}/{raw.get('name', '')}".replace("//", "/")
        if prefix and not path.startswith(prefix):
            path = f"{prefix}/{raw.get('name', '')}"
        return DriveItemSummary(
            id=raw.get("id", ""),
            name=raw.get("name", ""),
            kind=DriveItemKind.folder if is_folder else DriveItemKind.file,
            path=path,
            size=int(raw.get("size") or 0),
            mime_type=(raw.get("file") or {}).get("mimeType"),
            web_url=raw.get("webUrl"),
            last_modified=raw.get("lastModifiedDateTime"),
            created_by=((raw.get("createdBy") or {}).get("user") or {}).get("displayName"),
            modified_by=((raw.get("lastModifiedBy") or {}).get("user") or {}).get("displayName"),
            etag=raw.get("eTag"),
        )

    def _map_permission(self, raw: dict[str, Any]) -> DrivePermissionInfo:
        granted = None
        granted_type = None
        if raw.get("grantedToV2"):
            identity = raw["grantedToV2"]
            if identity.get("user"):
                granted = identity["user"].get("email") or identity["user"].get("displayName")
                granted_type = "user"
            elif identity.get("group"):
                granted = identity["group"].get("displayName")
                granted_type = "group"
            elif identity.get("siteUser"):
                granted = identity["siteUser"].get("displayName")
                granted_type = "user"
        elif raw.get("grantedTo"):
            user = (raw["grantedTo"] or {}).get("user") or {}
            granted = user.get("email") or user.get("displayName")
            granted_type = "user"
        link = raw.get("link") or {}
        if link:
            granted_type = granted_type or "link"
        return DrivePermissionInfo(
            id=raw.get("id", ""),
            roles=list(raw.get("roles") or []),
            granted_to=granted,
            granted_to_type=granted_type,
            link_scope=link.get("scope"),
        )

    async def _ensure_remote_folder(self, token: str, folder_name: str) -> None:
        try:
            await self._graph("GET", f"/me/drive/root:/{folder_name}", token)
            return
        except GraphError as exc:
            if exc.status_code != 404:
                raise
        await self._graph(
            "POST",
            "/me/drive/root/children",
            token,
            json_body={
                "name": folder_name,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "fail",
            },
        )

    # ── Mock drive ───────────────────────────────────────────────

    def _ensure_mock_seed(self) -> None:
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM mock_drive_items").fetchone()["c"]
            if count:
                return

        samples = [
            ("mock-folder-templates", "Templates", "folder", None, "/Templates", None, "owner"),
            (
                "mock-mom",
                "MOM_Orion_v1.2.docx",
                "file",
                "mock-folder-templates",
                "/Templates/MOM_Orion_v1.2.docx",
                b"YMSLI MOM template content v1.2",
                "write",
            ),
            (
                "mock-qmm",
                "QMM_Proposal_v3.1.docx",
                "file",
                "mock-folder-templates",
                "/Templates/QMM_Proposal_v3.1.docx",
                b"YMSLI QMM proposal approved content",
                "read",
            ),
            (
                "mock-plan",
                "Project_Plan_v1.1.xlsx",
                "file",
                "mock-folder-templates",
                "/Templates/Project_Plan_v1.1.xlsx",
                b"PK mock xlsx bytes",
                "write",
            ),
            (
                "mock-api",
                "API_Spec_Template.docx",
                "file",
                "mock-folder-templates",
                "/Templates/API_Spec_Template.docx",
                b"API specification template body",
                "read",
            ),
        ]

        now = _utcnow()
        with self._connect() as conn:
            for item_id, name, kind, parent, path, content, role in samples:
                content_path = None
                size = 0
                if content is not None:
                    content_path = str(self.mock_root / f"{item_id}.bin")
                    Path(content_path).write_bytes(content)
                    size = len(content)
                    # seed two versions
                    v1 = self.mock_root / f"{item_id}_v1.bin"
                    v1.write_bytes(content + b"\n# version 1")
                    conn.execute(
                        "INSERT INTO mock_versions (id, item_id, content_path, size, modified_at, modified_by) VALUES (?, ?, ?, ?, ?, ?)",
                        (f"{item_id}-v1", item_id, str(v1), len(content) + 12, "2026-01-10T10:00:00+00:00", "template-admin"),
                    )
                    conn.execute(
                        "INSERT INTO mock_versions (id, item_id, content_path, size, modified_at, modified_by) VALUES (?, ?, ?, ?, ?, ?)",
                        (f"{item_id}-v2", item_id, content_path, size, now, "demo.user@ymsli.com"),
                    )
                conn.execute(
                    """
                    INSERT INTO mock_drive_items
                    (id, name, kind, parent_id, path, size, mime_type, content_path, created_at, modified_at, created_by, modified_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        name,
                        kind,
                        parent,
                        path,
                        size,
                        "application/octet-stream" if kind == "file" else None,
                        content_path,
                        now,
                        now,
                        "template-admin",
                        "demo.user@ymsli.com",
                    ),
                )
                conn.execute(
                    "INSERT INTO mock_permissions (id, item_id, roles, granted_to, granted_to_type) VALUES (?, ?, ?, ?, ?)",
                    (f"perm-{item_id}", item_id, json.dumps([role]), "demo.user@ymsli.com", "user"),
                )
                # joiner gets read on QMM only via separate perm simulation when username changes
                if item_id == "mock-qmm":
                    conn.execute(
                        "INSERT INTO mock_permissions (id, item_id, roles, granted_to, granted_to_type) VALUES (?, ?, ?, ?, ?)",
                        ("perm-qmm-joiner", item_id, json.dumps(["read"]), "joiner@ymsli.com", "user"),
                    )
            conn.commit()

        # Seed hub commits for QMM to show GitHub-style history
        self._record_commit(
            item_id="mock-qmm",
            item_name="QMM_Proposal_v3.1.docx",
            message="chore: import approved QMM proposal v3.1",
            author="template-admin",
            size=34,
            graph_version_id="mock-qmm-v1",
        )
        self._record_commit(
            item_id="mock-qmm",
            item_name="QMM_Proposal_v3.1.docx",
            message="docs: align risk section with 2026 standards",
            author="sales-ops",
            size=34,
            graph_version_id="mock-qmm-v2",
        )

    def _mock_list(self, folder: str, username_hint: str) -> list[DriveItemSummary]:
        folder = folder.strip("/")
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM mock_drive_items").fetchall()
        items: list[DriveItemSummary] = []
        for r in rows:
            path = r["path"].strip("/")
            parent = "/".join(path.split("/")[:-1])
            if folder and parent != folder:
                continue
            if not folder and r["parent_id"] is not None and folder == "":
                # show root + Templates folder children only when folder specified;
                # at root show top-level folders/files (parent_id is null)
                if r["parent_id"] is not None:
                    continue
            access = self._mock_access(r["id"], username_hint)
            if not access.can_read:
                continue
            items.append(
                DriveItemSummary(
                    id=r["id"],
                    name=r["name"],
                    kind=DriveItemKind(r["kind"]),
                    path=r["path"],
                    size=r["size"] or 0,
                    mime_type=r["mime_type"],
                    last_modified=r["modified_at"],
                    created_by=r["created_by"],
                    modified_by=r["modified_by"],
                    access=access.access,
                    can_read=access.can_read,
                    can_write=access.can_write,
                    version_count=len(self._mock_versions(r["id"])),
                )
            )
        # If listing Templates, include children
        if folder.lower() == "templates":
            items = []
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM mock_drive_items WHERE parent_id = ?",
                    ("mock-folder-templates",),
                ).fetchall()
            for r in rows:
                access = self._mock_access(r["id"], username_hint)
                if not access.can_read:
                    continue
                items.append(
                    DriveItemSummary(
                        id=r["id"],
                        name=r["name"],
                        kind=DriveItemKind(r["kind"]),
                        path=r["path"],
                        size=r["size"] or 0,
                        mime_type=r["mime_type"],
                        last_modified=r["modified_at"],
                        created_by=r["created_by"],
                        modified_by=r["modified_by"],
                        access=access.access,
                        can_read=access.can_read,
                        can_write=access.can_write,
                        version_count=len(self._mock_versions(r["id"])),
                    )
                )
        return items

    def _mock_access(self, item_id: str, username_hint: str) -> FileAccessReport:
        with self._connect() as conn:
            item = conn.execute("SELECT * FROM mock_drive_items WHERE id = ?", (item_id,)).fetchone()
            perms = conn.execute(
                "SELECT * FROM mock_permissions WHERE item_id = ?", (item_id,)
            ).fetchall()
        if not item:
            return FileAccessReport(
                item_id=item_id,
                item_name="",
                current_user=username_hint,
                access=FileAccessMode.none,
                can_read=False,
                can_write=False,
                rationale="Item not found.",
            )
        upn = username_hint.lower()
        mapped: list[DrivePermissionInfo] = []
        best = FileAccessMode.none
        rank = {FileAccessMode.none: 0, FileAccessMode.read: 1, FileAccessMode.write: 2, FileAccessMode.owner: 3}
        for p in perms:
            roles = json.loads(p["roles"])
            granted = (p["granted_to"] or "").lower()
            mapped.append(
                DrivePermissionInfo(
                    id=p["id"],
                    roles=roles,
                    granted_to=p["granted_to"],
                    granted_to_type=p["granted_to_type"],
                )
            )
            # demo.user and consultant map to write defaults; joiner only explicit
            applies = granted == upn or (
                upn in ("demo.user@ymsli.com", "consultant", "consultant@ymsli.com", "approver", "approver@ymsli.com")
                and granted == "demo.user@ymsli.com"
            )
            if upn.startswith("joiner") and granted != "joiner@ymsli.com":
                applies = False
            if not applies:
                continue
            role_set = {r.lower() for r in roles}
            if "owner" in role_set:
                candidate = FileAccessMode.owner
            elif "write" in role_set:
                candidate = FileAccessMode.write
            else:
                candidate = FileAccessMode.read
            if rank[candidate] > rank[best]:
                best = candidate

        # joiner default: read-only on templates folder listing for non-explicit files
        if best == FileAccessMode.none and not upn.startswith("joiner"):
            best = FileAccessMode.read

        return FileAccessReport(
            item_id=item_id,
            item_name=item["name"],
            current_user=username_hint,
            access=best,
            can_read=best != FileAccessMode.none,
            can_write=best in (FileAccessMode.write, FileAccessMode.owner),
            permissions=mapped,
            rationale=self._access_rationale(best, mapped),
        )

    def _mock_versions(self, item_id: str) -> list[DriveVersionEntry]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM mock_versions WHERE item_id = ? ORDER BY modified_at DESC",
                (item_id,),
            ).fetchall()
        return [
            DriveVersionEntry(
                id=r["id"],
                last_modified=r["modified_at"],
                size=r["size"] or 0,
                modified_by=r["modified_by"],
                source="onedrive",
            )
            for r in rows
        ]

    def _mock_download(self, item_id: str) -> tuple[str, bytes]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM mock_drive_items WHERE id = ?", (item_id,)).fetchone()
        if not row or not row["content_path"]:
            raise GraphError("File content not found.", 404)
        return row["name"], Path(row["content_path"]).read_bytes()

    def _mock_upload(self, filename: str, content: bytes, folder: str, author: str) -> DriveItemSummary:
        folder = folder.strip("/") or "Templates"
        item_id = f"mock-{hashlib.sha1(filename.encode()).hexdigest()[:8]}"
        path = f"/{folder}/{filename}"
        content_path = str(self.mock_root / f"{item_id}.bin")
        Path(content_path).write_bytes(content)
        now = _utcnow()
        parent = "mock-folder-templates" if folder.lower() == "templates" else None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO mock_drive_items
                (id, name, kind, parent_id, path, size, mime_type, content_path, created_at, modified_at, created_by, modified_by)
                VALUES (?, ?, 'file', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (item_id, filename, parent, path, len(content), "application/octet-stream", content_path, now, now, author, author),
            )
            conn.execute(
                "INSERT OR REPLACE INTO mock_permissions (id, item_id, roles, granted_to, granted_to_type) VALUES (?, ?, ?, ?, ?)",
                (f"perm-{item_id}", item_id, json.dumps(["owner"]), "demo.user@ymsli.com", "user"),
            )
            conn.execute(
                "INSERT INTO mock_versions (id, item_id, content_path, size, modified_at, modified_by) VALUES (?, ?, ?, ?, ?, ?)",
                (f"{item_id}-v{uuid.uuid4().hex[:4]}", item_id, content_path, len(content), now, author),
            )
            conn.commit()
        return DriveItemSummary(
            id=item_id,
            name=filename,
            kind=DriveItemKind.file,
            path=path,
            size=len(content),
            can_read=True,
            can_write=True,
            access=FileAccessMode.owner,
            last_modified=now,
            modified_by=author,
        )

    def _mock_write_content(self, item_id: str, content: bytes, author: str) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM mock_drive_items WHERE id = ?", (item_id,)).fetchone()
            if not row:
                raise GraphError("Item not found.", 404)
            content_path = row["content_path"] or str(self.mock_root / f"{item_id}.bin")
            Path(content_path).write_bytes(content)
            now = _utcnow()
            conn.execute(
                "UPDATE mock_drive_items SET size = ?, modified_at = ?, modified_by = ?, content_path = ? WHERE id = ?",
                (len(content), now, author, content_path, item_id),
            )
            conn.execute(
                "INSERT INTO mock_versions (id, item_id, content_path, size, modified_at, modified_by) VALUES (?, ?, ?, ?, ?, ?)",
                (f"{item_id}-v{uuid.uuid4().hex[:4]}", item_id, content_path, len(content), now, author),
            )
            conn.commit()

    def _mock_restore(self, item_id: str, version_id: str, author: str) -> None:
        with self._connect() as conn:
            ver = conn.execute(
                "SELECT * FROM mock_versions WHERE id = ? AND item_id = ?",
                (version_id, item_id),
            ).fetchone()
            if not ver:
                raise GraphError("Version not found.", 404)
            data = Path(ver["content_path"]).read_bytes()
        self._mock_write_content(item_id, data, author)


def decode_content_base64(content_base64: str) -> bytes:
    return base64.b64decode(content_base64)
