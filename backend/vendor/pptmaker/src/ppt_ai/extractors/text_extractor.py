from pptx.enum.text import PP_ALIGN

try:
    from pptx.oxml.xmlchemy import OxmlElement
except ImportError:
    OxmlElement = None

from src.ppt_ai.models import (
    ParagraphNode,
    RunNode,
    FontStyle
)


class TextExtractor:
    """
    Responsible for reading and writing PowerPoint text.
    """

    # ======================================================
    # READ
    # ======================================================

    @staticmethod
    def extract(shape):

        paragraphs = []

        if not shape.has_text_frame:
            return paragraphs

        for para in shape.text_frame.paragraphs:

            paragraph = ParagraphNode(
                text=para.text,
                alignment=str(para.alignment),
                level=para.level,
                runs=[]
            )

            for run in para.runs:

                color = None

                try:
                    if run.font.color.rgb:
                        color = str(run.font.color.rgb)
                except Exception:
                    pass

                style = FontStyle(
                    name=run.font.name,
                    size=run.font.size.pt if run.font.size else None,
                    bold=run.font.bold,
                    italic=run.font.italic,
                    underline=run.font.underline,
                    color=color
                )

                paragraph.runs.append(
                    RunNode(
                        text=run.text,
                        style=style
                    )
                )

            paragraphs.append(paragraph)

        return paragraphs

    # ======================================================
    # WRITE
    # ======================================================

    @staticmethod
    def apply(shape, paragraphs):

        if not shape.has_text_frame:
            return

        text_frame = shape.text_frame
        existing_paragraphs = list(text_frame.paragraphs)

        if not existing_paragraphs:
            existing_paragraphs.append(text_frame.add_paragraph())

        for index, paragraph_node in enumerate(paragraphs):
            if index < len(existing_paragraphs):
                para = existing_paragraphs[index]
            else:
                para = text_frame.add_paragraph()
                existing_paragraphs.append(para)

            para.level = paragraph_node.level

            if para.runs:
                para.runs[0].text = paragraph_node.text
                for run in para.runs[1:]:
                    run.text = ""
                run = para.runs[0]
            else:
                run = para.add_run()
                run.text = paragraph_node.text

            style = None

            if paragraph_node.runs:
                style = paragraph_node.runs[0].style

            if style:
                run.font.name = style.name

                if style.size:
                    run.font.size = style.size

                run.font.bold = style.bold

                run.font.italic = style.italic

                run.font.underline = style.underline

        for leftover in existing_paragraphs[len(paragraphs):]:
            for run in leftover.runs:
                run.text = ""
            leftover.level = 0