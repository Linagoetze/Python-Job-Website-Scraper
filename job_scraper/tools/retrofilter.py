"""One-off script: re-apply all filters to stored unreviewed jobs and
regenerate jobs.xlsx. Failing rows are marked 'rejected', never deleted.

Usage: python -m job_scraper.tools.retrofilter
"""

from __future__ import annotations

import argparse

from job_scraper.config_loader import (
    default_jobs_db_path,
    default_jobs_xlsx_path,
    default_title_keywords_path,
    load_rules,
)
from job_scraper.filtering import build_hybrid_pattern, load_title_exclude_keywords
from job_scraper.pipeline import refilter_stored_jobs
from job_scraper.storage.db import JobStore
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
        prog="python -m job_scraper.tools.retrofilter",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    ).parse_args()


def main() -> None:
    _parse_args()
    db_path = default_jobs_db_path()
    xlsx_path = default_jobs_xlsx_path()

    rules = load_rules()
    title_keywords = load_title_exclude_keywords(default_title_keywords_path())
    hybrid_pattern = build_hybrid_pattern(rules)

    with JobStore(db_path) as store:
        # The drop rows are discarded here on purpose: they belong to a run,
        # and this one-off script does not open one.
        counts, _ = refilter_stored_jobs(store, rules, title_keywords, hybrid_pattern)

    print(f"Marked {counts['title_keywords']} rows rejected by title keywords")
    print(f"Marked {counts['rules']} rows rejected by rules, {counts['title']} by seniority")
    print(f"Total rows marked rejected (kept in the database): {sum(counts.values())}")

    write_xlsx(db_path, xlsx_path)
    print("jobs.xlsx regenerated")


if __name__ == "__main__":
    main()
