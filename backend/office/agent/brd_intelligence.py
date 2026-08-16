"""BRD note format helpers from TemplateHub-Agent."""

from __future__ import annotations

import re
from typing import Any

BRD_FORMAT_HELP = """**Expected BRD note format** (or attach a file — Smart-fill maps it onto the form)

```
Process: 55.10 Capital Asset Acquisition
Prepared by: Ayushi Jain
Date: 13 August 2026
Area path: YNS-FnO-ERP
Overview: <one short process overview paragraph>

Items:
Epic | Name | Description | Acceptance criteria
Feature | Name | Description | Acceptance criteria
User Story | Name | Description | Acceptance criteria
```

- One item per line under **Items** (optional 5th column: process-flow steps)
- Type must be `Epic`, `Feature`, or `User Story`
- Missing flow steps are generated from the description / acceptance
- Detail pages after the summary table keep the sample page layout
- Gaps are filled from the SE37 knowledge-base summary on S3 (`KB/`) — process maps, HQ requirements, and requirement slides. The KB is not used to invent fields the BRD does not state.
"""

BRD_SAMPLE_NOTES = """Process: 55.10 Capital Asset Acquisition
Prepared by: Ayushi Jain
Date: 13 August 2026
Area path: YNS-FnO-ERP
Overview: Standardize capital asset acquisition from request through capitalization in D365 F&O with clear approvals and audit trail.

Items:
Epic | Capital Asset Acquisition | End-to-end process for acquiring and capitalizing fixed assets | All stages covered with approvals and audit trail
Feature | Capex Request Creation | Allow users to create capital expenditure requests for required assets | Request captured with asset details, estimated cost, business justification, and required date
Feature | Capex Approval Workflow | Route capital expenditure requests to authorized Finance approvers | Approved or rejected request with comments and approval history
Feature | Purchase Order for Capital Asset | Create purchase orders for approved capital asset requests | Purchase order created with approved asset and supplier details
Feature | Asset Receipt | Record receipt of the capital asset against the purchase order | Asset receipt recorded and purchase order updated
Feature | Fixed Asset Creation | Create fixed asset records for acquired capital assets | Fixed asset master created with mandatory fields validated
User Story | Create Capex Request | User can create a capital expenditure request with asset description, estimated cost, and required date | Mandatory fields validated and request submitted for approval
"""


def _split_flow_text(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    if " ; " in raw:
        return [p.strip() for p in raw.split(" ; ") if p.strip()]
    numbered = re.findall(r"(?:^|[;\n])\s*\d+[.)]\s*([^\n;]+)", raw)
    if numbered:
        return [n.strip() for n in numbered if n.strip()]
    if "\n" in raw:
        return [ln.strip().lstrip("-•* ").strip() for ln in raw.splitlines() if ln.strip()]
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", raw) if len(s.strip()) > 12]
    return sents[:8] if len(sents) >= 2 else [raw]


def generate_item_flow(item: dict[str, Any], all_items: list[dict[str, Any]], overview: str = "") -> list[str]:
    """Process-flow steps for one BRD item, derived from notes rather than invented systems."""
    existing = item.get("flow") or item.get("process_flow") or item.get("steps")
    if isinstance(existing, list) and existing:
        return [str(x).strip() for x in existing if str(x).strip()][:8]
    if isinstance(existing, str) and existing.strip():
        return _split_flow_text(existing)[:8]
    from_acc = _split_flow_text(str(item.get("acceptance") or ""))
    if len(from_acc) >= 2:
        return from_acc[:8]
    from_desc = _split_flow_text(str(item.get("description") or ""))
    if len(from_desc) >= 2:
        return from_desc[:8]
    typ = str(item.get("type") or "Feature")
    name = re.sub(r"^\[YNS\]\s*", "", str(item.get("name") or "")).strip() or "this step"
    if typ == "Epic":
        feats = [
            re.sub(r"^\[YNS\]\s*", "", str(row.get("name") or "")).strip()
            for row in all_items
            if str(row.get("type") or "") == "Feature" and str(row.get("name") or "").strip()
        ]
        if feats:
            return feats[:8]
        ov = _split_flow_text(overview)
        if ov:
            return ov[:6]
    if typ == "User Story":
        return [
            f"Open {name}",
            "Enter required details and validate mandatory fields",
            "Submit and record the outcome",
        ]
    return [
        f"Start {name}",
        f"Complete required checks for {name}",
        "Record the outcome and continue the process",
    ]


def polish_brd_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """Normalize items and attach a process flow to each row."""
    out = dict(ctx or {})
    items_in = out.get("items") or []
    items: list[dict[str, Any]] = []
    for item in items_in:
        if isinstance(item, str):
            parts = [p.strip() for p in item.split("|")]
            item = {
                "type": parts[0] if parts else "Feature",
                "name": parts[1] if len(parts) > 1 else item,
                "description": parts[2] if len(parts) > 2 else "",
                "acceptance": parts[3] if len(parts) > 3 else "",
                "flow": parts[4] if len(parts) > 4 else "",
            }
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        typ = str(item.get("type") or "Feature").strip()
        low = typ.lower().replace("_", " ")
        if "epic" in low:
            typ = "Epic"
        elif "user" in low and "story" in low:
            typ = "User Story"
        elif "feature" in low:
            typ = "Feature"
        row = {
            "type": typ,
            "name": name,
            "description": str(item.get("description") or item.get("desc") or "").strip(),
            "acceptance": str(item.get("acceptance") or item.get("acceptance_criteria") or "").strip(),
            "flow": item.get("flow") or item.get("process_flow") or item.get("steps") or "",
        }
        items.append(row)
    overview = str(out.get("overview") or "")
    for row in items:
        row["flow"] = generate_item_flow(row, items, overview)
    if items:
        out["items"] = items
    return out

