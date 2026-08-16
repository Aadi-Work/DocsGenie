"""
Integrated Updater

Orchestrates all components of the AI PowerPoint editing system.
Combines document parsing, planning, validation, format preservation,
and versioning into a cohesive update pipeline.
"""

from typing import List, Optional, Dict, Any
from pathlib import Path
import re
from datetime import datetime
from pptx import Presentation
from pptx.util import Inches

from src.ppt_ai.document_parser import DocumentTreeBuilder, DocumentParser
from src.ppt_ai.document_tree import DocumentTree, DocumentNode, ElementType, ImageNode
from src.ppt_ai.format_preservation import FormatPreserver, FormatExtractor
from src.ppt_ai.change_plan import UpdatePlan, UpdateOperation, OperationType, OperationBuilder, PlanValidator, ConflictDetector
from src.ppt_ai.nlp_interpreter import CommandInterpreter, MultiCommandProcessor
from src.ppt_ai.validation_engine import ValidationEngine, ValidationReport
from src.ppt_ai.version_manager import VersionManager, AuditLog, ModificationType
from src.ppt_ai.semantic_slide import SemanticSlideBuilder, SemanticSlide
from src.ppt_ai.table_semantics import TableSchemaBuilder, TableUpdater, TableQueryEngine
from src.ppt_ai.image_parser import ImageParser
from src.ppt_ai.semantic_graph import SemanticGraphBuilder, SemanticGraph, SemanticQueryEngine
from src.ppt_ai.tree_planner import TreePlanner
from src.ppt_ai.renderer import DocumentTreeRenderer
from src.ppt_ai.component_analyzer import ComponentAnalyzer
from src.ppt_ai.layout_analyzer import LayoutAnalyzer
from src.ppt_ai.semantic_analyzer import SemanticAnalyzer
from src.ppt_ai.schema_matcher import SemanticSchemaMatcher, normalise
import re

import re

def normalize_text(s: str) -> str:
    """Normalizes text by stripping punctuation, extra spaces, and lowercasing."""
    if not s:
        return ""
    # Strip non-alphanumeric chars to make matching resilient against whitespace or symbols
    s_clean = re.sub(r"[^\w\s]", " ", str(s).lower())
    return " ".join(s_clean.split())


def extract_table_matrix(tree):
    """
    Extracts a 2D matrix of cell objects from any table representation.
    Handles python-pptx GraphicFrame, Table, custom Node trees, and dict structures.
    """
    # 1. Unwrap GraphicFrame or wrapper nodes to get the underlying Table object
    if hasattr(tree, "has_table") and tree.has_table:
        tree = tree.table
    elif hasattr(tree, "table") and tree.table is not None:
        tree = tree.table
    elif hasattr(tree, "shape"):
        shape = tree.shape
        if hasattr(shape, "has_table") and shape.has_table:
            tree = shape.table
        elif not hasattr(shape, "has_table") and hasattr(shape, "table") and shape.table is not None:
            tree = shape.table

    # 2. Extract matrix from Table object
    if hasattr(tree, "rows"):
        matrix = []
        for r in tree.rows:
            if hasattr(r, "cells"):
                matrix.append(list(r.cells))
            elif isinstance(r, (list, tuple)):
                matrix.append(list(r))
        if matrix:
            return matrix

    # 3. Custom tree attributes (.matrix, .children, .nodes)
    if hasattr(tree, "matrix"):
        return tree.matrix

    children = getattr(tree, "children", None) or getattr(tree, "nodes", None) or []
    if children:
        if all(hasattr(c, "cells") or hasattr(c, "children") for c in children):
            matrix = []
            for child in children:
                row_cells = getattr(child, "cells", None) or getattr(child, "children", None) or [child]
                matrix.append(list(row_cells))
            if matrix:
                return matrix

    if hasattr(tree, "cells"):
        return [tree.cells]

    if isinstance(tree, (list, tuple)):
        return list(tree)

    return []


def get_cell_text(cell) -> str:
    """Extracts text from various cell representations safely."""
    if cell is None:
        return ""
    if isinstance(cell, str):
        return cell
    if hasattr(cell, "text"):
        return getattr(cell, "text", "")
    if hasattr(cell, "value"):
        return getattr(cell, "value", "")
    if isinstance(cell, dict):
        return cell.get("text", cell.get("value", ""))
    return str(cell)


