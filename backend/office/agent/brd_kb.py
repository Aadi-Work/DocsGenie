"""Fill missing BRD form fields from the SE37 knowledge-base summary on S3."""

from __future__ import annotations

import re
from typing import Any

from app.services.kb_service import load_kb_summary

_CODE = re.compile(r"\b(\d{2}(?:\.\d{2,3}){0,3})\b")
_STEP = re.compile(r"^(\d{2}(?:\.\d{2,3})+)\s+(.+)$")
_NOISE_LINE = re.compile(r"^(?:\d{2}|requirement definition|process|how it works\??|methods of acquisition)$", re.I)

ALIASES = (
    (("acquire to dispose", "a2d", "capital asset", "fixed asset", "capex"), "55"),
    (("order to cash", "o2c"), "35"),
    (("procure to pay", "p2p", "purchase order"), "20"),
    (("prospect to quote", "p2q"), "30"),
    (("plan to produce", "manufactur"), "25"),
    (("inbound to outbound", "i2o", "warehouse"), "45"),
    (("record to result", "r2r", "general ledger", "chart of account"), "65"),
    (("design to retire", "d2r", "product master"), "10"),
    (("case to resolution", "c2r", "warranty"), "50"),
)


def _clean(text: Any) -> str:
    raw = str(text or "").replace("\u00a0", " ").strip()
    raw = raw.lstrip("| ").strip()
    raw = re.sub(r"[ \t]+", " ", raw)
    return raw


def _codes_in(text: str) -> list[str]:
    seen: list[str] = []
    for match in _CODE.finditer(text or ""):
        code = match.group(1)
        if code not in seen:
            seen.append(code)
    return seen


def _parse_map(process_map: str) -> list[dict[str, str]]:
    steps: list[dict[str, str]] = []
    for line in (process_map or "").splitlines():
        line = _clean(line)
        match = _STEP.match(line)
        if not match:
            continue
        steps.append({"code": match.group(1), "name": _clean(match.group(2))})
    return steps


def _parent_prefix(code: str) -> str:
    parts = (code or "").split(".")
    return ".".join(parts[:-1]) if len(parts) > 1 else code


def _under(code: str, prefix: str) -> bool:
    return code == prefix or code.startswith(prefix + ".")


def _depth(code: str) -> int:
    return len((code or "").split("."))


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{4,}", (text or "").lower()) if t not in {"with", "from", "that", "this", "process"}}


def _domain_root(domain: dict[str, Any]) -> str:
    bp = _clean(domain.get("business_process"))
    match = re.match(r"^(\d{2})\b", bp)
    if match:
        return match.group(1)
    steps = _parse_map(str(domain.get("process_map") or ""))
    return steps[0]["code"].split(".")[0] if steps else ""


def _score_domain(domain: dict[str, Any], query: str, codes: list[str]) -> int:
    bp = _clean(domain.get("business_process"))
    hay = f"{bp}\n{domain.get('process_map') or ''}\n{domain.get('source_file') or ''}".lower()
    score = 0
    root = _domain_root(domain)
    for code in codes:
        if root and (code == root or code.startswith(root + ".")):
            score += 80 + 15 * min(_depth(code), 3)
        elif code in hay:
            score += 40
    q = query.lower()
    for needles, alias_root in ALIASES:
        if alias_root == root and any(n in q for n in needles):
            score += 50
            break
    tokens = _tokenize(query)
    hay_tokens = _tokenize(hay)
    score += min(40, 3 * len(tokens & hay_tokens))
    return score


def _pick_scope(domain: dict[str, Any], query: str, codes: list[str]) -> str:
    steps = _parse_map(str(domain.get("process_map") or ""))
    map_codes = [s["code"] for s in steps]
    root = _domain_root(domain)
    for code in sorted(codes, key=lambda c: (_depth(c), len(c)), reverse=True):
        if any(_under(existing, code) or _under(code, existing) for existing in map_codes) or (
            root and (code == root or code.startswith(root + "."))
        ):
            return code
    tokens = _tokenize(query)
    best, best_score = root, 0
    for step in steps:
        overlap = len(tokens & _tokenize(step["name"] + " " + step["code"]))
        if overlap > best_score:
            best, best_score = step["code"], overlap
    if best_score >= 2 and best:
        # Prefer the L2 parent so we get sibling features.
        parts = best.split(".")
        return ".".join(parts[:2]) if len(parts) >= 2 else best
    return root


