"""Score the filter ladder against a hand-labelled gold set, offline (WP8c).

WP8a made every exclusion visible. This module makes the ladder *measurable*:
it replays the filters over rows the owner has labelled by hand and reports how
much of what they wanted survived, so a rule change is measured rather than
guessed.

    python -m job_scraper.eval                      # baseline, current config
    python -m job_scraper.eval --beta 3             # weight recall harder
    python -m job_scraper.eval --compare A B        # two config dirs, diffed

The labelled set holds one row per job with a label of `review` (the owner
wants to see it) or `discard`. **`review` is the positive class**, so:

    true positive   kept,    labelled review    the ladder earned its keep
    false positive  kept,    labelled discard   noise the owner scrolls past
    false negative  dropped, labelled review    a job lost sight-unseen
    true negative   dropped, labelled discard   the ladder working

The two error types are not equal and the report does not pretend they are. A
false positive costs a line in a spreadsheet; a false negative costs a job the
owner never learns existed, which is CLAUDE.md's first priority. Hence F-beta
with beta > 1 (default 2), and hence the full false-negative listing: every one
is named, with the rule that killed it, because a count alone cannot be argued
with.

**No network, ever.** The replay reads titles and metadata that are already in
the labels file. Nothing here opens a detail page, and the two layers that
cannot be decided from metadata are reported as unevaluated rather than
guessed at — see `UNREPLAYABLE_LAYERS`.
"""

from __future__ import annotations

import argparse
import csv
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from job_scraper.config_loader import (
    default_labels_path,
    default_title_keywords_path,
    load_rules,
    package_dir,
)
from job_scraper.drops import (
    LAYER_DETAIL,
    LAYER_REVIEW_STATUS,
    LAYER_RULES,
    LAYER_SENIORITY,
    LAYER_TITLE_KEYWORD,
)
from job_scraper.experience_filter import apply_combined_title_filter
from job_scraper.filtering import (
    _HYBRID_PENDING_REASON,
    _UNRESOLVED_PENDING_REASON,
    DROP_RULE_KEY,
    build_hybrid_pattern,
    build_non_place_pattern,
    load_title_exclude_keywords,
    matches_rules,
)

logger = logging.getLogger(__name__)

# Recall matters more than precision here: an unwanted job costs a glance, a
# missed one costs the job. Overridable with --beta.
DEFAULT_BETA = 2.0

# The label vocabulary. Compared casefolded, so "Review" and "review" are one
# label — the file is hand-edited in a spreadsheet and case drifts.
LABEL_POSITIVE = "review"
LABEL_NEGATIVE = "discard"

# The replayable ladder, in the order `pipeline.run_pipeline` runs it. Layer
# names come from `drops.py` so the report, the drop log and the pipeline can
# never disagree about what a layer is called.
#
# This order is duplicated from the pipeline rather than extracted from it:
# extracting the ladder is WP8's business, not this package's, and doing it
# here would mean changing filtering behaviour in the session that is meant to
# measure it. `tests/test_eval.py` pins the order so the duplication cannot
# drift silently.
LADDER: tuple[str, ...] = (
    LAYER_RULES,
    LAYER_TITLE_KEYWORD,
    LAYER_SENIORITY,
)

# Layers the harness deliberately does not replay, and why. Both are reported
# as unevaluated: a job this harness keeps may still be dropped by one of them
# in a real run, so the numbers below are an upper bound on the ladder's recall
# rather than the whole truth, and saying so is cheaper than a silent
# overstatement.
UNREPLAYABLE_LAYERS: tuple[tuple[str, str], ...] = (
    (
        LAYER_REVIEW_STATUS,
        "review history, not a rule: it depends on what the owner already rejected",
    ),
    (
        LAYER_DETAIL,
        "needs the detail page, and this harness makes no HTTP request",
    ),
)

