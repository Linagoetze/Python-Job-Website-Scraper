"""Which configured sources never get a posting past the location filter? (SP12)

    python -m job_scraper.tools.source_yield
    python -m job_scraper.tools.source_yield --db /path/to/copy.sqlite3 --places 0

A read-only report over the store and sources.yaml. It writes nothing, edits no
config and removes no source: dropping a source is the owner's edit. It opens
the store with SQLite's read-only mode, so it cannot write even by mistake, and
it never reads rules.json. The location values it prints are what the store
holds, for the owner's terminal only.

Each source is put in one of four classes, with its evidence beside the label:

  A  working             passed Layer 0 in the drop-log window, or has stored rows
  B  truthfully elsewhere  no pass, nothing ever stored, and every value it was
                         dropped on names a place
  C  starved, not dead   no pass, but the location field cannot carry the verdict
                         (empty, a placeholder, a label, a grade, the title, one
                         constant), or every drop is one rule other than "city
                         not on the list", which is a configuration question
  D  too new to say      fewer than --min-runs scrapes in the window

A row deferred at Layer 0 (a conditional city, a placeholder, an empty field)
passed Layer 0, whatever Layer 5 later made of it. The counts here are taken
from the drop log and `source_health`, and the report checks them against each
other before printing anything else.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from job_scraper.config_loader import default_jobs_db_path, default_sources_path, load_sources
from job_scraper.drops import (
    LAYER_DETAIL,
    LAYER_REVIEW_STATUS,
    LAYER_RULES,
    LAYER_SENIORITY,
    LAYER_TITLE_KEYWORD,
    REFILTER_PREFIX,
)
from job_scraper.filtering import RULE_LOC_UNLISTED_CITY

# K: a source scraped fewer times than this in the window is too new to judge.
# Measured on the store (2026-10-11): a board's second run lists almost exactly
# its first run's postings, and a small board's first new posting in the window
# typically arrived three or four runs in. Five runs, half the drop log's ten,
# is the least that has usually seen a board change at all.
DEFAULT_MIN_RUNS = 5

# A value signature fires when this share of a source's Layer 0 drops carries
# it. A board of one country may show the odd placeholder; once a quarter of
# its drops name no place, the location verdict on it is not about geography.
STARVED_SHARE = 0.25

# One value on this many distinct postings is a constant, not a coincidence. A
# one-office employer looks the same, and so does a reader writing a default
# (jobsinlund's reader writes its own city when the API gives none), which is
# why it is a C and the value is shown: the owner can tell the two apart.
CONSTANT_MIN_POSTINGS = 5

# Default number of distinct location values printed per source.
DEFAULT_PLACES = 10

CLASS_WORKING = "A"
CLASS_ELSEWHERE = "B"
CLASS_STARVED = "C"
CLASS_TOO_NEW = "D"

CLASS_NAMES = {
    CLASS_WORKING: "working",
    CLASS_ELSEWHERE: "truthfully elsewhere",
    CLASS_STARVED: "starved, not dead",
    CLASS_TOO_NEW: "too new to say",
}

# What a location value is, as far as the field's shape can tell.
VALUE_EMPTY = "empty"
VALUE_PLACEHOLDER = "placeholder"
VALUE_LABEL = "label"
VALUE_GRADE = "grade"
VALUE_TITLE = "equal to the title"
VALUE_PLACE = "place"

_PLACEHOLDER = re.compile(
    r"^(?:\d+|multiple|several|various|many)\s+(?:locations?|places|cities|offices|sites)$"
    r"|^(?:various|multiple|several)$",
    re.IGNORECASE,
)
# Words a page uses to name the field rather than fill it. ISS's "Title" was
# one (docs/DECISIONS.md, WP8g).
_LABELS = frozenset(
    {
        "title",
        "location",
        "locations",
        "city",
        "country",
        "place",
        "department",
        "unknown",
        "n/a",
        "na",
        "none",
        "null",
        "tbd",
        "tba",
        "-",
        "–",
        "—",
        "not specified",
        "see description",
        "ort",
        "plats",
        "standort",
        "arbetsort",
    }
)
# UN-system and common pay grades: impactpool once put one in the location
# column for every posting (docs/DECISIONS.md, 2026-09-25).
_GRADE = re.compile(
    r"^(?:p|d|g|gs|ns|no|noa|nob|noc|nod|sb|fs|ics|iica|lica|ug)[\s-]?\d{1,2}$"
    r"|^(?:grade|level|band)\s+\S+$",
    re.IGNORECASE,
)

# Layer 5 rules that settle a deferred location or hybrid state. A pass that
# ends on one of these still passed Layer 0; the report counts them so that is
# visible rather than inferred.
_DEFERRED_SETTLED_PREFIXES = ("location:", "hybrid:")
# A deferred job Layer 5 could not check is held back unstored for the run.
_UNVERIFIED_MARK = "could not read the description"

_LATER_LAYERS = (LAYER_TITLE_KEYWORD, LAYER_SENIORITY, LAYER_REVIEW_STATUS, LAYER_DETAIL)


def value_kind(location: str, title: str) -> str:
    """What one dropped location value is: a place, or a sign of a starved field."""
    value = " ".join(location.split())
    if not value:
        return VALUE_EMPTY
    if title and value.casefold() == " ".join(title.split()).casefold():
        return VALUE_TITLE
    if _PLACEHOLDER.match(value):
        return VALUE_PLACEHOLDER
    if value.casefold() in _LABELS:
        return VALUE_LABEL
    if _GRADE.match(value):
        return VALUE_GRADE
    return VALUE_PLACE


@dataclass
class SourceYield:
    """Everything the store says about one configured source, over the window."""

    name: str
    strategy: str
    health_runs: int = 0  # successful scrapes in source_health, all history
    window_runs: int = 0  # successful scrapes inside the drop-log window
    seen: int = 0
    l0_drops: int = 0
    drops_by_rule: Counter[str] = field(default_factory=Counter)
    later_drops: int = 0  # passed Layer 0, dropped at a later layer
    deferred_settled: int = 0  # of those, Layer 5 settling a deferred state
    drop_log_rows: int = 0  # every scrape-layer drop-log row in the window
    values: Counter[str] = field(default_factory=Counter)  # L0 drop locations
    value_kinds: Counter[str] = field(default_factory=Counter)
    constant_postings: int = 0  # distinct postings behind a single L0 value
    stored: int = 0
    stored_by_status: Counter[str] = field(default_factory=Counter)
    last_stored: str = ""

    @property
    def passes(self) -> int:
        return self.seen - self.l0_drops


@dataclass(frozen=True)
class Verdict:
    klass: str
    reasons: tuple[str, ...]
    provisional: str = ""  # for D: the class the evidence points at so far


def starved_signatures(y: SourceYield) -> list[str]:
    """Why a source's Layer 0 drops say nothing about where its jobs are."""
    found: list[str] = []
    if y.window_runs and not y.seen:
        found.append("nothing listed in the window: an empty board or a broken reader")
        return found
    if not y.l0_drops:
        return found
    for kind in (VALUE_EMPTY, VALUE_PLACEHOLDER, VALUE_LABEL, VALUE_GRADE, VALUE_TITLE):
        n = y.value_kinds.get(kind, 0)
        if n and n / y.l0_drops >= STARVED_SHARE:
            found.append(f"location {kind} on {n} of {y.l0_drops} drops")
    if len(y.values) == 1 and y.constant_postings >= CONSTANT_MIN_POSTINGS:
        found.append(
            f"one constant value on all {y.constant_postings} postings: "
            "a one-office employer, or a default the reader writes"
        )
    rules = list(y.drops_by_rule)
    if len(rules) == 1 and rules[0] != RULE_LOC_UNLISTED_CITY:
        found.append(f"every drop is one rule, {rules[0]!r}: a configuration question")
    return found


