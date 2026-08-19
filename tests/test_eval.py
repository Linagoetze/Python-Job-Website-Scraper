"""The offline evaluation harness (WP8c).

Three things are pinned here.

**The arithmetic**, because a metric that is quietly wrong is worse than no
metric: it is an argument for a rule change that nobody can check.

**The behaviour of the ladder itself.** `test_regression_pins_current_ladder`
scores a fixture config against a fixture gold set and asserts every number and
every false negative by hand. A refactor that changes which jobs the filters
drop — the risk WP8 is about to take — fails here rather than being noticed
months later in a spreadsheet. The fixtures are deliberately *not* the owner's
real `rules.json` and `labels.csv`: those are gitignored personal data and they
change whenever the owner changes their mind, which would make this test a
tripwire for the wrong thing.

**That it is offline.** One test forbids the process a socket for the duration
of a full evaluation. "Makes no HTTP request" is the harness's central claim,
and inspection is not evidence.
"""

from __future__ import annotations

import csv
import json
import math
import shutil
import socket
from pathlib import Path

import pytest

from job_scraper.drops import (
    LAYER_LANGUAGE,
    LAYER_NON_ENGLISH,
    LAYER_RULES,
    LAYER_SENIORITY,
    LAYER_TITLE_KEYWORD,
)
from job_scraper.eval import (
    DEFAULT_BETA,
    LADDER,
    Confusion,
    LabelledJob,
    compare,
    confusion_of,
    evaluate,
    format_comparison,
    format_report,
    layer_reports,
    load_labels,
    load_ladder_config,
    main,
    replay,
)
from job_scraper.filtering import RULE_LOC_EMPTY, RULE_LOC_UNLISTED_CITY

FIXTURES = Path(__file__).parent / "fixtures"
LABELS = FIXTURES / "eval_labels.csv"
CONFIG = FIXTURES / "eval_config"


@pytest.fixture
def gold() -> list[LabelledJob]:
    jobs, _ = load_labels(LABELS)
    return jobs


@pytest.fixture
def config():
    return load_ladder_config(CONFIG)


# ---------------------------------------------------------------------------
# Reading the gold set
# ---------------------------------------------------------------------------


def test_semicolon_delimiter_is_sniffed(gold: list[LabelledJob]) -> None:
    # The fixture is semicolon-separated, as a spreadsheet on a Swedish locale
    # writes it. Guessing comma would give one column and no usable rows.
    assert len(gold) == 14
    assert {job.title for job in gold} >= {"Junior Data Analyst", "Sales Manager"}


