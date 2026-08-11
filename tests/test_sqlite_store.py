"""Unit tests for the SQLite store: upserts, statuses, and the delisting rule."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from job_scraper.storage.db import JobStore


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


def test_hybrid_confirmed_ratchets_up_and_never_back(tmp_path: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    with JobStore(db) as store:
        run1 = store.begin_run()
        store.upsert_jobs([dict(_job(_KEY), hybrid_confirmed=1)], run1)
        run2 = store.begin_run()
        # A later run that skipped the detail fetch offers 0; must not unconfirm.
        store.upsert_jobs([dict(_job(_KEY), hybrid_confirmed=0)], run2)
        (row,) = store.all_jobs()
    assert row["hybrid_confirmed"] == 1


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


def test_wp4_database_gains_the_new_columns_in_place(tmp_path: Path) -> None:
    """A database created before misses/hybrid_confirmed existed must be
    migrated by ALTER TABLE, keeping its rows and history."""
    db = tmp_path / "jobs.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE runs (run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                           started_at TEXT NOT NULL, finished_at TEXT);
        CREATE TABLE jobs (
            dedupe_key TEXT PRIMARY KEY, source_name TEXT NOT NULL,
            company TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '', detail_url TEXT NOT NULL DEFAULT '',
            apply_url TEXT NOT NULL DEFAULT '', first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL, last_run_id INTEGER NOT NULL REFERENCES runs(run_id),
            status TEXT NOT NULL DEFAULT 'new'
                CHECK (status IN ('new', 'seen', 'shortlisted', 'rejected', 'delisted')),
            experience_level TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE source_health (
            source_name TEXT NOT NULL, run_id INTEGER NOT NULL REFERENCES runs(run_id),
            rows_found INTEGER NOT NULL, ok INTEGER NOT NULL, error TEXT,
            PRIMARY KEY (source_name, run_id)
        );
        INSERT INTO runs (started_at) VALUES ('2026-08-01T00:00:00+00:00');
        INSERT INTO jobs (dedupe_key, source_name, first_seen, last_seen, last_run_id)
        VALUES ('k1', 'acme', '2026-08-01T00:00:01+00:00', '2026-08-01T00:00:01+00:00', 1);
        """
    )
    conn.commit()
    conn.close()

    with JobStore(db) as store:
        (row,) = store.all_jobs()
    assert row["first_seen"] == "2026-08-01T00:00:01+00:00", "history must survive"
    assert row["misses"] == 0
    assert row["hybrid_confirmed"] == 0


# --- consecutive-miss delisting ----------------------------------------------


def _seed(store: JobStore, *jobs: dict[str, str], status: str = "new") -> int:
    run_id = store.begin_run()
    store.upsert_jobs(list(jobs), run_id, initial_status=status)
    store.finish_run(run_id)
    return run_id


def test_one_miss_is_not_delisting(tmp_path: Path) -> None:
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        _seed(store, _job(_KEY))
        assert store.note_misses_and_delist({"acme": set()}, threshold=2) == 0
        (row,) = store.all_jobs()
        assert row["status"] == "new"
        assert row["misses"] == 1


def test_threshold_consecutive_misses_delist_but_never_delete(tmp_path: Path) -> None:
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        _seed(store, _job(_KEY))
        assert store.note_misses_and_delist({"acme": set()}, threshold=2) == 0
        assert store.note_misses_and_delist({"acme": set()}, threshold=2) == 1
        (row,) = store.all_jobs()
        assert row["status"] == "delisted", "marked, not deleted"


def test_a_sighting_resets_the_miss_counter(tmp_path: Path) -> None:
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        _seed(store, _job(_KEY))
        store.note_misses_and_delist({"acme": set()}, threshold=2)
        # Sighted again: the counter starts over, so the miss streak was not consecutive.
        store.note_misses_and_delist({"acme": {_KEY}}, threshold=2)
        assert store.note_misses_and_delist({"acme": set()}, threshold=2) == 0
        (row,) = store.all_jobs()
        assert row["status"] == "new"
        assert row["misses"] == 1


def test_unscraped_source_accrues_no_misses(tmp_path: Path) -> None:
    """A failed or absent source must never erode its stored jobs' history."""
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        _seed(store, _job(_KEY))
        for _ in range(5):
            assert store.note_misses_and_delist({"other-source": set()}, threshold=2) == 0
        (row,) = store.all_jobs()
        assert row["status"] == "new"
        assert row["misses"] == 0


