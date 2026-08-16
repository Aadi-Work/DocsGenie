"""
Table Semantics Module

Advanced table understanding without relying on row/column numbers.
Enables commands like "Update 'Need Support' for 'South America' to 'Yes'"
or "Change 'Project Status' to 'Delayed'".
"""

from typing import Optional, Dict, List, Any, Tuple
from pptx.table import Table
from dataclasses import dataclass, field


@dataclass
class CellReference:
    """Semantic reference to a table cell."""
    table_id: str
    row_header: str
    col_header: str
    row_index: int = -1
    col_index: int = -1
    value: Any = None


@dataclass
class TableSchema:
    """Describes the semantic structure of a table."""
    table_id: str
    table_name: str

    # Physical dimensions are extraction metadata.  Keep them alongside the
    # semantic header map so callers never need to guess which table model
    # they received.
    num_rows: int = 0
    num_cols: int = 0
    
    # Header information
    header_row_index: int = 0
    header_col_index: int = -1
    
    column_headers: List[str] = field(default_factory=list)
    row_headers: List[str] = field(default_factory=list)
    
    # Data grid: (row_idx, col_idx) -> value
    data: Dict[Tuple[int, int], Any] = field(default_factory=dict)
    
    # Semantic mapping: (row_name, col_name) -> (row_idx, col_idx)
    semantic_map: Dict[Tuple[str, str], Tuple[int, int]] = field(default_factory=dict)

    # Every grid coordinate maps to the merge-origin coordinate.  This means
    # a request can target a visually merged cell without referring to a
    # non-existent (spanned) node.
    cell_roots: Dict[Tuple[int, int], Tuple[int, int]] = field(default_factory=dict)
    
    # Column types (inferred)
    column_types: Dict[str, str] = field(default_factory=dict)  # "text", "number", "status", "date"
    
    def get_cell(self, row_header: str, col_header: str) -> Optional[Any]:
        """Get cell value by semantic reference."""
        key = (row_header, col_header)
        if key in self.semantic_map:
            row_idx, col_idx = self.semantic_map[key]
            return self.data.get((row_idx, col_idx))
        return None
    
    def set_cell(self, row_header: str, col_header: str, value: Any) -> bool:
        """Set cell value by semantic reference."""
        key = (row_header, col_header)
        if key in self.semantic_map:
            row_idx, col_idx = self.semantic_map[key]
            self.data[(row_idx, col_idx)] = value
            return True
        return False
    
    def find_row_by_header(self, search_value: str, partial: bool = True) -> Optional[str]:
        """Find row header by value."""
        search_value_lower = search_value.lower() if partial else search_value
        
        for row_header in self.row_headers:
            row_header_lower = row_header.lower() if partial else row_header
            if search_value_lower in row_header_lower:
                return row_header
        
        return None
    
    def find_col_by_header(self, search_value: str, partial: bool = True) -> Optional[str]:
        """Find column header by value."""
        search_value_lower = search_value.lower() if partial else search_value
        
        for col_header in self.column_headers:
            col_header_lower = col_header.lower() if partial else col_header
            if search_value_lower in col_header_lower:
                return col_header
        
        return None


