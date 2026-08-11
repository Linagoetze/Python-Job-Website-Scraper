"""One-off import of the legacy CSV blocklist into the SQLite store.

The legacy blocklist means "already seen", not "rejected" (the old routine
blocklisted every job after each run), so every row is imported as status
'seen'. The CSV itself is only read, never modified. Idempotent: re-running
changes nothing once the rows are in.

Usage: python -m job_scraper.tools.import_blocklist [--blocklist PATH] [--db PATH]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from job_scraper.blocklist import default_blocklist_path, import_legacy_blocklist
from job_scraper.config_loader import default_jobs_db_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--blocklist", type=Path, default=default_blocklist_path())
    parser.add_argument("--db", type=Path, default=default_jobs_db_path())
    args = parser.parse_args()

    if not args.blocklist.is_file():
        raise SystemExit(f"Nothing to import: {args.blocklist} does not exist")

    inserted, flipped = import_legacy_blocklist(args.db, args.blocklist)
    print(
        f"Imported {args.blocklist} -> {args.db}: "
        f"{inserted} rows inserted as 'seen', {flipped} existing rows flipped to 'seen'"
    )


if __name__ == "__main__":
    main()
