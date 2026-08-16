"""
Change Planning Engine

Generates, validates, and detects conflicts in proposed updates before execution.
Makes updates safer, easier to debug, and allows users to preview changes.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional, Set
from datetime import datetime
from uuid import uuid4


class OperationType(Enum):
    """Types of update operations."""
    UPDATE_TEXT = "update_text"
    INSERT_TEXT = "insert_text"
    DELETE_TEXT = "delete_text"
    APPEND_TEXT = "append_text"
    UPDATE_TABLE_CELL = "update_table_cell"
    INSERT_TABLE_ROW = "insert_table_row"
    DELETE_TABLE_ROW = "delete_table_row"
    UPDATE_FORMATTING = "update_formatting"
    MOVE_ELEMENT = "move_element"
    DELETE_ELEMENT = "delete_element"
    INSERT_ELEMENT = "insert_element"


class ConflictType(Enum):
    """Types of conflicts that can occur."""
    OVERLAPPING_UPDATES = "overlapping_updates"
    REFERENCE_MISMATCH = "reference_mismatch"
    STRUCTURAL_CHANGE = "structural_change"
    FORMATTING_CONFLICT = "formatting_conflict"
    ORDERING_CONFLICT = "ordering_conflict"
    CIRCULAR_DEPENDENCY = "circular_dependency"


@dataclass
class UpdateOperation:
    """Represents a single update operation."""
    operation_id: str
    operation_type: OperationType
    target_id: str  # Node ID or cell address
    target_path: str  # Human-readable path (e.g., "Current Status > Risk Item 2")
    
    # Content changes
    old_value: Any = None
    new_value: Any = None
    
    # Metadata
    timestamp: datetime = field(default_factory=datetime.now)
    description: str = ""
    priority: int = 0  # 0=normal, 1=high, -1=low
    depends_on: List[str] = field(default_factory=list)  # IDs of dependent operations
    tags: List[str] = field(default_factory=list)
    
    # Validation info
    validated: bool = False
    validation_errors: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "operation_id": self.operation_id,
            "operation_type": self.operation_type.value,
            "target_id": self.target_id,
            "target_path": self.target_path,
            "old_value": str(self.old_value)[:100],
            "new_value": str(self.new_value)[:100],
            "timestamp": self.timestamp.isoformat(),
            "description": self.description,
            "priority": self.priority,
            "depends_on": self.depends_on,
            "tags": self.tags,
            "validated": self.validated,
            "validation_errors": self.validation_errors,
        }


@dataclass
class ConflictInfo:
    """Represents a conflict between operations."""
    conflict_id: str
    conflict_type: ConflictType
    operations_involved: List[str]  # IDs of conflicting operations
    severity: str  # "critical", "warning", "info"
    description: str
    resolution_suggestion: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "conflict_id": self.conflict_id,
            "conflict_type": self.conflict_type.value,
            "operations_involved": self.operations_involved,
            "severity": self.severity,
            "description": self.description,
            "resolution_suggestion": self.resolution_suggestion,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class UpdatePlan:
    """A plan consisting of multiple update operations."""
    plan_id: str
    name: str
    description: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    
    operations: List[UpdateOperation] = field(default_factory=list)
    conflicts: List[ConflictInfo] = field(default_factory=list)
    
    # Execution status
    status: str = "draft"  # draft, validated, executing, completed, failed, cancelled
    execution_started: Optional[datetime] = None
    execution_completed: Optional[datetime] = None
    
    # Execution results
    successful_operations: List[str] = field(default_factory=list)
    failed_operations: List[str] = field(default_factory=list)
    skipped_operations: List[str] = field(default_factory=list)
    execution_errors: Dict[str, str] = field(default_factory=dict)
    
    def add_operation(self, operation: UpdateOperation) -> None:
        """Add an operation to the plan."""
        self.operations.append(operation)
    
    def remove_operation(self, operation_id: str) -> bool:
        """Remove an operation from the plan."""
        for i, op in enumerate(self.operations):
            if op.operation_id == operation_id:
                self.operations.pop(i)
                return True
        return False
    
    def get_operation(self, operation_id: str) -> Optional[UpdateOperation]:
        """Get an operation by ID."""
        for op in self.operations:
            if op.operation_id == operation_id:
                return op
        return None
    
    def add_conflict(self, conflict: ConflictInfo) -> None:
        """Add a detected conflict."""
        self.conflicts.append(conflict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "plan_id": self.plan_id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "status": self.status,
            "num_operations": len(self.operations),
            "num_conflicts": len(self.conflicts),
            "num_successful": len(self.successful_operations),
            "num_failed": len(self.failed_operations),
            "operations": [op.to_dict() for op in self.operations],
            "conflicts": [c.to_dict() for c in self.conflicts],
        }


class ConflictDetector:
    """Detects conflicts between update operations."""
    
    @staticmethod
    def detect_overlapping_updates(operations: List[UpdateOperation]) -> List[ConflictInfo]:
        """Detect operations targeting the same element."""
        conflicts = []
        target_map: Dict[str, List[str]] = {}
        
        for op in operations:
            if op.target_id not in target_map:
                target_map[op.target_id] = []
            target_map[op.target_id].append(op.operation_id)
        
        for target_id, op_ids in target_map.items():
            if len(op_ids) > 1:
                # Check if operations are compatible
                ops = [op for op in operations if op.operation_id in op_ids]
                
                # Multiple text updates on same target = conflict
                text_ops = [op for op in ops if op.operation_type in [
                    OperationType.UPDATE_TEXT,
                    OperationType.DELETE_TEXT,
                    OperationType.UPDATE_FORMATTING,
                ]]
                # A dependent pair is an intentional sequential edit of the
                # same semantic node, not an overlapping conflict.
                chained = all(
                    any(previous.operation_id in op.depends_on for previous in text_ops[:index])
                    for index, op in enumerate(text_ops[1:], start=1)
                )
                if len(text_ops) > 1 and not chained:
                    conflict = ConflictInfo(
                        conflict_id=f"overlap_{uuid4().hex[:8]}",
                        conflict_type=ConflictType.OVERLAPPING_UPDATES,
                        operations_involved=op_ids,
                        severity="critical",
                        description=f"Multiple operations target the same element: {target_id}",
                        resolution_suggestion="Review and consolidate operations targeting the same element.",
                    )
                    conflicts.append(conflict)
        
        return conflicts
    
    @staticmethod
    def detect_ordering_conflicts(operations: List[UpdateOperation]) -> List[ConflictInfo]:
        """Detect operations with ordering dependencies."""
        conflicts = []
        
        # Build dependency graph
        op_map = {op.operation_id: op for op in operations}
        
        for op in operations:
            for dep_id in op.depends_on:
                if dep_id not in op_map:
                    conflict = ConflictInfo(
                        conflict_id=f"missing_dep_{uuid4().hex[:8]}",
                        conflict_type=ConflictType.CIRCULAR_DEPENDENCY,
                        operations_involved=[op.operation_id],
                        severity="critical",
                        description=f"Operation {op.operation_id} depends on missing operation {dep_id}",
                        resolution_suggestion="Add missing dependent operation or remove dependency.",
                    )
                    conflicts.append(conflict)
        
        # Detect circular dependencies
        visited: Set[str] = set()
        rec_stack: Set[str] = set()
        
        def has_cycle(op_id: str) -> bool:
            visited.add(op_id)
            rec_stack.add(op_id)
            
            op = op_map.get(op_id)
            if not op:
                return False
            
            for dep_id in op.depends_on:
                if dep_id not in visited:
                    if has_cycle(dep_id):
                        return True
                elif dep_id in rec_stack:
                    return True
            
            rec_stack.remove(op_id)
            return False
        
        for op in operations:
            if op.operation_id not in visited:
                if has_cycle(op.operation_id):
                    conflict = ConflictInfo(
                        conflict_id=f"cycle_{uuid4().hex[:8]}",
                        conflict_type=ConflictType.CIRCULAR_DEPENDENCY,
                        operations_involved=[op.operation_id],
                        severity="critical",
                        description=f"Circular dependency detected in operation {op.operation_id}",
                        resolution_suggestion="Review dependencies and remove circular references.",
                    )
                    conflicts.append(conflict)
        
        return conflicts
    
    @staticmethod
    def detect_all_conflicts(plan: UpdatePlan) -> List[ConflictInfo]:
        """Detect all conflicts in a plan."""
        all_conflicts = []
        
        # Run all detectors
        all_conflicts.extend(ConflictDetector.detect_overlapping_updates(plan.operations))
        all_conflicts.extend(ConflictDetector.detect_ordering_conflicts(plan.operations))
        
        return all_conflicts


class PlanValidator:
    """Validates update plans."""
    
    @staticmethod
    def validate_operation(operation: UpdateOperation) -> bool:
        """Validate a single operation."""
        errors = []
        
        # Check required fields
        if not operation.target_id:
            errors.append("Target ID is required")
        
        if operation.operation_type in [
            OperationType.UPDATE_TEXT,
            OperationType.INSERT_TEXT,
            OperationType.UPDATE_TABLE_CELL,
        ]:
            if operation.new_value is None:
                errors.append("New value is required for this operation type")
        
        operation.validation_errors = errors
        operation.validated = len(errors) == 0
        
        return operation.validated
    
    @staticmethod
    def validate_plan(plan: UpdatePlan) -> Tuple[bool, List[str]]:
        """Validate an entire plan."""
        errors = []
        
        # Validate all operations
        for op in plan.operations:
            if not PlanValidator.validate_operation(op):
                errors.extend([f"Operation {op.operation_id}: {err}" for err in op.validation_errors])
        
        # Detect conflicts
        conflicts = ConflictDetector.detect_all_conflicts(plan)
        for conflict in conflicts:
            if conflict.severity == "critical":
                errors.append(f"Conflict: {conflict.description}")
            plan.add_conflict(conflict)
        
        # Update plan status
        if errors:
            plan.status = "invalid"
        else:
            plan.status = "validated"
        
        return len(errors) == 0, errors


class OperationBuilder:
    """Builder for creating update operations."""
    
    @staticmethod
    def update_text(
        target_id: str,
        target_path: str,
        old_value: str,
        new_value: str,
        description: str = "",
    ) -> UpdateOperation:
        """Create a text update operation."""
        return UpdateOperation(
            operation_id=f"op_{uuid4().hex[:8]}",
            operation_type=OperationType.UPDATE_TEXT,
            target_id=target_id,
            target_path=target_path,
            old_value=old_value,
            new_value=new_value,
            description=description or f"Update '{old_value[:30]}...' to '{new_value[:30]}...'",
        )
    
    @staticmethod
    def insert_text(
        target_id: str,
        target_path: str,
        text: str,
        description: str = "",
    ) -> UpdateOperation:
        """Create a text insertion operation."""
        return UpdateOperation(
            operation_id=f"op_{uuid4().hex[:8]}",
            operation_type=OperationType.INSERT_TEXT,
            target_id=target_id,
            target_path=target_path,
            new_value=text,
            description=description or f"Insert '{text[:30]}...'",
        )
    
    @staticmethod
    def delete_text(
        target_id: str,
        target_path: str,
        old_value: str,
        description: str = "",
    ) -> UpdateOperation:
        """Create a text deletion operation."""
        return UpdateOperation(
            operation_id=f"op_{uuid4().hex[:8]}",
            operation_type=OperationType.DELETE_TEXT,
            target_id=target_id,
            target_path=target_path,
            old_value=old_value,
            description=description or f"Delete '{old_value[:30]}...'",
        )
    
    @staticmethod
    def update_table_cell(
        target_id: str,
        target_path: str,
        row_header: str,
        col_header: str,
        old_value: str,
        new_value: str,
    ) -> UpdateOperation:
        """Create a table cell update operation."""
        return UpdateOperation(
            operation_id=f"op_{uuid4().hex[:8]}",
            operation_type=OperationType.UPDATE_TABLE_CELL,
            target_id=target_id,
            target_path=f"{target_path}[{row_header},{col_header}]",
            old_value=old_value,
            new_value=new_value,
            description=f"Update table cell [{row_header},{col_header}] to '{new_value}'",
        )


# Type hint for validate_plan return type
from typing import Tuple