# Fields the replay cannot see, because the labels file has titles and metadata
# only. Stated in the report rather than buried here: with match_in set to
# title_and_description, a real run matches include/exclude keywords against
# the listing snippet too, so keyword rules that rely on the snippet look
# weaker in the replay than they are.
MISSING_FIELDS: tuple[str, ...] = ("raw_snippet", "department")


# ---------------------------------------------------------------------------
# The labelled set
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LabelledJob:
    """One hand-labelled row: enough job metadata to re-run the ladder over it."""

    dedupe_key: str
    title: str
    company: str
    source_name: str
    location: str
    wanted: bool

    def as_record(self) -> dict[str, Any]:
        """The job dict the filters expect.

        Deliberately not carrying the labels file's own `layer`/`rule` columns:
        those record what some earlier run's ladder did, and the whole point is
        to recompute that with the configuration under test.
        """
        return {
            "dedupe_key": self.dedupe_key,
            "title": self.title,
            "company": self.company,
            "source_name": self.source_name,
            "location": self.location,
        }


@dataclass
class LabelIssues:
    """What `load_labels` had to throw away, so the report can admit it."""

    unreadable: list[tuple[int, str]] = field(default_factory=list)
    duplicates_collapsed: int = 0
    conflicting_keys: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.unreadable or self.duplicates_collapsed or self.conflicting_keys)


def _sniff_delimiter(sample: str) -> str:
    """The delimiter of a CSV header line.

    The gold set is exported from a spreadsheet, which on a Swedish or German
    locale writes semicolons. Guessing wrongly would silently produce one
    column and a file of unreadable rows, so this is checked rather than
    assumed.
    """
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        return ","


def load_labels(path: Path) -> tuple[list[LabelledJob], LabelIssues]:
    """Read the gold set. Returns (jobs, issues); the jobs are unique by key.

    Tolerant about shape because the file is hand-maintained: the delimiter is
    sniffed, header names are matched casefolded, `source_name` may be absent,
    and unknown extra columns are ignored. Intolerant about labels: a row
    whose label is neither `review` nor `discard` is not guessed at, it is
    reported.

    A key labelled both ways is dropped rather than resolved. Picking a winner
    would silently invent a ground truth the owner did not state, and the
    number of such rows is small enough to fix by hand once named.
    """
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found. The gold set is a CSV with columns "
            "dedupe_key, title, company, location, label (label: review or discard)."
        )

    text = path.read_text(encoding="utf-8-sig")
    delimiter = _sniff_delimiter(text.split("\n", 1)[0])
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    fieldnames = [str(name or "").strip().casefold() for name in (reader.fieldnames or [])]
    for required in ("dedupe_key", "title", "label"):
        if required not in fieldnames:
            raise ValueError(
                f"{path} has no {required!r} column (found: {', '.join(fieldnames) or 'nothing'})"
            )

    issues = LabelIssues()
    by_key: dict[str, LabelledJob] = {}
    conflicting: set[str] = set()

    def cell(row: dict[str, Any], name: str) -> str:
        for key, value in row.items():
            if str(key or "").strip().casefold() == name:
                return str(value or "").strip()
        return ""

    # Row 1 is the header, so the first data row is line 2 — the number a
    # spreadsheet shows, which is the point of reporting it at all.
    for line_no, row in enumerate(reader, start=2):
        key = cell(row, "dedupe_key")
        title = cell(row, "title")
        label = cell(row, "label").casefold()
        if not key or not title:
            issues.unreadable.append((line_no, "missing dedupe_key or title"))
            continue
        if label == LABEL_POSITIVE:
            wanted = True
        elif label == LABEL_NEGATIVE:
            wanted = False
        else:
            issues.unreadable.append((line_no, f"unrecognised label {label or '(blank)'!r}"))
            continue

        job = LabelledJob(
            dedupe_key=key,
            title=title,
            company=cell(row, "company"),
            source_name=cell(row, "source_name"),
            location=cell(row, "location"),
            wanted=wanted,
        )
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = job
        elif existing.wanted == job.wanted:
            issues.duplicates_collapsed += 1
        else:
            conflicting.add(key)

    for key in conflicting:
        by_key.pop(key, None)
    issues.conflicting_keys = sorted(conflicting)
    return list(by_key.values()), issues


