"""CLI entry point for the job scraper."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from job_scraper.config_loader import (
    default_jobs_db_path,
    default_jobs_xlsx_path,
    default_rules_path,
    default_sources_path,
    default_title_keywords_path,
    load_rules,
)
from job_scraper.pipeline import (
    DEFAULT_DELIST_AFTER,
    DEFAULT_KEEP_DROP_RUNS,
    RunSummary,
    run_pipeline,
)
from job_scraper.scoring import ScoringSummary, score_new_jobs
from job_scraper.storage.xlsx_store import write_xlsx

_RULE = "─" * 48


def format_summary(summary: RunSummary, scoring: ScoringSummary | None = None) -> str:
    """Render the end-of-run funnel: how many jobs each stage dropped, how many
    remained, the new rows written this run, and where the table now stands.

    The two closing totals are deliberately both present. "Still listed this
    run" counts every stored job this run saw on its source, whatever its
    review status; "Unreviewed jobs in table" counts only the ones not yet
    decided about. A run that finds 100 jobs still listed and 2 unreviewed is a
    normal run, not a lost-data incident — which is how the old single line,
    labelled "Jobs now in table", read."""

    # All numeric columns end at the same character position for vertical scanning.
    _NUMCOL = 46  # column where the dropped/total numbers right-align to

    def row(label: str, value: str, indent: int = 0) -> str:
        # right-align *value* so it ends at _NUMCOL, regardless of indent/label.
        return f"{' ' * indent}{label}".ljust(_NUMCOL - len(value)) + value

    def cut(label: str, dropped: int, remaining: tuple[int, str] | None = None) -> str:
        line = row(f"− {label}", "−" + format(dropped, ","), indent=2)
        if remaining is not None:
            kept, note = remaining
            line += f"   → {kept:>5,} {note}"
        return line

    after_keyword = summary.jobs_kept - summary.jobs_keyword_excluded
    passed_titles = after_keyword - summary.jobs_title_excluded
    after_blocklist = passed_titles - summary.jobs_blocklist_excluded
    rules_excluded = summary.jobs_extracted - summary.jobs_kept

    lines = [
        "Run summary",
        _RULE,
        f"Sources           {summary.sources_processed} / {summary.sources_total} processed  "
        f"({summary.sources_skipped} skipped)",
        "",
        row("Jobs seen (all pages, dupes incl.)", f"{summary.jobs_extracted:,}"),
        cut("off-criteria (location/keywords)", rules_excluded,
            (summary.jobs_kept, "match your criteria")),
        cut("title keyword", summary.jobs_keyword_excluded),
        cut("senior-level title", summary.jobs_title_excluded,
            (passed_titles, "passed title filters")),
        cut("blocklisted (rejected)", summary.jobs_blocklist_excluded,
            (after_blocklist, "after blocklist")),
        row("already in table (skipped)", f"{summary.jobs_already_stored:,}", indent=6),
        row("stored, hybrid recheck", f"{summary.jobs_stored_rechecked:,}", indent=6),
        row("new, detail-checked", f"{summary.jobs_new_checked:,}", indent=6),
        cut(f"needs 3+ yrs / PhD ({summary.jobs_phd_excluded} PhD)",
            summary.jobs_detail_excluded - summary.jobs_hybrid_excluded
            - summary.jobs_location_excluded),
        cut("non-hybrid (distant city)", summary.jobs_hybrid_excluded),
        cut("location unresolvable in the text", summary.jobs_location_excluded,
            (summary.jobs_kept_new, "new jobs kept")),
        _RULE,
        row("New rows written", f"{summary.rows_written:,}"),
        row("Marked delisted", f"{summary.rows_delisted:,}"),
        row("Still listed this run", f"{summary.jobs_still_listed:,}"),
        row("Unreviewed jobs in table", f"{summary.jobs_unreviewed:,}"),
        row("Exclusions logged", f"{summary.exclusions_logged:,}"),
    ]
    if scoring is not None:
        if scoring.skipped_reason:
            lines.append(f"Scoring skipped: {scoring.skipped_reason}")
        else:
            lines.append(row("Jobs scored (LLM)", f"{scoring.jobs_scored:,}"))
            if scoring.jobs_failed:
                lines.append(row("Scoring failures (retried next run)",
                                 f"{scoring.jobs_failed:,}"))
            lines.append(row("Estimated scoring cost",
                             f"${scoring.estimated_cost_usd:.4f}"))
    return "\n".join(lines)


def main() -> None:
    default_xlsx = default_jobs_xlsx_path()

    parser = argparse.ArgumentParser(description="Scrape configured career pages and filter jobs.")
    parser.add_argument(
        "--sources",
        type=Path,
        default=default_sources_path(),
        help="Path to sources.yaml",
    )
    parser.add_argument(
        "--rules",
        type=Path,
        default=default_rules_path(),
        help="Path to rules.json",
    )
    parser.add_argument(
        "--output-xlsx",
        type=Path,
        default=default_xlsx,
        dest="output_xlsx",
        help=f"Styled xlsx output (default: {default_xlsx})",
    )
    parser.add_argument(
        "--output-db",
        type=Path,
        default=default_jobs_db_path(),
        dest="output_db",
        help=f"SQLite job store (default: {default_jobs_db_path()})",
    )
    parser.add_argument(
        "--title-keywords",
        type=Path,
        default=default_title_keywords_path(),
        dest="title_keywords",
        help=f"Path to title_exclude_keywords.csv (default: {default_title_keywords_path()})",
    )
    parser.add_argument(
        "--delist-after",
        type=int,
        default=DEFAULT_DELIST_AFTER,
        dest="delist_after",
        help=(
            "Consecutive successful runs a job must go unseen before it is marked "
            f"delisted (default: {DEFAULT_DELIST_AFTER})"
        ),
    )
    parser.add_argument(
        "--keep-drop-runs",
        type=int,
        default=DEFAULT_KEEP_DROP_RUNS,
        dest="keep_drop_runs",
        help=(
            "Runs of filter exclusions to keep in the drop log, read back with "
            f"`python -m job_scraper.drops` (default: {DEFAULT_KEEP_DROP_RUNS}). "
            "Older rows are pruned at the end of each run."
        ),
    )
    parser.add_argument(
        "--allow-empty-delist",
        action="store_true",
        dest="allow_empty_delist",
        help=(
            "Delist stored jobs for a source that returned zero rows this run. "
            "Off by default: a zero-row result is usually a broken selector, not "
            "a genuinely empty page, and delisting would erase real history."
        ),
    )
    parser.add_argument(
        "--score",
        action="store_true",
        dest="score",
        help=(
            "Force the LLM scoring stage on for this run, overriding rules.json's "
            "scoring_enabled. Costs API credits — see rules.json and "
            "config/profile.md."
        ),
    )
    parser.add_argument(
        "--show-all",
        action="store_true",
        dest="show_all",
        help=(
            "Put every job on the xlsx review sheet, not just unreviewed ones. "
            "The archive sheet always holds every job either way."
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    try:
        summary = run_pipeline(
            sources_path=args.sources,
            rules_path=args.rules,
            out_db_path=args.output_db,
            title_keywords_path=args.title_keywords,
            allow_empty_delist=args.allow_empty_delist,
            delist_after=args.delist_after,
            keep_drop_runs=args.keep_drop_runs,
        )
    except FileNotFoundError as exc:
        # Missing config on a fresh clone — the message carries the fix, so show
        # it on its own rather than buried in a traceback.
        raise SystemExit(str(exc)) from None

    # rules.json's scoring_enabled is authoritative; --score forces the stage
    # on for this run regardless, e.g. to try it once before flipping the
    # config. Off by default costs nothing: score_new_jobs is never called.
    rules = load_rules(args.rules)
    scoring_enabled = args.score or bool(rules.get("scoring_enabled", False))

    # Score before exporting, so the spreadsheet is sorted by fresh scores.
    scoring = score_new_jobs(args.output_db) if scoring_enabled else None

    shown = write_xlsx(args.output_db, args.output_xlsx, show_all=args.show_all)

    print(format_summary(summary, scoring), file=sys.stderr)
    print(f"Output: {args.output_xlsx.resolve()}")
    if summary.exclusions_logged:
        print("Why was something dropped? python -m job_scraper.drops")
    if shown:
        print("Reviewed them? python -m job_scraper.review --seen-all")


if __name__ == "__main__":
    main()
