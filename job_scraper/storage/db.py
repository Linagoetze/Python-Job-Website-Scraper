"""SQLite store for jobs, runs, and per-source health.

The authoritative job store since WP5. Rows are never hard-deleted: a job that
disappears from its source is marked `status = 'delisted'` after `misses`
consecutive successful scrapes without a sighting, and a job that stops
passing the filters is marked `'rejected'`; both keep their history.

Unlike the old CSV store, this one knows *when* it saw things:
`first_seen`/`last_seen` on jobs (ISO-8601 UTC strings), a `runs` table, and a
`source_health` row per scrape attempt, which WP10 uses to warn about sources
whose counts collapse.

Since WP8a it also knows what it threw away: `run_exclusions` holds one row per
filter exclusion per run, naming the rule that fired, pruned to the last N runs.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from job_scraper.urlutil import canonical_detail_url, dedupe_key_from_url, normalize_http_url

JOB_STATUSES = ("new", "seen", "shortlisted", "rejected", "delisted")

# Statuses a human set (or will set, from WP5b's review commands). Automated
# passes — delisting, re-filtering — must never overwrite these.
REVIEW_STATUSES = ("shortlisted", "rejected")


def dedupe_key_for_job(job: dict[str, Any]) -> str:
    """Stable deduplication key from an extractor job dict ('' if it has no URL)."""
    u = normalize_http_url(
        (job.get("detail_url") or "").strip() or (job.get("apply_url") or "").strip()
    )
    return dedupe_key_from_url(u)


def job_to_row(job: dict[str, Any]) -> dict[str, Any] | None:
    """Convert an extractor job dict to a store row, or None if it has no key.

    The stored detail_url is the canonical form (`canonical_detail_url` may add
    a locale prefix, e.g. Oatly), while the dedupe key is computed from the raw
    URL — `dedupe_key_from_url` folds both variants to the same key, which is
    what lets a job stored in one run be recognised in the next.
    """
    key = dedupe_key_for_job(job)
    if not key:
        return None
    raw_du = (job.get("detail_url") or "").strip() or (job.get("apply_url") or "").strip()
    raw_au = (job.get("apply_url") or "").strip()
    src = str(job.get("source_name") or "").strip()
    listing = str(job.get("listing_url") or "").strip()
    du = canonical_detail_url(src, listing, raw_du) if raw_du else ""
    au = normalize_http_url(raw_au) if raw_au and raw_au != du else ""
    return {
        "dedupe_key": key,
        "source_name": str(job.get("source_name") or ""),
        "company": str(job.get("company") or ""),
        "title": str(job.get("title") or ""),
        "location": str(job.get("location") or ""),
        "detail_url": du,
        "apply_url": au,
        "experience_level": str(job.get("experience_level") or ""),
        "hybrid_confirmed": int(job.get("hybrid_confirmed") or 0),
        "description_text": str(job.get("description_text") or ""),
        "description_fetched_at": str(job.get("description_fetched_at") or ""),
    }

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
    experience_level TEXT NOT NULL DEFAULT '',
    misses           INTEGER NOT NULL DEFAULT 0,
    hybrid_confirmed INTEGER NOT NULL DEFAULT 0,
    description_text TEXT NOT NULL DEFAULT '',
    description_fetched_at TEXT NOT NULL DEFAULT '',
    -- LLM scoring (WP7). score is NULL until a job has been scored — 0 is a
    -- real (terrible) score and must stay distinguishable from "not scored".
    -- scored_description_sha256 records what text the score judged, so a job
    -- is re-scored only when its description actually changes.
    score            INTEGER,
    score_seniority_fit TEXT NOT NULL DEFAULT '',
    score_relevance  TEXT NOT NULL DEFAULT '',
    score_reasoning  TEXT NOT NULL DEFAULT '',
    score_flags      TEXT NOT NULL DEFAULT '',
    scored_at        TEXT NOT NULL DEFAULT '',
    scored_description_sha256 TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS source_health (
    source_name TEXT NOT NULL,
    run_id      INTEGER NOT NULL REFERENCES runs(run_id),
    rows_found  INTEGER NOT NULL,
    ok          INTEGER NOT NULL,
    error       TEXT,
    PRIMARY KEY (source_name, run_id)
);

-- One row per filter exclusion (WP8a). Written inside the run's transaction,
-- so a run's drops commit or roll back with everything else it decided.
--
-- Deliberately not keyed on dedupe_key and deliberately no foreign key to
-- jobs: the whole point is the jobs that were *not* stored, and the same job
-- can legitimately be dropped twice in one run (once as scraped, once as a
-- stored row the re-filter pass rejected). `rule` names the specific keyword,
-- term or language code that fired — the layer alone was what made a false
-- negative impossible to find in the first place.
--
-- Pruned to the last N runs by `prune_exclusions`, so it cannot grow forever.
CREATE TABLE IF NOT EXISTS run_exclusions (
    run_id      INTEGER NOT NULL REFERENCES runs(run_id),
    dedupe_key  TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    company     TEXT NOT NULL DEFAULT '',
    source_name TEXT NOT NULL DEFAULT '',
    location    TEXT NOT NULL DEFAULT '',
    layer       TEXT NOT NULL,
    rule        TEXT NOT NULL,
    excluded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_exclusions_run ON run_exclusions (run_id);

-- Which job each row of the last export's review sheet holds (WP5b). This is
-- how `python -m job_scraper.review --reject 7` knows what row 7 was, and it
-- is recorded rather than re-derived: re-deriving the sort at review time
-- would silently address a different job if a scrape had run in between.
-- Plain data, no spreadsheet syntax — the =HYPERLINK() rule still holds.
CREATE TABLE IF NOT EXISTS export_rows (
    row_number INTEGER PRIMARY KEY,
    dedupe_key TEXT NOT NULL REFERENCES jobs(dedupe_key)
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
        # A database created by an earlier WP predates newer columns; add them
        # in place rather than losing the first_seen history it has accrued.
        have = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        for column in ("misses", "hybrid_confirmed"):
            if column not in have:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
        for column in ("description_text", "description_fetched_at"):
            if column not in have:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        if "score" not in have:
            conn.execute("ALTER TABLE jobs ADD COLUMN score INTEGER")
        for column in (
            "score_seniority_fit",
            "score_relevance",
            "score_reasoning",
            "score_flags",
            "scored_at",
            "scored_description_sha256",
        ):
            if column not in have:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
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
        stored one: it means "not determined this run", not "none". Likewise an
        empty description_text never overwrites a stored one — a row upserted
        without a fresh detail fetch (e.g. a cached job whose last_seen is just
        being refreshed) must not blank out a description captured earlier;
        description_fetched_at moves in lockstep, only when description_text
        does. A sighting resets the consecutive-miss counter, and
        hybrid_confirmed only ever ratchets up — a run that could not
        re-verify the hybrid arrangement (e.g. it skipped the detail fetch)
        must not unconfirm it.
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
                                  last_run_id, status, experience_level,
                                  misses, hybrid_confirmed,
                                  description_text, description_fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
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
                                  THEN 'seen' ELSE jobs.status END,
                    misses = 0,
                    hybrid_confirmed = MAX(jobs.hybrid_confirmed, excluded.hybrid_confirmed),
                    description_text = CASE WHEN excluded.description_text != ''
                                            THEN excluded.description_text
                                            ELSE jobs.description_text END,
                    description_fetched_at = CASE WHEN excluded.description_text != ''
                                            THEN excluded.description_fetched_at
                                            ELSE jobs.description_fetched_at END
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
                    int(job.get("hybrid_confirmed") or 0),
                    str(job.get("description_text") or ""),
                    str(job.get("description_fetched_at") or ""),
                ),
            )
            if key in existing:
                updated += 1
            else:
                existing.add(key)
                inserted += 1
        return inserted, updated

    def note_misses_and_delist(
        self,
        seen_keys_by_source: dict[str, set[str]],
        threshold: int,
        force_delist_sources: set[str] | None = None,
    ) -> int:
        """Apply the consecutive-miss delisting rule. Returns jobs newly delisted.

        *seen_keys_by_source* holds, per source successfully scraped this run,
        every dedupe key the extractor offered (before any filter — a job that
        is listed but filtered is not "missing"). A sighted job's miss counter
        resets; an unsighted one increments, and at *threshold* consecutive
        misses the job is marked 'delisted'. Sources absent from the dict were
        not scraped successfully, so their jobs are left entirely alone — a
        broken selector must never erode stored history one miss at a time.

        Review decisions survive: only 'new' and 'seen' jobs are ever flipped
        to 'delisted'. A shortlisted or rejected job keeps its status (the miss
        counter still accrues, so the information is not lost).

        *force_delist_sources* is the --allow-empty-delist escape hatch: the
        owner has said these sources genuinely emptied, so their unreviewed
        jobs are delisted now rather than after *threshold* runs.
        """
        if threshold < 1:
            raise ValueError(f"delist threshold must be >= 1, got {threshold}")
        conn = self._c()
        delisted = 0
        for source, keys in seen_keys_by_source.items():
            stored = list(
                conn.execute(
                    "SELECT dedupe_key, status FROM jobs WHERE source_name = ?", (source,)
                )
            )
            sighted = [(r["dedupe_key"],) for r in stored if r["dedupe_key"] in keys]
            missing = [
                (r["dedupe_key"],)
                for r in stored
                if r["dedupe_key"] not in keys and r["status"] != "delisted"
            ]
            conn.executemany("UPDATE jobs SET misses = 0 WHERE dedupe_key = ?", sighted)
            conn.executemany("UPDATE jobs SET misses = misses + 1 WHERE dedupe_key = ?", missing)
            cur = conn.execute(
                "UPDATE jobs SET status = 'delisted'"
                " WHERE source_name = ? AND status IN ('new', 'seen') AND misses >= ?",
                (source, threshold),
            )
            delisted += cur.rowcount
        for source in force_delist_sources or ():
            cur = conn.execute(
                "UPDATE jobs SET status = 'delisted'"
                " WHERE source_name = ? AND status IN ('new', 'seen')",
                (source,),
            )
            delisted += cur.rowcount
        return delisted

    def set_status(self, keys: list[str], status: str) -> int:
        """Set *status* on every job in *keys*. Returns the number updated."""
        if status not in JOB_STATUSES:
            raise ValueError(f"unknown status {status!r}")
        cur = self._c().executemany(
            "UPDATE jobs SET status = ? WHERE dedupe_key = ?", ((status, k) for k in keys)
        )
        return cur.rowcount

    def mark_all_new(self, status: str) -> int:
        """Flip every unreviewed ('new') job to *status*. Returns the number flipped.

        Only 'new' rows are touched: a sweep must never overwrite a decision
        the owner has already recorded on a row.
        """
        if status not in JOB_STATUSES:
            raise ValueError(f"unknown status {status!r}")
        cur = self._c().execute("UPDATE jobs SET status = ? WHERE status = 'new'", (status,))
        return cur.rowcount

    def mark_new_as_seen(self) -> int:
        """Mark every unreviewed ('new') job as 'seen'. Returns the number flipped."""
        return self.mark_all_new("seen")

    def import_seen_rows(self, rows: list[dict[str, Any]], run_id: int) -> tuple[int, int]:
        """Import legacy blocklist rows as 'seen'. Returns (inserted, flipped).

        A key not yet stored is inserted with status 'seen'. A stored key that
        is 'new' or 'delisted' is flipped to 'seen' — the legacy blocklist is
        the record that the owner has already had it in the spreadsheet — but
        its timestamps are left alone, and a review status (shortlisted,
        rejected) is never demoted. Idempotent.
        """
        now = utc_now_iso()
        conn = self._c()
        inserted = flipped = 0
        for row in rows:
            key = str(row.get("dedupe_key") or "").strip()
            if not key:
                continue
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO jobs (dedupe_key, source_name, company, title,
                                            location, detail_url, apply_url,
                                            first_seen, last_seen, last_run_id, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'seen')
                """,
                (
                    key,
                    str(row.get("source_name") or ""),
                    str(row.get("company") or ""),
                    str(row.get("title") or ""),
                    str(row.get("location") or ""),
                    str(row.get("detail_url") or ""),
                    str(row.get("apply_url") or ""),
                    now,
                    now,
                    run_id,
                ),
            )
            if cur.rowcount:
                inserted += 1
            else:
                cur = conn.execute(
                    "UPDATE jobs SET status = 'seen'"
                    " WHERE dedupe_key = ? AND status IN ('new', 'delisted')",
                    (key,),
                )
                flipped += cur.rowcount
        return inserted, flipped

    def record_score(
        self,
        dedupe_key: str,
        *,
        score: int,
        seniority_fit: str,
        relevance: str,
        reasoning: str,
        flags: str,
        description_sha256: str,
        scored_at: str | None = None,
    ) -> None:
        """Persist one LLM scoring result against the description it judged.

        The upsert path never touches these columns, so a score survives every
        subsequent scrape until the description itself changes and the scorer
        writes a new one.
        """
        self._c().execute(
            """
            UPDATE jobs SET score = ?, score_seniority_fit = ?, score_relevance = ?,
                            score_reasoning = ?, score_flags = ?, scored_at = ?,
                            scored_description_sha256 = ?
            WHERE dedupe_key = ?
            """,
            (
                score,
                seniority_fit,
                relevance,
                reasoning,
                flags,
                scored_at or utc_now_iso(),
                description_sha256,
                dedupe_key,
            ),
        )

    def jobs_by_keys(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        """dedupe_key -> full row, for the keys that are actually stored."""
        found: dict[str, dict[str, Any]] = {}
        conn = self._c()
        for key in keys:
            row = conn.execute("SELECT * FROM jobs WHERE dedupe_key = ?", (key,)).fetchone()
            if row is not None:
                found[key] = dict(row)
        return found

    # -- exclusions (the drop log) --------------------------------------------

    def record_exclusions(
        self,
        run_id: int,
        exclusions: list[dict[str, Any]],
        *,
        now: str | None = None,
    ) -> int:
        """Log this run's filter exclusions. Returns the number of rows written.

        *exclusions* are plain dicts (dedupe_key, title, company, source_name,
        location, layer, rule) — the same data-in/data-out shape as everything
        else the store takes. They all share one *excluded_at*: they belong to
        one run, and it is the run_id that orders them.
        """
        stamped = now or utc_now_iso()
        self._c().executemany(
            "INSERT INTO run_exclusions (run_id, dedupe_key, title, company, source_name,"
            " location, layer, rule, excluded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    run_id,
                    str(e.get("dedupe_key") or ""),
                    str(e.get("title") or ""),
                    str(e.get("company") or ""),
                    str(e.get("source_name") or ""),
                    str(e.get("location") or ""),
                    str(e.get("layer") or ""),
                    str(e.get("rule") or ""),
                    stamped,
                )
                for e in exclusions
            ),
        )
        return len(exclusions)

    def prune_exclusions(self, keep_runs: int) -> int:
        """Keep only the *keep_runs* most recent runs' exclusions. Returns rows deleted.

        Counted over the runs that actually logged exclusions, not over `runs`:
        a run that dropped nothing should not push a useful one out of the
        window. A full scrape logs thousands of rows, so without this the table
        would be the largest thing in the database within a month.
        """
        if keep_runs < 1:
            raise ValueError(f"keep_runs must be >= 1, got {keep_runs}")
        cur = self._c().execute(
            "DELETE FROM run_exclusions WHERE run_id NOT IN ("
            " SELECT run_id FROM (SELECT DISTINCT run_id FROM run_exclusions"
            "  ORDER BY run_id DESC LIMIT ?))",
            (keep_runs,),
        )
        return cur.rowcount

    def latest_exclusion_run(self) -> int | None:
        """The most recent run_id that logged any exclusion, or None."""
        row = self._c().execute("SELECT MAX(run_id) AS r FROM run_exclusions").fetchone()
        return None if row is None or row["r"] is None else int(row["r"])

    def exclusions(
        self,
        run_id: int,
        *,
        layer: str | None = None,
        rule: str | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        """This run's exclusions, newest layer last, optionally filtered.

        The three filters match case-insensitively on a substring, so
        `--rule locations` finds the '0-rules' drops named 'locations: ...' and
        `--rule hybrid` finds both hybrid cases without anyone having to type a
        rule string exactly. Note that each filter matches its own column only:
        the location cases live in `rule`, so `--layer locations` matches
        nothing — `layer` holds ids like '0-rules'.
        """
        clauses = ["run_id = ?"]
        params: list[Any] = [run_id]
        for column, value in (("layer", layer), ("rule", rule), ("source_name", source)):
            if value:
                clauses.append(f"LOWER({column}) LIKE ?")
                params.append(f"%{value.lower()}%")
        rows = self._c().execute(
            f"SELECT * FROM run_exclusions WHERE {' AND '.join(clauses)}"
            " ORDER BY layer, rule, source_name, title",
            params,
        )
        return [dict(r) for r in rows]

    def count_sighted_in_run(self, run_id: int) -> int:
        """Stored jobs this run saw still listed, whatever their review status.

        The honest companion to the unreviewed count: a run that turns up 100
        jobs the owner has already decided about is not a run that found
        nothing.
        """
        row = self._c().execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE last_run_id = ?", (run_id,)
        ).fetchone()
        return int(row["n"])

    def count_with_status(self, statuses: tuple[str, ...]) -> int:
        unknown = set(statuses) - set(JOB_STATUSES)
        if unknown:
            raise ValueError(f"unknown statuses {sorted(unknown)!r}")
        placeholders = ", ".join("?" for _ in statuses)
        row = self._c().execute(
            f"SELECT COUNT(*) AS n FROM jobs WHERE status IN ({placeholders})", statuses
        ).fetchone()
        return int(row["n"])

    # -- export addressing ----------------------------------------------------

    def replace_export_rows(self, rows: list[tuple[int, str]]) -> None:
        """Record row_number -> dedupe_key for the export just written.

        Wholesale replacement: only the most recent export is addressable, so
        a row number can never resolve against a spreadsheet the owner no
        longer has in front of them.
        """
        conn = self._c()
        conn.execute("DELETE FROM export_rows")
        conn.executemany(
            "INSERT INTO export_rows (row_number, dedupe_key) VALUES (?, ?)", rows
        )

    def export_row_map(self) -> dict[int, str]:
        """row_number -> dedupe_key for the last export (empty if never exported)."""
        return {
            int(r["row_number"]): str(r["dedupe_key"])
            for r in self._c().execute("SELECT row_number, dedupe_key FROM export_rows")
        }

    def job_index(self) -> dict[str, dict[str, Any]]:
        """dedupe_key -> {status, hybrid_confirmed} for every stored job."""
        return {
            r["dedupe_key"]: {"status": r["status"], "hybrid_confirmed": r["hybrid_confirmed"]}
            for r in self._c().execute("SELECT dedupe_key, status, hybrid_confirmed FROM jobs")
        }

    def jobs_with_status(self, statuses: tuple[str, ...]) -> list[dict[str, Any]]:
        unknown = set(statuses) - set(JOB_STATUSES)
        if unknown:
            raise ValueError(f"unknown statuses {sorted(unknown)!r}")
        placeholders = ", ".join("?" for _ in statuses)
        rows = self._c().execute(
            f"SELECT * FROM jobs WHERE status IN ({placeholders})"
            " ORDER BY source_name, dedupe_key",
            statuses,
        )
        return [dict(r) for r in rows]

    def all_jobs(self) -> list[dict[str, Any]]:
        rows = self._c().execute("SELECT * FROM jobs ORDER BY source_name, dedupe_key")
        return [dict(r) for r in rows]