def classify(y: SourceYield, min_runs: int = DEFAULT_MIN_RUNS) -> Verdict:
    """Put a source in A, B, C or D, keeping the evidence that decided it.

    A first: a stored row is history, so a source that has one is never B
    however barren its window. C before D, because a starved field needs no
    history to be true and a bug report should not wait for one.
    """
    if y.passes > 0 or y.stored > 0:
        why = []
        if y.passes > 0:
            why.append(f"{y.passes} of {y.seen} rows passed Layer 0 in the window")
        else:
            why.append("no pass in the window")
        if y.stored:
            why.append(f"{y.stored} stored, the last first seen {y.last_stored}")
        return Verdict(CLASS_WORKING, tuple(why))
    starved = starved_signatures(y)
    if starved:
        return Verdict(CLASS_STARVED, tuple(starved))
    elsewhere = (
        f"0 of {y.seen} rows passed, nothing ever stored, "
        f"{len(y.values)} distinct places, all real-place shaped",
    )
    if y.window_runs < min_runs:
        return Verdict(
            CLASS_TOO_NEW,
            (f"{y.window_runs} scrape run(s) in the window, fewer than {min_runs}",),
            provisional=CLASS_ELSEWHERE if y.seen else "",
        )
    return Verdict(CLASS_ELSEWHERE, elsewhere)


