"""
Shape Classifier

Classifies PowerPoint shapes into high-level semantic roles.

NOTE:
This classifier identifies only the ROLE OF THE SHAPE.

It does NOT identify:
    - Sections
    - Paragraphs
    - Bullet lists
    - Headings

Those belong to DocumentParser.
"""

from pptx.enum.shapes import MSO_SHAPE_TYPE

from src.ppt_ai.models import ShapeRole


class ShapeClassifier:

    @staticmethod
    def classify(shape):
        """
        Classify a PowerPoint shape into one of the ShapeRole values.
        """

        # ---------------------------------------------------------
        # TABLE
        # ---------------------------------------------------------
        if getattr(shape, "has_table", False):
            return ShapeRole.TABLE

        # ---------------------------------------------------------
        # IMAGE
        # ---------------------------------------------------------
        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            return ShapeRole.IMAGE

        # ---------------------------------------------------------
        # CHART
        # ---------------------------------------------------------
        if hasattr(shape, "has_chart"):
            try:
                if shape.has_chart:
                    return ShapeRole.CHART
            except Exception:
                pass

        # ---------------------------------------------------------
        # CONNECTORS / LINES
        #
        # python-pptx exposes connectors as LINE.
        # ---------------------------------------------------------
        if shape.shape_type == MSO_SHAPE_TYPE.LINE:
            return ShapeRole.UNKNOWN

        # ---------------------------------------------------------
        # GROUP SHAPES
        # ---------------------------------------------------------
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            return ShapeRole.UNKNOWN

        # ---------------------------------------------------------
        # TEXT SHAPES
        # ---------------------------------------------------------
        if getattr(shape, "has_text_frame", False):

            text = shape.text.strip()

            if not text:
                return ShapeRole.TEXT

            upper = text.upper()

            # -----------------------------------------------------
            # TITLE PLACEHOLDERS
            # -----------------------------------------------------
            if (
                shape.name.lower().startswith("title")
                or "TITLE" in shape.name.upper()
            ):
                return ShapeRole.TITLE

            # -----------------------------------------------------
            # STATUS BADGES
            # -----------------------------------------------------
            if upper in {
                "ON TRACK",
                "AT RISK",
                "OFF TRACK",
                "PAUSED",
                "DELAYED",
                "IN PROGRESS",
                "COMPLETED",
            }:
                return ShapeRole.STATUS_BADGE

            # -----------------------------------------------------
            # LEGEND
            # -----------------------------------------------------
            if (
                "🟩" in text
                or "🟨" in text
                or "🟥" in text
                or "⬜" in text
            ):
                return ShapeRole.LEGEND

            # -----------------------------------------------------
            # FOOTER
            # -----------------------------------------------------
            if (
                "CONFIDENTIAL" in upper
                or "COPYRIGHT" in upper
            ):
                return ShapeRole.FOOTER

            # -----------------------------------------------------
            # EVERYTHING ELSE
            # -----------------------------------------------------
            return ShapeRole.TEXT

        # ---------------------------------------------------------
        # DEFAULT
        # ---------------------------------------------------------
        return ShapeRole.UNKNOWN
