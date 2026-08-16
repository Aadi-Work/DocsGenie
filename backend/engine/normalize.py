"""
Value normalisation.

The AI decides *what* goes where. This module decides *what shape* it takes,
deterministically, so the same source text always yields the same cell value.
Anything it cannot parse confidently it returns untouched rather than guessing.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Optional, Tuple

from .ir import ValueFormat

try:
    from dateutil import parser as _dateutil
except Exception:                                       # pragma: no cover
    _dateutil = None

MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], 1)}
MONTHS.update({m[:3].lower(): i for m, i in list(MONTHS.items())})

_DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), ("y", "m", "d")),
    (re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b"), ("d", "m", "y")),   # DMY default
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b"), ("d", "M", "y")),
    (re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b"), ("M", "d", "y")),
]
_TIME_RE = re.compile(r"\b(\d{1,2})[:.](\d{2})\s*(am|pm|AM|PM)?\b")
_NUM_RE = re.compile(r"-?[\d,]*\.?\d+")
_CURRENCY_SYMS = "₹$€£¥"


_DAY_MONTH_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\b")


def parse_date(text: str, dayfirst: bool = True, default_year: Optional[int] = None) -> Optional[dt.date]:
    s = (text or "").strip()
    if not s:
        return None
    if isinstance(text, (dt.date, dt.datetime)):
        return text.date() if isinstance(text, dt.datetime) else text
    for rx, order in _DATE_PATTERNS:
        m = rx.search(s)
        if not m:
            continue
        parts = dict(zip(order, m.groups()))
        try:
            year = int(parts["y"])
            month = MONTHS[parts["M"].lower()[:3]] if "M" in parts else int(parts["m"])
            day = int(parts["d"])
            if not dayfirst and "m" in parts and day <= 12 < month:
                day, month = month, day
            return dt.date(year, month, day)
        except Exception:
            continue
    if default_year is not None:
        # "by 20th August" - a bare day+month with no year at all, common in
        # relative narrative phrasing ("by the 20th of August"). The caller
        # supplies the year to use (typically the meeting's own year, when
        # known) since the text itself doesn't say.
        m = _DAY_MONTH_RE.search(s)
        if m:
            try:
                day, month = int(m.group(1)), MONTHS[m.group(2).lower()[:3]]
                return dt.date(default_year, month, day)
            except Exception:
                pass
    if _dateutil is not None:
        try:
            return _dateutil.parse(s, dayfirst=dayfirst, fuzzy=True).date()
        except Exception:
            return None
    return None


def parse_time(text: str) -> Optional[dt.time]:
    m = _TIME_RE.search(text or "")
    if not m:
        return None
    h, mi, mer = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    if mer == "pm" and h < 12:
        h += 12
    if mer == "am" and h == 12:
        h = 0
    if 0 <= h <= 23 and 0 <= mi <= 59:
        return dt.time(h, mi)
    return None


def parse_number(text: Any) -> Optional[float]:
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    s = str(text or "").strip()
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    for sym in _CURRENCY_SYMS:
        s = s.replace(sym, "")
    s = s.replace("%", "").strip()
    m = _NUM_RE.search(s)
    if not m:
        return None
    try:
        v = float(m.group(0).replace(",", ""))
        return -v if neg else v
    except ValueError:
        return None


def coerce(value: Any, fmt: ValueFormat | str, dayfirst: bool = True) -> Tuple[Any, bool]:
    """
    Returns (coerced_value, ok). ok=False means the value does not fit the
    target format - the validation gate turns that into a refusal, not a guess.
    """
    if isinstance(fmt, str):
        try:
            fmt = ValueFormat(fmt)
        except ValueError:
            fmt = ValueFormat.TEXT
    if value is None:
        return None, False

    if fmt == ValueFormat.DATE:
        if isinstance(value, (dt.date, dt.datetime)):
            return (value.date() if isinstance(value, dt.datetime) else value), True
        d = parse_date(str(value), dayfirst)
        return (d, True) if d else (str(value), False)

    if fmt == ValueFormat.TIME:
        if isinstance(value, dt.time):
            return value, True
        t = parse_time(str(value))
        return (t, True) if t else (str(value), False)

    if fmt in (ValueFormat.NUMBER, ValueFormat.CURRENCY, ValueFormat.PERCENT):
        n = parse_number(value)
        if n is None:
            return str(value), False
        if fmt == ValueFormat.PERCENT and "%" in str(value):
            n = n / 100.0
        return n, True

    if fmt == ValueFormat.BOOL:
        s = str(value).strip().lower()
        if s in ("yes", "y", "true", "1", "present", "attended"):
            return True, True
        if s in ("no", "n", "false", "0", "absent"):
            return False, True
        return str(value), False

    if fmt == ValueFormat.LIST:
        if isinstance(value, (list, tuple)):
            return "\n".join(f"• {v}" for v in value), True
        return str(value), True

    if isinstance(value, (list, tuple)):
        return "\n".join(str(v) for v in value), True
    return str(value), True


def to_display(value: Any, number_format: Optional[str] = None) -> str:
    """Flatten a value for formats (docx/pptx) that hold only text."""
    if value is None:
        return ""
    if isinstance(value, dt.datetime):
        return value.strftime("%d-%b-%Y %H:%M")
    if isinstance(value, dt.date):
        return value.strftime("%d-%b-%Y")
    if isinstance(value, dt.time):
        return value.strftime("%H:%M")
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (list, tuple)):
        return "\n".join(str(v) for v in value)
    return str(value)