class TableSchemaBuilder:
    """Builds semantic schemas from PowerPoint tables."""
    
    @staticmethod
    def build_from_table(
        table: Table,
        table_id: str = "",
        table_name: str = "",
        header_row_idx: int = 0,
        header_col_idx: int = -1,
    ) -> TableSchema:
        """Build a schema from a PowerPoint table."""
        schema = TableSchema(
            table_id=table_id or f"table_{id(table)}",
            table_name=table_name or "Table",
            header_row_index=header_row_idx,
            header_col_index=header_col_idx,
        )
        
        num_rows = len(table.rows)
        num_cols = len(table.columns)
        schema.num_rows = num_rows
        schema.num_cols = num_cols
        
        # Map every visual grid coordinate to the first occurrence of its XML
        # cell.  python-pptx exposes merged cells through multiple coordinates;
        # their shared ``_tc`` object gives us a reliable merge-origin key.
        # Keep the XML objects themselves as keys.  Using ``id(_tc)`` is not
        # safe here because temporary lxml wrappers may be released and their
        # Python ids reused while the table is being scanned.
        roots: Dict[Any, Tuple[int, int]] = {}
        for row_idx in range(num_rows):
            for col_idx in range(num_cols):
                cell = table.cell(row_idx, col_idx)
                key = getattr(cell, "_tc", cell)
                schema.cell_roots[(row_idx, col_idx)] = roots.setdefault(key, (row_idx, col_idx))

        # Extract column headers
        if header_row_idx < num_rows:
            for col_idx in range(num_cols):
                cell = table.rows[header_row_idx].cells[col_idx]
                header_text = cell.text.strip()
                schema.column_headers.append(header_text)
                
                # Infer column type
                schema.column_types[header_text] = TableSchemaBuilder._infer_column_type(
                    table, col_idx, header_row_idx
                )

        # A leading serial-number column is not a domain entity. Prefer a
        # labelled business-key column so requests can say a project/system
        # name rather than exposing physical column positions.
        if header_col_idx < 0 and schema.column_headers:
            key_terms = ("project", "system", "name", "item", "interface", "workstream")
            header_col_idx = next(
                (index for index, label in enumerate(schema.column_headers)
                 if any(term in label.casefold() for term in key_terms)),
                0,
            )
            schema.header_col_index = header_col_idx
        
        # Extract row headers (first column if specified)
        if header_col_idx >= 0:
            for row_idx in range(num_rows):
                if row_idx == header_row_idx:
                    continue
                cell = table.rows[row_idx].cells[header_col_idx]
                header_text = cell.text.strip()
                schema.row_headers.append(header_text)
        else:
            # Use first column as row headers
            for row_idx in range(num_rows):
                if row_idx == header_row_idx:
                    continue
                cell = table.rows[row_idx].cells[0]
                header_text = cell.text.strip()
                schema.row_headers.append(header_text)
        
        # Build data grid and semantic map
        for row_idx in range(num_rows):
            if row_idx == header_row_idx:
                continue
            
            row_header = schema.row_headers[row_idx - (header_row_idx + 1)] if row_idx > header_row_idx else ""
            
            for col_idx in range(num_cols):
                if col_idx == header_col_idx and header_col_idx >= 0:
                    continue
                
                cell = table.rows[row_idx].cells[col_idx]
                cell_value = cell.text.strip()
                
                col_header = schema.column_headers[col_idx] if col_idx < len(schema.column_headers) else ""
                
                # Store in data grid
                schema.data[(row_idx, col_idx)] = cell_value
                
                # Store in semantic map
                if row_header and col_header:
                    schema.semantic_map[(row_header, col_header)] = schema.cell_roots[(row_idx, col_idx)]
        
        return schema
    
    @staticmethod
    def _infer_column_type(table: Table, col_idx: int, skip_row: int = 0) -> str:
        """Infer the data type of a column."""
        # Sample values from the column
        sample_values = []
        
        for row_idx in range(len(table.rows)):
            if row_idx == skip_row:
                continue
            
            if col_idx < len(table.rows[row_idx].cells):
                cell_value = table.rows[row_idx].cells[col_idx].text.strip()
                sample_values.append(cell_value)
        
        if not sample_values:
            return "text"
        
        # Check for status values
        status_keywords = ["pending", "completed", "in progress", "delayed", "on track", "at risk", "off track"]
        if any(keyword in value.lower() for value in sample_values for keyword in status_keywords):
            return "status"
        
        # Check for dates
        date_patterns = [r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}", r"\d{4}[-/]\d{1,2}[-/]\d{1,2}"]
        import re
        if any(re.search(pattern, value) for value in sample_values for pattern in date_patterns):
            return "date"
        
        # Check for numbers
        if all(TableSchemaBuilder._is_number(value) for value in sample_values if value):
            return "number"
        
        # Check for yes/no
        yes_no_keywords = ["yes", "no", "true", "false", "y", "n"]
        if all(value.lower() in yes_no_keywords for value in sample_values if value):
            return "boolean"
        
        return "text"
    
    @staticmethod
    def _is_number(value: str) -> bool:
        """Check if a value is a number."""
        try:
            float(value)
            return True
        except ValueError:
            return False