def test_comma_delimiter_is_sniffed(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"
    path.write_text(
        "dedupe_key,title,company,location,label\nk1,Junior Analyst,Acme,Lund,review\n",
        encoding="utf-8",
    )
    jobs, issues = load_labels(path)
    assert [job.title for job in jobs] == ["Junior Analyst"]
    assert not issues


def test_labels_are_case_insensitive_and_deduplicated(gold: list[LabelledJob]) -> None:
    jobs, issues = load_labels(LABELS)
    # The fixture repeats key /1 with the label spelled "Review".
    assert issues.duplicates_collapsed == 1
    assert len({job.dedupe_key for job in jobs}) == len(jobs)


def test_a_key_labelled_both_ways_is_excluded_not_guessed(gold: list[LabelledJob]) -> None:
    _, issues = load_labels(LABELS)
    assert issues.conflicting_keys == ["https://example.test/14"]
    assert all(job.dedupe_key != "https://example.test/14" for job in gold)


def test_an_unrecognised_label_is_reported_with_its_line_number() -> None:
    _, issues = load_labels(LABELS)
    assert issues.unreadable == [(19, "unrecognised label '(blank)'")]


def test_source_name_is_optional(gold: list[LabelledJob]) -> None:
    # The real gold set has no source_name column; the harness must not need one.
    assert all(job.source_name == "" for job in gold)


def test_a_missing_required_column_fails_loudly(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"
    path.write_text("dedupe_key,title\nk1,Junior Analyst\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no 'label' column"):
        load_labels(path)


def test_a_missing_labels_file_says_what_the_file_should_contain(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="review or discard"):
        load_labels(tmp_path / "nope.csv")


# ---------------------------------------------------------------------------
# The metrics
# ---------------------------------------------------------------------------


def test_confusion_counts_review_as_the_positive_class() -> None:
    matrix = Confusion(true_positives=3, false_positives=1, false_negatives=2, true_negatives=4)
    assert matrix.total == 10
    assert matrix.precision == pytest.approx(0.75)
    assert matrix.recall == pytest.approx(0.6)


def test_fbeta_at_one_is_the_harmonic_mean() -> None:
    matrix = Confusion(true_positives=3, false_positives=1, false_negatives=2, true_negatives=4)
    assert matrix.fbeta(1.0) == pytest.approx(2 * 0.75 * 0.6 / (0.75 + 0.6))


def test_beta_above_one_favours_recall() -> None:
    """The whole reason the default is 2: losing a wanted job must hurt more."""
    # Mirror images: 0.5 precision / 0.9 recall against 0.9 precision / 0.5 recall.
    high_recall = Confusion(
        true_positives=9, false_positives=9, false_negatives=1, true_negatives=1
    )
    high_precision = Confusion(
        true_positives=9, false_positives=1, false_negatives=9, true_negatives=1
    )
    assert high_recall.fbeta(2.0) > high_precision.fbeta(2.0)
    # Reversing the weighting reverses the verdict, which is what makes beta
    # a real knob rather than decoration.
    assert high_recall.fbeta(0.5) < high_precision.fbeta(0.5)


def test_metrics_are_nan_not_zero_when_undefined() -> None:
    """A ladder that kept nothing has no precision; 0.000 would be a lie."""
    empty = Confusion(true_positives=0, false_positives=0, false_negatives=5, true_negatives=5)
    assert math.isnan(empty.precision)
    assert math.isnan(empty.fbeta(DEFAULT_BETA))
    assert empty.recall == 0.0


# ---------------------------------------------------------------------------
# The replay
# ---------------------------------------------------------------------------


def test_ladder_order_matches_the_pipeline() -> None:
    """Pins the order `eval.replay` runs the layers in.

    `run_pipeline` runs rules, then the combined title scan (keyword before
    seniority), then the non-English filter, then the language filter. The
    harness duplicates that order; if the pipeline's order changes, this is
    the test that must be updated deliberately rather than the harness
    silently measuring a ladder nobody runs.
    """
    assert LADDER == (
        LAYER_RULES,
        LAYER_TITLE_KEYWORD,
        LAYER_SENIORITY,
        LAYER_NON_ENGLISH,
        LAYER_LANGUAGE,
    )


def test_every_labelled_job_gets_exactly_one_verdict(gold, config) -> None:
    verdicts = replay(gold, config)
    assert len(verdicts) == len(gold)
    assert {v.job.dedupe_key for v in verdicts} == {job.dedupe_key for job in gold}


def test_the_rule_recorded_is_the_filter_s_own_rule_string(gold, config) -> None:
    """The harness must report what the drop log would, not a paraphrase."""
    by_title = {v.job.title: v for v in replay(gold, config)}
    assert by_title["Programme Officer"].rule == RULE_LOC_UNLISTED_CITY
    assert by_title["Office Coordinator"].rule == RULE_LOC_EMPTY
    assert by_title["Graphic Designer"].rule == "title_keyword: 'design' (prefix)"
    assert by_title["Head of Operations"].rule == "seniority: 'Head of' (word)"
    assert by_title["Danish speaking Customer Agent"].rule == "language_speaker: 'danish'"


def test_a_conditional_city_is_kept_but_flagged_for_layer_two(gold, config) -> None:
    """Layer 0 admits a hybrid-gated city provisionally; Layer 2 is not replayable."""
    by_title = {v.job.title: v for v in replay(gold, config)}
    provisional = by_title["Data Coordinator"]
    assert provisional.kept
    assert provisional.pending_hybrid
    # "Hybrid" in the title satisfies the gate outright, so that one is settled.
    assert by_title["Hybrid Product Analyst"].kept
    assert not by_title["Hybrid Product Analyst"].pending_hybrid


def test_an_unresolvable_location_is_kept_but_flagged_for_layer_two(gold, config) -> None:
    """WP8d's provisional state, kept separate from an outright keep.

    Without this flag the harness would count a deferred job as a recall win it
    has not earned: Layer 2 settles these against the description and fails
    closed, and no offline replay can know which way that goes.
    """
    by_title = {v.job.title: v for v in replay(gold, config)}
    provisional = by_title["Field Officer"]
    assert provisional.kept
    assert provisional.pending_location
    assert not provisional.pending_hybrid
    # A job that named a real city is decided here and now, not deferred.
    assert not by_title["Junior Data Analyst"].pending_location


def test_replay_is_deterministic(gold, config) -> None:
    """langdetect seeds itself randomly; the harness seeds it so pins can hold."""
    first = [(v.job.dedupe_key, v.layer, v.rule) for v in replay(gold, config)]
    second = [(v.job.dedupe_key, v.layer, v.rule) for v in replay(gold, config)]
    assert first == second


def test_evaluation_makes_no_network_connection(gold, config, monkeypatch) -> None:
    """The central claim, pinned rather than inspected."""

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("the eval harness must not open a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    result = evaluate(gold, config)
    assert result.confusion.total == len(gold)


# ---------------------------------------------------------------------------
# The regression pin
# ---------------------------------------------------------------------------


def test_regression_pins_current_ladder(gold, config) -> None:
    """Every number the harness reports for the fixture config, asserted by hand.

    This is the test WP8c exists to leave behind: if a later refactor changes
    which jobs the filters drop, or which rule takes the credit, this fails.
    """
    result = evaluate(gold, config)

    assert (
        result.confusion.true_positives,
        result.confusion.false_positives,
        result.confusion.false_negatives,
        result.confusion.true_negatives,
    ) == (4, 1, 3, 6)
    assert result.confusion.precision == pytest.approx(0.8)
    assert result.confusion.recall == pytest.approx(0.5714285714285714)
    assert result.confusion.fbeta(2.0) == pytest.approx(0.6060606060606061)
    # One of those four true positives is provisional, not won: 'Field Officer'
    # is deferred to Layer 2, which this harness cannot replay and which fails
    # closed. See test_an_unresolvable_location_is_kept_but_flagged_for_layer_two.
    assert len(result.pending_location_wanted) == 1

    # (layer, reached, dropped, false negatives it caused)
    assert [(r.layer, r.reached, r.dropped, r.dropped_wanted) for r in result.layers] == [
        (LAYER_RULES, 14, 3, 1),
        (LAYER_TITLE_KEYWORD, 11, 2, 1),
        (LAYER_SENIORITY, 9, 2, 1),
        (LAYER_NON_ENGLISH, 7, 1, 0),
        (LAYER_LANGUAGE, 6, 1, 0),
    ]

    assert [(v.job.title, v.layer, v.rule) for v in result.false_negatives] == [
        ("Graphic Designer", LAYER_TITLE_KEYWORD, "title_keyword: 'design' (prefix)"),
        ("Head of Operations", LAYER_SENIORITY, "seniority: 'Head of' (word)"),
        ("Programme Officer", LAYER_RULES, RULE_LOC_UNLISTED_CITY),
    ]


def test_cumulative_recall_only_ever_falls(gold, config) -> None:
    """A layer cannot resurrect a job, so the cumulative curve is monotonic."""
    reports = layer_reports(replay(gold, config))
    recalls = [r.cumulative.recall for r in reports]
    assert recalls == sorted(recalls, reverse=True)


def test_report_names_every_false_negative(gold, config) -> None:
    _, issues = load_labels(LABELS)
    text = format_report(evaluate(gold, config), gold, issues, LABELS)
    assert "False negatives: 3 jobs" in text
    for title in ("Graphic Designer", "Head of Operations", "Programme Officer"):
        assert title in text
    # And admits what it could not evaluate.
    assert "1d-review-status not replayed" in text
    assert "2-detail not replayed" in text
    assert "unresolvable location field" in text
    assert "ceiling on the recall this buys" in text


# ---------------------------------------------------------------------------
# Comparing two configurations
# ---------------------------------------------------------------------------


def _variant(tmp_path: Path, *, keywords: str | None = None, rules: dict | None = None) -> Path:
    """A copy of the fixture config with one thing changed."""
    variant = tmp_path / "variant"
    shutil.copytree(CONFIG, variant)
    if keywords is not None:
        (variant / "title_exclude_keywords.csv").write_text(keywords, encoding="utf-8")
    if rules is not None:
        (variant / "rules.json").write_text(json.dumps(rules), encoding="utf-8")
    return variant


def test_comparison_lists_what_a_loosened_rule_recovers(gold, config, tmp_path: Path) -> None:
    loosened = load_ladder_config(_variant(tmp_path, keywords="keyword,match\nsales,word\n"))
    diff = compare(evaluate(gold, config), evaluate(gold, loosened))

    assert [c.job.title for c in diff.newly_kept] == ["Graphic Designer"]
    assert diff.newly_dropped == []
    assert diff.after.confusion.recall > diff.before.confusion.recall
    assert diff.after.confusion.false_negatives == diff.before.confusion.false_negatives - 1

    text = format_comparison(diff)
    # The rule that used to fire is printed in full, not truncated into a column.
    assert "title_keyword: 'design' (prefix)" in text
    assert "Net effect on wanted jobs: 1 recovered, 0 newly lost." in text


def test_comparison_lists_what_a_tightened_rule_costs(gold, config, tmp_path: Path) -> None:
    tightened = load_ladder_config(
        _variant(tmp_path, keywords="keyword,match\ndesign,prefix\nsales,word\nanalyst,word\n")
    )
    diff = compare(evaluate(gold, config), evaluate(gold, tightened))

    assert sorted(c.job.title for c in diff.newly_dropped) == [
        "Hybrid Product Analyst",
        "Junior Data Analyst",
    ]
    assert diff.newly_kept == []
    assert diff.after.confusion.recall < diff.before.confusion.recall
    assert "1 recovered" not in format_comparison(diff)


def test_identical_configurations_diff_to_nothing(gold, config, tmp_path: Path) -> None:
    diff = compare(evaluate(gold, config), evaluate(gold, load_ladder_config(_variant(tmp_path))))
    assert diff.changes == []
    assert "No job is treated differently" in format_comparison(diff)


# ---------------------------------------------------------------------------
# Config loading and the CLI
# ---------------------------------------------------------------------------


def test_a_config_dir_without_rules_fails_rather_than_defaulting(tmp_path: Path) -> None:
    """Naming the wrong directory must not quietly compare the live config with itself."""
    with pytest.raises(FileNotFoundError):
        load_ladder_config(tmp_path)


def test_a_config_dir_without_keywords_is_a_config_with_no_keyword_layer(
    gold, tmp_path: Path, caplog
) -> None:
    variant = _variant(tmp_path)
    (variant / "title_exclude_keywords.csv").unlink()
    with caplog.at_level("WARNING"):
        config = load_ladder_config(variant)
    assert config.title_keywords == []
    assert "will not exclude anything" in caplog.text
    assert all(v.layer != LAYER_TITLE_KEYWORD for v in replay(gold, config))


def test_cli_prints_the_baseline_report(capsys) -> None:
    main(["--labels", str(LABELS), "--config-dir", str(CONFIG)])
    out = capsys.readouterr().out
    assert "Filter ladder evaluation" in out
    assert "False negatives: 3 jobs" in out


def test_cli_compare_distinguishes_two_directories_of_the_same_name(
    tmp_path: Path, capsys
) -> None:
    """Two dirs both called 'config' must not print two identically-named columns."""
    a = tmp_path / "a" / "config"
    b = tmp_path / "b" / "config"
    for target in (a, b):
        shutil.copytree(CONFIG, target)
    main(["--labels", str(LABELS), "--compare", str(a), str(b)])
    out = capsys.readouterr().out
    assert "config (A)" in out and "config (B)" in out


def test_cli_rejects_a_beta_that_is_not_positive() -> None:
    with pytest.raises(SystemExit, match="must be positive"):
        main(["--labels", str(LABELS), "--config-dir", str(CONFIG), "--beta", "0"])


def test_cli_rejects_a_gold_set_with_no_usable_rows(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["dedupe_key", "title", "label"])
        writer.writerow(["k1", "Junior Analyst", "maybe"])
    with pytest.raises(SystemExit, match="no usable labelled rows"):
        main(["--labels", str(path), "--config-dir", str(CONFIG)])


def test_confusion_of_matches_the_verdicts_it_was_built_from(gold, config) -> None:
    verdicts = replay(gold, config)
    matrix = confusion_of(verdicts)
    assert matrix.total == len(verdicts)
    assert matrix.true_positives + matrix.false_positives == sum(1 for v in verdicts if v.kept)


def test_costly_rules_names_the_rule_to_loosen_first(gold, config) -> None:
    """The 'which one rule would bring back the most?' view, worst first."""
    _, issues = load_labels(LABELS)
    text = format_report(evaluate(gold, config), gold, issues, LABELS)
    assert "Rules that cost wanted jobs" in text
    # Each fixture rule costs exactly one wanted job, so all three are listed…
    for rule in (
        RULE_LOC_UNLISTED_CITY,
        "title_keyword: 'design' (prefix)",
        "seniority: 'Head of' (word)",
    ):
        assert rule in text
    # …and a rule that only ever dropped unwanted jobs is not.
    assert "language_speaker: 'danish'" not in text.split("Rules that cost wanted jobs")[1].split(
        "False negatives"
    )[0]