# ---------------------------------------------------------------------------
# The configuration under test
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LadderConfig:
    """Everything the replayable layers need, compiled once (never per job)."""

    name: str
    rules: dict[str, Any]
    title_keywords: list[tuple[str, str]]
    hybrid_pattern: re.Pattern[str] | None
    non_place_pattern: re.Pattern[str] | None


def load_ladder_config(config_dir: Path, name: str | None = None) -> LadderConfig:
    """Load `rules.json` and `title_exclude_keywords.csv` from *config_dir*.

    A config directory is the unit of comparison: copy the live one, edit the
    copy, and point `--compare` at both. `rules.json` is required — a missing
    one means the wrong directory was named, and defaulting to the live rules
    would quietly compare a config against itself. A missing keyword CSV is a
    legitimate configuration (no keyword layer) but is logged, since deleting
    that file is also a plausible mistake.
    """
    rules = load_rules(config_dir / "rules.json")
    keywords_path = config_dir / default_title_keywords_path().name
    if keywords_path.is_file():
        title_keywords = load_title_exclude_keywords(keywords_path)
    else:
        title_keywords = []
        logger.warning(
            "No %s in %s: the title keyword layer will not exclude anything",
            keywords_path.name,
            config_dir,
        )
    return LadderConfig(
        name=name or config_dir.name,
        rules=rules,
        title_keywords=title_keywords,
        hybrid_pattern=build_hybrid_pattern(rules),
        non_place_pattern=build_non_place_pattern(rules),
    )


# ---------------------------------------------------------------------------
# The replay
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    """What the ladder did with one labelled job."""

    job: LabelledJob
    layer: str | None
    rule: str | None
    pending_hybrid: bool = False
    pending_location: bool = False

    @property
    def kept(self) -> bool:
        return self.layer is None

    @property
    def wanted(self) -> bool:
        return self.job.wanted

    @property
    def is_false_negative(self) -> bool:
        return self.wanted and not self.kept


