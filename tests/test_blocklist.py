"""Tests for the review-status replacement of the CSV blocklist.

The critical semantics: the legacy blocklist.csv was produced by a routine
that blocklisted *every* surfaced job after each run, so its rows mean
"already seen", not "rejected" — the import must never turn them into
rejections, and must preserve every row.
"""

from __future__ import annotations

import csv
from pathlib import Path

from job_scraper.blocklist import (
    import_legacy_blocklist,
    mark_all_new_seen,
    read_legacy_blocklist,
)
from job_scraper.storage.db import JobStore

_LEGACY_FIELDS = ["dedupe_key", "source_name", "company", "title", "detail_url"]


def _write_legacy_blocklist(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write("﻿")
        w = csv.DictWriter(f, fieldnames=_LEGACY_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _legacy_row(key: str, title: str = "Project Manager") -> dict[str, str]:
    return {
        "dedupe_key": key,
        "source_name": "greenhouse",
        "company": "Acme",
        "title": title,
        "detail_url": key,
    }


def _statuses(db: Path) -> dict[str, str]:
    with JobStore(db) as store:
        return {r["dedupe_key"]: r["status"] for r in store.all_jobs()}


def test_import_brings_every_row_in_as_seen_never_rejected(tmp_path: Path) -> None:
    bl = tmp_path / "blocklist.csv"
    db = tmp_path / "jobs.sqlite3"
    rows = [_legacy_row(f"https://x/jobs/{i}") for i in range(265)]
    _write_legacy_blocklist(bl, rows)

    inserted, flipped = import_legacy_blocklist(db, bl)

    assert (inserted, flipped) == (265, 0)
    statuses = _statuses(db)
    assert len(statuses) == 265, "all 265 rows preserved"
    assert set(statuses.values()) == {"seen"}, "'already seen', not 'rejected'"


def test_import_never_touches_the_csv(tmp_path: Path) -> None:
    bl = tmp_path / "blocklist.csv"
    _write_legacy_blocklist(bl, [_legacy_row("https://x/jobs/1")])
    before = bl.read_bytes()

    import_legacy_blocklist(tmp_path / "jobs.sqlite3", bl)

    assert bl.read_bytes() == before


def test_import_is_idempotent(tmp_path: Path) -> None:
    bl = tmp_path / "blocklist.csv"
    db = tmp_path / "jobs.sqlite3"
    _write_legacy_blocklist(bl, [_legacy_row("https://x/jobs/1")])

    assert import_legacy_blocklist(db, bl) == (1, 0)
    assert import_legacy_blocklist(db, bl) == (0, 0)
    assert len(_statuses(db)) == 1


def test_import_with_missing_or_headerless_file_is_a_noop(tmp_path: Path) -> None:
    assert read_legacy_blocklist(tmp_path / "nope.csv") == []
    bad = tmp_path / "bad.csv"
    bad.write_text("title\nProject Manager\n", encoding="utf-8")
    assert read_legacy_blocklist(bad) == []
    assert import_legacy_blocklist(tmp_path / "jobs.sqlite3", bad) == (0, 0)


def test_mark_all_new_seen_flips_only_unreviewed_jobs(tmp_path: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    with JobStore(db) as store:
        run_id = store.begin_run()
        store.upsert_jobs(
            [
                {"dedupe_key": "https://x/jobs/1", "source_name": "acme"},
                {"dedupe_key": "https://x/jobs/2", "source_name": "acme"},
            ],
            run_id,
        )
        store.set_status(["https://x/jobs/2"], "shortlisted")
        store.finish_run(run_id)

    assert mark_all_new_seen(db) == 1

    assert _statuses(db) == {
        "https://x/jobs/1": "seen",
        "https://x/jobs/2": "shortlisted",
    }
