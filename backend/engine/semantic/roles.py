"""
Semantic role taxonomy.

This is NOT a per-template mapping. It is a *vocabulary* of meanings that any
template's labels can be classified into. "Meeting Date", "Date of Meeting" and
"Conducted On" all resolve to `meeting_date` regardless of which cell they sit in.

Roles are open-world: `unknown:<slug>` roles are minted at runtime for labels the
taxonomy has never seen, so an unfamiliar template still produces a usable spec.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..ir import ValueFormat


@dataclass
class RoleDef:
    role: str
    synonyms: List[str]
    value_format: ValueFormat = ValueFormat.TEXT
    is_collection: bool = False
    critical: bool = False           # demands a higher confidence threshold
    item_fields: List[str] = field(default_factory=list)
    description: str = ""


# --------------------------------------------------------------------------
# Base taxonomy. Extend via RoleRegistry.load_pack() - do not fork this file.
# --------------------------------------------------------------------------
BASE_ROLES: List[RoleDef] = [
    # ---- document / meeting identity
    RoleDef("document_title", ["title", "document title", "subject", "topic", "re"],
            description="Overall title of the document"),
    RoleDef("meeting_title", ["meeting title", "meeting name", "meeting subject",
                              "agenda title", "discussion topic"]),
    RoleDef("purpose", ["purpose", "purpose of meeting", "objective", "meeting purpose",
                       "aim", "goal of meeting"]),
    RoleDef("meeting_date", ["meeting date", "date", "date of meeting", "conducted on",
                             "held on", "dated", "meeting held on", "date & time", "day"],
            ValueFormat.DATE, critical=True),
    RoleDef("meeting_time", ["time", "meeting time", "start time", "timing", "from",
                             "commenced at"], ValueFormat.TIME),
    RoleDef("end_time", ["end time", "closed at", "concluded at", "to"], ValueFormat.TIME),
    RoleDef("duration", ["duration", "length"], ValueFormat.TEXT),
    RoleDef("location", ["location", "venue", "place", "meeting room", "room",
                         "held at", "mode", "platform"]),
    RoleDef("facilitator", ["facilitator", "chair", "chairperson", "chaired by",
                            "organizer", "organiser", "host", "conducted by",
                            "moderator", "meeting lead"]),
    RoleDef("recorder", ["minutes by", "recorded by", "prepared by", "scribe",
                         "note taker", "notes by", "author", "documented by"]),
    RoleDef("approver", ["approved by", "reviewed by", "sign off", "signed by",
                         "authorized by"], critical=True),
    RoleDef("department", ["department", "dept", "team", "business unit", "division"]),
    RoleDef("project_name", ["project", "project name", "project title", "programme",
                             "program", "initiative"]),
    RoleDef("project_code", ["project code", "project id", "job number", "wbs",
                             "reference", "ref no", "document no", "doc id"]),
    RoleDef("customer", ["customer", "client", "account", "customer name",
                         "client name", "party"]),
    RoleDef("vendor", ["vendor", "supplier", "contractor"]),
    RoleDef("version", ["version", "rev", "revision", "issue"]),
    RoleDef("status", ["status", "state", "current status", "progress"]),
    RoleDef("priority", ["priority", "severity", "criticality"]),
    RoleDef("owner", ["owner", "responsible", "assignee", "assigned to", "responsibility",
                      "action by", "who", "person responsible", "spoc"]),
    RoleDef("due_date", ["due", "due date", "target date", "deadline", "by when",
                         "completion date", "eta", "target completion"], ValueFormat.DATE),
    RoleDef("start_date", ["start date", "from date", "commencement", "kickoff"], ValueFormat.DATE),
    RoleDef("end_date", ["end date", "to date", "finish date", "closure date"], ValueFormat.DATE),

    # ---- narrative blocks
    RoleDef("agenda", ["agenda", "agenda items", "points of discussion",
                       "topics", "purpose"], ValueFormat.LIST, is_collection=True),
    RoleDef("summary", ["summary", "executive summary", "overview", "abstract",
                        "brief", "background", "minutes", "notes"]),
    RoleDef("discussion", ["discussion", "key discussion",
                           "deliberations", "details", "remarks", "observations"]),
    RoleDef("discussion_points", ["discussion points", "meeting summary",
                                 "key discussion points", "summary points",
                                 "points discussed", "minutes of discussion"],
            is_collection=True, item_fields=["point"]),
    RoleDef("decisions", ["decision", "decisions", "decisions taken", "conclusions",
                          "resolutions", "outcome", "agreed"], ValueFormat.LIST, is_collection=True),
    RoleDef("risks", ["risk", "risks", "issues", "concerns", "blockers", "challenges"],
            ValueFormat.LIST, is_collection=True),
    RoleDef("next_steps", ["next steps", "way forward", "follow up", "follow-up",
                           "next meeting", "recommendations"], ValueFormat.LIST, is_collection=True),
    RoleDef("comments", ["comments", "notes", "additional notes", "remarks", "any other business", "aob"]),

    # ---- collections (tables)
    RoleDef("attendees", ["attendees", "participants", "present", "members present",
                          "attendance", "invitees", "participant list", "present members"],
            is_collection=True, item_fields=["name", "role", "department", "email", "present"]),
    RoleDef("absentees", ["absent", "absentees", "apologies", "not present", "regrets"],
            is_collection=True, item_fields=["name", "role", "reason"]),
    RoleDef("action_items", ["action items", "actions", "action plan", "action point",
                             "action points", "tasks", "task list", "to do", "todo",
                             "deliverables", "follow up actions", "action tracker"],
            is_collection=True, critical=True,
            item_fields=["task", "owner", "due_date", "status", "priority", "remarks"]),
    RoleDef("agenda_items", ["agenda item", "sr no", "s no", "sl no", "item",
                             "topic discussed", "point"], is_collection=True,
            item_fields=["item", "presenter", "discussion", "outcome"]),

    # ---- finance-ish (proves the taxonomy is not meeting-specific)
    RoleDef("amount", ["amount", "value", "total", "cost", "price", "budget"],
            ValueFormat.CURRENCY, critical=True),
    RoleDef("quantity", ["qty", "quantity", "units", "no of", "count"], ValueFormat.NUMBER),
    RoleDef("invoice_number", ["invoice no", "invoice number", "bill no", "po number",
                               "purchase order"], critical=True),
    RoleDef("line_items", ["line items", "items", "particulars", "description of goods",
                           "bill of materials"], is_collection=True,
            item_fields=["description", "quantity", "unit_price", "amount"]),
]

# Sub-field synonyms used to classify *columns inside* a repeating table.
ITEM_FIELD_SYNONYMS: Dict[str, List[str]] = {
    "name": ["name", "attendee", "participant", "member", "person", "employee", "full name"],
    "role": ["role", "designation", "title", "position", "function"],
    "department": ["department", "dept", "team", "division", "org"],
    "email": ["email", "e-mail", "mail id", "email id"],
    "present": ["present", "attended", "attendance", "y/n", "yes/no"],
    "task": ["task", "action", "action item", "activity", "description", "work",
             "deliverable", "action required", "particulars"],
    "owner": ["owner", "responsible", "assignee", "assigned to", "action by", "by whom",
              "who", "responsibility", "spoc", "person"],
    "due_date": ["due", "due date", "target date", "deadline", "by when", "eta",
                 "completion date", "timeline"],
    "status": ["status", "state", "progress", "open/closed", "closure status"],
    "priority": ["priority", "severity", "importance"],
    "remarks": ["remarks", "comments", "notes", "observation"],
    "item": ["item", "topic"],
    "point": ["point", "detail", "details", "discussion point", "summary point"],
    "presenter": ["presenter", "speaker", "presented by", "led by"],
    "discussion": ["discussion", "details", "summary", "minutes", "notes"],
    "outcome": ["outcome", "decision", "conclusion", "resolution", "result"],
    "reason": ["reason", "cause", "justification"],
    "description": ["description", "particulars", "details", "item description"],
    "quantity": ["qty", "quantity", "units", "nos", "count"],
    "unit_price": ["rate", "unit price", "price", "unit cost"],
    "amount": ["amount", "total", "value", "line total"],
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")

# A serial-number column ("Sl.No.", "Sr No", "#") is auto-generated by the
# renderer (1, 2, 3, ...) rather than sourced from data - it's never a real
# field to extract or map, just row numbering. Kept separate from the
# generic "item" field so a table's real item_fields list never has to
# account for it explicitly.
SERIAL_NUMBER_HEADERS = ["sl no", "sl. no.", "sr no", "sr. no.", "s no", "s. no.",
                        "serial no", "serial number", "#", "no.", "no"]


def looks_like_serial_number_header(header: str) -> bool:
    h = _SLUG_RE.sub(" ", (header or "").strip().lower()).strip()
    return h in {_SLUG_RE.sub(" ", s).strip() for s in SERIAL_NUMBER_HEADERS}


def slugify(text: str) -> str:
    return _SLUG_RE.sub("_", (text or "").strip().lower()).strip("_")[:48] or "field"


class RoleRegistry:
    """Holds the taxonomy and mints open-world roles for unseen labels."""

    def __init__(self, roles: Optional[List[RoleDef]] = None):
        self._roles: Dict[str, RoleDef] = {}
        for r in (roles if roles is not None else BASE_ROLES):
            self._roles[r.role] = r

    # -- access ---------------------------------------------------------
    def all(self) -> List[RoleDef]:
        return list(self._roles.values())

    def get(self, role: str) -> Optional[RoleDef]:
        return self._roles.get(role)

    def is_collection(self, role: str) -> bool:
        d = self.get(role)
        return bool(d and d.is_collection)

    def is_critical(self, role: str) -> bool:
        d = self.get(role)
        return bool(d and d.critical)

    def expected_format(self, role: str) -> ValueFormat:
        d = self.get(role)
        return d.value_format if d else ValueFormat.UNKNOWN

    # -- extension ------------------------------------------------------
    def add(self, role_def: RoleDef) -> RoleDef:
        self._roles[role_def.role] = role_def
        return role_def

    def load_pack(self, pack: Dict[str, dict]) -> None:
        """Domain packs: {"hse_incident": {"synonyms": [...], "value_format": "date"}}"""
        for role, cfg in pack.items():
            self.add(RoleDef(
                role=role,
                synonyms=[s.lower() for s in cfg.get("synonyms", [])],
                value_format=ValueFormat(cfg.get("value_format", "text")),
                is_collection=bool(cfg.get("is_collection", False)),
                critical=bool(cfg.get("critical", False)),
                item_fields=cfg.get("item_fields", []),
                description=cfg.get("description", ""),
            ))

    def mint_unknown(self, label: str, is_collection: bool = False) -> RoleDef:
        """An unfamiliar label still gets a stable, machine-usable role."""
        role = f"x_{slugify(label)}"
        if role in self._roles:
            return self._roles[role]
        return self.add(RoleDef(role=role, synonyms=[label.lower()],
                                is_collection=is_collection,
                                description=f"Discovered from template label: {label!r}"))


DEFAULT_REGISTRY = RoleRegistry()