def evaluate_tree_for_cell(tree, target_row: str, target_col: str):
    """
    Evaluates a table tree and returns (matched_cell, row_idx, col_idx, total_score).
    Supports token-overlap fuzzy matching across headers and row labels.
    """
    matrix = extract_table_matrix(tree)
    if not matrix or len(matrix) == 0:
        return None, -1, -1, 0

    norm_target_row = normalize_text(target_row)
    norm_target_col = normalize_text(target_col)

    target_row_tokens = set(norm_target_row.split())
    target_col_tokens = set(norm_target_col.split())

    best_col_idx = -1
    best_col_score = 0

    # Look across the first 2 rows for column header match
    max_header_rows = min(2, len(matrix))
    for r_idx in range(max_header_rows):
        row = matrix[r_idx]
        for c_idx, cell in enumerate(row):
            cell_text = get_cell_text(cell)
            norm_cell = normalize_text(cell_text)
            if not norm_cell:
                continue

            score = 0
            if norm_target_col == norm_cell:
                score = 50
            elif norm_target_col in norm_cell or norm_cell in norm_target_col:
                score = 35
            else:
                cell_tokens = set(norm_cell.split())
                if target_col_tokens and cell_tokens:
                    overlap = len(target_col_tokens & cell_tokens) / len(target_col_tokens)
                    if overlap >= 0.4:
                        score = int(overlap * 30)

            if score > best_col_score:
                best_col_score = score
                best_col_idx = c_idx

    # If column matching failed, fall back to checking all columns if needed
    if best_col_idx == -1:
        best_col_score = 5 # Graceful fallback if column name is implicit or missing

    best_row_idx = -1
    best_row_score = 0

    # Search for row match across all rows (inspecting first 2 cells of each row)
    start_row = 1 if len(matrix) > 1 else 0
    for r_idx in range(start_row, len(matrix)):
        row = matrix[r_idx]
        for c_idx in range(min(2, len(row))):
            cell = row[c_idx]
            cell_text = get_cell_text(cell)
            norm_cell = normalize_text(cell_text)
            if not norm_cell:
                continue

            score = 0
            if norm_target_row == norm_cell:
                score = 50
            elif norm_target_row in norm_cell or norm_cell in norm_target_row:
                score = 35
            else:
                cell_tokens = set(norm_cell.split())
                if target_row_tokens and cell_tokens:
                    overlap = len(target_row_tokens & cell_tokens) / len(target_row_tokens)
                    if overlap >= 0.3:
                        score = int(overlap * 30)

            if score > best_row_score:
                best_row_score = score
                best_row_idx = r_idx

    if best_row_idx == -1 or best_row_score < 10:
        return None, -1, -1, 0

    target_col_final = best_col_idx if best_col_idx != -1 else 0
    
    # Ensure indices are within bounds
    if best_row_idx < len(matrix) and target_col_final < len(matrix[best_row_idx]):
        matched_cell = matrix[best_row_idx][target_col_final]
        total_score = best_col_score + best_row_score
        return matched_cell, best_row_idx, target_col_final, total_score

    return None, -1, -1, 0



def normalize_text(text: str) -> str:
        """Normalize whitespace and case for robust matching."""
        if not text:
            return ""
        # Replace non-breaking spaces, vertical tabs, and line breaks with regular space
        cleaned = text.replace('\xa0', ' ').replace('\x0b', ' ').replace('\n', ' ').replace('\r', ' ')
        return " ".join(cleaned.split()).strip().lower()   