def replay(jobs: list[LabelledJob], config: LadderConfig) -> list[Verdict]:
    """Run the replayable ladder over *jobs* and record where each one fell.

    Calls the pipeline's own filter functions, not copies of them: a harness
    that reimplements the rules measures the harness. The layers run in
    `LADDER` order, which is `run_pipeline`'s order.
    """
    by_key = {job.dedupe_key: job for job in jobs}
    verdicts: list[Verdict] = []

    def dropped(records: list[dict[str, Any]], layer: str) -> None:
        for record in records:
            key = str(record.get("dedupe_key") or "")
            rule = str(record.get(DROP_RULE_KEY) or "") or "unattributed"
            verdicts.append(Verdict(job=by_key[key], layer=layer, rule=rule))

    # Layer 0 — rules (location, include/exclude keywords).
    kept: list[dict[str, Any]] = []
    for job in jobs:
        record = job.as_record()
        ok, reasons = matches_rules(
            record,
            config.rules,
            config.hybrid_pattern,
            non_place_pattern=config.non_place_pattern,
        )
        if ok:
            kept.append(dict(record, matched_reasons=reasons))
        else:
            verdicts.append(Verdict(job=job, layer=LAYER_RULES, rule=reasons[0]))

    # Layers 1a and 1 — one title scan, keyword first, then seniority.
    kept, keyword_excluded, seniority_excluded = apply_combined_title_filter(
        kept, config.title_keywords, config.rules
    )
    dropped(keyword_excluded, LAYER_TITLE_KEYWORD)
    dropped(seniority_excluded, LAYER_SENIORITY)

    for record in kept:
        reasons = record.get("matched_reasons") or []
        verdicts.append(
            Verdict(
                job=by_key[str(record.get("dedupe_key") or "")],
                layer=None,
                rule=None,
                # Layer 0 admitted this one from a hybrid-gated city; in a real
                # run Layer 2 settles it against the description, and can still
                # drop it. Counted as kept, flagged as provisional.
                pending_hybrid=_HYBRID_PENDING_REASON in reasons,
                # Same again for WP8d's unresolvable location field, and the
                # flag matters more here: Layer 2 fails closed, so a recall
                # gain reported over these jobs is an upper bound, not a
                # result. Without the flag the harness would credit the ladder
                # with every job it merely deferred.
                pending_location=_UNRESOLVED_PENDING_REASON in reasons,
            )
        )

    # Stable, and in the order the gold set lists them: a diff of two runs
    # should show rule changes, not reordering.
    order = {key: i for i, key in enumerate(by_key)}
    verdicts.sort(key=lambda v: order[v.job.dedupe_key])
    return verdicts


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Confusion:
    """The 2x2, with `review` as the positive class and "kept" as predicted-positive."""

    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int

    @property
    def total(self) -> int:
        return (
            self.true_positives
            + self.false_positives
            + self.false_negatives
            + self.true_negatives
        )

    @property
    def precision(self) -> float:
        """Of what the ladder kept, how much the owner wanted. NaN if it kept nothing."""
        denominator = self.true_positives + self.false_positives
        return self.true_positives / denominator if denominator else math.nan

    @property
    def recall(self) -> float:
        """Of what the owner wanted, how much survived. NaN if nothing was wanted."""
        denominator = self.true_positives + self.false_negatives
        return self.true_positives / denominator if denominator else math.nan

    def fbeta(self, beta: float = DEFAULT_BETA) -> float:
        """Weighted harmonic mean; beta > 1 weights recall, which is the point."""
        precision, recall = self.precision, self.recall
        if math.isnan(precision) or math.isnan(recall):
            return math.nan
        b2 = beta * beta
        denominator = b2 * precision + recall
        return (1 + b2) * precision * recall / denominator if denominator else 0.0


def confusion_of(verdicts: list[Verdict]) -> Confusion:
    return Confusion(
        true_positives=sum(1 for v in verdicts if v.wanted and v.kept),
        false_positives=sum(1 for v in verdicts if not v.wanted and v.kept),
        false_negatives=sum(1 for v in verdicts if v.wanted and not v.kept),
        true_negatives=sum(1 for v in verdicts if not v.wanted and not v.kept),
    )


@dataclass(frozen=True)
class LayerReport:
    """One layer's own damage, and where the ladder stood after it ran."""

    layer: str
    reached: int
    dropped: int
    dropped_wanted: int
    dropped_unwanted: int
    cumulative: Confusion
    rules: list[tuple[str, int, int]]

    @property
    def drop_precision(self) -> float:
        """Of this layer's drops, how many were genuinely unwanted."""
        return self.dropped_unwanted / self.dropped if self.dropped else math.nan


def layer_reports(verdicts: list[Verdict]) -> list[LayerReport]:
    """Per-layer figures, in ladder order.

    `cumulative` is the confusion matrix as it stood after that layer ran —
    the same metric the overall report shows, evaluated part-way down the
    ladder, so the layer that costs the recall is visible rather than inferred.
    """
    position = {layer: index for index, layer in enumerate(LADDER)}
    # How far each job got: its layer's position, or past the end if it survived.
    graded = [
        (verdict, len(LADDER) if verdict.kept else position[str(verdict.layer)])
        for verdict in verdicts
    ]

    reports: list[LayerReport] = []
    for index, layer in enumerate(LADDER):
        here = [v for v, depth in graded if depth == index]
        rule_counts: dict[str, tuple[int, int]] = {}
        for verdict in here:
            rule = str(verdict.rule)
            wanted, total = rule_counts.get(rule, (0, 0))
            rule_counts[rule] = (wanted + int(verdict.wanted), total + 1)
        reports.append(
            LayerReport(
                layer=layer,
                reached=sum(1 for _, depth in graded if depth >= index),
                dropped=len(here),
                dropped_wanted=sum(1 for v in here if v.wanted),
                dropped_unwanted=sum(1 for v in here if not v.wanted),
                # Predicted-positive after this layer = everything still standing.
                cumulative=Confusion(
                    true_positives=sum(1 for v, d in graded if v.wanted and d > index),
                    false_positives=sum(1 for v, d in graded if not v.wanted and d > index),
                    false_negatives=sum(1 for v, d in graded if v.wanted and d <= index),
                    true_negatives=sum(1 for v, d in graded if not v.wanted and d <= index),
                ),
                rules=sorted(
                    ((rule, w, t) for rule, (w, t) in rule_counts.items()),
                    key=lambda item: (-item[1], -item[2], item[0]),
                ),
            )
        )
    return reports


