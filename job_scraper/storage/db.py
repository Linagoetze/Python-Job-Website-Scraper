"""SQLite store for jobs, runs, and per-source health.

WP4: this is a shadow store. The pipeline writes to it after every run, but
`data/jobs.csv` remains the source of truth until WP5 cuts over. Rows here are
never hard-deleted: a job that disappears from the authoritative store is
marked `status = 'delisted'` and keeps its history.

Unlike the CSV, this store knows *when* it saw things: `first_seen`/`last_seen`
on jobs (ISO-8601 UTC strings), a `runs` table, and a `source_health` row per
scrape attempt, which WP10 uses to warn about sources whose counts collapse.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

JOB_STATUSES = ("new", "seen", "shortlisted", "rejected", "delisted")

# jobs.status is a TEXT column with a CHECK rather than a lookup table: five
# fixed values, one user, and a constraint violation is the loud failure we
# want if a typo'd status ever reaches the store.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    dedupe_key       TEXT PRIMARY KEY,
    source_name      TEXT NOT NULL,
    company          TEXT NOT NULL DEFAULT '',
    title            TEXT NOT NULL DEFAULT '',
    location         TEXT NOT NULL DEFAULT '',
    detail_url       TEXT NOT NULL DEFAULT '',
    apply_url        TEXT NOT NULL DEFAULT '',
    first_seen       TEXT NOT NULL,
    last_seen        TEXT NOT NULL,
    last_run_id      INTEGER NOT NULL REFERENCES runs(run_id),
    status           TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'seen', 'shortlisted', 'rejected', 'delisted')),
    experience_level TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS source_health (
    source_name TEXT NOT NULL,
    run_id      INTEGER NOT NULL REFERENCES runs(run_id),
    rows_found  INTEGER NOT NULL,
    ok          INTEGER NOT NULL,
    error       TEXT,
    PRIMARY KEY (source_name, run_id)
);
"""


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string, e.g. '2026-08-10T14:03:07+00:00'."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobStore:
    """Context-managed SQLite store. One `with` block is one transaction:
    everything commits together on a clean exit and rolls back together on an
    exception, so a crash mid-run can never leave a half-written run behind.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> JobStore:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        self._conn = conn
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        assert self._conn is not None
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._conn.close()
            self._conn = None

    def _c(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("JobStore must be used inside a 'with' block")
        return self._conn

    # -- runs -----------------------------------------------------------------

    def begin_run(self, started_at: str | None = None) -> int:
        cur = self._c().execute(
            "INSERT INTO runs (started_at) VALUES (?)",
            (started_at or utc_now_iso(),),
        )
        assert cur.lastrowid is not None
        return cur.lastrowid

    def finish_run(self, run_id: int, finished_at: str | None = None) -> None:
        self._c().execute(
            "UPDATE runs SET finished_at = ? WHERE run_id = ?",
            (finished_at or utc_now_iso(), run_id),
        )

    # -- source health --------------------------------------------------------

    def record_source_health(
        self,
        run_id: int,
        source_name: str,
        rows_found: int,
        ok: bool,
        error: str | None = None,
    ) -> None:
        self._c().execute(
            "INSERT OR REPLACE INTO source_health"
            " (source_name, run_id, rows_found, ok, error) VALUES (?, ?, ?, ?, ?)",
            (source_name, run_id, rows_found, int(ok), error),
        )

    # -- jobs -----------------------------------------------------------------

    def upsert_jobs(
        self,
        jobs: list[dict[str, Any]],
        run_id: int,
        *,
        now: str | None = None,
        initial_status: str = "new",
    ) -> tuple[int, int]:
        """Insert unseen jobs and refresh seen ones. Returns (inserted, updated).

        A new key gets first_seen = last_seen = *now* and *initial_status*. An
        existing key keeps its first_seen and its status (review decisions must
        survive a scrape) and has last_seen/last_run_id bumped — except that a
        'delisted' job which reappears goes back to 'seen', since it is
        evidently listed again. An empty experience_level never overwrites a
        stored one: it means "not determined this run", not "none".
        """
        if initial_status not in JOB_STATUSES:
            raise ValueError(f"unknown status {initial_status!r}")
        now = now or utc_now_iso()
        conn = self._c()
        existing = {row[0] for row in conn.execute("SELECT dedupe_key FROM jobs")}
        inserted = updated = 0
        for job in jobs:
            key = str(job.get("dedupe_key") or "")
            if not key:
                continue
            conn.execute(
                """
                INSERT INTO jobs (dedupe_key, source_name, company, title, location,
                                  detail_url, apply_url, first_seen, last_seen,
                                  last_run_id, status, experience_level)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dedupe_key) DO UPDATE SET
                    source_name = excluded.source_name,
                    company = excluded.company,
                    title = excluded.title,
                    location = excluded.location,
                    detail_url = excluded.detail_url,
                    apply_url = excluded.apply_url,
                    last_seen = excluded.last_seen,
                    last_run_id = excluded.last_run_id,
                    experience_level = CASE WHEN excluded.experience_level != ''
                                            THEN excluded.experience_level
                                            ELSE jobs.experience_level END,
                    status = CASE WHEN jobs.status = 'delisted'
                                  THEN 'seen' ELSE jobs.status END
                """,
                (
                    key,
                    str(job.get("source_name") or ""),
                    str(job.get("company") or ""),
                    str(job.get("title") or ""),
                    str(job.get("location") or ""),
                    str(job.get("detail_url") or ""),
                    str(job.get("apply_url") or ""),
                    now,
                    now,
                    run_id,
                    initial_status,
                    str(job.get("experience_level") or ""),
                ),
            )
            if key in existing:
                updated += 1
            else:
                existing.add(key)
                inserted += 1
        return inserted, updated

    def mark_delisted_except(self, present_keys: set[str]) -> int:
        """Mark every job whose key is not in *present_keys* as 'delisted'.

        Never deletes. last_seen is left alone — it records the last time the
        job was actually observed. WP4's mirror calls this with "everything in
        the authoritative CSV", so any status can be overwritten here; WP5
        replaces this with the N-consecutive-misses rule and must reconcile
        delisting with review statuses properly.
        """
        conn = self._c()
        stored = [
            row[0]
            for row in conn.execute("SELECT dedupe_key FROM jobs WHERE status != 'delisted'")
        ]
        missing = [k for k in stored if k not in present_keys]
        conn.executemany(
            "UPDATE jobs SET status = 'delisted' WHERE dedupe_key = ?",
            ((k,) for k in missing),
        )
        return len(missing)

    def count_jobs(self) -> int:
        (n,) = next(iter(self._c().execute("SELECT COUNT(*) FROM jobs")))
        return int(n)

    def all_jobs(self) -> list[dict[str, Any]]:
        rows = self._c().execute("SELECT * FROM jobs ORDER BY source_name, dedupe_key")
        return [dict(r) for r in rows]

    def active_jobs(self) -> list[dict[str, Any]]:
        rows = self._c().execute(
            "SELECT * FROM jobs WHERE status != 'delisted' ORDER BY source_name, dedupe_key"
        )
        return [dict(r) for r in rows]