class PresentationUpdatePipeline:

    def __init__(self, presentation_path: str):
        self.presentation_path = presentation_path
        self.prs = Presentation(presentation_path)
        
        # Core components
        self.format_preserver = FormatPreserver()
        self.validation_engine = ValidationEngine()
        self.version_manager = VersionManager()
        self.audit_log = AuditLog()
        
        # Data structures
        self.document_trees: Dict[int, Dict[str, DocumentTree]] = {}  # slide_index -> {shape_name: tree}
        self.semantic_graph: Optional[SemanticGraph] = None
        self.semantic_slides: Dict[int, Dict[str, SemanticSlide]] = {}
        self.table_schemas: Dict[tuple[int, str], Any] = {}
        self.image_elements: List[Any] = []
        
        # Current state
        self.current_update_plan: Optional[UpdatePlan] = None
        self.validation_report: Optional[ValidationReport] = None
        self.tree_planner = TreePlanner(self.document_trees)
        self.renderer = DocumentTreeRenderer()
        self.component_analyzer = ComponentAnalyzer()
        self.layout_analyzer = LayoutAnalyzer()
        self.semantic_analyzer = SemanticAnalyzer()
    
    def parse_presentation(self) -> None:
        """Parse the entire presentation into document trees."""
        print("\n[PARSE] Analyzing presentation structure...")
        
        for slide_idx, slide in enumerate(self.prs.slides, start=1):
            self.document_trees[slide_idx] = {}
            
            for shape in slide.shapes:
                shape_name = getattr(shape, 'name', f'shape_{shape.shape_id}')
                
                # Parse text frames
                if hasattr(shape, 'text_frame') and shape.text_frame:
                    # PowerPoint permits repeated display names.  Retain every
                    # shape in the AST using a stable internal key instead of
                    # silently overwriting sibling status badges.
                    tree_key = shape_name if shape_name not in self.document_trees[slide_idx] else f"{shape_name}__{shape.shape_id}"
                    parser = DocumentParser(slide_idx, shape.shape_id, tree_key)
                    tree = parser.parse_text_frame(shape.text_frame)
                    if tree is None:
                        print(f"  ! Warning: parser returned None for {shape_name} on slide {slide_idx}")
                        continue
                    geometry = {"left": int(shape.left), "top": int(shape.top), "width": int(shape.width), "height": int(shape.height)}
                    for node in tree.nodes.values():
                        node.geometry = geometry.copy()
                    self.component_analyzer.analyze_tree(tree, int(self.prs.slide_height))
                    self.layout_analyzer.analyze_tree(tree, int(self.prs.slide_width), int(self.prs.slide_height))
                    self.semantic_analyzer.analyze_tree(tree)
                    self.document_trees[slide_idx][tree_key] = tree
                    self.semantic_slides.setdefault(slide_idx, {})[tree_key] = SemanticSlideBuilder.build_from_document_tree(tree)
                    print(f"  - Parsed {shape_name}: {len(tree.nodes)} nodes")
                
                # Parse tables
                if getattr(shape, 'has_table', False):
                    schema = TableSchemaBuilder.build_from_table(
                        shape.table,
                        table_id=f"table_{shape.shape_id}",
                        table_name=shape_name,
                    )
                    self.table_schemas[(slide_idx, shape_name)] = schema
                    self.document_trees[slide_idx][shape_name] = DocumentTreeBuilder.build_table_tree(
                        slide_idx, shape.shape_id, shape_name, shape.table
                    )
                    self.semantic_slides.setdefault(slide_idx, {})[shape_name] = SemanticSlideBuilder.build_from_document_tree(self.document_trees[slide_idx][shape_name])
                    print(
                        f"[TABLE DEBUG] "
                        f"slide={slide_idx} | "
                        f"original_shape_name='{shape.name}' | "
                        f"shape_id={shape.shape_id} | "
                        f"rows={len(shape.table.rows)} | "
                        f"cols={len(shape.table.columns)}"
                    )                 
                    print(f"  - Parsed table {shape_name}: {schema.num_rows}x{schema.num_cols}")
                
                # Parse images
                if hasattr(shape, 'image'):
                    img_element = ImageParser.extract_image_metadata(shape, slide_idx)
                    if img_element:
                        self.image_elements.append(img_element)
                        image_tree = DocumentTree(slide_idx, shape.shape_id, shape_name)
                        image_tree.add_node(ImageNode(
                            element_id=f"image_{slide_idx}_{shape.shape_id}", text=img_element.caption or shape_name,
                            slide_index=slide_idx, shape_id=shape.shape_id, shape_name=shape_name,
                            semantic_role=img_element.image_type.value, component_type=img_element.image_type.value,
                            geometry={"left": int(shape.left), "top": int(shape.top), "width": int(shape.width), "height": int(shape.height)},
                        ))
                        self.document_trees[slide_idx][shape_name] = image_tree
                        self.semantic_slides.setdefault(slide_idx, {})[shape_name] = SemanticSlideBuilder.build_from_document_tree(image_tree)
                        print(f"  - Found image: {img_element.image_type.value}")
    
    def build_semantic_graph(self) -> None:
        """Build the semantic graph from parsed documents."""
        print("\n[GRAPH] Building semantic graph...")
        
        self.semantic_graph = SemanticGraph()
        
        # Add nodes from document trees
        for slide_trees in self.document_trees.values():
            for tree in slide_trees.values():
                partial_graph = SemanticGraphBuilder.build_from_document_tree(tree)
                
                # Merge graphs
                for node_id, node in partial_graph.nodes.items():
                    self.semantic_graph.add_node(node)
                
                for (src, tgt, rel), edge in partial_graph.edges.items():
                    self.semantic_graph.add_edge(src, tgt, edge.relation_type)

        # Create semantic slide models for all parsed trees
        self.semantic_slides = {
            slide_idx: {
                shape_name: SemanticSlideBuilder.build_from_document_tree(tree)
                for shape_name, tree in slide_trees.items()
            }
            for slide_idx, slide_trees in self.document_trees.items()
        }
        
        if self.semantic_graph:
            summary = SemanticQueryEngine(self.semantic_graph).get_graph_summary()
            print(f"  - Graph built: {summary['total_nodes']} nodes, {summary['total_edges']} edges")
    
    def create_update_plan_from_json(self, change_request: Dict[str, Any]) -> UpdatePlan:
        """Create an update plan from a forgiving intent-based request.

        The legacy implementation below is retained temporarily for API
        compatibility, but the semantic matcher is now the sole planning
        path.  It only emits IDs that exist in the document AST.
        """
        return self._create_semantic_update_plan(change_request)

        """Create an update plan from a JSON change request."""
        plan_id = f"plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        plan = UpdatePlan(
            plan_id=plan_id,
            name=f"Update Plan {plan_id}",
            description=change_request.get("description", ""),
        )
        
        print(f"\n[PLAN] Creating update plan from {len(change_request.get('changes', []))} changes...")
        
        for change in change_request.get('changes', []):
            change_type = change.get('type')
            
            slide = int(change.get('slide', 0))
            shape_name = change.get('shape_name', '')
            print("\n" + "=" * 80)
            print("TABLE/SHAPE LOOKUP DEBUG")
            print(f"Requested slide : {slide}")
            print(f"Requested shape : '{shape_name}'")

            slide_trees = self.document_trees.get(slide, {})

            print(f"Available shapes: {list(slide_trees.keys())}")

            for available_name, available_tree in slide_trees.items():
                print(
                    f"  - Shape: '{available_name}'"
                    f" | Nodes: {len(getattr(available_tree, 'nodes', []))}"
                )

            print("=" * 80)
            print("\n-------------------------")
            print("Processing change")
            print(change)
            print()

            print("Slides parsed:")
            print(list(self.document_trees.keys()))

            print()

            print("Shapes on requested slide:")
            print(list(self.document_trees.get(slide, {}).keys()))

            print("-------------------------")
            tree = self.document_trees.get(slide, {}).get(shape_name)
            if tree is None:
                raise ValueError(f"No document tree for slide {slide}, shape '{shape_name}'")

            # --- Inside create_update_plan_from_json ---
            if change_type == "table_cell":
                req_slide = change.get("slide")
                req_shape = change.get("shape_name")
                target_row = change.get("row_match", "")
                target_col = change.get("column_name", "")
                new_value = str(change.get("value", ""))

                candidates = []

                # Resolution order for parsed shapes/tables:
                # 1. Local variable `slide_trees` / `parsed_slides`
                # 2. Instance attributes on `self`
                trees_source = {}
                for attr in ['slide_trees', 'parsed_slides', 'slides', '_slides']:
                    if hasattr(self, attr) and getattr(self, attr):
                        trees_source = getattr(self, attr)
                        break

                # If stored directly as python-pptx Presentation object on self
                if not trees_source and hasattr(self, 'prs') and self.prs:
                    for i, slide in enumerate(self.prs.slides, 1):
                        trees_source[i] = {
                            getattr(s, 'name', f"Shape_{j}"): s
                            for j, s in enumerate(slide.shapes)
                            if getattr(s, 'has_table', False)
                        }

                for slide_num, shapes_dict in trees_source.items():
                    if not isinstance(shapes_dict, dict):
                        shapes_dict = {req_shape or "Table": shapes_dict}

                    for shape_name, tree in shapes_dict.items():
                        cell, r_idx, c_idx, score = evaluate_tree_for_cell(tree, target_row, target_col)
                        if cell is not None and score > 0:
                            if req_slide is not None and int(slide_num) == int(req_slide):
                                score += 20
                            if req_shape and str(req_shape).lower() in str(shape_name).lower():
                                score += 15

                            candidates.append({
                                "score": score,
                                "slide": slide_num,
                                "shape_name": shape_name,
                                "cell": cell,
                                "row_idx": r_idx,
                                "col_idx": c_idx
                            })

                if not candidates:
                    print("\n" + "="*80)
                    print(f"[DEBUG INSPECTION] Failed to find row: '{target_row}' | col: '{target_col}'")
                    print(f"trees_source keys (Slide numbers loaded): {list(trees_source.keys())}")
                    print("Inspecting extracted table contents across presentation:")
                    
                    for s_num, s_dict in trees_source.items():
                        if isinstance(s_dict, dict):
                            for s_name, t_obj in s_dict.items():
                                mat = extract_table_matrix(t_obj)
                                if mat:
                                    headers = [get_cell_text(c).replace("\n", " ") for c in mat[0]]
                                    rows = [get_cell_text(r[0]).replace("\n", " ") for r in mat[1:] if len(r) > 0]
                                    print(f"  Slide {s_num} | Shape '{s_name}':")
                                    print(f"    Headers: {headers}")
                                    print(f"    Sample Rows: {rows[:4]}")
                                else:
                                    print(f"  Slide {s_num} | Shape '{s_name}': [No matrix extracted - object type: {type(t_obj)}]")
                        else:
                            mat = extract_table_matrix(s_dict)
                            print(f"  Slide {s_num} | Direct shape/table matrix length: {len(mat)}")
                    print("="*80 + "\n")

                    raise ValueError(
                        f"Could not locate table cell matching row '{target_row}' and column '{target_col}' anywhere in the presentation."
                    )

                best_match = max(candidates, key=lambda x: x["score"])

                matched_cell = best_match["cell"]
                found_slide = best_match["slide"]
                found_shape = best_match["shape_name"]
                matched_row_idx = best_match["row_idx"]
                col_idx = best_match["col_idx"]

                # Update text on target cell
                # Update text on target cell
                old_value = get_cell_text(matched_cell)

                if hasattr(matched_cell, "text"):
                    matched_cell.text = new_value
                elif hasattr(matched_cell, "text_frame"):
                    matched_cell.text_frame.text = new_value
                elif isinstance(matched_cell, dict):
                    matched_cell["text"] = new_value

                target_id = getattr(
                    matched_cell,
                    "node_id",
                    getattr(matched_cell, "id", f"{found_shape}_r{matched_row_idx}_c{col_idx}")
                )

                plan.add_operation(
                    OperationBuilder.update_text(
                        target_id=target_id,
                        target_path=f"Slide {found_slide}/{found_shape}/r{matched_row_idx}_c{col_idx}",
                        old_value=old_value,
                        new_value=new_value,
                        description=(
                            f"Updated table cell on Slide {found_slide} ({found_shape}) "
                            f"for row '{target_row}', col '{target_col}'"
                        ),
                    )
                )
            elif change_type == 'section':
                semantic_slide = self.semantic_slides.get(slide, {}).get(shape_name)
                if semantic_slide is None:
                    raise ValueError(f"No semantic slide model for slide {slide}, shape '{shape_name}'")

                match_text = str(change.get('match', '')).strip().lower()
                section = next(
                    (sec for sec in semantic_slide.sections if match_text in sec.heading.lower()),
                    None,
                )

                if section is None:
                    section = next(
                        (
                            sec for sec in semantic_slide.sections
                            if any(match_text in item.text.lower() for item in sec.paragraphs + sec.bullets)
                        ),
                        None,
                    )

                prior = None
                if section is None:
                    prior = next(
                        (
                            op for op in reversed(plan.operations)
                            if op.operation_type == OperationType.UPDATE_TEXT
                            and op.target_id
                            and op.new_value is not None
                            and str(op.new_value).strip().lower() == match_text
                        ),
                        None,
                    )
                    if prior:
                        target_node = tree.get_node(prior.target_id)
                        if target_node is not None:
                            section = next(
                                (sec for sec in semantic_slide.sections if sec.node_id == target_node.element_id),
                                None,
                            )
                            if section is None and target_node.parent_id:
                                parent_node = tree.get_parent(target_node.element_id)
                                if parent_node and parent_node.element_type == ElementType.SECTION:
                                    section = next(
                                        (sec for sec in semantic_slide.sections if sec.node_id == parent_node.element_id),
                                        None,
                                    )

                if section is None:
                    raise ValueError(f"Section '{change.get('match')}' not found in semantic model of '{shape_name}'")

                resolved_heading = section.heading
                if prior is not None and prior.target_id == section.node_id:
                    resolved_heading = str(prior.new_value or section.heading)

                replace_mode = change.get('replace_mode', 'paragraph')
                if replace_mode == 'section':
                    op = OperationBuilder.update_text(
                        target_id=section.node_id,
                        target_path=f"Slide {slide}/{shape_name}/{resolved_heading}",
                        old_value=resolved_heading,
                        new_value=str(change.get('value', '')),
                        description=f"Replace body of section '{resolved_heading}' in '{shape_name}'",
                    )
                    op.tags.append("replace_section_body")
                    if prior is not None:
                        op.depends_on.append(prior.operation_id)
                    plan.add_operation(op)
                else:
                    target_node = None
                    paragraph_index = change.get('paragraph_index')
                    relative_paragraph = change.get('relative_paragraph')
                    all_items = section.paragraphs + section.bullets

                    if paragraph_index is not None:
                        idx = int(paragraph_index)
                        if 0 <= idx < len(all_items):
                            target_node = all_items[idx]

                    elif relative_paragraph is not None:
                        offset = int(relative_paragraph)
                        if 0 <= offset < len(all_items):
                            target_node = all_items[offset]

                    if target_node is None:
                        if section.heading.lower() == match_text:
                            target_node = section
                        elif match_text:
                            target_node = next(
                                (item for item in all_items if match_text in item.text.lower()),
                                None,
                            )

                    if target_node is None:
                        target_node = all_items[0] if all_items else section

                    target_id = target_node.node_id if hasattr(target_node, 'node_id') else section.node_id
                    old_value = target_node.text if hasattr(target_node, 'text') else section.heading
                    target_path = f"Slide {slide}/{shape_name}/{old_value}"
                    description = (
                        f"Update section heading '{section.heading}' in '{shape_name}'"
                        if target_id == section.node_id
                        else f"Update section body node in '{shape_name}'"
                    )

                    plan.add_operation(OperationBuilder.update_text(
                        target_id=target_id,
                        target_path=target_path,
                        old_value=old_value,
                        new_value=str(change.get('value', '')),
                        description=description,
                    ))

            elif change_type == 'status':
                target = next((node for node in tree.nodes.values() if node.semantic_role == 'status'), None)
                if target is None:
                    target = next((node for node in tree.find_by_text(str(change.get('match', ''))) if node.text.strip()), None)
                if target is None:
                    raise ValueError(f"Status target not found in '{shape_name}'")
                plan.add_operation(OperationBuilder.update_text(
                    target_id=target.element_id, target_path=f"{slide}/{shape_name}/{target.text}",
                    old_value=target.text, new_value=str(change.get('value', '')),
                    description=f"Update status to {change.get('value', '')}",
                ))
        
        print(f"  - Plan created with {len(plan.operations)} operations")
        
        return plan
    
    def _create_semantic_update_plan(self, request: Dict[str, Any]) -> UpdatePlan:
        """Turn concise JSON or natural-language requests into AST operations.

        Accepted table forms include ``{entity, field, value}`` and the
        legacy ``{row_match, column_name, value}``.  ``slide`` and
        ``shape_name`` are optional hints, never required identifiers.
        """
        plan_id = f"plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        plan = UpdatePlan(plan_id=plan_id, name=f"Semantic Update Plan {plan_id}",
                          description=str(request.get("description", "")))
        matcher = SemanticSchemaMatcher(self.document_trees, self.table_schemas)
        raw_changes = request.get("changes", request.get("updates", request.get("requests", [])))
        if isinstance(raw_changes, (str, dict)):
            raw_changes = [raw_changes]
        for raw in raw_changes or []:
            change = self._normalise_intent(raw)
            value = change.get("value")
            if value is None:
                raise ValueError(f"Every update needs a value: {raw!r}")
            slide = change.get("slide")
            slide = int(slide) if str(slide or "").isdigit() else None
            shape_name = str(change.get("shape_name") or change.get("shape") or "")

            cell_id = change.get("cell_id")
            if cell_id:
                # A caller that already resolved the exact cell (for example a
                # header-less "key | value" row, which resolve_table cannot
                # reach because it treats row 0 as column headers, not data)
                # can address it directly by its document-tree node ID.
                plan.add_operation(OperationBuilder.update_table_cell(
                    target_id=str(cell_id),
                    target_path=f"Slide {slide}/{shape_name or 'table'}/{cell_id}",
                    row_header="", col_header="",
                    old_value="", new_value=str(value),
                ))
                continue

            entity, field = change.get("entity", ""), change.get("field", "")
            if entity and field:
                target = matcher.resolve_table(entity, field, slide, shape_name)
                if target is None:
                    raise ValueError(f"No table cell matches entity '{entity}' and field '{field}'.")
                plan.add_operation(OperationBuilder.update_table_cell(
                    target_id=target.node.element_id,
                    target_path=f"Slide {target.slide}/{target.shape_name}/{target.row_label}/{target.field_label}",
                    row_header=target.row_label, col_header=target.field_label,
                    old_value=target.node.text, new_value=str(value),
                ))
                badge = self._find_row_status_badge(target, str(value))
                if badge is not None:
                    badge_slide, badge_key, badge_node = badge
                    badge_op = OperationBuilder.update_text(
                        target_id=badge_node.element_id,
                        target_path=f"Slide {badge_slide}/{badge_key}/status badge",
                        old_value=badge_node.text, new_value=str(value),
                        description=f"Update the row status badge to '{value}'",
                    )
                    badge_op.tags.append("__status_badge__")
                    plan.add_operation(badge_op)
                continue

            anchor = str(change.get("anchor") or change.get("match") or change.get("target") or "")
            if not anchor:
                raise ValueError(f"Could not identify what to update: {raw!r}")
            target = matcher.resolve_text(anchor, slide, shape_name, prefer_body=bool(change.get("body")))
            if target is None:
                raise ValueError(f"No text target matches '{anchor}'.")
            if change.get("action") == "include":
                plan.add_operation(OperationBuilder.insert_text(
                    target_id=target.node.element_id,
                    target_path=f"Slide {target.slide}/{target.shape_name}/{target.node.text}",
                    text=str(value),
                    description=f"Add '{value}' under '{anchor}' on slide {target.slide}",
                ))
                continue
            op = OperationBuilder.update_text(
                target_id=target.node.element_id,
                target_path=f"Slide {target.slide}/{target.shape_name}/{target.node.text}",
                old_value=target.node.text, new_value=str(value),
                description=f"Update '{anchor}' on slide {target.slide}",
            )
            if str(change.get("mode", change.get("replace_mode", ""))).casefold() in {"section", "body"}:
                op.tags.append("replace_section_body")
            if target.kind == "text_whole_shape":
                op.tags.append("replace_whole_shape")
            plan.add_operation(op)
        print(f"  - Semantic plan created with {len(plan.operations)} operations")
        return plan

    def _find_row_status_badge(self, target, value: str):
        """Associate a table row with its nearby status badge by geometry."""
        if "status" not in target.field_label.casefold():
            return None
        slide = self.prs.slides[target.slide - 1]
        table_shape = next((shape for shape in slide.shapes if shape.shape_id == target.node.shape_id), None)
        if table_shape is None or not getattr(table_shape, "has_table", False):
            return None
        row = target.node.table_row_index
        if row <= 0 or row >= len(table_shape.table.rows):
            return None
        row_top = int(table_shape.top) + sum(int(table_shape.table.rows[index].height) for index in range(row))
        row_center = row_top + int(table_shape.table.rows[row].height) / 2
        candidates = []
        for key, tree in self.document_trees.get(target.slide, {}).items():
            if tree.shape_id == table_shape.shape_id or not tree.nodes:
                continue
            status_node = next((node for node in tree.nodes.values()
                                if node.text.strip() and any(label in node.text.casefold()
                                for label in ("on track", "at risk", "off track", "delayed", "blocked"))), None)
            if status_node is None:
                continue
            center = status_node.geometry.get("top", 0) + status_node.geometry.get("height", 0) / 2
            candidates.append((abs(center - row_center), key, status_node))
        if not candidates:
            return None
        distance, key, node = min(candidates, key=lambda item: item[0])
        if distance > int(table_shape.table.rows[row].height) * 0.75:
            return None
        return target.slide, key, node

    @staticmethod
    def _normalise_intent(raw: Any) -> Dict[str, Any]:
        """Support short JSON, old JSON, and common plain-English requests."""
        if isinstance(raw, dict):
            change = dict(raw)
            change.setdefault("entity", change.get("row_match", change.get("row", "")))
            change.setdefault("field", change.get("column_name", change.get("column", "")))
            return change
        text = str(raw).strip()
        include = re.search(r'(?:set|update)\s+["\']?(?P<anchor>.+?)["\']?\s+in\s+slide\s+(?P<slide>\d+)\s+to\s+include\s+(?P<value>.+)$', text, re.I)
        if include:
            result = {key: value.strip() for key, value in include.groupdict().items() if value}
            result["action"] = "include"
            return result
        # Update "SFA Interfaces" Previous Status to "Delayed" on slide 3
        table = re.search(r'(?:update|set|change)\s+["\']?(?P<entity>.+?)["\']?\s+(?P<field>[A-Za-z][\w /&()-]*?)\s+(?:to|as)\s+["\']?(?P<value>.+?)["\']?(?:\s+on\s+slide\s+(?P<slide>\d+))?$', text, re.I)
        if table:
            result = {key: value.strip() for key, value in table.groupdict().items() if value}
            # A field is usually a short label; use a known separator when
            # possible, otherwise table schema matching will still reject bad fits.
            return result
        text_update = re.search(r'(?:update|set|change)\s+["\']?(?P<anchor>.+?)["\']?\s+(?:to|as|with)\s+["\']?(?P<value>.+?)["\']?(?:\s+on\s+slide\s+(?P<slide>\d+))?$', text, re.I)
        if text_update:
            return {key: value.strip() for key, value in text_update.groupdict().items() if value}
        raise ValueError(f"Could not understand request: {text!r}")

    def create_update_plan_from_commands(self, commands: List[str]) -> UpdatePlan:
        """Create an update plan from natural language commands."""
        # Commands share the same semantic resolver as JSON; this keeps both
        # input routes free of shape IDs and direct row/column references.
        return self._create_semantic_update_plan({"changes": commands})

        plan_id = f"plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        plan = UpdatePlan(
            plan_id=plan_id,
            name=f"NL Update Plan {plan_id}",
            description=f"{len(commands)} commands to execute",
        )
        
        print(f"\n[PLAN] Processing {len(commands)} natural language commands...")
        
        # Merge document trees into a single tree for interpretation
        combined_tree = None
        for slide_trees in self.document_trees.values():
            for tree in slide_trees.values():
                combined_tree = tree
                break
        
        if combined_tree:
            processor = MultiCommandProcessor(combined_tree)
            operations = processor.process_commands(commands)
            
            for op in operations:
                plan.add_operation(op)
                print(f"  - {op.description}")
        
        return plan
    
    def validate_plan(self, plan: UpdatePlan) -> bool:
        """Validate an update plan."""
        print(f"\n[VALIDATE] Validating plan {plan.plan_id}...")
        
        is_valid, errors = PlanValidator.validate_plan(plan)
        
        if errors:
            print(f"  - Validation failed with {len(errors)} errors:")
            for error in errors[:5]:
                print(f"    - {error}")
        else:
            print("  - Plan is valid")
        
        return is_valid
    
    def execute_plan(self, plan: UpdatePlan, dry_run: bool = False) -> bool:
        """Execute an update plan."""
        if not self.validate_plan(plan):
            print("  - Plan validation failed, aborting execution")
            return False
        
        print(f"\n[EXECUTE] {'(DRY RUN) ' if dry_run else ''}Executing plan...")
        
        plan.status = "executing"
        plan.execution_started = datetime.now()
        
        successful = 0
        failed = 0
        
        for operation in plan.operations:
            try:
                # Execute operation (simplified)
                self._execute_operation(operation, dry_run)

                # Record audit entry
                self.audit_log.add_entry(
                    self.version_manager.record_audit_entry(
                        modification_type=self._audit_type_for(operation),
                        object_type=operation.target_id,
                        object_id=operation.target_id,
                        object_path=operation.target_path,
                        previous_value=operation.old_value,
                        new_value=operation.new_value,
                        change_description=operation.description,
                        operation_id=operation.operation_id,
                    )
                )

                plan.successful_operations.append(operation.operation_id)
                successful += 1
                
                print(f"  ✓ {operation.description}")
            
            except Exception as e:
                plan.failed_operations.append(operation.operation_id)
                plan.execution_errors[operation.operation_id] = str(e)
                failed += 1
                print(f"  ✗ {operation.description}: {str(e)}")
        
        plan.execution_completed = datetime.now()
        plan.status = "completed" if failed == 0 else "partial"
        
        print(f"\n  Summary: {successful} succeeded, {failed} failed")
        
        return failed == 0

    @staticmethod
    def _audit_type_for(operation: UpdateOperation) -> ModificationType:
        """Translate plan operations into the version manager's vocabulary."""
        mapping = {
            OperationType.UPDATE_TABLE_CELL: ModificationType.TABLE_CELL_UPDATE,
            OperationType.INSERT_TEXT: ModificationType.CONTENT_INSERTION,
            OperationType.DELETE_TEXT: ModificationType.CONTENT_DELETION,
            OperationType.DELETE_ELEMENT: ModificationType.ELEMENT_DELETE,
            OperationType.MOVE_ELEMENT: ModificationType.ELEMENT_MOVE,
            OperationType.UPDATE_FORMATTING: ModificationType.FORMATTING_UPDATE,
        }
        return mapping.get(operation.operation_type, ModificationType.TEXT_UPDATE)
    
    def _execute_operation(self, operation: UpdateOperation, dry_run: bool = False) -> None:
        """Apply an operation to the semantic tree, never to python-pptx."""
        if dry_run:
            return
        if "replace_section_body" in operation.tags:
            self.tree_planner.replace_section_body(operation.target_id, str(operation.new_value))
        else:
            self.tree_planner.apply(operation)
    
    def validate_presentation(self) -> ValidationReport:
        """Validate the presentation integrity."""
        print("\n[VALIDATE_PRESENTATION] Running integrity checks...")
        
        # Collect all trees for validation
        all_trees = []
        for slide_trees in self.document_trees.values():
            all_trees.extend(slide_trees.values())
        
        # Validate each tree
        reports = []
        for tree in all_trees:
            report = self.validation_engine.validate_document_tree(tree)
            reports.append(report)
        
        # Summarize
        total_passed = sum(1 for r in reports if r.passed)
        print(f"  - {total_passed}/{len(reports)} documents passed validation")
        
        if reports:
            return reports[0]
        
        from src.ppt_ai.validation_engine import ValidationReport
        return ValidationReport(
            report_id="none",
            slide_index=0,
            shape_name="None",
        )
    
    def save_presentation(self, output_path: str) -> str:
        """Save the updated presentation."""
        print(f"\n[SAVE] Saving presentation to {output_path}...")
        self._render_document_trees()
        try:
            self.prs.save(output_path)
            print("  - Saved successfully")
            return output_path
        except PermissionError:
            requested = Path(output_path)
            alternate = requested.with_name(
                f"{requested.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{requested.suffix}"
            )
            self.prs.save(str(alternate))
            print(f"  - Default file is open; saved as {alternate}")
            return str(alternate)

    def _find_table_cell(self, tree: DocumentTree, row_match: Any, column_name: Any, schema: Any = None) -> Optional[DocumentNode]:
        def normalise(value: Any) -> str:
            return re.sub(r"\s+", " ", str(value).replace("\n", " ")).strip().casefold()

        row_match, column_name = normalise(row_match), normalise(column_name)
        target_row = target_col = None

        # The schema knows the source table's semantic headers and is more
        # reliable than comparing raw rendered text from merged/header cells.
        if schema is not None:
            matching_row = schema.find_row_by_header(str(row_match), partial=True)
            matching_col = schema.find_col_by_header(str(column_name), partial=True)
            if matching_row and matching_col:
                target_row, target_col = schema.semantic_map.get((matching_row, matching_col), (None, None))

        headers = {
            node.table_column_index: normalise(node.text)
            for node in tree.nodes.values()
            if node.element_type == ElementType.TABLE_CELL and node.table_row_index == 0
        }
        if target_col is None:
            target_col = next((index for index, text in headers.items() if column_name == text or column_name in text or text in column_name), None)
        if target_col is None:
            return None

        if target_row is not None:
            candidate = next((node for node in tree.nodes.values()
                              if node.element_type == ElementType.TABLE_CELL
                              and node.table_row_index == target_row and node.table_column_index == target_col), None)
            if candidate is not None:
                return candidate

        # A leading serial-number column is common ("S. No" in the supplied
        # template), so a project/row label must be searched across every cell
        # in the row rather than assuming it is always column zero.
        matched_row = next((node.table_row_index for node in tree.nodes.values()
                            if node.element_type == ElementType.TABLE_CELL
                            and node.table_row_index > 0 and row_match in normalise(node.text)), None)
        if matched_row is not None:
            return next((node for node in tree.nodes.values()
                         if node.element_type == ElementType.TABLE_CELL
                         and node.table_row_index == matched_row and node.table_column_index == target_col), None)

        for node in tree.nodes.values():
            if node.element_type != ElementType.TABLE_CELL or node.table_column_index != 0 or node.table_row_index == 0:
                continue
            if row_match in normalise(node.text):
                return next((candidate for candidate in tree.nodes.values() if candidate.table_row_index == node.table_row_index and candidate.table_column_index == target_col), None)
        return None

    def _render_document_trees(self) -> None:
        """Materialize all semantic trees in one controlled rendering pass."""
        for slide_index, trees in self.document_trees.items():
            slide = self.prs.slides[slide_index - 1]
            shapes = {shape.shape_id: shape for shape in slide.shapes}
            for shape_name, tree in trees.items():
                shape = shapes.get(tree.shape_id)
                if shape is None:
                    continue
                if any(node.element_type == ElementType.TABLE for node in tree.nodes.values()) and getattr(shape, 'has_table', False):
                    self.renderer.render_table_tree(shape.table, tree)
                elif getattr(shape, 'has_text_frame', False):
                    self.renderer.render_text_tree(shape, tree)
    
    def export_audit_log(self, output_path: str) -> None:
        """Export audit log to file."""
        print(f"\n[EXPORT] Exporting audit log to {output_path}...")
        self.audit_log.export_to_json(output_path)
        print("  - Audit log exported")
    
    def export_version_history(self, output_path: str) -> None:
        """Export version history to file."""
        print(f"\n[EXPORT] Exporting version history to {output_path}...")
        self.version_manager.save_version_metadata(output_path)
        print("  - Version history exported")


