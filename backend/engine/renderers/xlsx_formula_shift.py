"""
openpyxl's ``insert_rows`` moves cells but does not translate formulas the way
Excel itself does on a manual row insert - a `=COUNTA(B24:B26)` below an
inserted row keeps reading `B24:B26` even though row 24 now holds something
else entirely. Left alone, that is exactly the kind of silent document
mistake the validation gate exists to prevent, so it is fixed here instead of
merely documented.

Scope, deliberately: only *unqualified* references (no other sheet's name in
front of them) on the sheet being edited are shifted. A formula pointing at
another sheet is left untouched - shifting it correctly would require
tracking insertions across every sheet a workbook contains, which this
renderer does not attempt. `structural_qa` still catches the case where a
formula result plainly changed shape.
"""

from __future__ import annotations

import re
from typing import Iterable, Tuple

from openpyxl.worksheet.worksheet import Worksheet

# Matches A1-style refs (optionally $-anchored), but not when immediately
# preceded by a sheet-qualifying "'Name'!" or "Name!" - those are left alone.
_SHEET_QUALIFIER = re.compile(r"(?:'[^']+'|[A-Za-z_][\w.]*)\s*!\s*$")
_CELL_REF = re.compile(r"(\$?)([A-Z]{1,3})(\$?)(\d+)")


def shift_formula_rows(formula: str, insert_at: int, count: int) -> str:
    """Add `count` to every unqualified row reference >= insert_at."""
    if count == 0 or not formula.startswith("="):
        return formula

    out = []
    last = 0
    for m in _CELL_REF.finditer(formula):
        out.append(formula[last:m.start()])
        prefix = formula[:m.start()]
        qualified = bool(_SHEET_QUALIFIER.search(prefix))
        col_abs, col, row_abs, row_s = m.groups()
        row = int(row_s)
        if not qualified and row >= insert_at:
            row += count
        out.append(f"{col_abs}{col}{row_abs}{row}")
        last = m.end()
    out.append(formula[last:])
    return "".join(out)


def shift_formulas_in_sheet(ws: Worksheet, insert_at: int, count: int,
                            skip_rows: Iterable[int] = ()) -> int:
    """
    Rewrite every formula on `ws` for a row insertion that already happened
    at `insert_at` (i.e. call this immediately after `ws.insert_rows`, while
    coordinates are still consistent). Returns the number of formulas changed.
    `skip_rows` excludes the freshly-inserted, still-blank rows themselves.
    """
    if count == 0:
        return 0
    skip = set(skip_rows)
    changed = 0
    for row in ws.iter_rows():
        for cell in row:
            if cell.row in skip:
                continue
            v = cell.value
            if isinstance(v, str) and v.startswith("="):
                new_v = shift_formula_rows(v, insert_at, count)
                if new_v != v:
                    cell.value = new_v
                    changed += 1
    return changed