def test_delisting_never_overwrites_review_statuses(tmp_path: Path) -> None:
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        _seed(store, _job(_KEY))
        store.set_status([_KEY], "shortlisted")
        for _ in range(3):
            store.note_misses_and_delist({"acme": set()}, threshold=2)
        (row,) = store.all_jobs()
        assert row["status"] == "shortlisted", "a review decision survives disappearance"
        assert row["misses"] == 3, "…but the misses are still recorded"


def test_reappearing_delisted_job_revives_as_seen(tmp_path: Path) -> None:
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        _seed(store, _job(_KEY))
        store.note_misses_and_delist({"acme": set()}, threshold=1)
        (row,) = store.all_jobs()
        assert row["status"] == "delisted"

        run2 = store.begin_run()
        store.upsert_jobs([_job(_KEY)], run2)
        (row,) = store.all_jobs()
        assert row["status"] == "seen", "listed again, and the owner has already had it"
        assert row["misses"] == 0


def test_force_delist_skips_the_threshold_but_not_review_statuses(tmp_path: Path) -> None:
    other = "https://acme.example/jobs/2"
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        _seed(store, _job(_KEY), _job(other))
        store.set_status([other], "rejected")
        delisted = store.note_misses_and_delist({}, threshold=2, force_delist_sources={"acme"})
        assert delisted == 1
        by_key = {r["dedupe_key"]: r for r in store.all_jobs()}
        assert by_key[_KEY]["status"] == "delisted"
        assert by_key[other]["status"] == "rejected"


def test_note_misses_rejects_a_nonsense_threshold(tmp_path: Path) -> None:
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        with pytest.raises(ValueError, match="threshold"):
            store.note_misses_and_delist({}, threshold=0)


# --- status helpers -----------------------------------------------------------


def test_mark_new_as_seen_flips_only_new(tmp_path: Path) -> None:
    other = "https://acme.example/jobs/2"
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        _seed(store, _job(_KEY), _job(other))
        store.set_status([other], "shortlisted")
        assert store.mark_new_as_seen() == 1
        by_key = {r["dedupe_key"]: r["status"] for r in store.all_jobs()}
        assert by_key == {_KEY: "seen", other: "shortlisted"}


def test_set_status_rejects_unknown_status(tmp_path: Path) -> None:
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        _seed(store, _job(_KEY))
        with pytest.raises(ValueError, match="starred"):
            store.set_status([_KEY], "starred")


def test_import_seen_rows_inserts_flips_and_preserves(tmp_path: Path) -> None:
    stored_new = "https://acme.example/jobs/2"
    stored_rejected = "https://acme.example/jobs/3"
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        _seed(store, _job(stored_new), _job(stored_rejected))
        store.set_status([stored_rejected], "rejected")

        run_id = store.begin_run()
        inserted, flipped = store.import_seen_rows(
            [{"dedupe_key": k} for k in (_KEY, stored_new, stored_rejected)], run_id
        )
        assert (inserted, flipped) == (1, 1)

        by_key = {r["dedupe_key"]: r["status"] for r in store.all_jobs()}
        assert by_key[_KEY] == "seen", "unknown key imported as seen"
        assert by_key[stored_new] == "seen", "'new' flipped: the owner has already had it"
        assert by_key[stored_rejected] == "rejected", "a review status is never demoted"

        # Idempotent: a second import changes nothing.
        run_id = store.begin_run()
        assert store.import_seen_rows(
            [{"dedupe_key": k} for k in (_KEY, stored_new, stored_rejected)], run_id
        ) == (0, 0)


def test_jobs_with_status_filters_and_validates(tmp_path: Path) -> None:
    other = "https://acme.example/jobs/2"
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        _seed(store, _job(_KEY), _job(other))
        store.set_status([other], "seen")
        assert [r["dedupe_key"] for r in store.jobs_with_status(("new",))] == [_KEY]
        assert len(store.jobs_with_status(("new", "seen"))) == 2
        with pytest.raises(ValueError, match="starred"):
            store.jobs_with_status(("starred",))


def test_job_index_reports_status_and_hybrid(tmp_path: Path) -> None:
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        run_id = store.begin_run()
        store.upsert_jobs([dict(_job(_KEY), hybrid_confirmed=1)], run_id)
        assert store.job_index() == {_KEY: {"status": "new", "hybrid_confirmed": 1}}
