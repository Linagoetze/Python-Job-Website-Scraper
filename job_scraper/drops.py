"""Read the drop log: what each filter layer excluded, and which rule fired.

Every filter exclusion used to be invisible. Layer 0 called `continue` on a
failing job and wrote nothing; layers 1a to 1c returned excluded lists that
were counted for the run summary and then discarded. So a false negative — a
job worth seeing that a rule quietly ate — could not be found at all, and a
rule change could not be shown to have helped.

The pipeline now records one row per exclusion in the store's `run_exclusions`
table, naming the specific keyword, term or location case that
fired. This module reads it back:

    python -m job_scraper.drops                    # last run, counts per rule
    python -m job_scraper.drops --show-drops       # last run, one line per job
    python -m job_scraper.drops --show-drops --layer locations --source impactpool
    python -m job_scraper.drops --drops-csv ~/drops.csv

Reading is free and offline. The log is built from titles and metadata already
fetched during the run — nothing here or in the recording path opens a detail
page or makes any HTTP request at all.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path
from typing import Any

from job_scraper.config_loader import default_jobs_db_path
from job_scraper.storage.db import JobStore, dedupe_key_for_job

# Layer names, matching the ladder as README documents it. The labels are
# historical — they record the order the filters were added, not the order they
# run — so the numeric prefix is kept and the execution order is the order of
# this list.
LAYER_RULES = "0-rules"
LAYER_TITLE_KEYWORD = "1a-title-keyword"
LAYER_SENIORITY = "1-seniority"
LAYER_REVIEW_STATUS = "1d-review-status"
LAYER_DETAIL = "2-detail"

# Exclusions from the re-filter pass over *stored* unreviewed jobs are logged
# under the same layer names behind this prefix. They are real exclusions and
# belong in the log, but they are a different population from this run's
# scrape, and mixing the two would make the funnel counts unreadable.
REFILTER_PREFIX = "refilter/"

RULE_REVIEW_REJECTED = "review status: already rejected"

# Columns of the CSV export, in the order the table declares them.
CSV_FIELDS = [
    "run_id",
    "dedupe_key",
    "title",
    "company",
    "source_name",
    "location",
    "layer",
    "rule",
    "excluded_at",
]


def refiltered(layer: str) -> str:
    """The re-filter pass's name for *layer*."""
    return f"{REFILTER_PREFIX}{layer}"


def exclusion(job: dict[str, Any], layer: str, rule: str) -> dict[str, Any]:
    """One drop-log row from a job dict, for the store to write.

    Titles and metadata only: everything here was already in hand when the job
    was excluded, so building the log costs no fetch.

    A stored row carries its own key; only a freshly scraped job dict has to
    have one derived, and `canonical_detail_url` can rewrite a stored URL, so
    re-deriving one for a stored row could disagree with the key it is filed
    under.
    """
    return {
        "dedupe_key": str(job.get("dedupe_key") or "") or dedupe_key_for_job(job),
        "title": str(job.get("title") or ""),
        "company": str(job.get("company") or ""),
        "source_name": str(job.get("source_name") or ""),
        "location": str(job.get("location") or ""),
        "layer": layer,
        "rule": rule,
    }


def _trim(value: object, width: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


def rule_counts(rows: list[dict[str, Any]]) -> list[tuple[str, str, int]]:
    """(layer, rule, count) over *rows*, most frequent first.

    Counted from the same rows the detail view lists, so a filtered summary and
    a filtered listing can never disagree about how many there were.
    """
    counter = Counter((str(r["layer"]), str(r["rule"])) for r in rows)
    return [
        (layer, rule, n)
        for (layer, rule), n in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def format_rule_counts(rows: list[dict[str, Any]], run_id: int) -> str:
    """The 'which rule fired most' table — the answer to 'why did I lose those?'."""
    counts = rule_counts(rows)
    if not counts:
        return f"Run {run_id} recorded no matching exclusions."
    total = sum(n for _, _, n in counts)
    lines = [
        f"Exclusions in run {run_id}: {total:,} across {len(counts)} rules",
        "",
        f"{'count':>7}  {'layer':<18}  rule",
        f"{'-' * 7}  {'-' * 18}  {'-' * 44}",
    ]
    lines += [f"{n:>7,}  {_trim(layer, 18):<18}  {rule}" for layer, rule, n in counts]
    return "\n".join(lines)


def format_exclusions(rows: list[dict[str, Any]]) -> str:
    """One line per excluded job: enough to spot a false negative by eye."""
    if not rows:
        return "No exclusions match."
    lines = [
        f"{'source':<16}  {'title':<44}  {'location':<24}  rule",
        f"{'-' * 16}  {'-' * 44}  {'-' * 24}  {'-' * 40}",
    ]
    lines += [
        f"{_trim(r['source_name'], 16):<16}  {_trim(r['title'], 44):<44}  "
        f"{_trim(r['location'], 24):<24}  {r['rule']}"
        for r in rows
    ]
    lines.append("")
    lines.append(f"{len(rows):,} exclusions")
    return "\n".join(lines)


def write_csv(rows: list[dict[str, Any]], path: Path) -> int:
    """Export *rows* to *path*. Returns the number written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show what the filters excluded on the last run, and which rule fired.",
        epilog=(
            "With no flags, prints the per-rule counts. The filters match on a "
            "case-insensitive substring, so --layer locations and --rule hybrid both work."
        ),
    )
    parser.add_argument(
        "--show-drops",
        action="store_true",
        dest="show_drops",
        help="List the individual excluded jobs instead of the per-rule counts",
    )
    parser.add_argument("--layer", help="Only exclusions whose layer contains this text")
    parser.add_argument("--rule", help="Only exclusions whose rule contains this text")
    parser.add_argument("--source", help="Only exclusions from sources matching this text")
    parser.add_argument(
        "--drops-csv",
        type=Path,
        dest="drops_csv",
        help="Also export the matching exclusions to this CSV path",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=default_jobs_db_path(),
        help=f"SQLite job store (default: {default_jobs_db_path()})",
    )
    args = parser.parse_args()

    with JobStore(args.db) as store:
        run_id = store.latest_exclusion_run()
        if run_id is None:
            raise SystemExit(
                "No exclusions recorded yet. Run `python -m job_scraper.run` first — "
                "the drop log is written as part of a run."
            )
        rows = store.exclusions(
            run_id, layer=args.layer, rule=args.rule, source=args.source
        )

    # The filters narrow whichever view was asked for, so the counts and the
    # listing always describe the same set of jobs.
    if args.show_drops:
        print(format_exclusions(rows))
    else:
        print(format_rule_counts(rows, run_id))

    if args.drops_csv:
        written = write_csv(rows, args.drops_csv)
        print(f"Wrote {written:,} exclusions to {args.drops_csv.resolve()}")


if __name__ == "__main__":
    main()