@dataclass(frozen=True)
class EvalResult:
    """Everything one configuration scored, ready to print or diff."""

    config_name: str
    beta: float
    verdicts: list[Verdict]
    confusion: Confusion
    layers: list[LayerReport]

    @property
    def false_negatives(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.is_false_negative]

    @property
    def pending_hybrid(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.kept and v.pending_hybrid]

    @property
    def pending_location(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.kept and v.pending_location]

    @property
    def pending_location_wanted(self) -> list[Verdict]:
        """The provisional jobs that were labelled `review`.

        The number worth quoting when this package is measured: the most recall
        WP8d can hand back, if Layer 2 confirms every one of them.
        """
        return [v for v in self.pending_location if v.wanted]


def evaluate(
    jobs: list[LabelledJob], config: LadderConfig, beta: float = DEFAULT_BETA
) -> EvalResult:
    verdicts = replay(jobs, config)
    return EvalResult(
        config_name=config.name,
        beta=beta,
        verdicts=verdicts,
        confusion=confusion_of(verdicts),
        layers=layer_reports(verdicts),
    )


# ---------------------------------------------------------------------------
# Comparing two configurations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    """One job the two configurations disagree about."""

    job: LabelledJob
    before: Verdict
    after: Verdict

    @property
    def newly_kept(self) -> bool:
        return self.after.kept and not self.before.kept


@dataclass(frozen=True)
class Comparison:
    before: EvalResult
    after: EvalResult
    changes: list[Change]

    @property
    def newly_kept(self) -> list[Change]:
        return [c for c in self.changes if c.newly_kept]

    @property
    def newly_dropped(self) -> list[Change]:
        return [c for c in self.changes if not c.newly_kept]


def compare(before: EvalResult, after: EvalResult) -> Comparison:
    """Which jobs the two configurations disagree about, and how the metrics moved."""
    after_by_key = {v.job.dedupe_key: v for v in after.verdicts}
    changes = [
        Change(job=b.job, before=b, after=a)
        for b in before.verdicts
        if (a := after_by_key.get(b.job.dedupe_key)) is not None and a.kept != b.kept
    ]
    return Comparison(before=before, after=after, changes=changes)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_RULE = "─" * 78


def _num(value: float) -> str:
    """A metric, or 'n/a' when its denominator was empty — never a misleading 0.000."""
    return "n/a" if math.isnan(value) else f"{value:.3f}"


def _delta(before: float, after: float) -> str:
    if math.isnan(before) or math.isnan(after):
        return "  n/a"
    return f"{after - before:+.3f}"


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    """'1 row' / '2 rows' — the report is read by a person, not parsed."""
    return f"{count:,} {singular if count == 1 else (plural or singular + 's')}"