def _group_chunks(domain: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    current = ""
    chunks = sorted(domain.get("chunks") or [], key=lambda c: int(c.get("slide_number") or 0))
    for chunk in chunks:
        codes = [_clean(c) for c in (chunk.get("process_codes") or []) if _clean(c)]
        if codes:
            current = codes[0]
        if not current:
            continue
        grouped.setdefault(current, []).append(chunk)
    return grouped


def _useful_text(chunk: dict[str, Any]) -> str:
    title = _clean(chunk.get("title"))
    content = _clean(chunk.get("content")).replace(" | ", "\n")
    if not content:
        return ""
    lines = []
    for line in content.splitlines():
        line = _clean(line)
        if not line or _NOISE_LINE.match(line) or line == title:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _section(text: str, heading: str) -> str:
    match = re.search(
        rf"{re.escape(heading)}\s*(?:\||:|-)?\s*(.+?)(?=\n(?:Preconditions|Target Business|Overview|Acceptance)|$)",
        text,
        re.I | re.S,
    )
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip(" |")


def _hq_lines(domain: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for chunk in domain.get("chunks") or []:
        if chunk.get("category") != "hq_requirement":
            continue
        for line in str(chunk.get("content") or "").splitlines():
            line = _clean(line)
            if re.match(r"^\d+\s*\|", line):
                parts = [p.strip() for p in line.split("|")]
                req = parts[1] if len(parts) > 1 else line
                mapping = parts[2] if len(parts) > 2 else ""
                text = req if not mapping else f"{req} Mapping: {mapping}"
                if len(text) > 20:
                    out.append(text)
    return out[:12]


def _description_for(code: str, grouped: dict[str, list[dict[str, Any]]], fallback: str) -> str:
    blobs = [_useful_text(c) for c in grouped.get(code) or []]
    blobs = [b for b in blobs if b]
    joined = "\n".join(blobs)
    overview = _section(joined, "Overview")
    if overview:
        return overview
    # First substantial paragraph that is not a heading-only slide.
    for blob in blobs:
        para = max(blob.split("\n"), key=len)
        if len(para) >= 40:
            return para
    if blobs:
        text = re.sub(r"\s+", " ", blobs[0]).strip()
        return text[:400]
    return fallback


def _acceptance_for(code: str, grouped: dict[str, list[dict[str, Any]]], hq: list[str]) -> str:
    blobs = [_useful_text(c) for c in grouped.get(code) or []]
    joined = "\n".join(b for b in blobs if b)
    pre = _section(joined, "Preconditions")
    notes = []
    if pre:
        notes.append(pre)
    for blob in blobs:
        for line in blob.splitlines():
            if re.search(r"\bnote\s*:", line, re.I) or re.search(r"\bmust\b|\bshould\b", line, re.I):
                notes.append(_clean(line))
    if not notes and hq:
        notes.extend(hq[:3])
    seen: list[str] = []
    for item in notes:
        if item and item not in seen:
            seen.append(item)
    return " ; ".join(seen[:6])


def _flow_for(code: str, steps: list[dict[str, str]], grouped: dict[str, list[dict[str, Any]]]) -> list[str]:
    children = [s for s in steps if _parent_prefix(s["code"]) == code]
    if children:
        return [f"{c['code']} {c['name']}" for c in children][:8]
    blobs = [_useful_text(c) for c in grouped.get(code) or []]
    flow: list[str] = []
    for blob in blobs:
        for line in blob.splitlines():
            line = _clean(line)
            if 12 <= len(line) <= 90 and not line.lower().startswith("overview"):
                if re.match(r"^(create|process|check|setup|acquire|post|transfer|approve|record)\b", line, re.I):
                    flow.append(line)
    seen: list[str] = []
    for item in flow:
        if item not in seen:
            seen.append(item)
    return seen[:8]


def _scoped_steps(steps: list[dict[str, str]], scope: str) -> tuple[dict[str, str], list[dict[str, str]]]:
    epic = next((s for s in steps if s["code"] == scope), None)
    if epic is None:
        name = next((s["name"] for s in steps if s["code"].startswith(scope)), scope)
        epic = {"code": scope, "name": name}
    want_depth = _depth(scope) + 1
    features = [s for s in steps if _under(s["code"], scope) and _depth(s["code"]) == want_depth]
    if not features:
        features = [s for s in steps if s["code"] == scope] or [epic]
    return epic, features[:16]


def _build_items(domain: dict[str, Any], scope: str) -> list[dict[str, Any]]:
    steps = _parse_map(str(domain.get("process_map") or ""))
    grouped = _group_chunks(domain)
    hq = _hq_lines(domain)
    epic, features = _scoped_steps(steps, scope)
    epic_name = f"{epic['code']} {epic['name']}".strip()
    overview = _description_for(scope, grouped, _clean(domain.get("scope_and_overview")) or epic_name)
    items: list[dict[str, Any]] = [
        {
            "type": "Epic",
            "name": epic_name,
            "description": overview,
            "acceptance": _acceptance_for(scope, grouped, hq) or "Process stages in the SE37 process map are covered with D365 F&O.",
            "flow": _flow_for(scope, steps, grouped),
        }
    ]
    for feat in features:
        if feat["code"] == scope and len(features) == 1 and feat["name"] == epic["name"]:
            continue
        name = f"{feat['code']} {feat['name']}".strip()
        fallback = f"SE37 process step {feat['code']} {feat['name']}."
        items.append(
            {
                "type": "Feature",
                "name": name,
                "description": _description_for(feat["code"], grouped, fallback),
                "acceptance": _acceptance_for(feat["code"], grouped, []),
                "flow": _flow_for(feat["code"], steps, grouped),
            }
        )
    return items


def _overview_text(domain: dict[str, Any], scope: str, items: list[dict[str, Any]]) -> str:
    bp = _clean(domain.get("business_process"))
    epic = next((i for i in items if i.get("type") == "Epic"), None)
    desc = str((epic or {}).get("description") or "").strip()
    map_head = _clean((domain.get("process_map") or "").splitlines()[0] if domain.get("process_map") else "")
    parts = [p for p in (desc, f"SE37 cycle: {bp}." if bp else "", f"Process map: {map_head}." if map_head else "") if p]
    text = " ".join(parts)
    return re.sub(r"\s+", " ", text).strip()[:800]


def _passages(domain: dict[str, Any], scope: str, limit: int = 12) -> str:
    grouped = _group_chunks(domain)
    chunks: list[dict[str, Any]] = []
    for code, group in grouped.items():
        if _under(code, scope) or _under(scope, code):
            chunks.extend(group)
    if not chunks:
        chunks = list(domain.get("chunks") or [])
    chunks = sorted(chunks, key=lambda c: int(c.get("slide_number") or 0))
    blocks: list[str] = []
    used = 0
    for chunk in chunks:
        if chunk.get("category") == "document_control":
            continue
        body = _useful_text(chunk)
        if len(body) < 20:
            continue
        title = _clean(chunk.get("title"))
        slide = chunk.get("slide_number")
        block = f"[{title} | slide {slide}]\n{body[:700]}"
        blocks.append(block)
        used += 1
        if used >= limit:
            break
    hq = _hq_lines(domain)
    if hq:
        blocks.append("HQ Requirements:\n" + "\n".join(f"- {line}" for line in hq[:8]))
    return "\n\n".join(blocks)[:7000]


def retrieve_brd_kb(notes: str, ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    pack = load_kb_summary()
    out: dict[str, Any] = {
        "hit": False,
        "source": pack.get("source") or "",
        "s3_key": pack.get("s3_key") or "",
        "process": "",
        "scope_code": "",
        "passages": "",
        "suggested": {},
        "error": pack.get("error") or "",
    }
    data = pack.get("data") if pack.get("ok") else None
    if not isinstance(data, dict):
        return out
    ctx = ctx or {}
    query = " ".join(
        p
        for p in (
            notes or "",
            str(ctx.get("process_name") or ""),
            str(ctx.get("overview") or ""),
            " ".join(str(i.get("name") or "") for i in (ctx.get("items") or []) if isinstance(i, dict)),
        )
        if p
    )
    codes = _codes_in(query)
    domains = data.get("domains") or []
    ranked = sorted((( _score_domain(d, query, codes), d) for d in domains), key=lambda x: x[0], reverse=True)
    if not ranked or ranked[0][0] < 20:
        out["error"] = "No matching SE37 process in the knowledge base for these notes"
        return out
    _score, domain = ranked[0]
    scope = _pick_scope(domain, query, codes)
    items = _build_items(domain, scope)
    process_name = items[0]["name"] if items else _clean(domain.get("business_process"))
    suggested = {
        "process_name": process_name,
        "overview": _overview_text(domain, scope, items),
        "area_path": "YNS-FnO-ERP",
        "prepared_by": "YMSLI",
        "doc_code": "SE52: Business Requirement Document",
        "items": items,
    }
    out.update(
        hit=True,
        process=process_name,
        domain=_clean(domain.get("business_process")),
        scope_code=scope,
        source_file=_clean(domain.get("source_file")),
        passages=_passages(domain, scope),
        suggested=suggested,
        score=ranked[0][0],
    )
    return out


def _blank(value: Any, min_len: int = 1) -> bool:
    if value is None:
        return True
    if isinstance(value, list):
        return len(value) == 0
    return len(str(value).strip()) < min_len


def _enrich_item(item: dict[str, Any], suggested: list[dict[str, Any]]) -> dict[str, Any]:
    name = str(item.get("name") or "").lower()
    codes = _codes_in(name)
    best = None
    best_score = 0
    for row in suggested:
        hay = f"{row.get('name') or ''} {row.get('description') or ''}".lower()
        score = 0
        for code in codes:
            if code and code in hay:
                score += 10
        score += len(_tokenize(name) & _tokenize(hay))
        if score > best_score:
            best, best_score = row, score
    if not best or best_score < 2:
        return item
    out = dict(item)
    if _blank(out.get("description"), 24):
        out["description"] = best.get("description") or out.get("description")
    if _blank(out.get("acceptance"), 12):
        out["acceptance"] = best.get("acceptance") or out.get("acceptance")
    if _blank(out.get("flow")):
        out["flow"] = best.get("flow") or out.get("flow")
    return out


def enrich_brd_from_kb(
    ctx: dict[str, Any],
    notes: str,
    pack: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fill only empty BRD fields from the matched SE37 summary. Never overwrite user text."""
    pack = pack if pack is not None else retrieve_brd_kb(notes, ctx)
    out = dict(ctx or {})
    meta = {
        "used": False,
        "source": pack.get("source") or "",
        "s3_key": pack.get("s3_key") or "",
        "process": pack.get("process") or "",
        "filled": [],
    }
    if not pack.get("hit"):
        meta["error"] = pack.get("error") or ""
        return out, meta
    suggested = pack.get("suggested") or {}
    filled: list[str] = []
    for key in ("process_name", "overview", "area_path", "prepared_by", "doc_code"):
        if _blank(out.get(key), 8 if key == "overview" else 1) and suggested.get(key):
            out[key] = suggested[key]
            filled.append(key)
    kb_items = list(suggested.get("items") or [])
    current = list(out.get("items") or [])
    if not current and kb_items:
        out["items"] = kb_items
        filled.append("items")
    elif current and kb_items:
        enriched = [_enrich_item(row, kb_items) if isinstance(row, dict) else row for row in current]
        if enriched != current:
            out["items"] = enriched
            filled.append("items")
        # If the user only named a process and left a thin item list, replace with KB structure.
        thin = all(
            isinstance(row, dict) and _blank(row.get("description"), 24) and _blank(row.get("acceptance"), 12)
            for row in current
        )
        if thin and len(kb_items) > len(current):
            out["items"] = kb_items
            if "items" not in filled:
                filled.append("items")
    if filled:
        meta["used"] = True
        meta["filled"] = filled
    return out, meta
