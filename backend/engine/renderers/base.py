"""
Renderer contract.

The renderer is the only component allowed to touch an Office file, and it
only ever executes a validated plan. It never decides *what* to write, only
*how* - which means formatting stays where it belongs: in the template.

Two invariants every renderer must uphold:
  * the original file is never modified (work happens on a copy);
  * styles are inherited or cloned, never recreated.
"""

from __future__ import annotations

import os
import re
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type

from ..ir import DocType
from ..logging_config import get_logger
from ..mapper import FillInstruction, FillPlan
from ..parsers.base import PLACEHOLDER_RE

log = get_logger("renderers")

# Mustache-style block markers - {{#SECTION}} and {{/SECTION}} - are
# structural (they mark where a repeating region begins/ends), not a value
# slot, so PLACEHOLDER_RE deliberately doesn't treat them as fillable. But
# they're still template-authoring syntax that a person should never see in
# a finished file, so the cleanup pass strips these too.
MUSTACHE_BLOCK_RE = re.compile(r"\{\{\s*[#/]\s*[\w.\-]+\s*\}\}")


def clear_if_unresolved_placeholder(text: Optional[str]) -> Optional[str]:
    """
    A {{placeholder}} the plan never resolved (no evidence, low confidence,
    or a role the taxonomy couldn't match) should never survive into the
    final file as visible raw template syntax - "no fill" means blank, not
    "here's our internal token syntax." Returns the cleared text, or the
    original unchanged if there was nothing to clear.
    """
    if not text:
        return text
    has_placeholder = PLACEHOLDER_RE.search(text)
    has_block_marker = MUSTACHE_BLOCK_RE.search(text)
    if not has_placeholder and not has_block_marker:
        return text
    cleared = PLACEHOLDER_RE.sub("", text)
    cleared = MUSTACHE_BLOCK_RE.sub("", cleared)
    return cleared.strip()


@dataclass
class WriteRecord:
    node_id: str
    role: str
    target: str
    value: Any = None
    rows_written: int = 0
    status: str = "written"           # written | skipped | failed
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"node_id": self.node_id, "role": self.role, "target": self.target,
                "value": self.value, "rows_written": self.rows_written,
                "status": self.status, "message": self.message}


@dataclass
class RenderResult:
    output_path: str
    records: List[WriteRecord] = field(default_factory=list)
    rows_added: int = 0

    @property
    def written(self) -> int:
        return sum(1 for r in self.records if r.status == "written")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.records if r.status == "failed")

    def to_dict(self) -> Dict[str, Any]:
        return {"output_path": self.output_path, "written": self.written,
                "failed": self.failed, "rows_added": self.rows_added,
                "records": [r.to_dict() for r in self.records]}


class BaseRenderer(ABC):
    doc_type: DocType

    def prepare_output(self, template_path: str, output_path: str) -> str:
        """Copy first. The template is an input, never a workspace."""
        out_dir = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(out_dir, exist_ok=True)
        if os.path.abspath(template_path) == os.path.abspath(output_path):
            log.error("Refusing to render over the template file: %s", template_path)
            raise ValueError("Refusing to write over the template. Choose a different output path.")
        shutil.copyfile(template_path, output_path)
        log.debug("Copied template %s -> %s", template_path, output_path)
        return output_path

    @abstractmethod
    def render(self, template_path: str, output_path: str, plan: FillPlan,
              clear_unresolved: bool = True) -> RenderResult:
        ...


_RENDERERS: Dict[str, Type[BaseRenderer]] = {}


def register(ext: str, cls: Type[BaseRenderer]) -> None:
    _RENDERERS[ext.lower().lstrip(".")] = cls


def get_renderer(path: str) -> BaseRenderer:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext not in _RENDERERS:
        raise ValueError(f"No renderer for '.{ext}' (have: {sorted(_RENDERERS)})")
    return _RENDERERS[ext]()


def render_plan(template_path: str, output_path: str, plan: FillPlan,
                clear_unresolved: bool = True) -> RenderResult:
    return get_renderer(template_path).render(template_path, output_path, plan, clear_unresolved)
