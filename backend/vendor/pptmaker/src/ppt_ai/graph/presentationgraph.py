from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Optional

from ..models import Relationship, RelationshipType, ShapeNode


class PresentationGraph:
    """
    Lightweight semantic graph for a PowerPoint presentation.

    The graph stores:
    - nodes: the semantic entities extracted from the deck
    - edges: relationships such as BELONGS_TO, INSIDE, OWNS, etc.
    - adjacency: a quick lookup of shape -> neighbors

    This replaces the slide/shape-only mental model with a graph-centric view
    where objects can be queried by ownership and relationship.

    Example:
        graph.add_node(status_badge)
        graph.add_node(project_row)
        graph.add_node(table)

        graph.add_edge(status_badge.id, project_row.id, RelationshipType.BELONGS_TO)
        graph.add_edge(project_row.id, table.id, RelationshipType.INSIDE)

        owner = graph.find_owner(status_badge)
        neighbors = graph.find_neighbors(status_badge)
        project = graph.find_project("Project Row")
    """

    def __init__(self) -> None:
        self.nodes: dict[int, ShapeNode] = {}
        self.edges: list[Relationship] = []
        self.adjacency: dict[int, list[dict[str, Any]]] = defaultdict(list)

    def add_node(self, shape: ShapeNode) -> ShapeNode:
        """Register a shape as a graph node."""
        self.nodes[shape.id] = shape
        return shape

    def add_edge(
        self,
        source_id: int,
        target_id: int,
        relation: RelationshipType,
        confidence: float = 1.0,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Relationship:
        """Add a relationship edge between two existing nodes."""
        edge = Relationship(
            source=source_id,
            target=target_id,
            relation=relation,
            confidence=confidence,
            metadata=metadata or {},
        )
        self.edges.append(edge)
        self.adjacency[source_id].append(
            {
                "target": target_id,
                "relation": relation,
                "confidence": confidence,
                "metadata": edge.metadata,
            }
        )

        return edge

    def find_owner(self, shape: ShapeNode | int) -> Optional[ShapeNode]:
        """
        Return the direct owner of a node.

        For example, a Status Badge belongs to a Project Row, so
        `find_owner(status_badge)` should return the project row node.
        """
        source_id = self._resolve_id(shape)

        for edge in self.edges:
            if edge.source != source_id:
                continue
            if edge.relation in {RelationshipType.BELONGS_TO, RelationshipType.OWNS}:
                return self.nodes.get(edge.target)

        return None

    def find_neighbors(self, shape: ShapeNode | int) -> list[ShapeNode]:
        """Return all nodes directly connected to the given shape."""
        shape_id = self._resolve_id(shape)
        neighbors: list[ShapeNode] = []

        for edge in self.edges:
            if edge.source == shape_id:
                node = self.nodes.get(edge.target)
                if node is not None and node.id not in {n.id for n in neighbors}:
                    neighbors.append(node)
            elif edge.target == shape_id:
                node = self.nodes.get(edge.source)
                if node is not None and node.id not in {n.id for n in neighbors}:
                    neighbors.append(node)

        return neighbors

    def find_project(self, name: str) -> Optional[ShapeNode]:
        """Find a project-like node by name, case-insensitively."""
        lookup = name.strip().lower()

        for node in self.nodes.values():
            if lookup in node.name.lower():
                return node

        return None

    def get_node(self, node_id: int) -> Optional[ShapeNode]:
        return self.nodes.get(node_id)

    def get_edges(self, relation: Optional[RelationshipType] = None) -> list[Relationship]:
        if relation is None:
            return list(self.edges)
        return [edge for edge in self.edges if edge.relation == relation]

    def _resolve_id(self, shape: ShapeNode | int) -> int:
        return shape.id if isinstance(shape, ShapeNode) else int(shape)

    def __contains__(self, node_id: int) -> bool:
        return node_id in self.nodes

    def __len__(self) -> int:
        return len(self.nodes)

    def __iter__(self):
        return iter(self.nodes.values())
