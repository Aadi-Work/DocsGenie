"""BFL-specific understanding: loose notes → polished Cover + function rows."""

from __future__ import annotations

import re
from typing import Any

from app.office.agent.template_extract import LIST_KEYS, _kv, _pipe_rows, _section_blob

_FREQ = {
    "on demand": "Ondemand",
    "ondemand": "Ondemand",
    "ad hoc": "Ondemand",
    "adhoc": "Ondemand",
    "daily": "Daily",
    "weekly": "Weekly",
    "monthly": "Monthly",
    "yearly": "Yearly",
    "annual": "Yearly",
    "real time": "Realtime",
    "realtime": "Realtime",
}
_MANUAL = {
    "manual": "Manual",
    "automatic": "Automatic",
    "auto": "Automatic",
    "semi": "Manual",
    "semi-automatic": "Manual",
}
_FIT = {"fit": "Fit", "gap": "Gap", "partial": "Partial"}
_TYPE = {
    "screen": "Screen",
    "batch": "Batch",
    "report": "Report",
    "interface": "Interface",
    "workflow": "Workflow",
    "job": "Batch",
}

BFL_FORMAT_HELP = """**Expected BFL note format** (flexible — bullets/prose also OK)

**Cover** (maps to Cover!C16 / C17 / C18)
```
Project: YNS FnO ERP
Workstream: Acquire to Dispose
Business process: 55. Acquire to Dispose
```

**Functions** — one row per line, pipes in this order. Maps to *Business Function List*:
```
Process | Description | Input | Output | Department | Frequency | Manual/Automatic | Type | Module | Fit/Gap | Steps
```

Frequency: Ondemand / Daily / Weekly / Monthly / Yearly  
Manual/Automatic: Manual / Automatic  
Type: Screen / Batch / Report / Interface / Workflow  
Fit/Gap: Fit / Gap / Partial  
Steps: `1. … 2. … 3. …` (also `;` separated)
"""

BFL_SAMPLE_NOTES = """Project: YNS FnO ERP
Workstream: Acquire to Dispose
Business process: 55. Acquire to Dispose

Functions:
55.10.001 Capital Acquisition Approval | Formal approval of capex requests before purchase | Capex request; business case | Approved capex request | Finance | Ondemand | Manual | Screen | Fixed Assets | Gap | 1. Raise capex request 2. Review business case 3. Approve or reject
55.10.002 Fixed Asset Master Creation | Create and maintain FA master data after approval | Approved capex request | FA master record | Finance | Ondemand | Manual | Screen | Fixed Assets | Fit | 1. Open FA list 2. Create asset record 3. Assign group and location
55.10.003 Asset Acquisition Posting | Capitalize the approved asset in the subledger | FA master; vendor invoice | Acquisition journal | Finance | Ondemand | Manual | Screen | Fixed Assets | Fit | 1. Enter acquisition date and cost 2. Post acquisition journal 3. Confirm NBV
55.10.004 Asset Transfer | Transfer an asset between cost centers or locations | Transfer request; FA number | Updated FA master | Finance | Ondemand | Manual | Screen | Fixed Assets | Fit | 1. Select asset 2. Enter new cost center 3. Post transfer
55.10.005 Depreciation Run | Periodic depreciation calculation and posting | FA book; period close calendar | Depreciation journal | Finance | Monthly | Automatic | Batch | Fixed Assets | Fit | 1. Open depreciation proposal 2. Review amounts 3. Post to GL
55.10.006 Asset Disposal | Retire or sell an asset and post the gain or loss | Disposal request; FA number | Disposal journal | Finance | Ondemand | Manual | Screen | Fixed Assets | Gap | 1. Enter disposal date and proceeds 2. Calculate gain/loss 3. Post retirement
"""


