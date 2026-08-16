"""
Validation Engine

Ensures presentation integrity after updates by verifying consistency,
structural correctness, and content validity.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Set
from enum import Enum
from pptx import Presentation


class ValidationLevel(Enum):
    """Levels of validation severity."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class ValidationResult:
    """Result of a single validation check."""
    check_id: str
    check_name: str
    level: ValidationLevel
    passed: bool
    message: str
    affected_objects: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationReport:
    """Complete validation report for a presentation."""
    report_id: str
    slide_index: int
    shape_name: str
    
    results: List[ValidationResult] = field(default_factory=list)
    passed: bool = True
    critical_failures: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    def add_result(self, result: ValidationResult) -> None:
        """Add a validation result."""
        self.results.append(result)
        
        if not result.passed:
            if result.level == ValidationLevel.CRITICAL:
                self.critical_failures.append(result.message)
                self.passed = False
            elif result.level == ValidationLevel.ERROR:
                self.passed = False
            elif result.level == ValidationLevel.WARNING:
                self.warnings.append(result.message)
    
    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of the validation report."""
        levels = {level: 0 for level in ValidationLevel}
        
        for result in self.results:
            if not result.passed:
                levels[result.level] += 1
        
        return {
            "report_id": self.report_id,
            "slide_index": self.slide_index,
            "shape_name": self.shape_name,
            "total_checks": len(self.results),
            "passed": self.passed,
            "critical_failures": len(self.critical_failures),
            "errors": levels[ValidationLevel.ERROR],
            "warnings": levels[ValidationLevel.WARNING],
            "info_messages": levels[ValidationLevel.INFO],
        }


class PresentationValidator:
    """Validates presentation structure and content."""
    
    @staticmethod
    def validate_text_overflow(
        text_frame,
        max_width: int,
        max_height: int,
    ) -> ValidationResult:
        """Check if text overflows its container."""
        check_id = "text_overflow"
        
        # Estimate text dimensions (simplified)
        total_text = ""
        for para in text_frame.paragraphs:
            total_text += para.text + "\n"
        
        # Count lines
        lines = total_text.strip().split('\n')
        estimated_height = len(lines) * 14  # Rough estimate in points
        
        passed = estimated_height <= max_height
        
        return ValidationResult(
            check_id=check_id,
            check_name="Text Overflow Check",
            level=ValidationLevel.WARNING if not passed else ValidationLevel.INFO,
            passed=passed,
            message="Text content fits within container" if passed else "Text may overflow container",
            details={
                "estimated_lines": len(lines),
                "estimated_height": estimated_height,
                "container_height": max_height,
            },
        )
    
    @staticmethod
    def validate_heading_structure(document_tree) -> ValidationResult:
        """Validate hierarchical heading structure."""
        from src.ppt_ai.document_tree import ElementType
        
        check_id = "heading_structure"
        
        # Get all headings
        headings = document_tree.find_by_type(ElementType.SECTION)
        
        if not headings:
            return ValidationResult(
                check_id=check_id,
                check_name="Heading Structure Validation",
                level=ValidationLevel.WARNING,
                passed=False,
                message="No section headings found in document",
                suggestions=["Add at least one section heading to structure the content"],
            )
        
        # Check heading levels are sequential
        levels = sorted(set(h.heading_level for h in headings if h.is_heading))
        
        passed = True
        issues = []
        
        # Level should start at 1
        if levels and levels[0] != 1:
            passed = False
            issues.append(f"Headings start at level {levels[0]}, should start at 1")
        
        # Check for gaps in levels
        for i in range(len(levels) - 1):
            if levels[i + 1] - levels[i] > 1:
                issues.append(f"Gap in heading levels: {levels[i]} -> {levels[i + 1]}")
        
        return ValidationResult(
            check_id=check_id,
            check_name="Heading Structure Validation",
            level=ValidationLevel.WARNING,
            passed=passed,
            message="Heading structure is valid" if passed else "Heading structure issues found",
            details={
                "total_headings": len(headings),
                "heading_levels": levels,
                "issues": issues,
            },
            suggestions=issues,
        )
    
    @staticmethod
    def validate_table_dimensions(table) -> ValidationResult:
        """Validate table structure."""
        check_id = "table_dimensions"
        
        try:
            num_rows = len(table.rows)
            num_cols = len(table.columns)
            
            # Check for empty rows or columns
            issues = []
            
            for row_idx, row in enumerate(table.rows):
                if all(cell.text.strip() == "" for cell in row.cells):
                    issues.append(f"Row {row_idx} is empty")
            
            # Check column consistency
            if num_rows > 1:
                first_row_cols = len(table.rows[0].cells)
                for row_idx, row in enumerate(table.rows):
                    if len(row.cells) != first_row_cols:
                        issues.append(f"Row {row_idx} has inconsistent column count")
            
            passed = len(issues) == 0
            
            return ValidationResult(
                check_id=check_id,
                check_name="Table Dimensions Validation",
                level=ValidationLevel.ERROR if not passed else ValidationLevel.INFO,
                passed=passed,
                message="Table structure is valid" if passed else "Table structure issues found",
                details={
                    "rows": num_rows,
                    "columns": num_cols,
                    "issues": issues,
                },
                suggestions=issues,
            )
        
        except Exception as e:
            return ValidationResult(
                check_id=check_id,
                check_name="Table Dimensions Validation",
                level=ValidationLevel.ERROR,
                passed=False,
                message=f"Error validating table: {str(e)}",
            )
    
    @staticmethod
    def validate_references(document_tree) -> ValidationResult:
        """Validate that all references point to valid nodes."""
        check_id = "reference_validity"
        
        issues = []
        node_ids = set(document_tree.nodes.keys())
        
        for node in document_tree.nodes.values():
            for ref_id in node.references:
                if ref_id not in node_ids:
                    issues.append(f"Node {node.element_id} references invalid node {ref_id}")
        
        passed = len(issues) == 0
        
        return ValidationResult(
            check_id=check_id,
            check_name="Reference Validity",
            level=ValidationLevel.ERROR if not passed else ValidationLevel.INFO,
            passed=passed,
            message="All references are valid" if passed else "Invalid references found",
            details={"invalid_references": issues},
            suggestions=issues,
        )
    
    @staticmethod
    def validate_formatting_consistency(document_tree) -> ValidationResult:
        """Check for overly inconsistent formatting."""
        check_id = "formatting_consistency"
        
        # Collect all formatting properties
        font_names: Set[str] = set()
        font_sizes: Set[int] = set()
        colors: Set[str] = set()
        
        for node in document_tree.nodes.values():
            font_names.add(node.formatting.font_name)
            font_sizes.add(node.formatting.font_size)
            if node.formatting.color_hex:
                colors.add(node.formatting.color_hex)
        
        # Too many variations might indicate inconsistency
        warnings = []
        
        if len(font_names) > 5:
            warnings.append(f"Too many fonts ({len(font_names)}): consider limiting to 2-3")
        
        if len(font_sizes) > 4:
            warnings.append(f"Too many font sizes ({len(font_sizes)}): consider limiting to 3-4")
        
        if len(colors) > 8:
            warnings.append(f"Too many colors ({len(colors)}): consider limiting color palette")
        
        passed = len(warnings) == 0
        
        return ValidationResult(
            check_id=check_id,
            check_name="Formatting Consistency",
            level=ValidationLevel.WARNING,
            passed=passed,
            message="Formatting is consistent" if passed else "Formatting inconsistencies found",
            details={
                "unique_fonts": len(font_names),
                "unique_sizes": len(font_sizes),
                "unique_colors": len(colors),
            },
            suggestions=warnings,
        )


class ValidationEngine:
    """Main validation engine that runs all checks."""
    
    def __init__(self):
        self.validator = PresentationValidator()
    
    def validate_document_tree(self, document_tree) -> ValidationReport:
        """Run all validation checks on a document tree."""
        from uuid import uuid4
        
        report = ValidationReport(
            report_id=f"report_{uuid4().hex[:8]}",
            slide_index=document_tree.slide_index,
            shape_name=document_tree.shape_name,
        )
        
        # Run all validation checks
        report.add_result(self.validator.validate_heading_structure(document_tree))
        report.add_result(self.validator.validate_references(document_tree))
        report.add_result(self.validator.validate_formatting_consistency(document_tree))
        
        return report
    
    def validate_text_frame(self, text_frame, shape_width: int, shape_height: int) -> ValidationReport:
        """Run validation on a text frame."""
        from uuid import uuid4
        
        report = ValidationReport(
            report_id=f"report_{uuid4().hex[:8]}",
            slide_index=0,
            shape_name="TextFrame",
        )
        
        report.add_result(self.validator.validate_text_overflow(
            text_frame,
            shape_width,
            shape_height,
        ))
        
        return report
    
    def validate_table(self, table) -> ValidationReport:
        """Run validation on a table."""
        from uuid import uuid4
        
        report = ValidationReport(
            report_id=f"report_{uuid4().hex[:8]}",
            slide_index=0,
            shape_name="Table",
        )
        
        report.add_result(self.validator.validate_table_dimensions(table))
        
        return report
