"""Write a styled xlsx of unreviewed jobs, highlighting recent rows in green.

The one place Excel syntax is allowed to exist: the store holds plain URLs,
and the `=HYPERLINK()` formulas are generated here at export time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from job_scraper.storage.db import JobStore

_DISPLAY_FIELDS = ["source_name", "title", "location", "detail_url", "apply_url"]
_URL_FIELDS = {"detail_url", "apply_url"}

_HEADER_FONT = Font(bold=True)
_NEW_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
_LINK_FONT = Font(color="0563C1", underline="single")
_COL_WIDTHS: dict[str, float] = {
    "source_name": 18,
    "title": 42,
    "location": 26,
    "detail_url": 55,
    "apply_url": 45,
}


def _hyperlink_formula(url: str) -> str:
    """Single-arg HYPERLINK so the formula has no commas."""
    return '=HYPERLINK("' + url.replace('"', '""') + '")'


def write_xlsx(db_path: Path, xlsx_path: Path) -> int:
    """Read unreviewed ('new') jobs from the store and write a styled xlsx.

    Rows first seen in the two most recent storing runs are filled light
    green. Every job stored by one run shares a single first_seen timestamp,
    so the two most recent distinct first_seen values among the displayed rows
    identify those runs.

    Returns the number of data rows written (the jobs now in the table).
    """
    with JobStore(db_path) as store:
        rows: list[dict[str, Any]] = store.jobs_with_status(("new",))

    # Alphabetical by source, newest first within each source (stable sorts).
    rows.sort(key=lambda r: str(r.get("first_seen") or ""), reverse=True)
    rows.sort(key=lambda r: str(r.get("source_name") or "").lower())

    recent_seen = sorted({str(r.get("first_seen") or "") for r in rows}, reverse=True)[:2]

    wb = Workbook()
    ws = wb.active
    ws.title = "Jobs"
    ws.freeze_panes = "A2"

    # --- Header row ---
    for col, field in enumerate(_DISPLAY_FIELDS, start=1):
        cell = ws.cell(row=1, column=col, value=field)
        cell.font = _HEADER_FONT

    # --- Column widths ---
    for col, field in enumerate(_DISPLAY_FIELDS, start=1):
        letter = ws.cell(row=1, column=col).column_letter
        ws.column_dimensions[letter].width = _COL_WIDTHS.get(field, 20)

    # --- Data rows ---
    for row_idx, row in enumerate(rows):
        is_new = str(row.get("first_seen") or "") in recent_seen
        ws_row = row_idx + 2  # 1-based, offset by header

        for col, field in enumerate(_DISPLAY_FIELDS, start=1):
            raw = str(row.get(field) or "")
            cell = ws.cell(row=ws_row, column=col)

            if field in _URL_FIELDS and raw:
                cell.value = _hyperlink_formula(raw)
                cell.font = _LINK_FONT
            else:
                cell.value = raw

            if is_new:
                cell.fill = _NEW_FILL

    wb.save(xlsx_path)
    return len(rows)
