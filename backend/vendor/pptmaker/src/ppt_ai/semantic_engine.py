from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .graph.presentationgraph import PresentationGraph
from .models import ShapeNode, RelationshipType, ShapeRole


@dataclass(slots=True)
class ProjectRecord:
    """Business object representing a project row and its status badge."""

    name: str
    status: str
    badge_shape: Optional[ShapeNode] = None
    row_shape: Optional[ShapeNode] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def update_status(self, new_status: str) -> None:
        self.status = new_status


@dataclass(slots=True)
class UpdateOperation:
    """Single edit that will later be applied to the PowerPoint file."""

    target_shape_id: int
    property_name: str
    old_value: Any
    new_value: Any
    metadata: dict[str, Any] = field(default_factory=dict)


class SemanticEngine:
    """
    Converts graph nodes into semantic business objects.

    The key idea is that the semantic object keeps references to original
    ShapeNode objects instead of copying the data.
    """

    def __init__(self) -> None:
        self.projects: list[ProjectRecord] = []

    def build_from_graph(self, graph: PresentationGraph) -> list[ProjectRecord]:
        """Create semantic project objects from the graph."""
        self.projects = []

        for node in graph.nodes.values():
            if node.role == ShapeRole.TABLE:
                continue

            name = node.name.strip()
            if not name:
                continue

            owner = graph.find_owner(node)
            if owner is None:
                continue

            badge_text = node.text.strip() if node.text else ""
            if badge_text and badge_text.upper() in {"ON TRACK", "DELAYED", "AT RISK", "OFF TRACK", "PAUSED"}:
                self.projects.append(
                    ProjectRecord(
                        name=owner.name,
                        status=badge_text,
                        badge_shape=node,
                        row_shape=owner,
                        metadata={"badge_text": badge_text},
                    )
                )

        return self.projects

    def find_project(self, name: str) -> Optional[ProjectRecord]:
        lookup = name.strip().lower()
        for project in self.projects:
            if lookup in project.name.lower():
                return project
        return None

    def plan_status_change(self, project_name: str, new_status: str) -> Optional[UpdateOperation]:
        project = self.find_project(project_name)
        if project is None or project.badge_shape is None:
            return None

        old_value = project.status
        if old_value == new_status:
            return None

        project.update_status(new_status)

        return UpdateOperation(
            target_shape_id=project.badge_shape.id,
            property_name="text",
            old_value=old_value,
            new_value=new_status,
            metadata={"project_name": project.name},
        )


class Updater:
    """Applies update operations to the live python-pptx presentation."""

    def __init__(self, presentation) -> None:
        self.presentation = presentation

    def _find_shape_by_id(self, shape_id: int):
        for slide in self.presentation.slides:
            for shape in slide.shapes:
                if getattr(shape, "shape_id", None) == shape_id:
                    return shape
        return None

    def apply_operation(self, operation: UpdateOperation) -> bool:
        shape = self._find_shape_by_id(operation.target_shape_id)
        if shape is None or not hasattr(shape, "text_frame"):
            return False

        if not shape.has_text_frame:
            return False

        text_frame = shape.text_frame
        if not text_frame.paragraphs:
            return False

        paragraphs = text_frame.paragraphs
        if paragraphs and paragraphs[0].runs:
            paragraphs[0].runs[0].text = str(operation.new_value)
        else:
            paragraphs[0].text = str(operation.new_value)

        return True

    def apply_plan(self, operations: list[UpdateOperation]) -> int:
        count = 0
        for op in operations:
            if self.apply_operation(op):
                count += 1
        return count