def update_presentation_end_to_end(
    presentation_path: str,
    change_request: Dict[str, Any],
    output_path: str,
    use_nlp: bool = False,
) -> str:
    """End-to-end presentation update pipeline."""
    
    print("\n" + "="*60)
    print("PPT-AI: Hierarchical Document Editing Pipeline")
    print("="*60)
    
    # Initialize pipeline
    pipeline = PresentationUpdatePipeline(presentation_path)
    
    # Parse presentation
    pipeline.parse_presentation()
  
    
    # Build semantic graph
    pipeline.build_semantic_graph()
    
    # Create plan
    if use_nlp:
        # Use natural language commands
        commands = change_request.get('commands', [])
        plan = pipeline.create_update_plan_from_commands(commands)
    else:
        # Use JSON format
        plan = pipeline.create_update_plan_from_json(change_request)
    
    # Execute plan
    success = pipeline.execute_plan(plan, dry_run=False)
    print("\nAFTER EXECUTION")
    
    # Validate
    pipeline.validate_presentation()
    
    # Save
    saved_output_path = pipeline.save_presentation(output_path)
    
    # Export logs
    pipeline.export_audit_log(saved_output_path.replace('.pptx', '_audit.json'))
    pipeline.export_version_history(saved_output_path.replace('.pptx', '_versions.json'))
    
    print("\n" + "="*60)
    print(f"Update {'SUCCESSFUL' if success else 'PARTIAL'}")
    print("="*60 + "\n")
    
    # Return the actual path.  When the requested file is open in PowerPoint,
    # ``save_presentation`` deliberately writes a timestamped sibling instead
    # of silently losing the update.
    return saved_output_path
