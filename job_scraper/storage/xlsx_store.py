"""Write a styled xlsx of jobs, highlighting newly added rows in light green."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

_DISPLAY_FIELDS = ["source_name", "title", "location", "detail_hyperlink", "apply_hyperlink"]

_HEADER_FONT = Font(bold=True)
_NEW_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
_LINK_FONT = Font(color="0563C1", underline="single")
_NEW_LINK_FONT = Font(color="0563C1", underline="single")  # fill handles the green, font stays blue
_COL_WIDTHS: dict[str, float] = {
    "source_name": 18,
    "title": 42,
    "location": 26,
    "detail_hyperlink": 55,
    "apply_hyperlink": 45,
}
_HYPERLINK_RE = re.compile(r'=HYPERLINK\("([^"]+)"')


def _url_from_formula(formula: str) -> str:
    """Extract the URL from a =HYPERLINK("url") formula string."""
    m = _HYPERLINK_RE.match(formula)
    return m.group(1) if m else ""


def write_xlsx(csv_path: Path, xlsx_path: Path) -> int:
    """Read *csv_path* and write a styled xlsx to *xlsx_path*.

    Rows whose run_id is among the two highest run_ids in the file are filled
    light green to indicate they were added in the two most recent scraper runs.

    Returns the number of data rows written (the total jobs now in the table).
    """
    rows: list[dict[str, str]] = []
    if csv_path.exists():
        with open(csv_path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))

    all_run_ids = sorted({int(r.get("run_id") or 0) for r in rows}, reverse=True)
    recent_run_ids = set(all_run_ids[:2])

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
        is_new = bool(recent_run_ids - {0}) and int(row.get("run_id") or 0) in recent_run_ids
        ws_row = row_idx + 2  # 1-based, offset by header

        for col, field in enumerate(_DISPLAY_FIELDS, start=1):
            raw = row.get(field, "")
            cell = ws.cell(row=ws_row, column=col)

            if field in ("detail_hyperlink", "apply_hyperlink") and raw:
                url = _url_from_formula(raw)
                if url:
                    cell.value = url
                    cell.hyperlink = url
                    cell.font = _NEW_LINK_FONT if is_new else _LINK_FONT
                else:
                    cell.value = raw
            else:
                cell.value = raw

            if is_new:
                cell.fill = _NEW_FILL

    wb.save(xlsx_path)
    return len(rows)
