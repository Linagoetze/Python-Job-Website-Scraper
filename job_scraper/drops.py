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
    python -m job_scraper.drops --show-drops --rule locations --source impactpool
    python -m job_scraper.drops --drops-csv ~/drops.csv

Reading is free and offline. The log is built from titles and metadata already
fetched during the run — nothing here or in the recording path opens a detail
page or makes any HTTP request at all.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from job_scraper.config_loader import default_jobs_db_path
from job_scraper.storage.db import JobStore, dedupe_key_for_job

# Stored ids for `run_exclusions.layer` — opaque, stable identifiers. They
# record the order the filters were *added*, not the order they run (WP8
# deleted 1c/1b, leaving gaps and a bare `1` running after `1a`), and
# ~49,000 historical rows already use this vocabulary. Never rename or
# renumber these; see LAYERS below for the human-facing ordinal and name.
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


@dataclass(frozen=True)
class Layer:
    """One rung of the ladder: a stable stored id, and how it is shown (WP8h).

    `id` is the only thing ever written to `run_exclusions.layer` or compared
    against in code — it must never change. `display` and `name` are
    presentation only, free to renumber or reword without touching a stored
    row.
    """

    id: str
    display: int
    name: str


# The ladder in execution order — the single source of truth for both the
# display ordinal and `eval.LADDER` (the subset it can replay). Order matches
# `pipeline.run_pipeline`: rules, then the combined title scan (keyword before
# seniority), then review status, then the detail-page checks. Named LAYERS,
# not LADDER, so it reads unambiguously next to `eval.LADDER` — that name is
# eval's own, for the smaller, replayable subset of this list.
LAYERS: tuple[Layer, ...] = (
    Layer(LAYER_RULES, 1, "Location and rules"),
    Layer(LAYER_TITLE_KEYWORD, 2, "Title keywords"),
    Layer(LAYER_SENIORITY, 3, "Seniority"),
    Layer(LAYER_REVIEW_STATUS, 4, "Review status"),
    Layer(LAYER_DETAIL, 5, "Detail page"),
)

_BY_ID: dict[str, Layer] = {layer.id: layer for layer in LAYERS}

# The other direction: `layer_ordinal` maps a stored id to its display number,
# and this maps a display number back to the layer it names. Both are derived
# from LAYERS, so the ladder is described in exactly one place (WP8i).
_BY_DISPLAY: dict[int, Layer] = {layer.display: layer for layer in LAYERS}

# ASCII digits only, and the whole argument. `str.isdigit` would also accept
# '\u00b3', which `int` then refuses.
_BARE_DIGITS = re.compile(r"[0-9]+")


def layer_ordinal(stored_id: str) -> int:
    """The display number (1-5) for *stored_id*.

    Only ever called with a current, non-retired id — WP8h's two retired ids
    (`1c-non-english`, `1b-language`) have no ordinal, so this raises rather
    than inventing one. Use `layer_display` for text that must also handle a
    retired or `refilter/`-prefixed id.
    """
    return _BY_ID[stored_id].display


def layer_short(stored_id: str) -> str:
    """'Layer 3' — the ordinal alone, for log lines that supply their own detail.

    The log messages in `pipeline.py` and `experience_filter.py` already name
    what each layer does ("(title keyword filter)"), so they want the number
    without the table's name repeated after it.
    """
    return f"Layer {_BY_ID[stored_id].display}"


def layer_sort_key(stored_id: str) -> tuple[int, int, str]:
    """Where *stored_id* sorts in a report: execution order, this run before re-filter.

    Ties in the drop log used to fall back to the stored id, which sorts
    alphabetically — '0-rules', '1-seniority', '1a-title-keyword' — and so
    printed the ladder as Layer 1, 3, 2, 4, 5. That was invisible while the
    stored ids were the only thing on screen and plainly wrong once WP8h put
    the ordinals there.

    Retired ids have no ordinal, so they sort after every current layer,
    together, by id: they are history, and history belongs at the foot of the
    table rather than interleaved with layers that still run.
    """
    is_refilter = stored_id.startswith(REFILTER_PREFIX)
    base_id = stored_id[len(REFILTER_PREFIX) :] if is_refilter else stored_id
    layer = _BY_ID.get(base_id)
    position = len(LAYERS) + 1 if layer is None else layer.display
    return (position, int(is_refilter), stored_id)


