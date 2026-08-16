"""
Semantic Graph Extension

Extends the Presentation Graph with semantic nodes and relationships.
Enables queries like "find all statuses", "get owner of project", "list dependencies".
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Set, Any
from uuid import uuid4

from src.ppt_ai.models import ShapeNode, RelationshipType


class SemanticNodeType(Enum):
    """Types of semantic nodes in the graph."""
    SHAPE = "shape"
    SECTION = "section"
    PARAGRAPH = "paragraph"
    BULLET = "bullet"
    TABLE_CELL = "table_cell"
    IMAGE = "image"
    CONCEPT = "concept"  # Abstract concepts like "project", "status", "team"
    ENTITY = "entity"  # Entities like "person", "location", "organization"


class SemanticRelationType(Enum):
    """Types of semantic relationships."""
    # Structural
    CONTAINS = "contains"
    PART_OF = "part_of"
    NEXT = "next"
    PREVIOUS = "previous"
    
    # Semantic
    HAS_STATUS = "has_status"
    HAS_OWNER = "has_owner"
    HAS_PRIORITY = "has_priority"
    HAS_DEADLINE = "has_deadline"
    
    # Reference
    REFERENCES = "references"
    REFERENCED_BY = "referenced_by"
    DEPENDS_ON = "depends_on"
    DEPENDENCY_OF = "dependency_of"
    
    # Relationship
    RELATED_TO = "related_to"
    SIMILAR_TO = "similar_to"
    CONFLICTS_WITH = "conflicts_with"
    COMPLEMENTARY_TO = "complementary_to"
    
    # Original relationships
    BELONGS_TO = "belongs_to"
    INSIDE = "inside"


@dataclass
class SemanticNode:
    """A semantic node in the presentation graph."""
    node_id: str
    node_type: SemanticNodeType
    
    # Content
    label: str
    description: str = ""
    
    # Metadata
    slide_index: int = 0
    shape_id: int = 0
    shape_name: str = ""
    
    # Semantic attributes
    semantic_type: str = ""  # e.g., "project", "status", "milestone", "risk"
    semantic_value: Any = None  # e.g., "ON TRACK", "Brazil", "Q1 2024"
    
    # Associated shape/document nodes
    associated_node_ids: List[str] = field(default_factory=list)
    
    # Properties
    properties: Dict[str, Any] = field(default_factory=dict)
    
    # Relationships
    outgoing_edges: List[tuple] = field(default_factory=list)  # (target_id, relation_type)
    incoming_edges: List[tuple] = field(default_factory=list)  # (source_id, relation_type)


@dataclass
class SemanticEdge:
    """A semantic relationship between nodes."""
    edge_id: str
    source_id: str
    target_id: str
    relation_type: SemanticRelationType
    
    # Metadata
    confidence: float = 1.0
    evidence: List[str] = field(default_factory=list)  # Why this relationship exists
    
    def __hash__(self):
        return hash((self.source_id, self.target_id, self.relation_type))


class SemanticGraph:
    """Semantic representation of the presentation."""
    
    def __init__(self):
        self.nodes: Dict[str, SemanticNode] = {}
        self.edges: Dict[tuple, SemanticEdge] = {}
        self.node_index_by_type: Dict[SemanticNodeType, List[str]] = {t: [] for t in SemanticNodeType}
        self.concept_index: Dict[str, List[str]] = {}  # concept -> node IDs
    
    def add_node(self, node: SemanticNode) -> None:
        """Add a node to the graph."""
        self.nodes[node.node_id] = node
        self.node_index_by_type[node.node_type].append(node.node_id)
        
        if node.semantic_type:
            if node.semantic_type not in self.concept_index:
                self.concept_index[node.semantic_type] = []
            self.concept_index[node.semantic_type].append(node.node_id)
    
    def remove_node(self, node_id: str) -> None:
        """Remove a node from the graph."""
        if node_id not in self.nodes:
            return
        
        node = self.nodes[node_id]
        
        # Remove from indices
        self.node_index_by_type[node.node_type].remove(node_id)
        
        if node.semantic_type in self.concept_index:
            self.concept_index[node.semantic_type].remove(node_id)
        
        # Remove edges
        edges_to_remove = []
        for (src, tgt), edge in self.edges.items():
            if src == node_id or tgt == node_id:
                edges_to_remove.append((src, tgt))
        
        for edge_key in edges_to_remove:
            del self.edges[edge_key]
        
        del self.nodes[node_id]
    
    def add_edge(
        self,
        source_id: str,
        target_id: str,
        relation_type: SemanticRelationType,
        confidence: float = 1.0,
        evidence: Optional[List[str]] = None,
    ) -> None:
        """Add an edge between nodes."""
        if source_id not in self.nodes or target_id not in self.nodes:
            return
        
        edge_id = f"edge_{uuid4().hex[:8]}"
        edge = SemanticEdge(
            edge_id=edge_id,
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type,
            confidence=confidence,
            evidence=evidence or [],
        )
        
        key = (source_id, target_id, relation_type)
        self.edges[key] = edge
        
        # Update node edges
        source_node = self.nodes[source_id]
        target_node = self.nodes[target_id]
        
        source_node.outgoing_edges.append((target_id, relation_type))
        target_node.incoming_edges.append((source_id, relation_type))
    
    def get_node(self, node_id: str) -> Optional[SemanticNode]:
        """Get a node by ID."""
        return self.nodes.get(node_id)
    
    def get_nodes_by_type(self, node_type: SemanticNodeType) -> List[SemanticNode]:
        """Get all nodes of a specific type."""
        return [self.nodes[nid] for nid in self.node_index_by_type[node_type]]
    
    def get_nodes_by_semantic_type(self, semantic_type: str) -> List[SemanticNode]:
        """Get all nodes with a specific semantic type."""
        node_ids = self.concept_index.get(semantic_type, [])
        return [self.nodes[nid] for nid in node_ids if nid in self.nodes]
    
    def get_outgoing_edges(
        self,
        node_id: str,
        relation_type: Optional[SemanticRelationType] = None,
    ) -> List[tuple]:
        """Get outgoing edges from a node."""
        node = self.nodes.get(node_id)
        if not node:
            return []
        
        if relation_type:
            return [(target, rel) for target, rel in node.outgoing_edges if rel == relation_type]
        
        return node.outgoing_edges
    
    def get_incoming_edges(
        self,
        node_id: str,
        relation_type: Optional[SemanticRelationType] = None,
    ) -> List[tuple]:
        """Get incoming edges to a node."""
        node = self.nodes.get(node_id)
        if not node:
            return []
        
        if relation_type:
            return [(source, rel) for source, rel in node.incoming_edges if rel == relation_type]
        
        return node.incoming_edges
    
    def find_path(self, source_id: str, target_id: str) -> Optional[List[str]]:
        """Find a path between two nodes (BFS)."""
        if source_id not in self.nodes or target_id not in self.nodes:
            return None
        
        from collections import deque
        
        queue = deque([(source_id, [source_id])])
        visited = {source_id}
        
        while queue:
            current_id, path = queue.popleft()
            
            if current_id == target_id:
                return path
            
            # Get outgoing edges
            for target, _ in self.get_outgoing_edges(current_id):
                if target not in visited:
                    visited.add(target)
                    queue.append((target, path + [target]))
        
        return None
    
    def find_related_concepts(self, node_id: str, max_depth: int = 2) -> Set[str]:
        """Find all concepts related to a node."""
        from collections import deque
        
        if node_id not in self.nodes:
            return set()
        
        concepts = set()
        queue = deque([(node_id, 0)])
        visited = {node_id}
        
        while queue:
            current_id, depth = queue.popleft()
            
            if depth > max_depth:
                continue
            
            current = self.nodes.get(current_id)
            if current and current.semantic_type:
                concepts.add(current.semantic_type)
            
            # Explore neighbors
            for target_id, _ in self.get_outgoing_edges(current_id):
                if target_id not in visited:
                    visited.add(target_id)
                    queue.append((target_id, depth + 1))
            
            for source_id, _ in self.get_incoming_edges(current_id):
                if source_id not in visited:
                    visited.add(source_id)
                    queue.append((source_id, depth + 1))
        
        return concepts


class SemanticGraphBuilder:
    """Builds semantic graphs from document structures."""
    
    @staticmethod
    def build_from_document_tree(document_tree) -> SemanticGraph:
        """Build a semantic graph from a document tree."""
        from src.ppt_ai.document_tree import ElementType
        
        graph = SemanticGraph()
        
        # Create nodes for each document element
        for node_id, doc_node in document_tree.nodes.items():
            # Determine semantic node type
            semantic_type_map = {
                ElementType.SECTION: SemanticNodeType.SECTION,
                ElementType.PARAGRAPH: SemanticNodeType.PARAGRAPH,
                ElementType.BULLET_ITEM: SemanticNodeType.BULLET,
                ElementType.TABLE_CELL: SemanticNodeType.TABLE_CELL,
                ElementType.IMAGE: SemanticNodeType.IMAGE,
            }
            
            node_type = semantic_type_map.get(doc_node.element_type, SemanticNodeType.SHAPE)
            
            semantic_node = SemanticNode(
                node_id=node_id,
                node_type=node_type,
                label=doc_node.text[:50],
                description=doc_node.text,
                slide_index=doc_node.slide_index,
                shape_id=doc_node.shape_id,
                shape_name=doc_node.shape_name,
                semantic_type=doc_node.semantic_role,
                semantic_value=doc_node.text,
            )
            
            graph.add_node(semantic_node)
        
        # Create edges for document structure
        for doc_node in document_tree.nodes.values():
            # CONTAINS relationship for parent-child
            if doc_node.parent_id:
                graph.add_edge(
                    doc_node.parent_id,
                    doc_node.element_id,
                    SemanticRelationType.CONTAINS,
                    confidence=1.0,
                    evidence=["Parent-child relationship in document tree"],
                )
            
            # NEXT/PREVIOUS relationships for siblings
            if doc_node.index_in_parent > 0:
                parent = document_tree.get_parent(doc_node.element_id)
                if parent and doc_node.index_in_parent - 1 < len(parent.children_ids):
                    prev_sibling_id = parent.children_ids[doc_node.index_in_parent - 1]
                    graph.add_edge(
                        prev_sibling_id,
                        doc_node.element_id,
                        SemanticRelationType.NEXT,
                        confidence=1.0,
                    )
        
        return graph


class SemanticQueryEngine:
    """Query engine for semantic graphs."""
    
    def __init__(self, graph: SemanticGraph):
        self.graph = graph
    
    def find_all_statuses(self) -> List[SemanticNode]:
        """Find all status-related nodes."""
        return self.graph.get_nodes_by_semantic_type("status")
    
    def find_owners(self, project_node_id: str) -> List[SemanticNode]:
        """Find owners of a project."""
        edges = self.graph.get_outgoing_edges(
            project_node_id,
            SemanticRelationType.HAS_OWNER,
        )
        
        return [self.graph.get_node(target_id) for target_id, _ in edges if self.graph.get_node(target_id)]
    
    def find_dependencies(self, node_id: str) -> List[SemanticNode]:
        """Find dependencies of a node."""
        edges = self.graph.get_outgoing_edges(
            node_id,
            SemanticRelationType.DEPENDS_ON,
        )
        
        return [self.graph.get_node(target_id) for target_id, _ in edges if self.graph.get_node(target_id)]
    
    def find_related_nodes(self, node_id: str, relation_type: Optional[SemanticRelationType] = None) -> List[SemanticNode]:
        """Find related nodes."""
        edges = self.graph.get_outgoing_edges(node_id, relation_type)
        return [self.graph.get_node(target_id) for target_id, _ in edges if self.graph.get_node(target_id)]
    
    def get_graph_summary(self) -> Dict[str, Any]:
        """Get a summary of the graph."""
        return {
            "total_nodes": len(self.graph.nodes),
            "nodes_by_type": {t.value: len(self.graph.get_nodes_by_type(t)) for t in SemanticNodeType},
            "total_edges": len(self.graph.edges),
            "concepts": sorted(self.graph.concept_index.keys()),
        }