# --- Reading the store -------------------------------------------------------


def open_readonly(path: Path) -> sqlite3.Connection:
    """The store, opened so that no statement can write to it.

    `mode=ro` also refuses to create a missing file, which `sqlite3.connect`
    would otherwise do silently and then report an empty store.
    """
    if not path.is_file():
        raise SystemExit(f"No store at {path}")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def window_runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """The scrape runs the drop log still holds, oldest first."""
    return conn.execute(
        """
        SELECT r.run_id, r.started_at FROM runs r
        WHERE r.kind = 'scrape'
          AND EXISTS (SELECT 1 FROM run_exclusions e WHERE e.run_id = r.run_id)
        ORDER BY r.run_id
        """
    ).fetchall()


def gather(
    conn: sqlite3.Connection, sources: list[dict[str, Any]], run_ids: list[int]
) -> list[SourceYield]:
    """One SourceYield per configured source, in sources.yaml order."""
    out: dict[str, SourceYield] = {}
    for src in sources:
        name = str(src.get("name") or "").strip()
        if name:
            out[name] = SourceYield(name, str(src.get("strategy") or "").strip().lower())
    window = set(run_ids)

    for row in conn.execute(
        "SELECT source_name, run_id, rows_found FROM source_health WHERE ok = 1"
    ):
        y = out.get(row["source_name"])
        if y is None:
            continue
        y.health_runs += 1
        if row["run_id"] in window:
            y.window_runs += 1
            y.seen += row["rows_found"]

    marks = ",".join("?" * len(run_ids)) or "NULL"
    keys_by_value: dict[str, dict[str, set[str]]] = {}
    for row in conn.execute(
        f"SELECT source_name, dedupe_key, title, location, layer, rule "
        f"FROM run_exclusions WHERE run_id IN ({marks})",
        run_ids,
    ):
        y = out.get(row["source_name"])
        layer = row["layer"]
        if y is None or layer.startswith(REFILTER_PREFIX):
            continue
        y.drop_log_rows += 1
        if layer == LAYER_RULES:
            location = row["location"] or ""
            y.l0_drops += 1
            y.drops_by_rule[row["rule"]] += 1
            y.values[location] += 1
            y.value_kinds[value_kind(location, row["title"] or "")] += 1
            keys_by_value.setdefault(y.name, {}).setdefault(location, set()).add(
                row["dedupe_key"] or row["title"] or ""
            )
        elif layer in _LATER_LAYERS:
            y.later_drops += 1
            if layer == LAYER_DETAIL and row["rule"].startswith(_DEFERRED_SETTLED_PREFIXES):
                y.deferred_settled += 1

    for name, by_value in keys_by_value.items():
        if len(by_value) == 1:
            out[name].constant_postings = len(next(iter(by_value.values())))

    for row in conn.execute(
        "SELECT source_name, status, COUNT(*) AS n, MAX(first_seen) AS last "
        "FROM jobs GROUP BY source_name, status"
    ):
        y = out.get(row["source_name"])
        if y is None:
            continue
        y.stored += row["n"]
        y.stored_by_status[row["status"]] += row["n"]
        y.last_stored = max(y.last_stored, (row["last"] or "")[:10])
    return list(out.values())


# --- Reconciliation ----------------------------------------------------------


@dataclass(frozen=True)
class RunCheck:
    """One run's funnel, rebuilt per source and summed, and what failed to add up."""

    run_id: int
    started_at: str
    seen: int
    l0_drops: int
    gaps: tuple[str, ...]
    # Only for the latest run, where `jobs.last_run_id` still says what it
    # sighted: passes against what the later layers and the store account for.
    accounted: str = ""

    @property
    def passes(self) -> int:
        return self.seen - self.l0_drops


