"""Tests for the review commands that replaced the blocklist-everything routine.

The properties that matter to the owner: a row number always addresses the job
that was on that row of the spreadsheet they are looking at, a mistyped row
changes nothing at all, and no decision ever removes a job from the database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from job_scraper.review import ReviewError, apply_review, resolve_rows
from job_scraper.storage.db import JobStore
from job_scraper.storage.xlsx_store import write_xlsx


def _job(key: str, title: str, source: str = "acme") -> dict[str, str]:
    return {
        "dedupe_key": key,
        "source_name": source,
        "company": "Acme",
        "title": title,
        "location": "Berlin",
        "detail_url": key,
        "apply_url": "",
    }


def _seed(db: Path, jobs: list[dict[str, Any]], *, now: str = "2026-08-01T00:00:00+00:00") -> None:
    with JobStore(db) as store:
        run_id = store.begin_run(now)
        store.upsert_jobs(jobs, run_id, now=now)
        store.finish_run(run_id)


def _statuses(db: Path) -> dict[str, str]:
    """dedupe_key -> status for everything in the store."""
    with JobStore(db) as store:
        return {r["dedupe_key"]: r["status"] for r in store.all_jobs()}


def _export(db: Path, xlsx: Path, *, show_all: bool = False) -> dict[int, str]:
    """Write the spreadsheet and return the row numbers it made addressable."""
    write_xlsx(db, xlsx, show_all=show_all)
    with JobStore(db) as store:
        return store.export_row_map()


def _row_of(mapping: dict[int, str], key: str) -> int:
    return next(n for n, k in mapping.items() if k == key)


def test_row_numbers_address_the_job_on_that_row(tmp_path: Path) -> None:
    db, xlsx = tmp_path / "jobs.sqlite3", tmp_path / "jobs.xlsx"
    _seed(db, [_job(f"https://x/jobs/{i}", f"Job {i}") for i in range(4)])
    mapping = _export(db, xlsx)

    result = apply_review(db, shortlist=[2], reject=[4])

    statuses = _statuses(db)
    assert statuses[mapping[2]] == "shortlisted"
    assert statuses[mapping[4]] == "rejected"
    assert statuses[mapping[3]] == "new", "untouched rows keep their status"
    # The echoed description is how a mistyped row number becomes visible.
    assert [(n, s) for n, s, _ in result.marked] == [(2, "shortlisted"), (4, "rejected")]
    assert all("Acme" in description for _, _, description in result.marked)


def test_reviewed_jobs_leave_the_sheet_but_never_the_database(tmp_path: Path) -> None:
    db, xlsx = tmp_path / "jobs.sqlite3", tmp_path / "jobs.xlsx"
    _seed(db, [_job("https://x/jobs/1", "Kept"), _job("https://x/jobs/2", "Rejected")])
    mapping = _export(db, xlsx)

    apply_review(db, reject=[_row_of(mapping, "https://x/jobs/2")])

    assert _statuses(db) == {
        "https://x/jobs/1": "new",
        "https://x/jobs/2": "rejected",
    }, "the row is kept, only its status changed"
    assert set(_export(db, xlsx).values()) == {"https://x/jobs/1"}, "gone from the review sheet"


def test_an_unknown_row_number_changes_nothing(tmp_path: Path) -> None:
    db, xlsx = tmp_path / "jobs.sqlite3", tmp_path / "jobs.xlsx"
    _seed(db, [_job("https://x/jobs/1", "Only")])
    _export(db, xlsx)

    with pytest.raises(ReviewError, match="No such row"):
        apply_review(db, shortlist=[2, 99])

    assert _statuses(db) == {"https://x/jobs/1": "new"}, "the valid row in the same command too"


def test_reviewing_before_any_export_fails_loudly(tmp_path: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    _seed(db, [_job("https://x/jobs/1", "Only")])

    with pytest.raises(ReviewError, match="No export to review against"):
        apply_review(db, reject=[2])


def test_a_row_in_both_lists_is_refused(tmp_path: Path) -> None:
    db, xlsx = tmp_path / "jobs.sqlite3", tmp_path / "jobs.xlsx"
    _seed(db, [_job("https://x/jobs/1", "Only")])
    _export(db, xlsx)

    with pytest.raises(ReviewError, match="both --shortlist and --reject"):
        apply_review(db, shortlist=[2], reject=[2])

    assert _statuses(db) == {"https://x/jobs/1": "new"}


def test_seen_all_flips_only_unreviewed_jobs(tmp_path: Path) -> None:
    db, xlsx = tmp_path / "jobs.sqlite3", tmp_path / "jobs.xlsx"
    _seed(db, [_job(f"https://x/jobs/{i}", f"Job {i}") for i in range(3)])
    mapping = _export(db, xlsx)

    result = apply_review(db, shortlist=[_row_of(mapping, "https://x/jobs/1")], seen_all=True)

    assert result.seen_all == 2, "the shortlisted job is not swept up by --seen-all"
    assert _statuses(db)["https://x/jobs/1"] == "shortlisted"
    assert sorted(_statuses(db).values()) == ["seen", "seen", "shortlisted"]


def test_seen_all_empties_the_review_sheet_without_deleting_history(tmp_path: Path) -> None:
    """The day-to-day promise: after --seen-all, the sheet shows only what is new."""
    db, xlsx = tmp_path / "jobs.sqlite3", tmp_path / "jobs.xlsx"
    _seed(db, [_job("https://x/jobs/1", "Old")], now="2026-08-01T00:00:00+00:00")
    _export(db, xlsx)

    apply_review(db, seen_all=True)
    _seed(db, [_job("https://x/jobs/2", "Fresh")], now="2026-08-02T00:00:00+00:00")

    assert set(_export(db, xlsx).values()) == {"https://x/jobs/2"}
    with JobStore(db) as store:
        assert len(store.all_jobs()) == 2, "the reviewed job is still in the store"


def test_a_new_export_renumbers_and_the_old_numbers_are_gone(tmp_path: Path) -> None:
    db, xlsx = tmp_path / "jobs.sqlite3", tmp_path / "jobs.xlsx"
    _seed(db, [_job(f"https://x/jobs/{i}", f"Job {i}") for i in range(3)])
    first = _export(db, xlsx)

    apply_review(db, reject=[2])
    second = _export(db, xlsx)

    assert len(second) == 2
    assert first[2] not in second.values(), "the rejected job is no longer addressable"
    assert sorted(second) == [2, 3], "renumbered from the top, not left with a hole"


def test_resolve_rows_is_all_or_nothing(tmp_path: Path) -> None:
    db, xlsx = tmp_path / "jobs.sqlite3", tmp_path / "jobs.xlsx"
    _seed(db, [_job("https://x/jobs/1", "Only")])
    _export(db, xlsx)

    with JobStore(db) as store:
        assert resolve_rows(store, [2]) == {2: "https://x/jobs/1"}
        assert resolve_rows(store, []) == {}, "no rows named, no export needed"
        with pytest.raises(ReviewError):
            resolve_rows(store, [2, 3])


def test_row_numbers_survive_a_scrape_that_reorders_the_table(tmp_path: Path) -> None:
    """Why the mapping is recorded rather than re-derived at review time."""
    db, xlsx = tmp_path / "jobs.sqlite3", tmp_path / "jobs.xlsx"
    _seed(db, [_job("https://x/jobs/1", "Wanted", source="zulu")])
    row = _row_of(_export(db, xlsx), "https://x/jobs/1")

    # A scrape lands a job that sorts above it, so re-deriving the order now
    # would make this row number address the wrong job.
    _seed(
        db,
        [_job("https://x/jobs/2", "Interloper", source="alpha")],
        now="2026-08-02T00:00:00+00:00",
    )

    apply_review(db, reject=[row])

    assert _statuses(db) == {
        "https://x/jobs/1": "rejected",
        "https://x/jobs/2": "new",
    }