def _norm_token(value: str, table: dict[str, str], default: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return default
    key = re.sub(r"\s+", " ", raw.lower())
    return table.get(key, raw[:40])


def _clean_title(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip(" -–—|")
    text = re.sub(r"^(?:hey|hi|hello)[,!\s-]*", "", text, flags=re.I)
    return text.strip()


def _infer_code_prefix(business_process: str) -> str:
    m = re.match(r"(\d+(?:\.\d+)*)", (business_process or "").strip())
    if not m:
        return "55.10"
    prefix = m.group(1)
    if prefix.count(".") == 0:
        return f"{prefix}.10"
    return prefix


def polish_function_row(
    row: dict[str, Any] | str,
    index: int,
    business_process: str = "",
) -> dict[str, str]:
    keys = LIST_KEYS["bfl"]["functions"]
    if isinstance(row, str):
        parts = [p.strip() for p in row.split("|")]
        row = {keys[i]: (parts[i] if i < len(parts) else "") for i in range(len(keys))}
    if not isinstance(row, dict):
        row = {}
    aliases = {
        "process": ["process", "name", "function", "business_process", "process_name"],
        "description": ["description", "desc", "summary"],
        "input": ["input", "inputs"],
        "output": ["output", "outputs"],
        "department": ["department", "dept", "related_departments"],
        "frequency": ["frequency", "freq"],
        "manual_auto": ["manual_auto", "manual", "automation"],
        "type": ["type", "item_type"],
        "module": ["module", "system_module", "system_module_name"],
        "fit_gap": ["fit_gap", "fit/gap", "fitgap"],
        "steps": ["steps", "functions", "function_steps", "procedure"],
    }
    out: dict[str, str] = {}
    for key in keys:
        val = ""
        for alt in aliases.get(key, [key]):
            if row.get(alt) not in (None, ""):
                val = str(row.get(alt)).strip()
                break
        out[key] = val

    process = out["process"]
    if process and not re.match(r"^\d", process):
        prefix = _infer_code_prefix(business_process)
        out["process"] = f"{prefix}.{index:03d} {process}"
    elif not process and out["description"]:
        prefix = _infer_code_prefix(business_process)
        out["process"] = f"{prefix}.{index:03d} {out['description'][:60]}"

    out["frequency"] = _norm_token(out["frequency"], _FREQ, "Ondemand")
    out["manual_auto"] = _norm_token(out["manual_auto"], _MANUAL, "Manual")
    out["fit_gap"] = _norm_token(out["fit_gap"], _FIT, "Fit")
    if out["type"]:
        out["type"] = _norm_token(out["type"], _TYPE, out["type"])
    else:
        out["type"] = "Screen"
    if not out["module"]:
        out["module"] = "Fixed Assets"
    if not out["department"]:
        out["department"] = "Finance"
    return out


def _rows_from_prose(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    keys = LIST_KEYS["bfl"]["functions"]

    expanded: list[str] = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        if re.search(r"\d+[.)]\s+\S", raw):
            parts = re.split(r"(?:(?<=\s)|^)\d+[.)]\s+", raw)
            chunks = [p.strip(" -–—;") for p in parts if p and len(p.strip()) > 5]
            if len(chunks) >= 2:
                expanded.extend(chunks)
                continue
        expanded.append(raw)

    skip_prefixes = (
        "project",
        "workstream",
        "business process",
        "functions",
        "bfl",
        "hey",
        "hi ",
        "hello",
        "we are",
        "for the",
    )

    for raw in expanded:
        raw = re.sub(r"^[-*•]+\s*", "", raw).strip()
        raw = re.sub(r"^\d+[.)]\s*", "", raw).strip()
        low = raw.lower()
        if len(raw) < 8:
            continue
        if any(low.startswith(p) for p in skip_prefixes):
            continue
        if "|" in raw:
            continue
        if len(raw) > 220 and not re.search(
            r"[:–—].{8,}|\binput\b|\boutput\b|\bmanual\b|\bgap\b",
            raw,
            re.I,
        ):
            continue

        row: dict[str, str] = {k: "" for k in keys}
        m = re.match(r"^([^:–—\-]{3,80})\s*[:–—\-]\s*(.+)$", raw)
        if m:
            row["process"] = m.group(1).strip()
            rest = m.group(2).strip()
        else:
            parts = re.split(r"[.;]", raw, maxsplit=1)
            row["process"] = parts[0].strip()[:80]
            rest = parts[1].strip() if len(parts) > 1 else raw

        if any(row["process"].lower().startswith(p) for p in skip_prefixes):
            continue
        if len(row["process"]) > 90:
            continue

        labeled = {
            "description": r"\b(?:description|desc)\b\s*[:\-]?\s*([^;|]+)",
            "input": r"\binput\b\s*[:\-]?\s*([^;|]+)",
            "output": r"\boutput\b\s*[:\-]?\s*([^;|]+)",
            "department": r"\b(?:department|dept|owner)\b\s*[:\-]?\s*([^;|]+)",
            "module": r"\b(?:module|app)\b\s*[:\-]?\s*([^;|]+)",
        }
        for key, pat in labeled.items():
            mm = re.search(pat, rest, re.I)
            if mm:
                row[key] = mm.group(1).strip()

        if not row["description"]:
            desc = rest
            desc = re.sub(
                r"\b(?:manual|automatic|auto|ondemand|on demand|daily|weekly|"
                r"monthly|fit|gap|screen|batch|report)\b",
                "",
                desc,
                flags=re.I,
            )
            desc = re.sub(r"\s{2,}", " ", desc).strip(" ;,")
            if ";" in desc:
                desc = desc.split(";", 1)[0].strip()
            row["description"] = desc[:160] if desc else row["process"]

        low_rest = rest.lower()
        for word, norm in _MANUAL.items():
            if re.search(rf"\b{re.escape(word)}\b", low_rest):
                row["manual_auto"] = norm
                break
        for word, norm in _FREQ.items():
            if word in low_rest:
                row["frequency"] = norm
                break
        for word, norm in _FIT.items():
            if re.search(rf"\b{re.escape(word)}\b", low_rest):
                row["fit_gap"] = norm
                break
        for word, norm in _TYPE.items():
            if re.search(rf"\b{re.escape(word)}\b", low_rest):
                row["type"] = norm
                break

        if row["process"]:
            rows.append(row)
    return rows


def summarize_bfl(notes: str) -> dict[str, Any]:
    text = (notes or "").strip()
    if not text:
        return {}

    kv = _kv(text)
    out: dict[str, Any] = {}

    for src, dest in (
        ("project_name", "project_name"),
        ("project", "project_name"),
        ("client", "project_name"),
        ("workstream", "workstream"),
        ("stream", "workstream"),
        ("business_process", "business_process"),
        ("process", "business_process"),
        ("top_level_process", "business_process"),
        ("cycle", "business_process"),
    ):
        if src in kv and dest not in out:
            out[dest] = _clean_title(kv[src])

    m = re.search(
        r"(?:BFL|business function list)\s+for\s+([^/\n]+)(?:\s*/\s*([^/\n]+))?(?:\s*/\s*([^\n.]+))?",
        text,
        re.I,
    )
    if m:
        out.setdefault("project_name", _clean_title(m.group(1)))
        if m.group(2):
            out.setdefault("workstream", _clean_title(m.group(2)))
        if m.group(3):
            out.setdefault("business_process", _clean_title(m.group(3)))

    if "acquire" in text.lower() and "dispose" in text.lower():
        out.setdefault("workstream", "Acquire to Dispose")
        out.setdefault("business_process", "55. Acquire to Dispose")

    blob = (
        _section_blob(
            text,
            "Functions",
            "Business Functions",
            "Function List",
            "Rows",
            "Function rows",
            "We need",
            "Scope",
        )
        or ""
    )
    if not blob:
        m = re.search(
            r"(?:functions?\s+we\s+need|functions?|function\s+list)\s*[:\-]\s*(.+)$",
            text,
            re.I | re.S,
        )
        blob = m.group(1).strip() if m else text

    rows = _pipe_rows(blob, LIST_KEYS["bfl"]["functions"], min_parts=2)
    if not rows:
        rows = _rows_from_prose(blob)
    if not rows:
        rows = _rows_from_prose(text)

    bp = str(out.get("business_process") or "")
    polished = [polish_function_row(r, i + 1, bp) for i, r in enumerate(rows)]
    polished = [
        r
        for r in polished
        if len(r.get("process", "")) >= 4 or len(r.get("description", "")) >= 4
    ]
    if polished:
        out["functions"] = polished

    out.setdefault("project_name", "YNS FnO ERP")
    out.setdefault("workstream", "Acquire to Dispose")
    out.setdefault("business_process", "55. Acquire to Dispose")
    return out
