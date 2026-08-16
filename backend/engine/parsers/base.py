"""Parser registry: file extension -> TemplateIR producer."""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from typing import Dict, Type

from ..logging_config import get_logger

_log = get_logger("parsers.base")

from ..ir import DocType, TemplateIR, ValueFormat

# Literal {{placeholder}} / <placeholder> / [PLACEHOLDER] already in the template.
PLACEHOLDER_RE = re.compile(r"\{\{\s*([\w.\-]+)\s*\}\}|\$\{\s*([\w.\-]+)\s*\}|<<\s*([\w.\- ]+)\s*>>")

# Cells that are visually "blank lines" the user drew to be written on.
BLANK_LINE_RE = re.compile(r"^[_\-.\s]{3,}$")


def detect_value_format(number_format: str | None, sample_text: str = "") -> ValueFormat:
    """Number format is a far better type signal than the label ever is."""
    nf = (number_format or "").lower()
    if not nf or nf == "general":
        pass
    else:
        if any(t in nf for t in ("yyyy", "yy", "dd", "mmm")) and "h" not in nf.replace("mmm", ""):
            return ValueFormat.DATE
        if any(t in nf for t in ("h:mm", "hh:mm", "am/pm", "ss")):
            return ValueFormat.TIME
        if "%" in nf:
            return ValueFormat.PERCENT
        if any(sym in nf for sym in ("$", "€", "£", "₹", "usd", "eur", "inr", "\\r")) or "#,##" in nf and "." in nf:
            return ValueFormat.CURRENCY
        if any(c in nf for c in ("0", "#")):
            return ValueFormat.NUMBER
    s = (sample_text or "").strip()
    if re.fullmatch(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}", s):
        return ValueFormat.DATE
    if re.fullmatch(r"\d{1,2}:\d{2}\s*(am|pm)?", s, re.I):
        return ValueFormat.TIME
    return ValueFormat.UNKNOWN


def find_placeholder(text: str) -> str | None:
    m = PLACEHOLDER_RE.search(text or "")
    if not m:
        return None
    return next(g for g in m.groups() if g)


def looks_like_label(text: str) -> bool:
    """
    Label heuristics - intentionally loose, because the semantic layer and the
    validation gate both get a veto later.
    """
    t = (text or "").strip()
    if not t or len(t) > 80:
        return False
    if t.endswith(":"):
        return True
    words = t.replace("/", " ").split()
    if len(words) > 8:
        return False
    if t.endswith((".", "!", "?")) and len(words) > 4:
        return False        # a sentence, not a label
    return True


class TemplateParser(ABC):
    doc_type: DocType

    @abstractmethod
    def parse(self, path: str) -> TemplateIR:
        ...


_REGISTRY: Dict[str, Type[TemplateParser]] = {}


def register(ext: str, cls: Type[TemplateParser]) -> None:
    _REGISTRY[ext.lower().lstrip(".")] = cls


def get_parser(path: str) -> TemplateParser:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext not in _REGISTRY:
        _log.error("No parser for '.%s' - supported: %s", ext, sorted(_REGISTRY))
        raise ValueError(f"No parser registered for '.{ext}' (have: {sorted(_REGISTRY)})")
    return _REGISTRY[ext]()


def parse_template(path: str) -> TemplateIR:
    return get_parser(path).parse(path)
