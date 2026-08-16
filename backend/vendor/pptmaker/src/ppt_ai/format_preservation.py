"""
Format Preservation Engine

Captures and preserves all formatting details when updating content.
Ensures that users only notice the changed text, not formatting differences.
"""

from typing import Optional, Dict, Any, List, Tuple

try:
    from pptx.util import Pt, Inches, Emu
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    from pptx.dml.color import RGBColor
    from pptx.text.text import TextFrame
    from pptx.text.text import _Paragraph
    from pptx.text.text import _Run
except ImportError:  # pragma: no cover - dependency may be absent in some environments
    Pt = Inches = Emu = None
    PP_ALIGN = MSO_ANCHOR = RGBColor = None
    TextFrame = _Paragraph = _Run = Any

from src.ppt_ai.document_tree import TextFormatting, Alignment


class FormatExtractor:
    """Extracts formatting information from PowerPoint text elements."""
    
    @staticmethod
    def is_bullet_paragraph(paragraph: _Paragraph) -> bool:
        """Detect native PowerPoint bullets stored in paragraph-properties XML."""
        try:
            p_pr = paragraph._p.pPr
            return p_pr is not None and any(
                child.tag.rsplit('}', 1)[-1] in {'buChar', 'buAutoNum', 'buBlip'}
                for child in p_pr
            )
        except Exception:
            return False

    @staticmethod
    def extract_run_formatting(run: _Run) -> TextFormatting:
        """Extract formatting from a text run."""
        formatting = TextFormatting()
        
        # Font properties
        if run.font.name:
            formatting.font_name = run.font.name
        
        if run.font.size:
            formatting.font_size = run.font.size.pt
        
        formatting.bold = run.font.bold or False
        formatting.italic = run.font.italic or False
        formatting.underline = run.font.underline or False
        
        # Color
        if run.font.color.type:
            try:
                rgb = run.font.color.rgb
                if rgb:
                    formatting.color_rgb = (rgb[0], rgb[1], rgb[2])
                    formatting.color_hex = f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
            except Exception:
                pass
        
        # Hyperlink
        if hasattr(run, '_r') and run._r is not None:
            # Check for hyperlink in the run's XML
            try:
                rPr = run._r.find('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}rPr')
                if rPr is not None:
                    hyperlink = rPr.find('.//{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed')
                    if hyperlink is not None:
                        formatting.hyperlink = hyperlink.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            except Exception:
                pass
        
        return formatting
    
    @staticmethod
    def extract_paragraph_formatting(paragraph: _Paragraph) -> TextFormatting:
        """Extract formatting from a paragraph."""
        formatting = TextFormatting()
        
        # Alignment
        if paragraph.alignment == PP_ALIGN.CENTER:
            formatting.alignment = Alignment.CENTER
        elif paragraph.alignment == PP_ALIGN.RIGHT:
            formatting.alignment = Alignment.RIGHT
        elif paragraph.alignment == PP_ALIGN.JUSTIFY:
            formatting.alignment = Alignment.JUSTIFY
        else:
            formatting.alignment = Alignment.LEFT
        
        # Line spacing
        if paragraph.line_spacing:
            formatting.line_spacing = paragraph.line_spacing
        
        # Indentation
        if paragraph.level is not None:
            formatting.indentation_level = paragraph.level
        
        # Get formatting from first run if available
        if paragraph.runs:
            first_run_format = FormatExtractor.extract_run_formatting(paragraph.runs[0])
            formatting.font_name = first_run_format.font_name
            formatting.font_size = first_run_format.font_size
            formatting.bold = first_run_format.bold
            formatting.italic = first_run_format.italic
            formatting.underline = first_run_format.underline
            formatting.color_rgb = first_run_format.color_rgb
            formatting.color_hex = first_run_format.color_hex
        
        # Bullet glyphs are not part of paragraph.text in python-pptx.
        if FormatExtractor.is_bullet_paragraph(paragraph):
            formatting.bullet_format = "•"
        
        return formatting
    
    @staticmethod
    def extract_text_frame_formatting(text_frame: TextFrame) -> List[TextFormatting]:
        """Extract formatting from all paragraphs in a text frame."""
        formatters = []
        for paragraph in text_frame.paragraphs:
            formatters.append(FormatExtractor.extract_paragraph_formatting(paragraph))
        return formatters