def format_label_summary(jobs: list[LabelledJob], issues: LabelIssues, path: Path) -> str:
    wanted = sum(1 for j in jobs if j.wanted)
    lines = [
        f"Gold set  {path}",
        f"          {len(jobs):,} labelled jobs — {wanted:,} review, "
        f"{len(jobs) - wanted:,} discard",
    ]
    if issues.duplicates_collapsed:
        lines.append(
            f"          {_plural(issues.duplicates_collapsed, 'duplicate row')} collapsed "
            "(same key, same label)"
        )
    if issues.conflicting_keys:
        lines.append(
            f"          {_plural(len(issues.conflicting_keys), 'key')} labelled both ways, "
            "excluded — fix by hand:"
        )
        lines += [f"            {key}" for key in issues.conflicting_keys]
    if issues.unreadable:
        lines.append(f"          {_plural(len(issues.unreadable), 'row')} skipped:")
        lines += [f"            line {n}: {why}" for n, why in issues.unreadable[:10]]
        if len(issues.unreadable) > 10:
            lines.append(f"            … and {len(issues.unreadable) - 10:,} more")
    return "\n".join(lines)


def format_confusion(matrix: Confusion, beta: float) -> str:
    lines = [
        "                    kept        dropped",
        f"  review     {matrix.true_positives:>9,}  {matrix.false_negatives:>13,}"
        "   ← false negatives are the expensive column",
        f"  discard    {matrix.false_positives:>9,}  {matrix.true_negatives:>13,}",
        "",
        f"  precision  {_num(matrix.precision)}     of what it kept, this much was wanted",
        f"  recall     {_num(matrix.recall)}     of what was wanted, this much survived",
        f"  F{beta:g}         {_num(matrix.fbeta(beta))}     recall weighted {beta:g}x precision",
    ]
    return "\n".join(lines)


def format_layers(result: EvalResult) -> str:
    lines = [
        f"{'layer':<18}  {'reached':>7}  {'dropped':>7}  {'lost':>5}  {'drop prec':>9}"
        f"  {'recall':>6}  {'F' + format(result.beta, 'g'):>6}",
        f"{'-' * 18}  {'-' * 7}  {'-' * 7}  {'-' * 5}  {'-' * 9}  {'-' * 6}  {'-' * 6}",
    ]
    for report in result.layers:
        lines.append(
            f"{report.layer:<18}  {report.reached:>7,}  {report.dropped:>7,}  "
            f"{report.dropped_wanted:>5,}  {_num(report.drop_precision):>9}  "
            f"{_num(report.cumulative.recall):>6}  {_num(report.cumulative.fbeta(result.beta)):>6}"
        )
    lines += [
        "",
        "  'lost' is jobs labelled review that this layer dropped; 'drop prec' is the "
        "share of",
        "  its drops that were genuinely unwanted. recall and F are cumulative — the "
        "ladder's",
        "  state after that layer ran.",
    ]
    for layer, why in UNREPLAYABLE_LAYERS:
        lines.append(f"  {layer} not replayed: {why}.")
    return "\n".join(lines)


def format_costly_rules(result: EvalResult) -> str:
    """The rules that cost wanted jobs, worst first — the loosen-this-one list.

    The false-negative listing answers "what did I lose?"; this answers "which
    single rule would bring back the most of it?", which is the question a rule
    change is actually made against. Rules that dropped nothing wanted are left
    out: they are working.
    """
    costly = [
        (report.layer, rule, lost, total)
        for report in result.layers
        for rule, lost, total in report.rules
        if lost
    ]
    if not costly:
        return "No rule dropped a job labelled 'review'."
    costly.sort(key=lambda row: (-row[2], -row[3], row[1]))
    lines = [
        "Rules that cost wanted jobs",
        f"{'lost':>5}  {'of drops':>8}  {'layer':<18}  rule",
        f"{'-' * 5}  {'-' * 8}  {'-' * 18}  {'-' * 40}",
    ]
    lines += [
        f"{lost:>5,}  {total:>8,}  {layer:<18}  {rule}" for layer, rule, lost, total in costly
    ]
    return "\n".join(lines)