class TableUpdater:
    """Updates table content using semantic references."""
    
    def __init__(self, table: Table, schema: TableSchema):
        self.table = table
        self.schema = schema

    def plan_update_by_semantic_reference(self, row_header: str, col_header: str, new_value: str):
        """Return a tree operation; rendering is performed later by the pipeline.

        This is the preferred API. ``update_by_semantic_reference`` remains a
        compatibility adapter for older callers that still own a live table.
        """
        from src.ppt_ai.change_plan import OperationBuilder
        row = self.schema.find_row_by_header(row_header, partial=True)
        col = self.schema.find_col_by_header(col_header, partial=True)
        if not row or not col or (row, col) not in self.schema.semantic_map:
            return None
        row_index, col_index = self.schema.semantic_map[(row, col)]
        return OperationBuilder.update_table_cell(
            target_id=f"{self.schema.table_id}_row_{row_index}_cell_{col_index}",
            target_path=f"{self.schema.table_name}/{row}/{col}", row_header=row,
            col_header=col, old_value=str(self.schema.data.get((row_index, col_index), "")),
            new_value=str(new_value),
        )
    
    def update_by_semantic_reference(
        self,
        row_header: str,
        col_header: str,
        new_value: str,
    ) -> bool:
        """Update a cell using semantic references."""
        # Find matching headers (with fuzzy matching)
        matching_row = self.schema.find_row_by_header(row_header, partial=True)
        matching_col = self.schema.find_col_by_header(col_header, partial=True)
        
        if not matching_row or not matching_col:
            return False
        
        # Get cell indices
        key = (matching_row, matching_col)
        if key not in self.schema.semantic_map:
            return False
        
        row_idx, col_idx = self.schema.semantic_map[key]
        
        # Update the cell
        if 0 <= row_idx < len(self.table.rows) and 0 <= col_idx < len(self.table.rows[row_idx].cells):
            cell = self.table.rows[row_idx].cells[col_idx]
            cell.text = new_value
            
            # Update schema
            self.schema.data[(row_idx, col_idx)] = new_value
            
            return True
        
        return False
    
    def update_all_matching_cells(
        self,
        search_value: str,
        new_value: str,
        partial: bool = True,
    ) -> int:
        """Update all cells matching a search value."""
        updated_count = 0
        
        for (row_idx, col_idx), cell_value in self.schema.data.items():
            if partial:
                if search_value.lower() in str(cell_value).lower():
                    self.table.rows[row_idx].cells[col_idx].text = new_value
                    self.schema.data[(row_idx, col_idx)] = new_value
                    updated_count += 1
            else:
                if str(cell_value) == search_value:
                    self.table.rows[row_idx].cells[col_idx].text = new_value
                    self.schema.data[(row_idx, col_idx)] = new_value
                    updated_count += 1
        
        return updated_count
    
    def insert_row_after(
        self,
        row_header: str,
        new_row_header: str,
        new_values: Optional[List[str]] = None,
    ) -> bool:
        """Insert a new row after a row matching the header."""
        matching_row = self.schema.find_row_by_header(row_header, partial=True)
        
        if not matching_row or matching_row not in self.schema.row_headers:
            return False
        
        # Find row index
        row_idx = self.schema.row_headers.index(matching_row)
        # Adjust for header row
        insert_row_idx = row_idx + self.schema.header_row_index + 1
        
        # Note: python-pptx doesn't easily support row insertion, so we'd need
        # to handle this at a higher level
        
        return False


class TableQueryEngine:
    """Queries tables using semantic understanding."""
    
    def __init__(self, schema: TableSchema):
        self.schema = schema
    
    def query_cell(self, row_header: str, col_header: str) -> Optional[Any]:
        """Query a cell value."""
        return self.schema.get_cell(row_header, col_header)
    
    def query_row(self, row_header: str) -> Dict[str, Any]:
        """Get all values in a row."""
        result = {}
        for col_header in self.schema.column_headers:
            value = self.schema.get_cell(row_header, col_header)
            if value:
                result[col_header] = value
        return result
    
    def query_column(self, col_header: str) -> Dict[str, Any]:
        """Get all values in a column."""
        result = {}
        for row_header in self.schema.row_headers:
            value = self.schema.get_cell(row_header, col_header)
            if value:
                result[row_header] = value
        return result
    
    def filter_rows_by_value(self, col_header: str, value: str) -> List[str]:
        """Filter rows by a column value."""
        matching_rows = []
        for row_header in self.schema.row_headers:
            cell_value = self.schema.get_cell(row_header, col_header)
            if cell_value and str(cell_value).lower() == value.lower():
                matching_rows.append(row_header)
        return matching_rows
    
    def get_statistics(self, col_header: str) -> Dict[str, Any]:
        """Get statistics for a column."""
        values = []
        
        for row_header in self.schema.row_headers:
            value = self.schema.get_cell(row_header, col_header)
            if value:
                values.append(value)
        
        # Try to compute statistics
        numeric_values = []
        for v in values:
            try:
                numeric_values.append(float(v))
            except (ValueError, TypeError):
                pass
        
        stats = {
            "total_values": len(values),
            "non_empty": len([v for v in values if v]),
            "unique_values": len(set(str(v) for v in values if v)),
        }
        
        if numeric_values:
            stats["numeric_count"] = len(numeric_values)
            stats["min"] = min(numeric_values)
            stats["max"] = max(numeric_values)
            stats["avg"] = sum(numeric_values) / len(numeric_values)
        
        return stats
