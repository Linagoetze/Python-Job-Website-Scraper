"""Review statuses for jobs the owner has already dealt with.

Replaces the CSV blocklist (WP5): instead of a separate file of keys the
pipeline must re-parse every run, a job's review state lives on its row in the
SQLite store, and the pipeline excludes stored 'rejected' jobs itself.

The legacy `data/curated/blocklist.csv` was built by a routine that blocklisted
*every* surfaced job after each run, so a row there means "already seen", not
"rejected". `import_legacy_blocklist` therefore imports rows as status 'seen',
and only ever reads the file — it stays untouched on disk as the pre-SQLite
record of what the owner has reviewed.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from job_scraper.config_loader import default_curated_dir, default_jobs_db_path
from job_scraper.storage.db import JobStore


def default_blocklist_path() -> Path:
    return default_curated_dir() / "blocklist.csv"


def mark_all_new_seen(db_path: Path | None = None) -> int:
    """Mark every unreviewed ('new') job as 'seen'. Returns the number flipped.

    The database replacement for the old blocklist-everything routine: the
    next run's spreadsheet then shows only jobs stored after this point, and
    nothing is deleted to achieve it.
    """
    with JobStore(db_path or default_jobs_db_path()) as store:
        return store.mark_new_as_seen()


def read_legacy_blocklist(path: Path | None = None) -> list[dict[str, Any]]:
    """Read the legacy blocklist rows (empty if the file is missing/empty)."""
    p = path or default_blocklist_path()
    if not p.is_file() or p.stat().st_size == 0:
        return []
    with p.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "dedupe_key" not in reader.fieldnames:
            return []
        return [row for row in reader if (row.get("dedupe_key") or "").strip()]


def import_legacy_blocklist(
    db_path: Path | None = None, blocklist_path: Path | None = None
) -> tuple[int, int]:
    """Import every legacy blocklist row into the store as status 'seen'.

    Returns (inserted, flipped): keys not yet stored are inserted as 'seen';
    stored keys that are 'new' or 'delisted' are flipped to 'seen'. Review
    statuses are never demoted and the CSV is never written. Idempotent.
    """
    rows = read_legacy_blocklist(blocklist_path)
    if not rows:
        return 0, 0
    with JobStore(db_path or default_jobs_db_path()) as store:
        run_id = store.begin_run()
        inserted, flipped = store.import_seen_rows(rows, run_id)
        store.finish_run(run_id)
    return inserted, flipped