def format_false_negatives(result: EvalResult) -> str:
    """Every lost job, named, with the rule that killed it.

    Listed in full rather than summarised or truncated: the count is the
    argument for changing a rule, but the titles are what decide which rule.
    """
    losses = result.false_negatives
    if not losses:
        return "False negatives: none. Every job labelled 'review' survived the ladder."
    by_layer: dict[str, list[Verdict]] = {}
    for verdict in losses:
        by_layer.setdefault(str(verdict.layer), []).append(verdict)

    lines = [f"False negatives: {len(losses):,} jobs labelled 'review' that the ladder dropped"]
    for layer in LADDER:
        group = by_layer.get(layer)
        if not group:
            continue
        lines += ["", f"  {layer}  ({len(group)})"]
        for index, verdict in enumerate(group, start=1):
            job = verdict.job
            where = " · ".join(x for x in (job.company, job.location) if x)
            lines.append(f"    {index:>3}. {job.title}")
            lines.append(f"         {verdict.rule}")
            if where:
                lines.append(f"         {where}")
    return "\n".join(lines)


def format_report(
    result: EvalResult, jobs: list[LabelledJob], issues: LabelIssues, labels_path: Path
) -> str:
    lines = [
        f"Filter ladder evaluation — config {result.config_name!r}",
        _RULE,
        format_label_summary(jobs, issues, labels_path),
        "",
        "Overall",
        format_confusion(result.confusion, result.beta),
        "",
        "Per layer",
        format_layers(result),
        "",
        format_costly_rules(result),
        "",
        format_false_negatives(result),
        "",
        "What this replay cannot see",
        f"  The gold set has no {', '.join(MISSING_FIELDS)}, so keyword rules that match "
        "against the",
        "  listing snippet see less text here than in a real run.",
    ]
    pending = result.pending_hybrid
    if pending:
        lines.append(
            f"  {_plural(len(pending), 'kept job')} in a hybrid-gated conditional city, which "
            "Layer 2 would still settle."
        )
    unresolved = result.pending_location
    if unresolved:
        wanted = len(result.pending_location_wanted)
        lines.append(
            f"  {_plural(len(unresolved), 'kept job')} with an unresolvable location field, "
            "which Layer 2 would"
        )
        lines.append(
            "  still settle against the description — and it fails closed, so read these as "
            "deferred,"
        )
        lines.append(
            f"  not kept. {_plural(wanted, 'of them was', 'of them were')} labelled review: "
            "that is the ceiling on the recall this buys, not the gain."
        )
    return "\n".join(lines)


def _verdict_text(verdict: Verdict) -> str:
    return "kept" if verdict.kept else f"{verdict.layer} — {verdict.rule}"


