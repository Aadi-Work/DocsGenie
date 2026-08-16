"""Heuristic MOM extraction mapped to MOM Sample.xlsx slots."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any


_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December|"
    "Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)


def _to_iso_date(text: str) -> str | None:
    text = (text or "").strip().rstrip(".,;")
    for fmt in (
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%d %B %Y",
        "%d %b %Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B, %Y",
        "%d %b, %Y",
    ):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _split_people(blob: str) -> list[str]:
    blob = re.sub(r"\band\b", ",", blob, flags=re.I)
    parts = [p.strip(" .") for p in blob.split(",")]
    people: list[str] = []
    for p in parts:
        if not p:
            continue
        if re.search(r"representatives|team|teams|attending|session", p, re.I):
            if re.search(r"^[A-Z][A-Za-z]+ Team$", p):
                people.append(p)
            continue
        if len(p) < 2:
            continue
        people.append(p)
    return people


def _agenda_items(text: str) -> list[str]:
    m = re.search(
        r"(?:agenda focused on|agenda included|agenda covered|agenda was)\s+(.+?)(?:\.|$)",
        text,
        re.I | re.S,
    )
    if not m:
        return []
    chunk = m.group(1)
    parts = re.split(r",|\band\b", chunk)
    items: list[str] = []
    for p in parts:
        p = p.strip(" .;")
        p = re.sub(
            r"^(reviewing|discussing|understanding|identifying|finalizing|confirming)\s+",
            "",
            p,
            flags=re.I,
        ).strip()
        if len(p) > 3:
            items.append(p[0].upper() + p[1:])
    return items


def _decision_items(text: str) -> list[str]:
    m = re.search(
        r"The key decisions from the meeting were to\s+(.+?)\.",
        text,
        re.I,
    )
    if m:
        return [p.strip() for p in re.split(r",|\band\b", m.group(1)) if len(p.strip()) > 5]
    found = re.findall(
        r"(?:It was also decided that|The team agreed that|The team agreed to)\s+([^.]+)",
        text,
        re.I,
    )
    return [f.strip() for f in found if len(f.strip()) > 8]


def _action_rows(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    action_section = text
    m = re.search(
        r"(?:action items[^:]*:|following action items[^:]*:)\s*(.+?)(?:\. The meeting concluded|$)",
        text,
        re.I | re.S,
    )
    if m:
        action_section = m.group(1)

    action_section = re.sub(r"\s*\n\s*", " ", action_section)
    segments = re.split(r";", action_section)
    for seg in segments:
        seg = seg.strip(" .")
        if "|" not in seg:
            continue
        parts = [p.strip() for p in seg.split("|")]
        if len(parts) < 2:
            continue
        owner = re.sub(
            r"^(?:the\s+)?following\s+action\s+items[^:]*:\s*",
            "",
            parts[0],
            flags=re.I,
        )
        owner = re.sub(r"^action items[^:]*:\s*", "", owner, flags=re.I).strip()
        task = parts[1] if len(parts) > 1 else ""
        row = {
            "action": task,
            "owner": owner,
            "due_date": parts[2] if len(parts) > 2 else "",
            "status": parts[3] if len(parts) > 3 else "Open",
            "remarks": parts[4] if len(parts) > 4 else "",
        }
        if row["due_date"]:
            iso = _to_iso_date(row["due_date"])
            if iso:
                row["due_date"] = iso
        if row["owner"] and row["action"]:
            rows.append(row)
    return rows


def extract_mom(notes: str) -> dict[str, Any]:
    text = " ".join(notes.split())
    out: dict[str, Any] = {}

    m = re.search(
        r"(?:The\s+)?([A-Z][^.]{5,120}?Meeting)\s+(?:was conducted|was held|took place)",
        text,
        re.I,
    )
    if not m:
        m = re.search(r"([A-Z][^.]{5,80}?Meeting)", text)
    if m:
        out["purpose"] = m.group(1).strip()

    m = re.search(rf"\bon\s+(\d{{1,2}}\s+(?:{_MONTHS})\s+\d{{4}})", text, re.I)
    if not m:
        m = re.search(rf"(\d{{1,2}}\s+(?:{_MONTHS})\s+\d{{4}})", text, re.I)
    if m:
        iso = _to_iso_date(m.group(1))
        if iso:
            out["meeting_date"] = iso

    m = re.search(r"\bat\s+the\s+([^.]+?)(?:\.|,|\s+The\s+meeting)", text, re.I)
    if not m:
        m = re.search(r"\bat\s+([A-Z][^.]{3,80}?(?:Room|Office|Hall|Floor)[^.,]*)", text)
    if m:
        out["venue"] = m.group(1).strip(" ,.")

    facilitator = None
    m = re.search(r"(?:facilitated by|chaired by|hosted by|led by)\s+([^,.]+)", text, re.I)
    if m:
        facilitator = m.group(1).strip()
        out["prepared_by"] = facilitator

    people: list[str] = []
    m = re.search(
        r"(?:facilitated by|chaired by)\s+[^,]+,\s*with\s+(.+?)\s+attending",
        text,
        re.I,
    )
    if m:
        people = _split_people(m.group(1))
        if facilitator and facilitator not in people:
            people = [facilitator, *people]
    else:
        m = re.search(r"(?:attendees?|participants?)\s*[:\-]\s*([^.]+)", text, re.I)
        if m:
            people = _split_people(m.group(1))
    if people:
        out["attendees_ymsli"] = people

    summary = _agenda_items(text)
    decisions = _decision_items(text)
    combined = summary + decisions
    seen: set[str] = set()
    summary_items = []
    for item in combined:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            summary_items.append(item)
    if summary_items:
        out["summary_items"] = summary_items

    actions = _action_rows(notes) or _action_rows(text)
    if actions:
        out["action_items"] = actions

    kv_actions = _pipe_action_lines(notes)
    if kv_actions:
        out["action_items"] = kv_actions

    if "purpose" not in out:
        for line in notes.splitlines():
            if ":" in line and re.match(r"^(purpose|title|subject|meeting)\b", line, re.I):
                out["purpose"] = line.split(":", 1)[1].strip()
                break
    if "purpose" not in out and len(notes.strip()) > 8:
        first = notes.strip().split(".")[0].strip()
        if len(first) > 5:
            out["purpose"] = first[:120]

    if "prepared_by" not in out:
        out["prepared_by"] = "YMSLI"
    if "meeting_date" not in out:
        out["meeting_date"] = datetime.now().date().isoformat()

    return out


def _pipe_action_lines(notes: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in notes.splitlines():
        line = line.strip().lstrip("-•* ").strip()
        if "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        head = " ".join(parts).lower()
        if parts[0].lower() in {"action", "task", "item"} and "owner" in head:
            continue
        due = parts[2] if len(parts) > 2 else ""
        rows.append(
            {
                "action": parts[0],
                "owner": parts[1],
                "due_date": _to_iso_date(due) or due,
                "status": parts[3] if len(parts) > 3 else "Open",
                "remarks": parts[4] if len(parts) > 4 else "",
            }
        )
    return rows
