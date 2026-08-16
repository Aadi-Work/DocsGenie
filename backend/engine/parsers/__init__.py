"""Importing this package registers every available parser."""

from .base import (TemplateParser, get_parser, parse_template, register,  # noqa: F401
                   detect_value_format, find_placeholder, looks_like_label)
from ..logging_config import get_logger
from . import xlsx_parser  # noqa: F401
from . import docx_parser  # noqa: F401
from . import pptx_parser  # noqa: F401

__all__ = ["parse_template", "get_parser", "register", "TemplateParser"]

_log = get_logger("parsers")
_log.debug("Registered parsers for: xlsx, xlsm, xltx, docx, dotx, pptx, potx")
