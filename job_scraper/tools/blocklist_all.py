"""DEPRECATED (WP5b): use `python -m job_scraper.review --seen-all` instead.

Mark every unreviewed job as seen, then regenerate jobs.xlsx — the post-run
half of the scrape-and-blocklist routine. It still works and still deletes
nothing, but `review` is the command that also lets you record a decision on
a single row rather than only on all of them at once.

Kept until the owner confirms the new flow. Safe to run repeatedly.

Usage: python -m job_scraper.tools.blocklist_all
"""

from __future__ import annotations

import argparse

from job_scraper.blocklist import mark_all_new_seen
from job_scraper.config_loader import default_jobs_db_path, default_jobs_xlsx_path
from job_scraper.storage.xlsx_store import write_xlsx


def _parse_args() -> None:
    """The front door this script did not have (WP10).

    It takes no options, and that is the point: without a parser `main()` read
    no `sys.argv` at all, so `--help` was not a flag it rejected but text it
    never looked at — and the command ran. That is how WP8b lost the record of
    which postings were unreviewed. An unrecognised argument now exits non-zero
    having done nothing, and `--help` prints this module's docstring.
    """
    argparse.ArgumentParser(
        prog="python -m job_scraper.tools.blocklist_all",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    ).parse_args()


def main() -> None:
    _parse_args()
    db_path = default_jobs_db_path()
    xlsx_path = default_jobs_xlsx_path()

    marked = mark_all_new_seen(db_path)
    table_total = write_xlsx(db_path, xlsx_path)

    print(f"Marked {marked} jobs as seen in {db_path} (rows kept, nothing deleted)")
    print(f"jobs.xlsx regenerated ({table_total} unreviewed jobs shown)")
    print("NOTE: deprecated — this is `python -m job_scraper.review --seen-all`.")


if __name__ == "__main__":
    main()
