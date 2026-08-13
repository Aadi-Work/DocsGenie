"""
Optional Semantic Kernel wiring for PS-08 compliance.

The production chat path uses AgentOrchestrator (same plugin steps).
When `semantic-kernel` is installed and SK_ENABLED=1, this module
exposes equivalent native functions that can be registered as SK plugins.
"""

from __future__ import annotations

from typing import Annotated

from app.deps import get_acl, get_catalog, get_generator, get_retriever
from app.models.schemas import AccessLevel


def search_templates(query: Annotated[str, "Natural language template query"], username: str = "consultant") -> str:
    retriever = get_retriever()
    acl = get_acl()
    hits = retriever.search(query, limit=5)
    lines = []
    for tmpl, score in hits:
        if acl.can_access(username, tmpl, AccessLevel.read):
            latest = get_catalog().latest_version(tmpl)
            lines.append(f"{tmpl.id} | {tmpl.name} | v{latest.version} | score={score:.2f}")
    return "\n".join(lines) or "No templates found."


def list_versions(template_id: Annotated[str, "Template id"], username: str = "consultant") -> str:
    catalog = get_catalog()
    acl = get_acl()
    tmpl = catalog.get(template_id)
    if not tmpl or not acl.can_access(username, tmpl, AccessLevel.read):
        return "Template not found or access denied."
    return "\n".join(
        f"v{v.version} [{v.status}] {v.changelog}" for v in tmpl.versions
    )


def generate_document(
    template_id: Annotated[str, "Template id"],
    answers_csv: Annotated[str, "key=value pairs separated by ||"],
    username: str = "consultant",
) -> str:
    catalog = get_catalog()
    acl = get_acl()
    generator = get_generator()
    tmpl = catalog.get(template_id)
    if not tmpl:
        return "Template not found."
    if not acl.can_access(username, tmpl, AccessLevel.write):
        return "Write access required."
    answers = {}
    for part in answers_csv.split("||"):
        if "=" in part:
            k, v = part.split("=", 1)
            answers[k.strip()] = v.strip()
    for ph in tmpl.placeholders:
        answers.setdefault(ph, f"[Pending: {ph}]")
    filename, _ = generator.generate(tmpl, answers)
    catalog.record_usage(template_id, "generate", username)
    return f"/api/files/{filename}"