def reconcile(conn: sqlite3.Connection, run_ids: list[int]) -> list[RunCheck]:
    """Check, run by run, that the per-source numbers add up to the run's own.

    The run summary's "L1 − N → M match your criteria" is `rows_found` summed
    over the sources that scraped cleanly, less the Layer 0 drops; neither total
    is stored, so this rebuilds it from the per-source rows the report uses.
    What can go wrong is a per-source split that does not add up: a drop logged
    under a source with no clean scrape that run, or more drops than rows. For
    the latest run it also accounts for every pass: dropped at Layers 1-3, held
    back unverified, or sighted in the store (which covers rows already stored,
    Layer 4's rejected ones and Layer 5's, all refreshed by the run). Postings
    listed with a blank title are removed before `rows_found` is counted
    (`_split_untitled`), so they appear on neither side and cannot cause a gap.
    """
    checks: list[RunCheck] = []
    latest = max(run_ids) if run_ids else None
    for run_id in run_ids:
        started = conn.execute(
            "SELECT started_at FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()["started_at"]
        found = {
            r["source_name"]: r["rows_found"]
            for r in conn.execute(
                "SELECT source_name, rows_found FROM source_health WHERE run_id = ? AND ok = 1",
                (run_id,),
            )
        }
        layer_counts: dict[str, Counter[str]] = {}
        unverified: Counter[str] = Counter()
        for r in conn.execute(
            "SELECT source_name, layer, rule FROM run_exclusions WHERE run_id = ?", (run_id,)
        ):
            if r["layer"].startswith(REFILTER_PREFIX):
                continue
            layer_counts.setdefault(r["source_name"], Counter())[r["layer"]] += 1
            if _UNVERIFIED_MARK in r["rule"]:
                unverified[r["source_name"]] += 1
        gaps: list[str] = []
        for source, counts in sorted(layer_counts.items()):
            if source not in found:
                gaps.append(f"{sum(counts.values())} drops under {source!r}, not scraped this run")
            elif counts[LAYER_RULES] > found[source]:
                gaps.append(
                    f"{source!r}: {counts[LAYER_RULES]} Layer 0 drops but {found[source]} rows"
                )
        seen = sum(found.values())
        l0 = sum(c[LAYER_RULES] for c in layer_counts.values())
        accounted = ""
        if run_id == latest:
            sighted = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE last_run_id = ?", (run_id,)
            ).fetchone()[0]
            title_drops = sum(
                c[LAYER_TITLE_KEYWORD] + c[LAYER_SENIORITY] for c in layer_counts.values()
            )
            held = sum(unverified.values())
            residual = (seen - l0) - title_drops - held - sighted
            accounted = (
                f"{seen - l0:,} passes = {title_drops:,} dropped on the title "
                f"+ {held:,} held back unverified + {sighted:,} sighted in the store"
            )
            if residual:
                accounted += (
                    f", leaving {residual:+,}: the same posting listed twice in one run, "
                    "or a posting with no dedupe key, is counted in rows but stored once"
                )
        checks.append(RunCheck(run_id, started[:10], seen, l0, tuple(gaps), accounted))
    return checks


# --- Rendering ---------------------------------------------------------------


def _status_text(y: SourceYield) -> str:
    if not y.stored:
        return "none ever stored"
    parts = ", ".join(f"{s} {n}" for s, n in sorted(y.stored_by_status.items()))
    return f"{y.stored} ever stored ({parts}), the last first seen {y.last_stored}"


def format_source(y: SourceYield, v: Verdict, total_log: int, places: int) -> list[str]:
    label = f"{v.klass}  {CLASS_NAMES[v.klass]}"
    if v.provisional:
        label += f" (evidence so far points at {v.provisional})"
    share = y.drop_log_rows / total_log if total_log else 0.0
    lines = [
        f"{y.name}  [{y.strategy or '?'}]  {label}",
        f"    runs: {y.health_runs} in source_health, {y.window_runs} in the drop-log window",
        f"    rows seen {y.seen:,}; passed Layer 0 {y.passes:,}; dropped at Layer 0 {y.l0_drops:,}",
    ]
    if y.later_drops:
        lines.append(
            f"    of the passes, {y.later_drops:,} dropped at a later layer"
            + (
                f" ({y.deferred_settled:,} by Layer 5 settling a deferred location or hybrid)"
                if y.deferred_settled
                else ""
            )
        )
    for rule, n in y.drops_by_rule.most_common():
        lines.append(f"    Layer 0 rule {rule!r}: {n:,}")
    lines.append(f"    stored: {_status_text(y)}")
    lines.append(f"    drop-log rows: {y.drop_log_rows:,} ({share:.1%} of the window)")
    for reason in v.reasons:
        lines.append(f"    why {v.klass}: {reason}")
    if y.values:
        shown = y.values.most_common(None if places == 0 else places)
        lines.append(f"    Layer 0 drop locations ({len(y.values)} distinct):")
        for value, n in shown:
            kind = value_kind(value, "")
            note = "" if kind == VALUE_PLACE else f"  <- {kind}"
            lines.append(f"      {n:>6,}  {value or '(empty)'}{note}")
        if len(shown) < len(y.values):
            lines.append(f"      … and {len(y.values) - len(shown)} more (--places 0 shows all)")
    return lines


_ORDER = (CLASS_ELSEWHERE, CLASS_STARVED, CLASS_TOO_NEW, CLASS_WORKING)


