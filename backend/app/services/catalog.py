from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import get_settings
from app.models.schemas import (
    AccessLevel,
    AnalyticsSummary,
    TemplateMeta,
    TemplateVersion,
    UserInfo,
)


DEMO_USERS: dict[str, UserInfo] = {
    "consultant": UserInfo(
        username="consultant",
        display_name="Aaditva Consultant",
        role="consultant",
        access={
            "*": AccessLevel.write,
            "tmpl-qmm-proposal": AccessLevel.write,
        },
    ),
    "approver": UserInfo(
        username="approver",
        display_name="Template Approver",
        role="approver",
        access={"*": AccessLevel.authorize},
    ),
    "joiner": UserInfo(
        username="joiner",
        display_name="New Joiner",
        role="joiner",
        access={
            "tmpl-mom": AccessLevel.read,
            "tmpl-onboarding": AccessLevel.read,
            "tmpl-status-report": AccessLevel.read,
            "tmpl-project-plan": AccessLevel.read,
        },
    ),
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class CatalogService:
    def __init__(self) -> None:
        settings = get_settings()
        self.db_path = Path(settings.database_path)
        self.data_path = Path(settings.data_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS templates (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    template_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    username TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def seed_from_json(self, force: bool = False) -> int:
        seed_file = self.data_path / "templates.json"
        if not seed_file.exists():
            raise FileNotFoundError(f"Missing seed file: {seed_file}")
        templates = json.loads(seed_file.read_text(encoding="utf-8"))
        with self._connect() as conn:
            existing = conn.execute("SELECT COUNT(*) AS c FROM templates").fetchone()["c"]
            if existing and not force:
                # Refresh definitions (incl. version snapshots) but keep usage stats
                for item in templates:
                    row = conn.execute(
                        "SELECT payload FROM templates WHERE id = ?", (item["id"],)
                    ).fetchone()
                    if row:
                        prev = json.loads(row["payload"])
                        item["usage_count"] = prev.get("usage_count", item.get("usage_count", 0))
                        item["last_used_at"] = prev.get("last_used_at", item.get("last_used_at"))
                    conn.execute(
                        "INSERT OR REPLACE INTO templates (id, payload) VALUES (?, ?)",
                        (item["id"], json.dumps(item)),
                    )
                conn.commit()
                return len(templates)
            if force:
                conn.execute("DELETE FROM templates")
            for item in templates:
                conn.execute(
                    "INSERT OR REPLACE INTO templates (id, payload) VALUES (?, ?)",
                    (item["id"], json.dumps(item)),
                )
            conn.commit()
        return len(templates)

    def list_templates(self) -> list[TemplateMeta]:
        with self._connect() as conn:
            rows = conn.execute("SELECT payload FROM templates ORDER BY id").fetchall()
        return [TemplateMeta.model_validate_json(r["payload"]) for r in rows]

    def get(self, template_id: str) -> Optional[TemplateMeta]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM templates WHERE id = ?", (template_id,)
            ).fetchone()
        return TemplateMeta.model_validate_json(row["payload"]) if row else None

    def save(self, template: TemplateMeta) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO templates (id, payload) VALUES (?, ?)",
                (template.id, template.model_dump_json()),
            )
            conn.commit()

    def latest_version(self, template: TemplateMeta) -> TemplateVersion:
        approved = [v for v in template.versions if v.status == "approved"]
        pool = approved or template.versions
        return sorted(pool, key=lambda v: v.created_at, reverse=True)[0]

    def record_usage(self, template_id: str, action: str, username: str) -> None:
        tmpl = self.get(template_id)
        if not tmpl:
            return
        tmpl.usage_count += 1
        tmpl.last_used_at = _utcnow()
        self.save(tmpl)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO usage_events (template_id, action, username, created_at) VALUES (?, ?, ?, ?)",
                (template_id, action, username, _utcnow()),
            )
            conn.commit()

    def analytics(self) -> AnalyticsSummary:
        templates = self.list_templates()
        most_used = sorted(templates, key=lambda t: t.usage_count, reverse=True)[:5]
        stale = sorted(
            templates,
            key=lambda t: t.last_used_at or "1970-01-01",
        )[:5]
        total_versions = sum(len(t.versions) for t in templates)
        return AnalyticsSummary(
            most_used=most_used,
            stale=stale,
            total_templates=len(templates),
            total_versions=total_versions,
        )


class AccessControl:
    def get_user(self, username: str) -> UserInfo:
        return DEMO_USERS.get(username, DEMO_USERS["joiner"])

    def can_access(self, username: str, template: TemplateMeta, needed: AccessLevel) -> bool:
        user = self.get_user(username)
        rank = {AccessLevel.read: 1, AccessLevel.write: 2, AccessLevel.authorize: 3}
        level = user.access.get(template.id) or user.access.get("*")
        if level is None:
            return False
        return rank[level] >= rank[needed] and rank[level] >= rank[template.required_access]

    def filter_templates(self, username: str, templates: list[TemplateMeta]) -> list[TemplateMeta]:
        return [t for t in templates if self.can_access(username, t, AccessLevel.read)]
