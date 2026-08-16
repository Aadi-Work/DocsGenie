"""Importing this package registers every available renderer."""

from .base import (BaseRenderer, RenderResult, WriteRecord,  # noqa: F401
                   get_renderer, register, render_plan)
from . import xlsx_renderer  # noqa: F401
from . import docx_renderer  # noqa: F401
from . import pptx_renderer  # noqa: F401

__all__ = ["render_plan", "get_renderer", "register", "BaseRenderer",
           "RenderResult", "WriteRecord"]
