"""Refresh the `location` column in the labelled gold set from the job store.

`data/curated/labels.csv` records what the extractor produced *at labelling
time*. Fix an extractor and the eval harness keeps replaying the old value, so
a fixed extractor scores as no improvement (see docs/REFACTOR-PLAN.md, the
"gold set is blind to extractor changes" decision). This script closes that
gap: it copies the current location for each `dedupe_key` back into the gold
set, and changes nothing else.

Two rules it will not break:

- **The judgement is yours.** The `label` column is never written. Neither is
  any other column: only `location` moves.
- **Dry run by default.** Nothing is written without `--apply`, and `--apply`
  takes a timestamped backup first, writes to a temp file and `os.replace()`s
  it into place, so an interrupted run cannot leave a half-written gold set.

A refreshed location can invalidate the judgement that was made without it —
a job labelled `review` on its title alone may turn out to be in Chennai. The
script cannot know that, so it prints those rows for you to re-judge by hand
rather than guessing. Re-judging is a separate, deliberate step.

Usage:
    python scripts/refresh_label_locations.py            # dry run, shows what would change
    python scripts/refresh_label_locations.py --apply    # write it
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LABELS = _PROJECT_ROOT / "data" / "curated" / "labels.csv"
DEFAULT_DB = _PROJECT_ROOT / "data" / "jobs.sqlite3"

# labels.csv is semicolon-separated; keep it that way on the way out.
DELIMITER = ";"
LOCATION_COL = "location"
LABEL_COL = "label"
KEY_COL = "dedupe_key"


def current_locations(
    db_path: Path,
) -> tuple[dict[str, tuple[int, str, str]], int]:
    """Map dedupe_key -> the most recently observed location.

    Two places hold one: `jobs` for postings that survived the filters and were
    stored, `run_exclusions` for those that were dropped. Most of the gold set
    is drops, so both are needed. When a key appears in both, the later run
    wins — a job seen again this week describes itself better than the same job
    did three runs ago.
    """
    # Read-only: this script must never be the reason the store changes.
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        best: dict[str, tuple[int, str]] = {}
        for key, loc, run, src in con.execute(
            "SELECT dedupe_key, location, last_run_id, source_name FROM jobs"
        ):
            best[key] = (run or 0, loc or "", src or "")
        for key, loc, run, src in con.execute(
            "SELECT dedupe_key, location, run_id, source_name FROM run_exclusions"
        ):
            seen = best.get(key)
            if seen is None or (run or 0) >= seen[0]:
                best[key] = (run or 0, loc or "", src or "")
        latest = con.execute("SELECT max(run_id) FROM runs").fetchone()[0] or 0
    finally:
        con.close()
    return best, latest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--apply", action="store_true", help="write the file (default is a dry run)"
    )
    parser.add_argument(
        "--report-all",
        action="store_true",
        help="list every changed row, not only the ones needing a re-judge",
    )
    args = parser.parse_args()

    if not args.labels.exists():
        print(f"No gold set at {args.labels}", file=sys.stderr)
        return 1
    if not args.db.exists():
        print(f"No job store at {args.db}", file=sys.stderr)
        return 1

    raw = args.labels.read_bytes()
    # This file is hand-curated and deliberately excluded from version control
    # (real job titles); a refresh must show 63 changed lines, not 63 plus a
    # spurious one because the writer terminated the final row when the
    # original did not.
    ends_with_newline = raw.endswith(b"\n")

    with args.labels.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=DELIMITER)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    for required in (KEY_COL, LOCATION_COL, LABEL_COL):
        if required not in fieldnames:
            print(f"{args.labels} has no {required!r} column", file=sys.stderr)
            return 1

    locations, latest_run = current_locations(args.db)

    changed: list[tuple[dict[str, str], str, str]] = []
    # A posting that vanished before the extractor serving it was fixed can
    # never be re-observed, so the store's newest value for it is still the
    # broken one and refreshing is a no-op that looks like success. There is no
    # general way to spot a wrong location, but there is a precise one: a value
    # this very run corrected on other rows is a value known to be wrong. Those
    # are collected once the changes are known, below.
    seen_runs: list[tuple[dict[str, str], int]] = []
    missing = 0
    for row in rows:
        seen = locations.get(row[KEY_COL])
        if seen is None:
            missing += 1
            continue
        run, new, source = seen
        old = row[LOCATION_COL] or ""
        if new != old:
            changed.append((row, old, new))
            row[LOCATION_COL] = new
        if run < latest_run:
            seen_runs.append((row, run))

    print(f"{len(rows)} labelled rows, {len(rows) - missing} found in the store.")
    if missing:
        print(
            f"{missing} not in the store — left untouched (delisted, or pruned "
            f"from the drop log's retained runs)."
        )
    print(f"{len(changed)} locations {'updated' if args.apply else 'would change'}.")

    # A judgement made without a location may not survive learning one. Only the
    # owner can decide, so name the rows and stop there.
    rejudge = [(r, o, n) for r, o, n in changed if r[LABEL_COL].strip().lower() == "review"]
    if rejudge:
        print(
            f"\n{len(rejudge)} of them are labelled 'review' — judged before this "
            f"location was known. Re-judge these by hand:\n"
        )
        for row, old, new in rejudge:
            print(f"  {row.get('company', '')[:20]:22} {row.get('title', '')[:42]:44}")
            print(f"  {'':22} {old!r} -> {new!r}")

    corrected = {old for _, old, _ in changed}
    stale = [(r, run) for r, run in seen_runs if r[LOCATION_COL] in corrected]
    if stale:
        print(
            f"\n{len(stale)} rows still hold a location this run corrected elsewhere, "
            f"so the value is known to be wrong — but the posting vanished before run "
            f"{latest_run} and can never be re-observed, so nothing can refresh it. "
            f"Correct or re-judge these by hand:\n"
        )
        for row, run in stale:
            print(
                f"  [{row[LABEL_COL]:8}] last seen run {run:>3}  "
                f"{row.get('company', '')[:16]:18} {row.get('title', '')[:34]:36} "
                f"{row[LOCATION_COL]!r}"
            )

    if args.report_all and changed:
        print(f"\nAll {len(changed)} changed rows:\n")
        for row, old, new in changed:
            print(
                f"  [{row[LABEL_COL]:8}] {row.get('company', '')[:18]:20} "
                f"{row.get('title', '')[:36]:38} {old!r} -> {new!r}"
            )

    if not args.apply:
        print("\nDry run — nothing written. Re-run with --apply to write it.")
        return 0
    if not changed:
        print("\nNothing to write.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = args.labels.with_suffix(f".csv.{stamp}.bak")
    shutil.copy2(args.labels, backup)

    # Atomic: a crash mid-write must not be able to truncate the gold set.
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=fieldnames, delimiter=DELIMITER)
    writer.writeheader()
    writer.writerows(rows)
    text = buf.getvalue()
    if not ends_with_newline:
        text = text.rstrip("\r\n")

    tmp = args.labels.with_suffix(args.labels.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, args.labels)

    print(f"\nWritten. Backup: {backup.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
