"""CLI entry point for the job scraper."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from job_scraper.config_loader import (
    default_jobs_csv_path,
    default_jobs_xlsx_path,
    default_rules_path,
    default_sources_path,
    default_title_keywords_path,
)
from job_scraper.pipeline import RunSummary, run_pipeline
from job_scraper.storage.xlsx_store import write_xlsx

_RULE = "─" * 48


def format_summary(summary: RunSummary, table_total: int) -> str:
    """Render the end-of-run funnel: how many jobs each stage dropped, how many
    remained, the new rows written this run, and the cumulative table total."""

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
    after_title = after_keyword - summary.jobs_title_excluded
    after_non_english = after_title - summary.jobs_non_english_excluded
    passed_titles = after_non_english - summary.jobs_language_excluded
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
        cut("senior-level title", summary.jobs_title_excluded),
        cut("non-English text", summary.jobs_non_english_excluded),
        cut("language-speaker", summary.jobs_language_excluded,
            (passed_titles, "passed title filters")),
        cut("blocklisted (rejected)", summary.jobs_blocklist_excluded,
            (after_blocklist, "after blocklist")),
        row("already in table (skipped)", f"{summary.jobs_already_stored:,}", indent=6),
        row("new, detail-checked", f"{summary.jobs_new_checked:,}", indent=6),
        cut(f"needs 3+ yrs / PhD ({summary.jobs_phd_excluded} PhD)",
            summary.jobs_detail_excluded - summary.jobs_hybrid_excluded),
        cut("non-hybrid (distant city)", summary.jobs_hybrid_excluded,
            (summary.jobs_kept_new, "new jobs kept")),
        _RULE,
        row("New rows written", f"{summary.rows_written:,}"),
        row("Delisted removed", f"{summary.rows_delisted:,}"),
        row("Jobs now in table", f"{table_total:,}"),
    ]
    return "\n".join(lines)


def main() -> None:
    default_csv = default_jobs_csv_path()
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
        "--output",
        type=Path,
        default=default_csv,
        help=f"Internal CSV store used for deduplication (default: {default_csv})",
    )
    parser.add_argument(
        "--output-xlsx",
        type=Path,
        default=default_xlsx,
        dest="output_xlsx",
        help=f"Styled xlsx output (default: {default_xlsx})",
    )
    parser.add_argument(
        "--title-keywords",
        type=Path,
        default=default_title_keywords_path(),
        dest="title_keywords",
        help=f"Path to title_exclude_keywords.csv (default: {default_title_keywords_path()})",
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
            out_csv_path=args.output,
            title_keywords_path=args.title_keywords,
        )
    except FileNotFoundError as exc:
        # Missing config on a fresh clone — the message carries the fix, so show
        # it on its own rather than buried in a traceback.
        raise SystemExit(str(exc)) from None

    table_total = write_xlsx(args.output, args.output_xlsx)

    print(format_summary(summary, table_total), file=sys.stderr)
    print(f"Output: {args.output_xlsx.resolve()}")


if __name__ == "__main__":
    main()