def format_comparison(comparison: Comparison) -> str:
    """The metrics side by side, then every job the two configurations disagree about.

    Each change is printed over three lines rather than squeezed into columns:
    the rule that fired is the whole point of the diff, and a truncated rule
    ("1a-title-keyword — ti…") answers nothing.
    """
    before, after = comparison.before, comparison.after
    beta = before.beta
    width = max(len(before.config_name), len(after.config_name), 12)
    lines = [
        f"Configuration diff — {before.config_name!r} → {after.config_name!r}",
        _RULE,
        f"{'':<16}  {before.config_name:>{width}}  {after.config_name:>{width}}  {'delta':>8}",
        f"{'-' * 16}  {'-' * width}  {'-' * width}  {'-' * 8}",
    ]

    def row(label: str, b: float, a: float) -> str:
        return f"{label:<16}  {_num(b):>{width}}  {_num(a):>{width}}  {_delta(b, a):>8}"

    def count_row(label: str, b: int, a: int) -> str:
        return f"{label:<16}  {b:>{width},}  {a:>{width},}  {a - b:>+8,}"

    lines += [
        row("precision", before.confusion.precision, after.confusion.precision),
        row("recall", before.confusion.recall, after.confusion.recall),
        row(f"F{beta:g}", before.confusion.fbeta(beta), after.confusion.fbeta(beta)),
        count_row(
            "kept",
            before.confusion.true_positives + before.confusion.false_positives,
            after.confusion.true_positives + after.confusion.false_positives,
        ),
        count_row(
            "false negatives", before.confusion.false_negatives, after.confusion.false_negatives
        ),
        count_row(
            "false positives", before.confusion.false_positives, after.confusion.false_positives
        ),
        "",
    ]

    if not comparison.changes:
        lines.append("No job is treated differently by the two configurations.")
        return "\n".join(lines)

    label_width = max(len(before.config_name), len(after.config_name)) + 1
    for heading, changes in (
        (f"Newly kept by {after.config_name!r}", comparison.newly_kept),
        (f"Newly dropped by {after.config_name!r}", comparison.newly_dropped),
    ):
        lines.append(f"{heading}: {len(changes):,}")
        # Wanted jobs first: a change that moves a labelled 'review' row is the
        # one worth reading, whichever direction it moved in.
        for change in sorted(changes, key=lambda c: (not c.job.wanted, c.job.title)):
            marker = LABEL_POSITIVE if change.job.wanted else LABEL_NEGATIVE
            lines.append(f"  [{marker}] {change.job.title}")
            lines.append(
                f"      {before.config_name + ':':<{label_width}} {_verdict_text(change.before)}"
            )
            lines.append(
                f"      {after.config_name + ':':<{label_width}} {_verdict_text(change.after)}"
            )
        lines.append("")

    gained = sum(1 for c in comparison.newly_kept if c.job.wanted)
    lost = sum(1 for c in comparison.newly_dropped if c.job.wanted)
    lines.append(f"Net effect on wanted jobs: {gained:,} recovered, {lost:,} newly lost.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    default_config = package_dir() / "config"
    parser = argparse.ArgumentParser(
        description=(
            "Replay the filter ladder over a hand-labelled gold set and score it. "
            "Offline: no HTTP request is made."
        ),
        epilog=(
            "'review' is the positive class, so a false negative is a job you wanted "
            "that a rule dropped. Every one is listed by title with the rule that "
            "killed it. F-beta defaults to beta=2, weighting recall over precision."
        ),
    )
    parser.add_argument(
        "--labels",
        type=Path,
        default=default_labels_path(),
        help=f"Labelled gold set CSV (default: {default_labels_path()})",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=default_config,
        dest="config_dir",
        help=f"Directory holding rules.json and the keyword CSV (default: {default_config})",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        type=Path,
        metavar=("BEFORE", "AFTER"),
        help="Score two config directories and diff what each keeps and drops",
    )
    parser.add_argument(
        "--beta",
        type=float,
        default=DEFAULT_BETA,
        help=f"F-beta weight; > 1 favours recall (default: {DEFAULT_BETA:g})",
    )
    args = parser.parse_args(argv)

    if args.beta <= 0:
        raise SystemExit("--beta must be positive; > 1 is what favours recall.")

    jobs, issues = load_labels(args.labels)
    if not jobs:
        raise SystemExit(f"{args.labels} holds no usable labelled rows.")

    if args.compare:
        before_dir, after_dir = args.compare
        # Distinct names even when both directories are called "config".
        before = evaluate(jobs, load_ladder_config(before_dir, str(before_dir.name)), args.beta)
        after = evaluate(jobs, load_ladder_config(after_dir, str(after_dir.name)), args.beta)
        if before.config_name == after.config_name:
            before = EvalResult(**{**vars(before), "config_name": f"{before.config_name} (A)"})
            after = EvalResult(**{**vars(after), "config_name": f"{after.config_name} (B)"})
        print(format_label_summary(jobs, issues, args.labels))
        print()
        print(format_comparison(compare(before, after)))
        return

    result = evaluate(jobs, load_ladder_config(args.config_dir), args.beta)
    print(format_report(result, jobs, issues, args.labels))


if __name__ == "__main__":
    main()