class FormatApplier:
    """Applies saved formatting when updating text."""
    
    @staticmethod
    def apply_to_run(run: _Run, formatting: TextFormatting) -> None:
        """Apply formatting to a text run."""
        run.font.name = formatting.font_name
        run.font.size = Pt(formatting.font_size)
        run.font.bold = formatting.bold
        run.font.italic = formatting.italic
        run.font.underline = formatting.underline
        
        if formatting.color_rgb:
            try:
                run.font.color.rgb = RGBColor(*formatting.color_rgb)
            except Exception:
                pass
        
        if formatting.background_color_rgb:
            try:
                # Note: PowerPoint text background is limited; this is best effort
                pass
            except Exception:
                pass
    
    @staticmethod
    def apply_to_paragraph(paragraph: _Paragraph, formatting: TextFormatting) -> None:
        """Apply formatting to a paragraph."""
        # Alignment
        alignment_map = {
            Alignment.LEFT: PP_ALIGN.LEFT,
            Alignment.CENTER: PP_ALIGN.CENTER,
            Alignment.RIGHT: PP_ALIGN.RIGHT,
            Alignment.JUSTIFY: PP_ALIGN.JUSTIFY,
        }
        paragraph.alignment = alignment_map.get(formatting.alignment, PP_ALIGN.LEFT)
        
        # Line spacing
        if formatting.line_spacing > 0:
            paragraph.line_spacing = formatting.line_spacing
        
        # Level (indentation)
        if formatting.indentation_level >= 0:
            paragraph.level = formatting.indentation_level
        
        # Apply run formatting to all runs
        for run in paragraph.runs:
            FormatApplier.apply_to_run(run, formatting)
    
    @staticmethod
    def apply_to_text_frame(text_frame: TextFrame, formatters: List[TextFormatting]) -> None:
        """Apply formatting to all paragraphs in a text frame."""
        for idx, paragraph in enumerate(text_frame.paragraphs):
            if idx < len(formatters):
                FormatApplier.apply_to_paragraph(paragraph, formatters[idx])


class FormatPreserver:
    """Main class for preserving formatting during text updates."""
    
    def __init__(self):
        self.extractor = FormatExtractor()
        self.applier = FormatApplier()
        self.saved_formats: Dict[str, List[TextFormatting]] = {}
    
    def save_text_frame_formatting(self, shape_id: int, text_frame: TextFrame) -> None:
        """Save the formatting of a text frame before updating it."""
        key = f"shape_{shape_id}"
        self.saved_formats[key] = self.extractor.extract_text_frame_formatting(text_frame)
    
    def restore_text_frame_formatting(self, shape_id: int, text_frame: TextFrame) -> None:
        """Restore previously saved formatting to a text frame."""
        key = f"shape_{shape_id}"
        if key in self.saved_formats:
            self.applier.apply_to_text_frame(text_frame, self.saved_formats[key])
    
    def update_text_preserve_formatting(
        self,
        text_frame: TextFrame,
        new_text: str,
        paragraph_index: Optional[int] = None,
    ) -> None:
        """Update text in a paragraph while preserving formatting."""
        # Save the current formatting
        saved_formats = self.extractor.extract_text_frame_formatting(text_frame)
        
        if paragraph_index is None:
            # Update all paragraphs
            paragraph_index = 0
        
        # Ensure the paragraph exists
        if paragraph_index < len(text_frame.paragraphs):
            paragraph = text_frame.paragraphs[paragraph_index]
            saved_format = saved_formats[paragraph_index] if paragraph_index < len(saved_formats) else TextFormatting()
            
            # Clear existing runs
            for run in paragraph.runs:
                run.text = ""
            
            # Add new text with preserved formatting
            if paragraph.runs:
                # Use existing run
                paragraph.runs[0].text = new_text
                self.applier.apply_to_run(paragraph.runs[0], saved_format)
            else:
                # Create new run
                run = paragraph.add_run()
                run.text = new_text
                self.applier.apply_to_run(run, saved_format)
    
    def merge_runs_with_formatting(
        self,
        text_frame: TextFrame,
        paragraph_index: int,
        new_segments: List[Tuple[str, Optional[TextFormatting]]],
    ) -> None:
        """
        Update a paragraph with multiple text segments, each with optional custom formatting.
        
        Args:
            text_frame: The text frame to update
            paragraph_index: Index of the paragraph
            new_segments: List of (text, formatting) tuples
        """
        if paragraph_index >= len(text_frame.paragraphs):
            return
        
        paragraph = text_frame.paragraphs[paragraph_index]
        saved_format = self.extractor.extract_paragraph_formatting(paragraph)
        
        # Clear existing runs
        for run in paragraph.runs:
            run.text = ""
        
        # Remove extra runs
        while len(paragraph.runs) > len(new_segments):
            # Remove from the end (can't delete easily in python-pptx)
            pass
        
        # Add new segments
        for idx, (text, custom_format) in enumerate(new_segments):
            if idx < len(paragraph.runs):
                run = paragraph.runs[idx]
            else:
                run = paragraph.add_run()
            
            run.text = text
            
            # Use custom formatting if provided, otherwise use saved format
            format_to_apply = custom_format if custom_format else saved_format
            self.applier.apply_to_run(run, format_to_apply)


class StyleTransfer:
    """Transfer styling from one element to another."""
    
    @staticmethod
    def copy_paragraph_style(source: _Paragraph, target: _Paragraph) -> None:
        """Copy paragraph styling from source to target."""
        target.alignment = source.alignment
        target.level = source.level
        
        if source.line_spacing:
            target.line_spacing = source.line_spacing
    
    @staticmethod
    def copy_run_style(source: _Run, target: _Run) -> None:
        """Copy run styling from source to target."""
        target.font.name = source.font.name
        target.font.size = source.font.size
        target.font.bold = source.font.bold
        target.font.italic = source.font.italic
        target.font.underline = source.font.underline
        
        if source.font.color.type:
            try:
                target.font.color.rgb = source.font.color.rgb
            except Exception:
                pass