def layer_display(stored_id: str) -> str:
    """Human label for *stored_id*: 'Layer N: Name'.

    Handles both edge cases a raw stored id can carry: a `refilter/` prefix
    (WP8a's re-filter pass over stored jobs, same layer, different population)
    is kept visible rather than swallowed, and a stored id absent from
    `LAYERS` — one of WP8's retired layers, still present in ~49,000 historical
    rows — renders as retired instead of raising, so old rows stay readable.
    """
    is_refilter = stored_id.startswith(REFILTER_PREFIX)
    base_id = stored_id[len(REFILTER_PREFIX) :] if is_refilter else stored_id
    layer = _BY_ID.get(base_id)
    if layer is None:
        return f"{stored_id} (retired)"
    label = f"Layer {layer.display}: {layer.name}"
    return f"{label} (re-filter)" if is_refilter else label


def layer_search_hint(stored_id: str) -> str:
    """The shortest sensible `--layer` argument for *stored_id*.

    The stored ids carry a numeric prefix recording the order the filters were
    added ('1-seniority'), which is exactly the part a display number gets
    confused with. Dropping it leaves a hint that is unambiguous, digit-free
    and therefore itself an acceptable argument.
    """
    _, _, tail = stored_id.partition("-")
    return tail or stored_id


def layer_query_error(value: str) -> str | None:
    """Why *value* is not a usable `--layer` argument, or None if it is (WP8i).

    `--layer` matches the stored ids, and the display numbers WP8h put on
    screen are not among them — so a bare digit is never the query the typist
    meant. Left alone it does not fail: '3' matches nothing and prints "no
    matching exclusions", which reads as "Layer 3 dropped nothing", and '2'
    matches '2-detail' and prints Layer 5's rows under Layer 5's heading. Both
    are answers someone might act on. Refusing is the only honest reply, and
    naming the stored id teaches the mapping on the way past.

    Only an argument that is *entirely* digits is refused. '1a', '1c' and
    'seniority' are ordinary substring searches and are none of this
    function's business.

    Surrounding whitespace is stripped before that test, so a fat-fingered
    `--layer " 3"` is refused rather than slipping through to print the empty
    table this function exists to prevent. Only the test is stripped: the
    caller still queries on the argument as typed, because trimming that would
    change what a *non*-digit argument searches for, which is outside this
    rule.

    The digits are reported back two ways on purpose. The opening quotes the
    argument as typed, since that is what the typist has to recognise; every
    claim about the ladder after it uses the parsed number, so `--layer 03`
    does not produce "layer 03 is stored as ...".
    """
    typed = value.strip()
    if not _BARE_DIGITS.fullmatch(typed):
        return None
    number = int(typed)
    opening = f"--layer {typed} is a display number, not a stored layer name."
    layer = _BY_DISPLAY.get(number)
    if layer is None:
        return (
            f"{opening} There is no layer {number}; "
            f"the ladder is {LAYERS[0].display}-{LAYERS[-1].display}."
        )
    return (
        f"{opening} Did you mean --layer {layer_search_hint(layer.id)}? "
        f"(layer {number} is stored as {layer.id!r})"
    )


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

    Equal counts break to ladder order, not to the stored id's alphabet — see
    `layer_sort_key`.
    """
    counter = Counter((str(r["layer"]), str(r["rule"])) for r in rows)
    return [
        (layer, rule, n)
        for (layer, rule), n in sorted(
            counter.items(), key=lambda kv: (-kv[1], layer_sort_key(kv[0][0]), kv[0][1])
        )
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
        # Wide enough for the longest label a stored id can produce: a
        # re-filtered layer ("Layer 1: Location and rules (re-filter)", 39).
        # The re-filter marker is the whole point of that suffix — WP8a keeps
        # the two populations separable — so it must not be the part that gets
        # trimmed away.
        f"{'count':>7}  {'layer':<39}  rule",
        f"{'-' * 7}  {'-' * 39}  {'-' * 44}",
    ]
    lines += [
        f"{n:>7,}  {_trim(layer_display(layer), 39):<39}  {rule}" for layer, rule, n in counts
    ]
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
            "case-insensitive substring, so --rule locations and --rule hybrid both work. "
            "Location cases are named by their rule, not their layer: --rule locations, "
            "not --layer locations."
        ),
    )
    parser.add_argument(
        "--show-drops",
        action="store_true",
        dest="show_drops",
        help="List the individual excluded jobs instead of the per-rule counts",
    )
    parser.add_argument(
        "--layer",
        metavar="ID",
        help=(
            "Only exclusions whose stored layer id contains this text, as a "
            "case-insensitive substring. It matches the stored id, never the display "
            "number, so a bare number is refused rather than answered: '1' would hit "
            "three unrelated ids and '3' none at all. In execution order — "
            + "; ".join(f"{layer.id} ({layer_display(layer.id)})" for layer in LAYERS)
        ),
    )
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

    # Before the store is opened: a query that cannot mean anything should cost
    # nothing, and must not reach the point where it could print half a table.
    if args.layer is not None:
        problem = layer_query_error(args.layer)
        if problem is not None:
            raise SystemExit(problem)

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
