from __future__ import annotations

from typing import Any, Optional

from app.models.schemas import (
    TemplateMeta,
    TemplateVersion,
    VersionCompareChange,
    VersionCompareResponse,
)
from app.services.catalog import CatalogService, _utcnow


class VersionService:
    """Maintain and compare template versions (definition snapshots)."""

    def __init__(self, catalog: CatalogService):
        self.catalog = catalog

    def _resolve_snapshot(self, template: TemplateMeta, version: TemplateVersion) -> dict[str, Any]:
        """Prefer stored snapshot; for latest approved fall back to live template fields."""
        latest = self.catalog.latest_version(template)
        has_snap = bool(version.placeholders or version.content_outline or version.description)
        if has_snap:
            return {
                "description": version.description or "",
                "placeholders": list(version.placeholders),
                "content_outline": list(version.content_outline),
                "context_questions": list(version.context_questions),
                "changelog": version.changelog,
                "status": version.status,
                "created_at": version.created_at,
                "created_by": version.created_by,
            }
        if version.version == latest.version:
            return {
                "description": template.description,
                "placeholders": list(template.placeholders),
                "content_outline": list(template.content_outline),
                "context_questions": list(template.context_questions),
                "changelog": version.changelog,
                "status": version.status,
                "created_at": version.created_at,
                "created_by": version.created_by,
            }
        # Older version without snapshot — synthetic reduced view
        return {
            "description": version.changelog or template.description,
            "placeholders": list(template.placeholders)[:-1] if template.placeholders else [],
            "content_outline": list(template.content_outline)[:-1] if template.content_outline else [],
            "context_questions": list(template.context_questions),
            "changelog": version.changelog,
            "status": version.status,
            "created_at": version.created_at,
            "created_by": version.created_by,
        }

    def get_version(self, template_id: str, version: str) -> Optional[tuple[TemplateMeta, TemplateVersion, dict[str, Any]]]:
        tmpl = self.catalog.get(template_id)
        if not tmpl:
            return None
        match = next((v for v in tmpl.versions if v.version == version), None)
        if not match:
            return None
        return tmpl, match, self._resolve_snapshot(tmpl, match)

    def compare(self, template_id: str, from_version: str, to_version: str) -> VersionCompareResponse:
        left = self.get_version(template_id, from_version)
        right = self.get_version(template_id, to_version)
        if not left or not right:
            raise ValueError("One or both versions were not found.")
        tmpl, _, a = left
        _, _, b = right

        changes: list[VersionCompareChange] = []
        for field in ("description", "placeholders", "content_outline", "context_questions", "changelog", "status"):
            before = a.get(field)
            after = b.get(field)
            if before == after:
                changes.append(
                    VersionCompareChange(field=field, change="unchanged", before=before, after=after)
                )
            elif isinstance(before, list) and isinstance(after, list):
                added = [x for x in after if x not in before]
                removed = [x for x in before if x not in after]
                if added or removed:
                    changes.append(
                        VersionCompareChange(
                            field=field,
                            change="changed",
                            before=before,
                            after=after,
                        )
                    )
                    if added:
                        changes.append(
                            VersionCompareChange(field=f"{field}:+", change="added", before=None, after=added)
                        )
                    if removed:
                        changes.append(
                            VersionCompareChange(field=f"{field}:-", change="removed", before=removed, after=None)
                        )
                else:
                    changes.append(
                        VersionCompareChange(field=field, change="unchanged", before=before, after=after)
                    )
            else:
                changes.append(
                    VersionCompareChange(field=field, change="changed", before=before, after=after)
                )

        meaningful = [c for c in changes if c.change != "unchanged" and ":" not in c.field]
        if not meaningful:
            summary = f"No structural differences between v{from_version} and v{to_version}."
        else:
            bits = [f"{c.field} {c.change}" for c in meaningful]
            summary = f"v{from_version} → v{to_version}: " + "; ".join(bits)

        return VersionCompareResponse(
            template_id=template_id,
            template_name=tmpl.name,
            from_version=from_version,
            to_version=to_version,
            changes=changes,
            summary=summary,
        )

    def create_version(
        self,
        template_id: str,
        *,
        version: str,
        changelog: str,
        created_by: str,
        status: str = "draft",
        description: Optional[str] = None,
        placeholders: Optional[list[str]] = None,
        content_outline: Optional[list[str]] = None,
        context_questions: Optional[list[str]] = None,
        promote_to_current: bool = True,
    ) -> TemplateMeta:
        tmpl = self.catalog.get(template_id)
        if not tmpl:
            raise ValueError("Template not found")
        if any(v.version == version for v in tmpl.versions):
            raise ValueError(f"Version {version} already exists")

        snap_desc = description if description is not None else tmpl.description
        snap_ph = placeholders if placeholders is not None else list(tmpl.placeholders)
        snap_outline = content_outline if content_outline is not None else list(tmpl.content_outline)
        snap_qs = context_questions if context_questions is not None else list(tmpl.context_questions)

        entry = TemplateVersion(
            version=version,
            status=status,  # type: ignore[arg-type]
            changelog=changelog,
            created_at=_utcnow(),
            created_by=created_by,
            description=snap_desc,
            placeholders=snap_ph,
            content_outline=snap_outline,
            context_questions=snap_qs,
        )
        tmpl.versions.append(entry)

        if promote_to_current and status == "approved":
            tmpl.description = snap_desc
            tmpl.placeholders = snap_ph
            tmpl.content_outline = snap_outline
            tmpl.context_questions = snap_qs
            # deprecate previous approved
            for v in tmpl.versions:
                if v.version != version and v.status == "approved":
                    v.status = "deprecated"

        self.catalog.save(tmpl)
        return tmpl
