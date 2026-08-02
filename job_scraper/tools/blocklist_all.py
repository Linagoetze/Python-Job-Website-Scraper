"""One-off script: move every job currently in jobs.csv into the permanent
blocklist, clear jobs.csv, and regenerate jobs.xlsx.

After this runs, none of the rejected postings will be scraped again: the
pipeline filters them out every run via job_scraper/blocklist.py.

Usage: python -m job_scraper.tools.blocklist_all
"""

import csv

from job_scraper.blocklist import append_to_blocklist, default_blocklist_path
from job_scraper.config_loader import default_jobs_csv_path, default_jobs_xlsx_path
from job_scraper.storage.csv_store import _rewrite_file
from job_scraper.storage.xlsx_store import write_xlsx


def main() -> None:
    csv_path = default_jobs_csv_path()
    xlsx_path = default_jobs_xlsx_path()
    blocklist_path = default_blocklist_path()

    if not csv_path.is_file() or csv_path.stat().st_size == 0:
        raise SystemExit(f"No jobs to blocklist: {csv_path} is missing or empty")

    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    added = append_to_blocklist(blocklist_path, rows)

    # Scope is "everything currently stored", so clear jobs.csv to header-only.
    _rewrite_file(csv_path, [])

    table_total = write_xlsx(csv_path, xlsx_path)

    print(f"Read {len(rows)} jobs from jobs.csv")
    print(f"Added {added} new keys to {blocklist_path} ({len(rows) - added} already present)")
    print(f"Cleared jobs.csv (now {table_total} rows)")
    print("jobs.xlsx regenerated")


if __name__ == "__main__":
    main()
