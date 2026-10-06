"""Re-apply the filters to stored unreviewed jobs and regenerate jobs.xlsx.

Failing rows are marked 'rejected', never deleted. Only 'new' rows are judged.
The pass is title, location, years and PhD: years and PhD are re-read from the
stored description with no HTTP. It cannot re-judge what a run reads from a
posting's raw_snippet or department, which the store does not hold, so a row a
run kept on those is left alone.

Each rejection is logged under a `refilter/` layer in a run of its own, so
`python -m job_scraper.drops` shows it like any other exclusion.

--dry-run prints every status change and every level change, writes nothing
(the whole transaction is rolled back) and leaves jobs.xlsx as it is.

Usage: python -m job_scraper.tools.retrofilter [--dry-run]
"""

from __future__ import annotations

import argparse

from job_scraper.config_loader import (
    default_jobs_db_path,
    default_jobs_xlsx_path,
    default_title_keywords_path,
    load_rules,
)
from job_scraper.filtering import (
    build_hybrid_pattern,
    build_non_place_pattern,
    build_remote_region_pattern,
    load_title_exclude_keywords,
)
from job_scraper.pipeline import RefilterResult, refilter_stored_jobs
from job_scraper.storage.db import JobStore
from job_scraper.storage.xlsx_store import write_xlsx


def _parse_args() -> argparse.Namespace:
    """The front door this script did not have (WP10).

    Without a parser `main()` read no `sys.argv` at all, so `--help` was not a
    flag it rejected but text it never looked at, and the command ran. That is
    how WP8b lost the record of which postings were unreviewed. An unrecognised
    argument now exits non-zero having done nothing, and `--help` prints this
    module's docstring.
    """
    parser = argparse.ArgumentParser(
        prog="python -m job_scraper.tools.retrofilter",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the changes the pass would make and write nothing",
    )
    return parser.parse_args()


def _print_changes(result: RefilterResult) -> None:
    """One line per change, so a dry run can be read row by row."""
    flips = result.drops
    print(f"Status changes (new -> rejected): {len(flips)}")
    for drop in flips:
        print(f"  {drop['source_name']}: {drop['title']} [{drop['layer']}: {drop['rule']}]")
    level_only = [c for c in result.level_changes if not c.rejected]
    print(f"Level-only changes (status stays new): {len(level_only)}")
    for change in level_only:
        print(f"  {change.source_name}: {change.title} [{change.was or 'blank'} -> {change.now}]")


def main() -> None:
    args = _parse_args()
    db_path = default_jobs_db_path()
    xlsx_path = default_jobs_xlsx_path()

    rules = load_rules()
    title_keywords = load_title_exclude_keywords(default_title_keywords_path())
    hybrid_pattern = build_hybrid_pattern(rules)
    non_place_pattern = build_non_place_pattern(rules)

    with JobStore(db_path, dry_run=args.dry_run) as store:
        result = refilter_stored_jobs(
            store,
            rules,
            title_keywords,
            hybrid_pattern,
            non_place_pattern,
            remote_region_pattern=build_remote_region_pattern(rules),
        )
        # The drops belong to a run, and this one opens its own so they are
        # logged like any other exclusion (WP8a). Opened only when there is
        # something to log: an empty run would become the "latest exclusion
        # run" and hide the last real one from `drops`.
        if result.drops:
            run_id = store.begin_run()
            store.record_exclusions(run_id, result.drops)
            store.finish_run(run_id)

    counts = result.counts
    if args.dry_run:
        print("Dry run: nothing was written.")
        _print_changes(result)
        return

    print(f"Marked {counts['title_keywords']} rows rejected by title keywords")
    print(f"Marked {counts['rules']} rows rejected by rules, {counts['title']} by seniority")
    print(f"Marked {counts['experience']} rows rejected by their stored description")
    print(f"Total rows marked rejected (kept in the database): {sum(counts.values())}")
    print(f"Rewrote the experience level of {len(result.level_changes)} rows")

    write_xlsx(db_path, xlsx_path)
    print("jobs.xlsx regenerated")


if __name__ == "__main__":
    main()
