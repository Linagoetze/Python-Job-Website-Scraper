"""The xlsx export: reads the store, shows only unreviewed jobs, and is the
one place `=HYPERLINK()` formulas are allowed to exist."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from job_scraper.storage.db import JobStore
from job_scraper.storage.xlsx_store import write_xlsx


def _seed(
    db: Path,
    jobs: list[dict[str, Any]],
    *,
    now: str,
    statuses: dict[str, str] | None = None,
) -> None:
    with JobStore(db) as store:
        run_id = store.begin_run(now)
        store.upsert_jobs(jobs, run_id, now=now)
        for key, status in (statuses or {}).items():
            store.set_status([key], status)
        store.finish_run(run_id)


def _job(key: str, title: str) -> dict[str, str]:
    return {
        "dedupe_key": key,
        "source_name": "acme",
        "title": title,
        "location": "Berlin",
        "detail_url": key,
        "apply_url": "",
    }


def _sheet_rows(xlsx: Path) -> list[tuple]:
    ws = load_workbook(xlsx).active
    return list(ws.iter_rows(min_row=2, values_only=True))


def test_only_unreviewed_jobs_are_shown(tmp_path: Path) -> None:
    db, xlsx = tmp_path / "jobs.sqlite3", tmp_path / "jobs.xlsx"
    _seed(
        db,
        [_job("https://x/jobs/1", "Shown"), _job("https://x/jobs/2", "Hidden")],
        now="2026-08-01T00:00:00+00:00",
        statuses={"https://x/jobs/2": "seen"},
    )

    assert write_xlsx(db, xlsx) == 1

    rows = _sheet_rows(xlsx)
    assert [r[1] for r in rows] == ["Shown"]


def test_urls_become_hyperlink_formulas_at_export_time(tmp_path: Path) -> None:
    db, xlsx = tmp_path / "jobs.sqlite3", tmp_path / "jobs.xlsx"
    _seed(db, [_job("https://x/jobs/1", "Analyst")], now="2026-08-01T00:00:00+00:00")

    write_xlsx(db, xlsx)

    (row,) = _sheet_rows(xlsx)
    assert row[3] == '=HYPERLINK("https://x/jobs/1")', "plain URL in the store, formula out"
    assert row[4] is None, "no apply URL, no formula"


def test_rows_from_the_two_most_recent_runs_are_highlighted(tmp_path: Path) -> None:
    db, xlsx = tmp_path / "jobs.sqlite3", tmp_path / "jobs.xlsx"
    _seed(db, [_job("https://x/jobs/old", "Old")], now="2026-08-01T00:00:00+00:00")
    _seed(db, [_job("https://x/jobs/mid", "Mid")], now="2026-08-02T00:00:00+00:00")
    _seed(db, [_job("https://x/jobs/new", "New")], now="2026-08-03T00:00:00+00:00")

    write_xlsx(db, xlsx)

    ws = load_workbook(xlsx).active
    filled = {
        ws.cell(row=r, column=2).value
        for r in range(2, ws.max_row + 1)
        if str(ws.cell(row=r, column=1).fill.start_color.rgb or "").endswith("C6EFCE")
    }
    assert filled == {"Mid", "New"}


def test_empty_store_writes_an_empty_table(tmp_path: Path) -> None:
    db, xlsx = tmp_path / "jobs.sqlite3", tmp_path / "jobs.xlsx"
    assert write_xlsx(db, xlsx) == 0
    assert _sheet_rows(xlsx) == []
