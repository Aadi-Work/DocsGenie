"""
Version Manager and Audit System

Tracks all modifications to presentations with full traceability.
Supports rollback, version comparison, and complete audit trails.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any
from datetime import datetime
from pathlib import Path
import json
from uuid import uuid4


class ModificationType(Enum):
    """Types of modifications."""
    TEXT_UPDATE = "text_update"
    FORMATTING_UPDATE = "formatting_update"
    CONTENT_INSERTION = "content_insertion"
    CONTENT_DELETION = "content_deletion"
    TABLE_CELL_UPDATE = "table_cell_update"
    TABLE_ROW_INSERT = "table_row_insert"
    TABLE_ROW_DELETE = "table_row_delete"
    ELEMENT_MOVE = "element_move"
    ELEMENT_DELETE = "element_delete"
    IMAGE_INSERT = "image_insert"
    IMAGE_DELETE = "image_delete"


@dataclass
class AuditEntry:
    """A single audit trail entry."""
    entry_id: str
    timestamp: datetime
    modification_type: ModificationType
    
    # Object being modified
    object_type: str  # "text", "table", "image", etc.
    object_id: str
    object_path: str  # e.g., "Slide 2 > Current Status > Risk Item 1"
    
    # Changes
    previous_value: Any = None
    new_value: Any = None
    
    # Metadata
    user: str = "system"
    session_id: str = ""
    change_description: str = ""
    
    # Reference to operation
    operation_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "entry_id": self.entry_id,
            "timestamp": self.timestamp.isoformat(),
            "modification_type": self.modification_type.value,
            "object_type": self.object_type,
            "object_id": self.object_id,
            "object_path": self.object_path,
            "previous_value": str(self.previous_value)[:200] if self.previous_value else None,
            "new_value": str(self.new_value)[:200] if self.new_value else None,
            "user": self.user,
            "session_id": self.session_id,
            "change_description": self.change_description,
            "operation_id": self.operation_id,
        }


@dataclass
class Version:
    """Represents a version of a presentation."""
    version_id: str
    version_number: int
    
    # Metadata
    created_at: datetime
    parent_version_id: Optional[str] = None
    
    # Content
    presentation_data: Dict[str, Any] = field(default_factory=dict)
    file_path: Optional[str] = None
    file_hash: Optional[str] = None
    
    # Changes from parent
    audit_entries: List[AuditEntry] = field(default_factory=list)
    
    # Description
    description: str = ""
    user: str = "system"
    tags: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "version_id": self.version_id,
            "version_number": self.version_number,
            "created_at": self.created_at.isoformat(),
            "parent_version_id": self.parent_version_id,
            "description": self.description,
            "user": self.user,
            "tags": self.tags,
            "num_changes": len(self.audit_entries),
            "audit_entries": [entry.to_dict() for entry in self.audit_entries],
        }


class VersionManager:
    """Manages versions and audit trails for presentations."""
    
    def __init__(self, workspace_dir: str = ".ppt_versions"):
        self.workspace_dir = Path(workspace_dir)
        self.workspace_dir.mkdir(exist_ok=True)
        
        self.versions: Dict[str, Version] = {}
        self.current_version_id: Optional[str] = None
        self.audit_log: List[AuditEntry] = []
    
    def create_version(
        self,
        description: str = "",
        user: str = "system",
        tags: Optional[List[str]] = None,
    ) -> Version:
        """Create a new version."""
        version_number = len(self.versions) + 1
        version_id = f"v{version_number}_{uuid4().hex[:8]}"
        
        parent_id = self.current_version_id
        
        version = Version(
            version_id=version_id,
            version_number=version_number,
            created_at=datetime.now(),
            parent_version_id=parent_id,
            description=description,
            user=user,
            tags=tags or [],
        )
        
        self.versions[version_id] = version
        self.current_version_id = version_id
        
        return version
    
    def record_audit_entry(
        self,
        modification_type: ModificationType,
        object_type: str,
        object_id: str,
        object_path: str,
        previous_value: Any = None,
        new_value: Any = None,
        user: str = "system",
        change_description: str = "",
        operation_id: Optional[str] = None,
    ) -> AuditEntry:
        """Record an audit entry."""
        entry_id = f"audit_{uuid4().hex[:8]}"
        
        entry = AuditEntry(
            entry_id=entry_id,
            timestamp=datetime.now(),
            modification_type=modification_type,
            object_type=object_type,
            object_id=object_id,
            object_path=object_path,
            previous_value=previous_value,
            new_value=new_value,
            user=user,
            session_id=self.current_version_id or "",
            change_description=change_description,
            operation_id=operation_id,
        )
        
        self.audit_log.append(entry)
        
        # Add to current version
        if self.current_version_id and self.current_version_id in self.versions:
            self.versions[self.current_version_id].audit_entries.append(entry)
        
        return entry
    
    def get_version(self, version_id: str) -> Optional[Version]:
        """Get a version by ID."""
        return self.versions.get(version_id)
    
    def get_current_version(self) -> Optional[Version]:
        """Get the current version."""
        if self.current_version_id:
            return self.versions.get(self.current_version_id)
        return None
    
    def list_versions(self) -> List[Version]:
        """List all versions in order."""
        return sorted(self.versions.values(), key=lambda v: v.version_number)
    
    def get_version_history(self, start_version: Optional[str] = None) -> List[Version]:
        """Get version history from a version to current."""
        if not start_version or start_version not in self.versions:
            start_version = self.current_version_id
        
        history = []
        current = self.versions.get(start_version)
        
        while current:
            history.insert(0, current)
            current = self.versions.get(current.parent_version_id) if current.parent_version_id else None
        
        return history
    
    def get_changes_between_versions(
        self,
        version_a_id: str,
        version_b_id: str,
    ) -> Dict[str, Any]:
        """Get all changes between two versions."""
        version_a = self.versions.get(version_a_id)
        version_b = self.versions.get(version_b_id)
        
        if not version_a or not version_b:
            return {}
        
        # Get all entries in version_b's history that are not in version_a's history
        history_b = {v.version_id: v for v in [version_b] + [
            self.versions.get(pid) for pid in self._get_parent_chain(version_b)
        ] if v}
        
        changes = []
        for version in [version_b]:
            for entry in version.audit_entries:
                changes.append(entry.to_dict())
        
        return {
            "from_version": version_a_id,
            "to_version": version_b_id,
            "num_changes": len(changes),
            "changes": changes,
        }
    
    def _get_parent_chain(self, version: Version) -> List[str]:
        """Get chain of parent version IDs."""
        chain = []
        current = version
        
        while current.parent_version_id:
            chain.append(current.parent_version_id)
            current = self.versions.get(current.parent_version_id)
            if not current:
                break
        
        return chain
    
    def save_version_metadata(self, output_file: str) -> None:
        """Save version metadata to a JSON file."""
        metadata = {
            "current_version_id": self.current_version_id,
            "versions": [v.to_dict() for v in self.list_versions()],
            "total_audit_entries": len(self.audit_log),
        }
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2)
    
    def load_version_metadata(self, input_file: str) -> None:
        """Load version metadata from a JSON file."""
        with open(input_file, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
        
        self.current_version_id = metadata.get("current_version_id")
        # Could restore versions here if needed


class AuditLog:
    """Manages the audit log for presentations."""
    
    def __init__(self, max_entries: int = 10000):
        self.entries: List[AuditEntry] = []
        self.max_entries = max_entries
        self.index_by_object: Dict[str, List[AuditEntry]] = {}
    
    def add_entry(self, entry: AuditEntry) -> None:
        """Add an entry to the log."""
        self.entries.append(entry)
        
        # Index by object ID
        if entry.object_id not in self.index_by_object:
            self.index_by_object[entry.object_id] = []
        self.index_by_object[entry.object_id].append(entry)
        
        # Trim if necessary
        if len(self.entries) > self.max_entries:
            self.entries = self.entries[-self.max_entries:]
    
    def get_entries_for_object(self, object_id: str) -> List[AuditEntry]:
        """Get all audit entries for a specific object."""
        return self.index_by_object.get(object_id, [])
    
    def get_entries_by_type(self, modification_type: ModificationType) -> List[AuditEntry]:
        """Get entries by modification type."""
        return [e for e in self.entries if e.modification_type == modification_type]
    
    def get_entries_since(self, timestamp: datetime) -> List[AuditEntry]:
        """Get entries since a specific timestamp."""
        return [e for e in self.entries if e.timestamp >= timestamp]
    
    def get_entries_by_user(self, user: str) -> List[AuditEntry]:
        """Get entries by user."""
        return [e for e in self.entries if e.user == user]
    
    def export_to_csv(self, output_file: str) -> None:
        """Export audit log to CSV."""
        import csv
        
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            # Header
            writer.writerow([
                "Entry ID",
                "Timestamp",
                "Type",
                "Object Type",
                "Object ID",
                "Object Path",
                "Previous Value",
                "New Value",
                "User",
                "Description",
            ])
            
            # Entries
            for entry in self.entries:
                writer.writerow([
                    entry.entry_id,
                    entry.timestamp.isoformat(),
                    entry.modification_type.value,
                    entry.object_type,
                    entry.object_id,
                    entry.object_path,
                    str(entry.previous_value)[:100] if entry.previous_value else "",
                    str(entry.new_value)[:100] if entry.new_value else "",
                    entry.user,
                    entry.change_description,
                ])
    
    def export_to_json(self, output_file: str) -> None:
        """Export audit log to JSON."""
        data = {
            "num_entries": len(self.entries),
            "entries": [entry.to_dict() for entry in self.entries],
        }
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
