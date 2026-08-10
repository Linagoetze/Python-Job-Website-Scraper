"""Unit tests for the SQLite store (WP4) and the one-off CSV migration."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from job_scraper.storage.db import JobStore
from job_scraper.tools.migrate_to_sqlite import migrate


def _job(key: str, **overrides: str) -> dict[str, str]:
    base = {
        "dedupe_key": key,
        "source_name": "acme",
        "company": "Acme",
        "title": "Data Analyst",
        "location": "Berlin",
        "detail_url": key,
        "apply_url": "",
        "experience_level": "unspecified",
    }
    base.update(overrides)
    return base


_KEY = "https://acme.example/jobs/1"


def _raw_query(db: Path, sql: str) -> list[tuple]:
    """Inspect the database file directly, outside any JobStore transaction."""
    conn = sqlite3.connect(db)
    try:
        return list(conn.execute(sql))
    finally:
        conn.close()


def test_store_opens_in_wal_mode(tmp_path: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    with JobStore(db) as store:
        store.begin_run()
    assert _raw_query(db, "PRAGMA journal_mode") == [("wal",)]


def test_upsert_inserts_then_updates(tmp_path: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    with JobStore(db) as store:
        run1 = store.begin_run("2026-08-10T10:00:00+00:00")
        inserted, updated = store.upsert_jobs([_job(_KEY)], run1, now="2026-08-10T10:00:01+00:00")
        store.finish_run(run1)
    assert (inserted, updated) == (1, 0)

    with JobStore(db) as store:
        run2 = store.begin_run()
        inserted, updated = store.upsert_jobs(
            [_job(_KEY, title="Data Analyst II")], run2, now="2026-08-11T10:00:01+00:00"
        )
        store.finish_run(run2)
        (row,) = store.all_jobs()

    assert (inserted, updated) == (0, 1)
    assert row["title"] == "Data Analyst II"
    assert row["first_seen"] == "2026-08-10T10:00:01+00:00", "first_seen must survive re-sighting"
    assert row["last_seen"] == "2026-08-11T10:00:01+00:00"
    assert row["last_run_id"] == run2
    assert row["status"] == "new", "status is a review decision; a scrape must not change it"


def test_empty_experience_level_does_not_overwrite_stored_one(tmp_path: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    with JobStore(db) as store:
        run1 = store.begin_run()
        store.upsert_jobs([_job(_KEY, experience_level="junior (<=2yr)")], run1)
        run2 = store.begin_run()
        store.upsert_jobs([_job(_KEY, experience_level="")], run2)
        (row,) = store.all_jobs()
    assert row["experience_level"] == "junior (<=2yr)"


def test_delisting_never_deletes_and_reappearance_revives(tmp_path: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    with JobStore(db) as store:
        run1 = store.begin_run()
        store.upsert_jobs([_job(_KEY)], run1)
        assert store.mark_delisted_except(set()) == 1
        (row,) = store.all_jobs()
        assert row["status"] == "delisted"
        assert store.active_jobs() == []

        # The job is listed again: it comes back as 'seen', not 'new'.
        run2 = store.begin_run()
        store.upsert_jobs([_job(_KEY)], run2)
        (row,) = store.active_jobs()
    assert row["status"] == "seen"


def test_upsert_rejects_unknown_initial_status(tmp_path: Path) -> None:
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        run_id = store.begin_run()
        with pytest.raises(ValueError, match="starred"):
            store.upsert_jobs([_job(_KEY)], run_id, initial_status="starred")


def test_check_constraint_rejects_bad_status(tmp_path: Path) -> None:
    """Backstop for SQL that bypasses upsert_jobs: the schema itself refuses."""
    db = tmp_path / "jobs.sqlite3"
    with JobStore(db) as store:
        store.begin_run()
    with pytest.raises(sqlite3.IntegrityError):
        _raw_query(
            db,
            "INSERT INTO jobs (dedupe_key, source_name, first_seen, last_seen,"
            " last_run_id, status) VALUES ('k', 's', 't', 't', 1, 'starred')",
        )


def test_exception_rolls_back_the_whole_run(tmp_path: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    with pytest.raises(RuntimeError, match="boom"):
        with JobStore(db) as store:
            run_id = store.begin_run()
            store.upsert_jobs([_job(_KEY)], run_id)
            raise RuntimeError("boom")
    assert _raw_query(db, "SELECT * FROM jobs") == []
    assert _raw_query(db, "SELECT * FROM runs") == []


def test_store_refuses_use_outside_with_block(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    with pytest.raises(RuntimeError):
        store.begin_run()


# --- migration from jobs.csv -------------------------------------------------


def _write_csv(path: Path) -> None:
    path.write_text(
        "﻿source_name,title,company,location,detail_hyperlink,apply_hyperlink,run_id\n"
        'acme,Data Analyst,Acme,Berlin,"=HYPERLINK(""https://acme.example/jobs/1"")",'
        '"=HYPERLINK(""https://acme.example/apply/1"")",3\n'
        'acme,Old Analyst,Acme,Berlin,"=HYPERLINK(""https://acme.example/jobs/2"")",,2\n',
        encoding="utf-8",
    )


def test_migration_parses_hyperlinks_and_imports_as_seen(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    db_path = tmp_path / "jobs.sqlite3"
    _write_csv(csv_path)

    inserted, updated = migrate(csv_path, db_path)
    assert (inserted, updated) == (2, 0)

    with JobStore(db_path) as store:
        jobs = {j["dedupe_key"]: j for j in store.all_jobs()}
    assert set(jobs) == {"https://acme.example/jobs/1", "https://acme.example/jobs/2"}
    job = jobs["https://acme.example/jobs/1"]
    assert job["detail_url"] == "https://acme.example/jobs/1", "URL, not an Excel formula"
    assert job["apply_url"] == "https://acme.example/apply/1"
    assert job["status"] == "seen", "already in the owner's spreadsheet, so not 'new'"
    assert job["first_seen"] == job["last_seen"]


def test_migration_refuses_to_run_twice_without_force(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    db_path = tmp_path / "jobs.sqlite3"
    _write_csv(csv_path)
    migrate(csv_path, db_path)

    with pytest.raises(SystemExit, match="--force"):
        migrate(csv_path, db_path)

    inserted, updated = migrate(csv_path, db_path, force=True)
    assert (inserted, updated) == (0, 2)


def test_migration_with_missing_csv_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="missing or empty"):
        migrate(tmp_path / "jobs.csv", tmp_path / "jobs.sqlite3")
