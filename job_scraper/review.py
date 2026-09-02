"""Record review decisions against the jobs in the spreadsheet (WP5b).

Replaces the blocklist-everything routine. That routine kept the next run's
table short by declaring every job dealt with, which worked but threw the
history away; now the store remembers when it first saw each job, so a
decision can be recorded on the job itself:

    python -m job_scraper.review --seen-all           # "looked at all of these"
    python -m job_scraper.review --shortlist 4 7      # by row number in jobs.xlsx
    python -m job_scraper.review --reject 5 9-12      # ranges are inclusive
    python -m job_scraper.review --shortlist 4 --reject-all   # reject the rest

Row numbers are the ones in the sheet's `#` column, which are also the row
numbers Excel shows down the left-hand side. They address the *last export*,
recorded in the store when jobs.xlsx was written — not a fresh sort of the
table, which could point at a different job if a scrape had run in between.

Nothing here deletes anything: a rejected job keeps its row and its history,
and is simply no longer offered for review.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

from job_scraper.config_loader import default_jobs_db_path, default_jobs_xlsx_path
from job_scraper.storage.db import JobStore
from job_scraper.storage.xlsx_store import write_xlsx


class ReviewError(Exception):
    """A mistake in what the owner asked for — reported, with nothing applied."""


@dataclass
class ReviewResult:
    """What a review invocation changed, for the user-facing summary."""

    marked: list[tuple[int, str, str]] = field(default_factory=list)
    seen_all: int = 0
    rejected_all: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.marked) or bool(self.seen_all) or bool(self.rejected_all)


def parse_rows(tokens: list[str]) -> list[int]:
    """Expand row arguments like ``["4", "9-12"]`` into row numbers.

    A range is inclusive at both ends, matching how people read a run of
    spreadsheet rows. Anything unparseable raises before a single status is
    touched, keeping the all-or-nothing promise of `resolve_rows`.
    """
    rows: list[int] = []
    for token in tokens:
        first, dash, last = token.partition("-")
        try:
            if dash:
                start, end = int(first), int(last)
            else:
                start = end = int(token)
        except ValueError:
            raise ReviewError(
                f"{token!r} is not a row number or a range like 9-12. Nothing was changed."
            ) from None
        if start > end:
            raise ReviewError(f"The range {token!r} runs backwards. Nothing was changed.")
        rows.extend(range(start, end + 1))
    return rows


def _describe(row: dict[str, object]) -> str:
    """One-line identification of a job, so a mistyped row number is visible."""
    title = str(row.get("title") or "(no title)")
    who = str(row.get("company") or "") or str(row.get("source_name") or "")
    return f"{title} ({who})" if who else title


def resolve_rows(store: JobStore, numbers: list[int]) -> dict[int, str]:
    """Map spreadsheet row numbers to dedupe keys, or raise.

    All-or-nothing on purpose: a command naming five rows, one of them a typo,
    must not half-apply. The caller resolves everything before it writes.
    """
    if not numbers:
        return {}
    mapping = store.export_row_map()
    if not mapping:
        raise ReviewError(
            "No export to review against. Run `python -m job_scraper.run` "
            "(or `python -m job_scraper.review` on its own) to write jobs.xlsx first."
        )
    known = sorted(mapping)
    missing = sorted({n for n in numbers if n not in mapping})
    if missing:
        rows = ", ".join(str(n) for n in missing)
        raise ReviewError(
            f"No such row in the current jobs.xlsx: {rows}. "
            f"The sheet has rows {known[0]}-{known[-1]} "
            "(row 1 is the header). Nothing was changed."
        )
    return {n: mapping[n] for n in numbers}


def apply_review(
    db_path: Path,
    *,
    shortlist: list[int] | None = None,
    reject: list[int] | None = None,
    seen_all: bool = False,
    reject_all: bool = False,
) -> ReviewResult:
    """Apply review decisions in one transaction. Returns what changed.

    Row-addressed decisions are applied before *seen_all* and *reject_all*,
    so a job shortlisted in the same command is not swept up by either.
    """
    shortlist = sorted(set(shortlist or ()))
    reject = sorted(set(reject or ()))
    both = sorted(set(shortlist) & set(reject))
    if both:
        rows = ", ".join(str(n) for n in both)
        raise ReviewError(f"Row {rows} is in both --shortlist and --reject. Nothing was changed.")
    if seen_all and reject_all:
        raise ReviewError(
            "--seen-all and --reject-all both sweep every unreviewed job; "
            "pick one. Nothing was changed."
        )

    result = ReviewResult()
    with JobStore(db_path) as store:
        wanted = {**dict.fromkeys(shortlist, "shortlisted"), **dict.fromkeys(reject, "rejected")}
        keys = resolve_rows(store, sorted(wanted))
        jobs = store.jobs_by_keys(list(keys.values()))

        for number in sorted(wanted):
            status = wanted[number]
            key = keys[number]
            store.set_status([key], status)
            result.marked.append((number, status, _describe(jobs.get(key, {}))))

        if seen_all:
            result.seen_all = store.mark_new_as_seen()
        if reject_all:
            result.rejected_all = store.mark_all_new("rejected")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record review decisions against the jobs in jobs.xlsx.",
        epilog=(
            "Row numbers are the '#' column of the last exported jobs.xlsx. "
            "Nothing is ever deleted: reviewed jobs keep their row and history."
        ),
    )
    parser.add_argument(
        "--seen-all",
        action="store_true",
        dest="seen_all",
        help="Mark every unreviewed job as seen — the replacement for blocklist-everything",
    )
    parser.add_argument(
        "--shortlist",
        nargs="+",
        metavar="ROW",
        default=[],
        help="Mark these spreadsheet rows as shortlisted; single numbers or ranges like 9-12",
    )
    parser.add_argument(
        "--reject",
        nargs="+",
        metavar="ROW",
        default=[],
        help="Mark these spreadsheet rows as rejected; single numbers or ranges like 9-12",
    )
    parser.add_argument(
        "--reject-all",
        action="store_true",
        dest="reject_all",
        help=(
            "Mark every unreviewed job as rejected — combine with --shortlist "
            "to keep the ones you want and reject the rest"
        ),
    )
    parser.add_argument(
        "--show-all",
        action="store_true",
        dest="show_all",
        help="Regenerate jobs.xlsx with every job on the review sheet, not just unreviewed ones",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=default_jobs_db_path(),
        help=f"SQLite job store (default: {default_jobs_db_path()})",
    )
    parser.add_argument(
        "--output-xlsx",
        type=Path,
        default=default_jobs_xlsx_path(),
        dest="output_xlsx",
        help=f"Styled xlsx output (default: {default_jobs_xlsx_path()})",
    )
    parser.add_argument(
        "--no-export",
        action="store_true",
        dest="no_export",
        help=(
            "Do not regenerate jobs.xlsx afterwards. The row numbers you are "
            "reading stay valid, so several commands can address the same sheet."
        ),
    )
    args = parser.parse_args()

    try:
        result = apply_review(
            args.db,
            shortlist=parse_rows(args.shortlist),
            reject=parse_rows(args.reject),
            seen_all=args.seen_all,
            reject_all=args.reject_all,
        )
    except ReviewError as exc:
        raise SystemExit(str(exc)) from None

    for number, status, description in result.marked:
        print(f"row {number:>4}  {status:<11} {description}")
    if args.seen_all:
        print(f"Marked {result.seen_all} unreviewed jobs as seen (rows kept, nothing deleted)")
    if args.reject_all:
        print(
            f"Marked {result.rejected_all} unreviewed jobs as rejected (rows kept, nothing deleted)"
        )
    if not result.changed:
        print(
            "No decisions recorded — pass --seen-all, --reject-all, "
            "--shortlist ROW or --reject ROW."
        )

    if args.no_export:
        if result.changed:
            print("jobs.xlsx not regenerated (--no-export): its row numbers still apply.")
        return

    shown = write_xlsx(args.db, args.output_xlsx, show_all=args.show_all)
    scope = "jobs, every status" if args.show_all else "unreviewed jobs"
    print(f"{args.output_xlsx.resolve()} regenerated ({shown} {scope}; archive sheet has all)")
    if result.changed:
        print("Reopen it before using row numbers again — they have been renumbered.")


if __name__ == "__main__":
    main()
