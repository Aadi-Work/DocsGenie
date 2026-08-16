from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.ppt_ai.document_tree import DocumentTree, DocumentNode, ElementType, TextFormatting


@dataclass
class SemanticParagraph:
    text: str
    formatting: TextFormatting
    node_id: str


@dataclass
class SemanticBullet:
    text: str
    level: int
    formatting: TextFormatting
    node_id: str


@dataclass
class SemanticSection:
    heading: str
    heading_level: int
    node_id: str
    paragraphs: List[SemanticParagraph] = field(default_factory=list)
    bullets: List[SemanticBullet] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SemanticTable:
    table_id: str
    rows: int
    cols: int
    headers: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SemanticImage:
    image_id: str
    caption: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SemanticSlide:
    slide_index: int
    shape_name: str
    title: str
    sections: List[SemanticSection] = field(default_factory=list)
    tables: List[SemanticTable] = field(default_factory=list)
    images: List[SemanticImage] = field(default_factory=list)
    relationships: List[Dict[str, Any]] = field(default_factory=list)

    def debug_outline(self) -> str:
        lines: List[str] = []
        lines.append(f"Slide {self.slide_index} - {self.shape_name}")
        if self.title:
            lines.append(f"Title: {self.title}")
        if self.sections:
            lines.append("Sections:")
            for section in self.sections:
                lines.append(f"- {section.heading}")
                for para in section.paragraphs:
                    lines.append(f"    ├── paragraph: {para.text}")
                for bullet in section.bullets:
                    lines.append(f"    ├── bullet: {bullet.text}")
        if self.tables:
            lines.append("Tables:")
            for table in self.tables:
                lines.append(f"- {table.table_id}: {table.rows}x{table.cols}")
        if self.images:
            lines.append("Images:")
            for image in self.images:
                lines.append(f"- {image.image_id}: {image.caption}")
        return "\n".join(lines)


class SemanticSlideBuilder:
    """Builds a semantic slide model from a document tree."""

    @staticmethod
    def build_from_document_tree(tree: DocumentTree) -> SemanticSlide:
        slide = SemanticSlide(
            slide_index=tree.slide_index,
            shape_name=tree.shape_name,
            title=tree.shape_name,
        )

        sections = []
        section_nodes = tree.find_by_type(ElementType.SECTION)
        for section in section_nodes:
            section_model = SemanticSection(
                heading=section.text,
                heading_level=section.heading_level,
                node_id=section.element_id,
            )
            for child_id in section.children_ids:
                child = tree.get_node(child_id)
                if child is None:
                    continue
                if child.element_type == ElementType.PARAGRAPH:
                    section_model.paragraphs.append(
                        SemanticParagraph(
                            text=child.text,
                            formatting=child.formatting.copy(),
                            node_id=child.element_id,
                        )
                    )
                elif child.element_type == ElementType.BULLET_ITEM:
                    section_model.bullets.append(
                        SemanticBullet(
                            text=child.text,
                            level=child.formatting.indentation_level,
                            formatting=child.formatting.copy(),
                            node_id=child.element_id,
                        )
                    )
            sections.append(section_model)

        if sections:
            slide.title = sections[0].heading
        else:
            paragraphs = tree.find_by_type(ElementType.PARAGRAPH)
            if paragraphs:
                slide.title = paragraphs[0].text

        slide.sections = sections
        slide.tables = SemanticSlideBuilder._build_tables(tree)
        slide.images = SemanticSlideBuilder._build_images(tree)
        slide.relationships = SemanticSlideBuilder._build_relationships(tree, slide)
        return slide

    @staticmethod
    def _build_tables(tree: DocumentTree) -> List[SemanticTable]:
        tables: List[SemanticTable] = []
        table_nodes = tree.find_by_type(ElementType.TABLE)
        for table_node in table_nodes:
            row_nodes = [tree.get_node(child_id) for child_id in table_node.children_ids]
            row_count = len(row_nodes)
            col_count = 0
            headers = []
            for row_node in row_nodes:
                if row_node is None:
                    continue
                cells = [tree.get_node(cell_id) for cell_id in row_node.children_ids]
                if row_node.table_row_index == 0:
                    headers = [cell.text for cell in cells if cell is not None]
                col_count = max(col_count, len([cell for cell in cells if cell is not None]))
            tables.append(SemanticTable(
                table_id=table_node.element_id,
                rows=row_count,
                cols=col_count,
                headers=headers,
            ))
        return tables

    @staticmethod
    def _build_images(tree: DocumentTree) -> List[SemanticImage]:
        images: List[SemanticImage] = []
        for node in tree.find_by_type(ElementType.IMAGE):
            images.append(SemanticImage(
                image_id=node.element_id,
                caption=node.image_caption or node.text,
                metadata={
                    "width": node.image_width,
                    "height": node.image_height,
                },
            ))
        return images

    @staticmethod
    def _build_relationships(tree: DocumentTree, slide: SemanticSlide) -> List[Dict[str, Any]]:
        relationships: List[Dict[str, Any]] = []
        for section in slide.sections:
            relationships.append({
                "source": slide.shape_name,
                "target": section.heading,
                "relation": "contains",
            })
            for para in section.paragraphs:
                relationships.append({
                    "source": section.heading,
                    "target": para.text,
                    "relation": "contains",
                })
            for bullet in section.bullets:
                relationships.append({
                    "source": section.heading,
                    "target": bullet.text,
                    "relation": "contains",
                })
        for table in slide.tables:
            relationships.append({
                "source": slide.shape_name,
                "target": table.table_id,
                "relation": "contains",
            })
        for image in slide.images:
            relationships.append({
                "source": slide.shape_name,
                "target": image.image_id,
                "relation": "contains",
            })
        return relationships
