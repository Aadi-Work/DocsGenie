"""Exact layouts learned from the real PS-08 sample Office files."""

from __future__ import annotations

from typing import Any

BFL_LAYOUT: dict[str, Any] = {
    "file": "BFL Sample.xlsx",
    "cover_sheet": "Cover",
    "cover": {
        "project_name": "C16",
        "workstream": "C17",
        "business_process": "C18",
    },
    "list_sheet": "Business Function List",
    "header_row": 2,
    "level1_row": 3,
    "functions_start_row": 5,
    "columns": {
        "id": 1,
        "level": 2,
        "process": 3,
        "description": 4,
        "steps": 5,
        "item_type": 6,
        "input": 7,
        "output": 8,
        "department": 9,
        "frequency": 10,
        "manual_auto": 11,
        "type": 12,
        "module": 13,
        "fit_gap": 14,
        "addon_id": 15,
    },
    "pipe_keys": [
        "process",
        "description",
        "input",
        "output",
        "department",
        "frequency",
        "manual_auto",
        "type",
        "module",
        "fit_gap",
        "steps",
    ],
}

MOM_LAYOUT: dict[str, Any] = {
    "file": "MOM Sample.xlsx",
    "sheet": "Sheet1",
    "cells": {
        "meeting_date": "B5",
        "purpose": "E5",
        "prepared_by": "B6",
        "venue": "E6",
        "attendees_ymsli": "B7",
        "attendees_ymesg": "E7",
    },
    "summary_rows": (11, 24),
    "action_start_row": 28,
}

POC_LAYOUT: dict[str, Any] = {
    "file": "POC List sample.xlsx",
    "cover": {"project_name": "Cover!B15", "workstream": "Cover!B16"},
    "list_sheet": "YMVN POC list",
    "start_row": 2,
    "pipe_keys": [
        "id",
        "cycle",
        "subprocess",
        "title",
        "description",
        "prereq",
        "steps",
        "decision",
        "azure_ids",
    ],
}

BRD_LAYOUT: dict[str, Any] = {
    "file": "BRD Sample.docx",
    "pipe_keys": ["type", "name", "description", "acceptance"],
}
