"""
Image and Diagram Parser

Recognizes and semantically understands images, timelines, SmartArt, diagrams, and other visual elements.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any
from pathlib import Path
from pptx.util import Inches
try:
    from PIL import Image
except ImportError:  # optional; simple-request fill does not need image analysis
    Image = None
import hashlib


class ImageType(Enum):
    """Types of images in presentations."""
    PHOTOGRAPH = "photograph"
    DIAGRAM = "diagram"
    CHART = "chart"
    SMARTART = "smartart"
    TIMELINE = "timeline"
    FLOWCHART = "flowchart"
    ICON = "icon"
    SCREENSHOT = "screenshot"
    LOGO = "logo"
    INFOGRAPHIC = "infographic"
    UNKNOWN = "unknown"


class DiagramType(Enum):
    """Types of diagrams."""
    FLOWCHART = "flowchart"
    MINDMAP = "mindmap"
    ORGANIZATIONAL = "organizational"
    SWIMLANE = "swimlane"
    VENN = "venn"
    PYRAMID = "pyramid"
    TIMELINE = "timeline"
    PROCESS = "process"
    CYCLE = "cycle"
    MATRIX = "matrix"


@dataclass
class ImageElement:
    """Represents an image or visual element in the presentation."""
    image_id: str
    slide_index: int
    shape_id: int
    shape_name: str
    
    # Image metadata
    image_type: ImageType = ImageType.UNKNOWN
    diagram_type: Optional[DiagramType] = None
    
    # Location and size
    left: int = 0  # EMUs
    top: int = 0   # EMUs
    width: int = 0  # EMUs
    height: int = 0  # EMUs
    
    # Content description
    caption: str = ""
    alt_text: str = ""
    title: str = ""
    description: str = ""
    
    # Image data
    image_data: Optional[bytes] = None
    image_hash: Optional[str] = None
    image_format: str = "png"  # png, jpg, gif, etc.
    
    # Semantic information
    labels: List[str] = field(default_factory=list)  # Text labels found in image
    objects_detected: List[Dict[str, Any]] = field(default_factory=list)  # Detected objects
    key_elements: List[str] = field(default_factory=list)  # Important elements
    
    # Relationships
    referenced_by: List[str] = field(default_factory=list)  # Node IDs referencing this
    references: List[str] = field(default_factory=list)  # Node IDs this references
    
    def compute_hash(self) -> None:
        """Compute hash of image data for deduplication."""
        if self.image_data:
            self.image_hash = hashlib.sha256(self.image_data).hexdigest()


@dataclass
class TimelineElement:
    """Represents a timeline in the presentation."""
    timeline_id: str
    slide_index: int
    
    # Timeline structure
    title: str = ""
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    
    # Milestones/events
    milestones: List[Dict[str, Any]] = field(default_factory=list)
    
    # Example milestone entry:
    # {
    #     "date": "2024-01-15",
    #     "title": "Project Kickoff",
    #     "description": "Team assembly and planning",
    #     "status": "completed",
    #     "position": 0.25,  # 0-1 relative position on timeline
    # }
    
    # Visual representation
    orientation: str = "horizontal"  # horizontal, vertical
    style: str = "linear"  # linear, circular


@dataclass
class DiagramElement:
    """Represents a diagram (flowchart, org chart, etc.)."""
    diagram_id: str
    slide_index: int
    
    diagram_type: DiagramType
    title: str = ""
    description: str = ""
    
    # Diagram nodes/shapes
    nodes: List[Dict[str, Any]] = field(default_factory=list)
    # Example node:
    # {
    #     "id": "node_1",
    #     "label": "Start",
    #     "shape": "ellipse",
    #     "color": "#FF0000",
    # }
    
    # Connections between nodes
    edges: List[Dict[str, Any]] = field(default_factory=list)
    # Example edge:
    # {
    #     "source": "node_1",
    #     "target": "node_2",
    #     "label": "Yes",
    # }


class ImageParser:
    """Parses and understands images in presentations."""
    
    # Common image type indicators
    DIAGRAM_KEYWORDS = {
        "flowchart": ["flow", "process", "decision", "arrow"],
        "timeline": ["timeline", "milestone", "date", "schedule"],
        "organizational": ["org", "hierarchy", "department", "manager"],
        "smartart": ["smartart", "layout", "hierarchy", "process"],
    }
    
    @staticmethod
    def detect_image_type(shape, caption: str = "") -> ImageType:
        """Detect the type of image."""
        caption_lower = caption.lower()
        
        # Check caption for keywords
        if any(word in caption_lower for word in ["timeline", "milestone", "schedule"]):
            return ImageType.TIMELINE
        
        if any(word in caption_lower for word in ["flowchart", "flow", "process", "diagram"]):
            return ImageType.FLOWCHART
        
        if any(word in caption_lower for word in ["smartart", "hierarchy", "org"]):
            return ImageType.SMARTART
        
        if any(word in caption_lower for word in ["screenshot", "screen", "interface"]):
            return ImageType.SCREENSHOT
        
        if any(word in caption_lower for word in ["icon", "symbol"]):
            return ImageType.ICON
        
        # Check shape properties
        if hasattr(shape, 'name'):
            name_lower = shape.name.lower()
            if "timeline" in name_lower:
                return ImageType.TIMELINE
            if "diagram" in name_lower or "flowchart" in name_lower:
                return ImageType.DIAGRAM
        
        # Default
        return ImageType.UNKNOWN
    
    @staticmethod
    def extract_image_metadata(shape, slide_index: int) -> Optional[ImageElement]:
        """Extract metadata from an image shape."""
        if not hasattr(shape, 'image'):
            return None
        
        try:
            image = shape.image
            image_bytes = image.blob
            
            # Get dimensions
            element = ImageElement(
                image_id=f"img_{getattr(shape, 'shape_id', 0)}",
                slide_index=slide_index,
                shape_id=getattr(shape, 'shape_id', 0),
                shape_name=getattr(shape, 'name', 'Image'),
                left=getattr(shape, 'left', 0),
                top=getattr(shape, 'top', 0),
                width=getattr(shape, 'width', 0),
                height=getattr(shape, 'height', 0),
                image_data=image_bytes,
                image_format=image.content_type.split('/')[-1] if hasattr(image, 'content_type') else 'png',
            )
            
            # Extract caption from shape text
            if hasattr(shape, 'text_frame') and shape.text_frame:
                element.caption = shape.text_frame.text
            
            # Detect image type
            element.image_type = ImageParser.detect_image_type(shape, element.caption)
            
            # Compute hash
            element.compute_hash()
            
            return element
        
        except Exception as e:
            print(f"Error extracting image metadata: {e}")
            return None
    
    @staticmethod
    def extract_timeline_from_shape(shape, slide_index: int) -> Optional[TimelineElement]:
        """Extract timeline information from a shape."""
        # This would require more sophisticated analysis
        # For now, create a basic structure
        
        timeline = TimelineElement(
            timeline_id=f"timeline_{getattr(shape, 'shape_id', 0)}",
            slide_index=slide_index,
        )
        
        if hasattr(shape, 'text_frame') and shape.text_frame:
            timeline.title = shape.text_frame.text[:50]
        
        return timeline
    
    @staticmethod
    def extract_diagram_from_shape(shape, slide_index: int, diagram_type: DiagramType) -> Optional[DiagramElement]:
        """Extract diagram information from a shape."""
        diagram = DiagramElement(
            diagram_id=f"diagram_{getattr(shape, 'shape_id', 0)}",
            slide_index=slide_index,
            diagram_type=diagram_type,
        )
        
        if hasattr(shape, 'text_frame') and shape.text_frame:
            diagram.title = shape.text_frame.text[:50]
        
        return diagram


class SmartArtParser:
    """Parses SmartArt graphics from presentations."""
    
    @staticmethod
    def is_smartart(shape) -> bool:
        """Check if a shape is SmartArt."""
        if not hasattr(shape, 'shape_type'):
            return False
        
        shape_type_name = str(shape.shape_type)
        return 'SMARTART' in shape_type_name.upper() or 'GROUPSHAPE' in shape_type_name.upper()
    
    @staticmethod
    def extract_smartart_structure(shape) -> Dict[str, Any]:
        """Extract the structure of a SmartArt graphic."""
        structure = {
            "type": "smartart",
            "children": [],
        }
        
        # SmartArt is typically a group shape
        if hasattr(shape, 'shapes'):
            for child_shape in shape.shapes:
                child_data = {
                    "name": getattr(child_shape, 'name', 'Unknown'),
                    "text": getattr(child_shape, 'text', ''),
                }
                if hasattr(child_shape, 'shapes'):
                    child_data["children"] = SmartArtParser.extract_smartart_structure(child_shape)
                structure["children"].append(child_data)
        
        return structure


class DiagramRelationshipExtractor:
    """Extracts relationships and connections from diagrams."""
    
    @staticmethod
    def extract_flow_connections(shapes: List[Any]) -> List[tuple]:
        """Extract flow connections between shapes based on position."""
        connections = []
        
        # Sort shapes by position (left to right, top to bottom)
        sorted_shapes = sorted(
            shapes,
            key=lambda s: (getattr(s, 'top', 0), getattr(s, 'left', 0))
        )
        
        # Find logical connections based on proximity and position
        for i, shape in enumerate(sorted_shapes):
            if i < len(sorted_shapes) - 1:
                next_shape = sorted_shapes[i + 1]
                
                # Check if shapes are likely connected
                shape_right = getattr(shape, 'left', 0) + getattr(shape, 'width', 0)
                next_left = getattr(next_shape, 'left', 0)
                
                # If next shape is to the right, likely connected
                if next_left > shape_right:
                    connections.append((shape, next_shape))
        
        return connections
