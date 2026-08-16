"""
PPT-AI Relationship Engine

Discovers spatial and semantic relationships between shapes on a slide
without relying on hardcoded layouts, slide numbers, or shape IDs.

Relationships detected:
- Proximity (nearest neighbors)
- Containment (shape inside another)
- Alignment (horizontal/vertical groupings)
- Label associations (text labeling other objects)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Optional
import math

if TYPE_CHECKING:
    from ..models import Shape, Slide


class RelationshipType(Enum):
    """Types of spatial/semantic relationships between shapes."""
    CONTAINS = auto()          # Shape A fully contains Shape B
    CONTAINED_BY = auto()      # Shape A is inside Shape B
    LEFT_OF = auto()           # Shape A is to the left of Shape B
    RIGHT_OF = auto()          # Shape A is to the right of Shape B
    ABOVE = auto()             # Shape A is above Shape B
    BELOW = auto()             # Shape A is below Shape B
    ALIGNED_HORIZONTAL = auto() # Shapes share a horizontal band
    ALIGNED_VERTICAL = auto()   # Shapes share a vertical band
    LABELS = auto()            # Shape A is a label for Shape B
    LABELED_BY = auto()        # Shape A is labeled by Shape B
    NEAREST_NEIGHBOR = auto()  # Shape B is the nearest to Shape A
    GROUPED_WITH = auto()      # Shapes appear to be in the same visual group


@dataclass
class Relationship:
    """A discovered relationship between two shapes."""
    source_id: str
    target_id: str
    relationship_type: RelationshipType
    confidence: float  # 0.0 to 1.0
    distance_emu: Optional[int] = None  # Distance in EMUs if applicable
    metadata: dict = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"Relationship({self.source_id} -> {self.target_id}: "
            f"{self.relationship_type.name}, conf={self.confidence:.2f})"
        )


@dataclass
class ShapeProxy:
    """
    Lightweight proxy for shape geometry calculations.
    Avoids tight coupling to the main Shape model.
    """
    shape_id: str
    name: str
    shape_type: str
    left: int    # EMUs
    top: int     # EMUs
    width: int   # EMUs
    height: int  # EMUs
    text: str = ""
    has_table: bool = False
    has_chart: bool = False
    has_picture: bool = False

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def center_x(self) -> int:
        return self.left + self.width // 2

    @property
    def center_y(self) -> int:
        return self.top + self.height // 2

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def is_text_only(self) -> bool:
        """Shape contains only text, no table/chart/picture."""
        return (
            bool(self.text.strip())
            and not self.has_table
            and not self.has_chart
            and not self.has_picture
        )

    @property
    def is_small_text(self) -> bool:
        """Likely a label: small shape with short text."""
        # Heuristic: less than 50 characters and small area
        max_label_chars = 50
        max_label_area = 2_000_000 * 2_000_000  # ~2 inches x 2 inches in EMUs²
        return (
            self.is_text_only
            and len(self.text.strip()) <= max_label_chars
            and self.area <= max_label_area
        )

    @classmethod
    def from_shape(cls, shape: "Shape") -> "ShapeProxy":
        """Create proxy from a Shape model instance."""
        text = ""
        if hasattr(shape, "text_content") and shape.text_content:
            # Concatenate all paragraph text
            text = " ".join(
                p.get("text", "") for p in shape.text_content.get("paragraphs", [])
            )

        return cls(
            shape_id=shape.shape_id,
            name=getattr(shape, "name", ""),
            shape_type=getattr(shape, "shape_type", "UNKNOWN"),
            left=shape.geometry.left if shape.geometry else 0,
            top=shape.geometry.top if shape.geometry else 0,
            width=shape.geometry.width if shape.geometry else 0,
            height=shape.geometry.height if shape.geometry else 0,
            text=text,
            has_table=getattr(shape, "table_data", None) is not None,
            has_chart=getattr(shape, "has_chart", False),
            has_picture=getattr(shape, "has_picture", False),
        )


class RelationshipEngine:
    """
    Discovers relationships between shapes on a slide.
    
    Usage:
        engine = RelationshipEngine()
        relationships = engine.analyze_slide(slide)
        
        # Find what labels a specific shape
        labels = engine.find_labels_for(shape_id, relationships)
        
        # Find nearest neighbors
        neighbors = engine.find_nearest(shape_id, relationships, k=3)
    """

    # Thresholds (in EMUs; 914400 EMU = 1 inch)
    EMU_PER_INCH = 914400
    PROXIMITY_THRESHOLD = EMU_PER_INCH * 0.5  # Half inch
    ALIGNMENT_TOLERANCE = EMU_PER_INCH * 0.1  # 0.1 inch tolerance for alignment
    LABEL_MAX_DISTANCE = EMU_PER_INCH * 0.75  # Labels within 0.75 inch

    def __init__(
        self,
        proximity_threshold: Optional[int] = None,
        alignment_tolerance: Optional[int] = None,
        label_max_distance: Optional[int] = None,
    ):
        """
        Initialize the relationship engine with optional custom thresholds.

        Args:
            proximity_threshold: Max distance (EMU) to consider shapes "nearby"
            alignment_tolerance: Tolerance (EMU) for alignment detection
            label_max_distance: Max distance (EMU) for label association
        """
        self.proximity_threshold = proximity_threshold or self.PROXIMITY_THRESHOLD
        self.alignment_tolerance = alignment_tolerance or self.ALIGNMENT_TOLERANCE
        self.label_max_distance = label_max_distance or self.LABEL_MAX_DISTANCE

    def analyze_slide(self, slide: "Slide") -> list[Relationship]:
        """
        Analyze all shapes on a slide and discover relationships.

        Args:
            slide: A Slide model instance

        Returns:
            List of discovered Relationship objects
        """
        # Convert shapes to proxies
        proxies = [ShapeProxy.from_shape(s) for s in slide.shapes if s.geometry]

        if len(proxies) < 2:
            return []

        relationships: list[Relationship] = []

        # Run all relationship detectors
        relationships.extend(self._detect_containment(proxies))
        relationships.extend(self._detect_directional(proxies))
        relationships.extend(self._detect_alignment(proxies))
        relationships.extend(self._detect_labels(proxies))
        relationships.extend(self._detect_nearest_neighbors(proxies))
        relationships.extend(self._detect_groups(proxies))

        return relationships

    def _detect_containment(self, proxies: list[ShapeProxy]) -> list[Relationship]:
        """Detect shapes that contain or are contained by other shapes."""
        relationships = []

        for a in proxies:
            for b in proxies:
                if a.shape_id == b.shape_id:
                    continue

                # Check if A contains B
                if self._contains(a, b):
                    confidence = self._containment_confidence(a, b)
                    relationships.append(
                        Relationship(
                            source_id=a.shape_id,
                            target_id=b.shape_id,
                            relationship_type=RelationshipType.CONTAINS,
                            confidence=confidence,
                            metadata={"container_area": a.area, "contained_area": b.area},
                        )
                    )
                    relationships.append(
                        Relationship(
                            source_id=b.shape_id,
                            target_id=a.shape_id,
                            relationship_type=RelationshipType.CONTAINED_BY,
                            confidence=confidence,
                        )
                    )

        return relationships

    def _contains(self, outer: ShapeProxy, inner: ShapeProxy) -> bool:
        """Check if outer shape fully contains inner shape."""
        return (
            outer.left <= inner.left
            and outer.top <= inner.top
            and outer.right >= inner.right
            and outer.bottom >= inner.bottom
            and outer.area > inner.area  # Outer must be larger
        )

    def _containment_confidence(self, outer: ShapeProxy, inner: ShapeProxy) -> float:
        """
        Calculate confidence for containment relationship.
        Higher if inner is well-centered and proportionally sized.
        """
        # How centered is the inner shape?
        outer_cx, outer_cy = outer.center_x, outer.center_y
        inner_cx, inner_cy = inner.center_x, inner.center_y

        max_offset = max(outer.width, outer.height) / 2
        offset = math.sqrt((outer_cx - inner_cx) ** 2 + (outer_cy - inner_cy) ** 2)
        centering_score = max(0, 1 - (offset / max_offset)) if max_offset > 0 else 0.5

        # Size ratio (inner shouldn't be too small or too large relative to outer)
        ratio = inner.area / outer.area if outer.area > 0 else 0
        # Ideal ratio is between 0.1 and 0.8
        if 0.1 <= ratio <= 0.8:
            size_score = 1.0
        elif ratio < 0.1:
            size_score = ratio / 0.1
        else:
            size_score = max(0, 1 - (ratio - 0.8) / 0.2)

        return (centering_score + size_score) / 2

    def _detect_directional(self, proxies: list[ShapeProxy]) -> list[Relationship]:
        """Detect left/right/above/below relationships for nearby shapes."""
        relationships = []

        for a in proxies:
            for b in proxies:
                if a.shape_id == b.shape_id:
                    continue

                distance = self._edge_distance(a, b)
                if distance > self.proximity_threshold:
                    continue

                direction = self._primary_direction(a, b)
                if direction is None:
                    continue

                confidence = self._directional_confidence(a, b, distance)
                relationships.append(
                    Relationship(
                        source_id=a.shape_id,
                        target_id=b.shape_id,
                        relationship_type=direction,
                        confidence=confidence,
                        distance_emu=int(distance),
                    )
                )

        return relationships

    def _primary_direction(
        self, source: ShapeProxy, target: ShapeProxy
    ) -> Optional[RelationshipType]:
        """Determine the primary directional relationship from source to target."""
        dx = target.center_x - source.center_x
        dy = target.center_y - source.center_y

        # Determine if horizontal or vertical dominates
        if abs(dx) > abs(dy):
            return RelationshipType.LEFT_OF if dx < 0 else RelationshipType.RIGHT_OF
        elif abs(dy) > abs(dx):
            return RelationshipType.ABOVE if dy < 0 else RelationshipType.BELOW

        return None  # Shapes are at the same position

    def _directional_confidence(
        self, a: ShapeProxy, b: ShapeProxy, distance: float
    ) -> float:
        """Calculate confidence for directional relationship."""
        # Closer shapes get higher confidence
        max_dist = self.proximity_threshold
        proximity_score = max(0, 1 - (distance / max_dist)) if max_dist > 0 else 0.5

        # Check alignment — shapes that align well get bonus confidence
        h_overlap = self._horizontal_overlap(a, b)
        v_overlap = self._vertical_overlap(a, b)
        alignment_score = max(h_overlap, v_overlap)

        return 0.6 * proximity_score + 0.4 * alignment_score

    def _detect_alignment(self, proxies: list[ShapeProxy]) -> list[Relationship]:
        """Detect shapes that are horizontally or vertically aligned."""
        relationships = []
        tolerance = self.alignment_tolerance

        for i, a in enumerate(proxies):
            for b in proxies[i + 1 :]:
                # Check horizontal alignment (same vertical band)
                if abs(a.center_y - b.center_y) <= tolerance:
                    confidence = 1.0 - (
                        abs(a.center_y - b.center_y) / tolerance
                        if tolerance > 0
                        else 0
                    )
                    relationships.append(
                        Relationship(
                            source_id=a.shape_id,
                            target_id=b.shape_id,
                            relationship_type=RelationshipType.ALIGNED_HORIZONTAL,
                            confidence=confidence,
                        )
                    )

                # Check vertical alignment (same horizontal band)
                if abs(a.center_x - b.center_x) <= tolerance:
                    confidence = 1.0 - (
                        abs(a.center_x - b.center_x) / tolerance
                        if tolerance > 0
                        else 0
                    )
                    relationships.append(
                        Relationship(
                            source_id=a.shape_id,
                            target_id=b.shape_id,
                            relationship_type=RelationshipType.ALIGNED_VERTICAL,
                            confidence=confidence,
                        )
                    )

        return relationships

    def _detect_labels(self, proxies: list[ShapeProxy]) -> list[Relationship]:
        """
        Detect label-to-target relationships.
        A label is typically a small text shape near a larger non-text shape.
        """
        relationships = []

        # Identify potential labels (small text shapes)
        potential_labels = [p for p in proxies if p.is_small_text]

        # Identify potential targets (larger shapes, tables, charts, etc.)
        potential_targets = [
            p
            for p in proxies
            if p.has_table or p.has_chart or p.has_picture or p.area > 1_000_000_000_000
        ]

        for label in potential_labels:
            best_target: Optional[ShapeProxy] = None
            best_distance = float("inf")

            for target in potential_targets:
                if label.shape_id == target.shape_id:
                    continue

                distance = self._edge_distance(label, target)
                if distance < best_distance and distance <= self.label_max_distance:
                    best_distance = distance
                    best_target = target

            if best_target is not None:
                confidence = self._label_confidence(label, best_target, best_distance)
                relationships.append(
                    Relationship(
                        source_id=label.shape_id,
                        target_id=best_target.shape_id,
                        relationship_type=RelationshipType.LABELS,
                        confidence=confidence,
                        distance_emu=int(best_distance),
                        metadata={"label_text": label.text.strip()},
                    )
                )
                relationships.append(
                    Relationship(
                        source_id=best_target.shape_id,
                        target_id=label.shape_id,
                        relationship_type=RelationshipType.LABELED_BY,
                        confidence=confidence,
                        metadata={"label_text": label.text.strip()},
                    )
                )

        return relationships

    def _label_confidence(
        self, label: ShapeProxy, target: ShapeProxy, distance: float
    ) -> float:
        """Calculate confidence that label is actually labeling target."""
        # Distance factor
        dist_score = max(0, 1 - (distance / self.label_max_distance))

        # Position factor: labels are often above or to the left
        direction = self._primary_direction(label, target)
        position_score = 0.5
        if direction in (RelationshipType.ABOVE, RelationshipType.LEFT_OF):
            position_score = 0.8
        elif direction == RelationshipType.RIGHT_OF:
            position_score = 0.3  # Labels on the right are less common

        # Alignment factor
        h_overlap = self._horizontal_overlap(label, target)
        v_overlap = self._vertical_overlap(label, target)
        alignment_score = max(h_overlap, v_overlap)

        return 0.4 * dist_score + 0.3 * position_score + 0.3 * alignment_score

    def _detect_nearest_neighbors(
        self, proxies: list[ShapeProxy]
    ) -> list[Relationship]:
        """Find the nearest neighbor for each shape."""
        relationships = []

        for a in proxies:
            nearest: Optional[ShapeProxy] = None
            min_distance = float("inf")

            for b in proxies:
                if a.shape_id == b.shape_id:
                    continue

                distance = self._center_distance(a, b)
                if distance < min_distance:
                    min_distance = distance
                    nearest = b

            if nearest is not None:
                relationships.append(
                    Relationship(
                        source_id=a.shape_id,
                        target_id=nearest.shape_id,
                        relationship_type=RelationshipType.NEAREST_NEIGHBOR,
                        confidence=1.0,
                        distance_emu=int(min_distance),
                    )
                )

        return relationships

    def _detect_groups(self, proxies: list[ShapeProxy]) -> list[Relationship]:
        """
        Detect shapes that appear to be visually grouped together.
        Uses a simple clustering approach based on proximity and alignment.
        """
        relationships = []

        # Build adjacency based on proximity
        groups: list[set[str]] = []

        for i, a in enumerate(proxies):
            for b in proxies[i + 1 :]:
                distance = self._edge_distance(a, b)
                if distance <= self.proximity_threshold:
                    # Check if either shape is already in a group
                    a_group = None
                    b_group = None

                    for g in groups:
                        if a.shape_id in g:
                            a_group = g
                        if b.shape_id in g:
                            b_group = g

                    if a_group is None and b_group is None:
                        # Create new group
                        groups.append({a.shape_id, b.shape_id})
                    elif a_group is not None and b_group is None:
                        a_group.add(b.shape_id)
                    elif a_group is None and b_group is not None:
                        b_group.add(a.shape_id)
                    elif a_group is not b_group:
                        # Merge groups
                        a_group.update(b_group)
                        groups.remove(b_group)

        # Create relationships for groups with more than 2 members
        for group in groups:
            if len(group) >= 2:
                shape_ids = list(group)
                for i, sid in enumerate(shape_ids):
                    for other_sid in shape_ids[i + 1 :]:
                        relationships.append(
                            Relationship(
                                source_id=sid,
                                target_id=other_sid,
                                relationship_type=RelationshipType.GROUPED_WITH,
                                confidence=0.7,
                                metadata={"group_size": len(group)},
                            )
                        )

        return relationships

    # -------------------------------------------------------------------------
    # Geometry Helper Methods
    # -------------------------------------------------------------------------

    def _edge_distance(self, a: ShapeProxy, b: ShapeProxy) -> float:
        """
        Calculate the minimum distance between the edges of two shapes.
        Returns 0 if shapes overlap.
        """
        # Horizontal gap
        if a.right < b.left:
            h_gap = b.left - a.right
        elif b.right < a.left:
            h_gap = a.left - b.right
        else:
            h_gap = 0  # Overlapping horizontally

        # Vertical gap
        if a.bottom < b.top:
            v_gap = b.top - a.bottom
        elif b.bottom < a.top:
            v_gap = a.top - b.bottom
        else:
            v_gap = 0  # Overlapping vertically

        # If both gaps are 0, shapes overlap
        if h_gap == 0 and v_gap == 0:
            return 0.0

        # If only one gap is 0, return the other
        if h_gap == 0:
            return float(v_gap)
        if v_gap == 0:
            return float(h_gap)

        # Both gaps exist: diagonal distance
        return math.sqrt(h_gap**2 + v_gap**2)

    def _center_distance(self, a: ShapeProxy, b: ShapeProxy) -> float:
        """Calculate distance between shape centers."""
        dx = a.center_x - b.center_x
        dy = a.center_y - b.center_y
        return math.sqrt(dx**2 + dy**2)

    def _horizontal_overlap(self, a: ShapeProxy, b: ShapeProxy) -> float:
        """
        Calculate horizontal overlap ratio (0 to 1).
        1.0 means one shape is entirely within the horizontal span of the other.
        """
        overlap_left = max(a.left, b.left)
        overlap_right = min(a.right, b.right)
        overlap_width = max(0, overlap_right - overlap_left)

        min_width = min(a.width, b.width)
        if min_width == 0:
            return 0.0

        return overlap_width / min_width

    def _vertical_overlap(self, a: ShapeProxy, b: ShapeProxy) -> float:
        """
        Calculate vertical overlap ratio (0 to 1).
        1.0 means one shape is entirely within the vertical span of the other.
        """
        overlap_top = max(a.top, b.top)
        overlap_bottom = min(a.bottom, b.bottom)
        overlap_height = max(0, overlap_bottom - overlap_top)

        min_height = min(a.height, b.height)
        if min_height == 0:
            return 0.0

        return overlap_height / min_height

    # -------------------------------------------------------------------------
    # Query Methods
    # -------------------------------------------------------------------------

    def filter_by_type(
        self, relationships: list[Relationship], rel_type: RelationshipType
    ) -> list[Relationship]:
        """Filter relationships by type."""
        return [r for r in relationships if r.relationship_type == rel_type]

    def find_related(
        self, shape_id: str, relationships: list[Relationship]
    ) -> list[Relationship]:
        """Find all relationships involving a shape (as source or target)."""
        return [
            r for r in relationships if r.source_id == shape_id or r.target_id == shape_id
        ]

    def find_labels_for(
        self, shape_id: str, relationships: list[Relationship]
    ) -> list[Relationship]:
        """Find all shapes that label a given shape."""
        return [
            r
            for r in relationships
            if r.target_id == shape_id and r.relationship_type == RelationshipType.LABELS
        ]

    def find_nearest(
        self, shape_id: str, relationships: list[Relationship], k: int = 1
    ) -> list[Relationship]:
        """Find k nearest neighbors of a shape based on distance."""
        related = [r for r in relationships if r.source_id == shape_id and r.distance_emu is not None]
        related.sort(key=lambda r: r.distance_emu or float("inf"))
        return related[:k]

    def get_relationship_graph(
        self, relationships: list[Relationship]
    ) -> dict[str, list[dict]]:
        """
        Build an adjacency-list representation of the relationship graph.
        
        Returns:
            Dict mapping shape_id to list of {"target": id, "type": str, "confidence": float}
        """
        graph: dict[str, list[dict]] = {}

        for r in relationships:
            if r.source_id not in graph:
                graph[r.source_id] = []

            graph[r.source_id].append({
                "target": r.target_id,
                "type": r.relationship_type.name,
                "confidence": r.confidence,
                "distance": r.distance_emu,
            })

        return graph