def format_report(
    yields: list[SourceYield],
    verdicts: dict[str, Verdict],
    runs: list[sqlite3.Row],
    checks: list[RunCheck],
    places: int,
    min_runs: int,
    unconfigured: list[str],
) -> str:
    total_log = sum(y.drop_log_rows for y in yields)
    first, last = runs[0], runs[-1]
    lines = [
        "Source yield",
        "─" * 52,
        f"Window: the drop log's {len(runs)} scrape runs, "
        f"{first['run_id']} ({first['started_at'][:10]}) to "
        f"{last['run_id']} ({last['started_at'][:10]})",
        "A pass is a row that reached Layer 1 or later, or was stored. A row Layer 0",
        "deferred (a conditional city, a placeholder, an empty field) is counted as a",
        "pass, even when Layer 5 then dropped it.",
        f"Too new: fewer than {min_runs} scrape runs in the window. Starved: a value "
        f"signature on {STARVED_SHARE:.0%}+ of drops.",
        "",
        "Reconciliation (compare with each run's 'L1 − N → M match your criteria'):",
    ]
    for c in checks:
        lines.append(
            f"  run {c.run_id} ({c.started_at}): seen {c.seen:,}, "
            f"L1 −{c.l0_drops:,} → {c.passes:,} match your criteria"
        )
        for gap in c.gaps:
            lines.append(f"    GAP: {gap}")
        if c.accounted:
            lines.append(f"    {c.accounted}")
    counts = Counter(v.klass for v in verdicts.values())
    lines += [
        "",
        "Classes: "
        + ", ".join(f"{k} {CLASS_NAMES[k]} {counts.get(k, 0)}" for k in sorted(CLASS_NAMES)),
    ]
    by_class: dict[str, list[SourceYield]] = {}
    for y in yields:
        by_class.setdefault(verdicts[y.name].klass, []).append(y)
    for klass in _ORDER:
        members = by_class.get(klass, [])
        if not members:
            continue
        log_share = sum(y.drop_log_rows for y in members) / total_log if total_log else 0.0
        lines += [
            "",
            f"── {klass}  {CLASS_NAMES[klass]}: {len(members)} source(s), "
            f"{log_share:.1%} of the window's drop-log rows",
        ]
        for y in members:
            lines.append("")
            lines += format_source(y, verdicts[y.name], total_log, places)
    if unconfigured:
        lines += [
            "",
            f"Not in sources.yaml, so left out: {', '.join(sorted(unconfigured))}",
        ]
    lines += [
        "",
        "Nothing was changed. Removing a source is a sources.yaml edit, and yours.",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Which configured sources never get a posting past the location filter? "
            "A read-only report over the store and sources.yaml (SP12)."
        ),
        epilog=(
            "Classes: A working; B truthfully elsewhere (every drop names a real place); "
            "C starved, not dead (the location field cannot carry the verdict); "
            "D too new to say. Writes nothing and never reads rules.json."
        ),
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help=f"SQLite job store, opened read-only (default: {default_jobs_db_path()})",
    )
    parser.add_argument(
        "--sources",
        type=Path,
        default=None,
        help=f"sources.yaml (default: {default_sources_path()})",
    )
    parser.add_argument(
        "--min-runs",
        type=int,
        default=DEFAULT_MIN_RUNS,
        metavar="K",
        help=(
            "Scrape runs in the window below which a source is too new "
            f"(default {DEFAULT_MIN_RUNS})"
        ),
    )
    parser.add_argument(
        "--places",
        type=int,
        default=DEFAULT_PLACES,
        metavar="N",
        help=f"Distinct drop locations to print per source, 0 for all (default {DEFAULT_PLACES})",
    )
    args = parser.parse_args(argv)

    sources = load_sources(args.sources or default_sources_path())
    with closing(open_readonly(args.db or default_jobs_db_path())) as conn:
        runs = window_runs(conn)
        if not runs:
            raise SystemExit("The drop log holds no scrape run yet; nothing to report.")
        run_ids = [r["run_id"] for r in runs]
        checks = reconcile(conn, run_ids)
        yields = gather(conn, sources, run_ids)
        configured = {y.name for y in yields}
        unconfigured = [
            r[0]
            for r in conn.execute("SELECT DISTINCT source_name FROM source_health")
            if r[0] not in configured
        ]
    verdicts = {y.name: classify(y, args.min_runs) for y in yields}
    print(format_report(yields, verdicts, runs, checks, args.places, args.min_runs, unconfigured))
    if any(c.gaps for c in checks):
        print("\nThe per-source counts do not reconcile: do not act on this report.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
