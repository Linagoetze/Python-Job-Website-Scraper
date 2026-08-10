"""One-off migration: populate the SQLite store from an existing jobs.csv.

Reads the CSV store, recovers plain URLs from its =HYPERLINK() formulas, and
upserts every row into the database created by job_scraper/storage/db.py. The
CSV is only read, never written — it remains the source of truth until WP5.

Imported rows get status 'seen', not 'new': everything already in jobs.csv has
been in the owner's spreadsheet, so nothing here should later surface as
unreviewed. The CSV stores no timestamps, so first_seen/last_seen are both set
to the migration time — history before that moment is simply unknown.

Usage: python -m job_scraper.tools.migrate_to_sqlite [--csv PATH] [--db PATH] [--force]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from job_scraper.config_loader import default_jobs_csv_path, default_jobs_db_path
from job_scraper.storage.csv_store import read_store_rows
from job_scraper.storage.db import JobStore


def migrate(csv_path: Path, db_path: Path, *, force: bool = False) -> tuple[int, int]:
    """Upsert every jobs.csv row into the database. Returns (inserted, updated)."""
    if not csv_path.is_file() or csv_path.stat().st_size == 0:
        raise SystemExit(f"Nothing to migrate: {csv_path} is missing or empty")

    rows = read_store_rows(csv_path)

    with JobStore(db_path) as store:
        already = store.count_jobs()
        if already and not force:
            raise SystemExit(
                f"{db_path} already holds {already} jobs. This is a one-off migration; "
                "pass --force to upsert into the existing database anyway "
                "(existing rows keep their first_seen and status)."
            )
        run_id = store.begin_run()
        inserted, updated = store.upsert_jobs(rows, run_id, initial_status="seen")
        store.finish_run(run_id)
    return inserted, updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", type=Path, default=default_jobs_csv_path())
    parser.add_argument("--db", type=Path, default=default_jobs_db_path())
    parser.add_argument(
        "--force",
        action="store_true",
        help="Proceed even if the database already contains jobs (upserts).",
    )
    args = parser.parse_args()

    inserted, updated = migrate(args.csv, args.db, force=args.force)
    print(f"Migrated {args.csv} -> {args.db}: {inserted} inserted, {updated} updated")


if __name__ == "__main__":
    main()
