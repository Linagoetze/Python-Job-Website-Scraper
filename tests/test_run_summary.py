"""The end-of-run funnel's layout (WP8h).

There was never a test pinning `format_summary`'s exact text, and WP8h's first
attempt shipped a regression because of it: adding the ladder ordinal to each
line made the two longest labels overrun the number column, rendering
`…in the text−1,100` with no gap at all. The bug appeared at four digits on one
line and five on another — both ordinary for a real run — and it was invisible
to every existing test.

So two things are pinned here. The **exact rendering** of a representative run,
because a funnel that silently misaligns is read wrong rather than noticed. And
the **invariant behind it**: every line keeps a gap between its label and its
number, at magnitudes far past anything a real run produces, so the next person
to lengthen a label finds out here instead of in their terminal.
"""

from __future__ import annotations

import re

from job_scraper.drops import LAYERS
from job_scraper.pipeline import RunSummary
from job_scraper.run import format_summary


def _summary(**overrides: int) -> RunSummary:
    """A coherent run, overridable one field at a time."""
    fields = dict(
        sources_total=30,
        sources_skipped=0,
        sources_processed=30,
        jobs_extracted=8000,
        jobs_kept=1200,
        jobs_keyword_excluded=200,
        jobs_title_excluded=100,
        jobs_blocklist_excluded=50,
        jobs_already_stored=300,
        jobs_new_checked=1200,
        jobs_stored_rechecked=10,
        jobs_detail_excluded=1200,
        jobs_phd_excluded=20,
        jobs_hybrid_excluded=100,
        jobs_location_excluded=1100,
        jobs_kept_new=0,
        rows_written=0,
        rows_delisted=5,
        jobs_still_listed=4000,
        jobs_unreviewed=100,
        exclusions_logged=2380,
    )
    fields.update(overrides)
    return RunSummary(**fields)


# The counts here are the ones that broke the first WP8h attempt: 1,100 on the
# longest label and 6,800 on the second longest.
EXPECTED = """\
Run summary
────────────────────────────────────────────────────
Sources           30 / 30 processed  (0 skipped)

Jobs seen (all pages, dupes incl.)             8,000
  L1  − off-criteria (location/keywords)      −6,800   → 1,200 match your criteria
  L2  − title keyword                           −200
  L3  − senior-level title                      −100   →   900 passed title filters
  L4  − blocklisted (rejected)                   −50   →   850 after blocklist
        already in table (skipped)               300
        stored, hybrid recheck                    10
        new, detail-checked                    1,200
  L5  − needs 3+ yrs / PhD (20 PhD)               −0
  L5  − non-hybrid (distant city)               −100
  L5  − location unresolvable in the text     −1,100   →     0 new jobs kept
────────────────────────────────────────────────────
New rows written                                   0
Marked delisted                                    5
Still listed this run                          4,000
Unreviewed jobs in table                         100
Exclusions logged                              2,380"""


def test_funnel_renders_exactly() -> None:
    """The golden layout. Update deliberately if a label changes, never to make
    a failure go away — a misaligned funnel is the thing this test exists for."""
    assert format_summary(_summary()) == EXPECTED


def test_the_regression_case_keeps_its_gap() -> None:
    """The specific line that broke: a four-digit count on the longest label."""
    line = next(
        ln
        for ln in format_summary(_summary()).splitlines()
        if "location unresolvable" in ln
    )
    assert "text−1,100" not in line
    assert "text     −1,100" in line


def test_no_label_ever_runs_into_its_number() -> None:
    """The invariant, at magnitudes no real run reaches.

    `format_summary` right-aligns to a fixed column; a label long enough to
    reach it must push the number right rather than abut it. Six-figure counts
    are not realistic — that is the point, since the column must degrade
    gracefully rather than exactly at the width someone happened to measure.
    """
    text = format_summary(
        _summary(
            jobs_extracted=812345,
            jobs_kept=106066,
            jobs_keyword_excluded=123456,
            jobs_location_excluded=234567,
            jobs_detail_excluded=400000,
            jobs_hybrid_excluded=100000,
            jobs_already_stored=987654,
            exclusions_logged=654321,
        )
    )
    for line in text.splitlines():
        # Everything up to the optional "   → ..." tail carries the number.
        head = line.split("   → ")[0]
        match = re.search(r"(−?[\d,]+)$", head)
        if match is None:
            continue
        label = head[: match.start()]
        assert label.endswith("  "), f"label runs into its number: {line!r}"


def test_every_ladder_layer_appears_in_the_funnel() -> None:
    """All five display ordinals are shown, so no layer drops jobs invisibly."""
    text = format_summary(_summary())
    for layer in LAYERS:
        assert f"L{layer.display}  − " in text, f"Layer {layer.display} missing"


def test_the_ordinals_run_in_execution_order() -> None:
    """L1 before L2 before … — the whole point of the renumbering."""
    text = format_summary(_summary())
    seen = [int(m) for m in re.findall(r"^  L(\d)  − ", text, flags=re.MULTILINE)]
    assert seen == sorted(seen)
    assert seen == [1, 2, 3, 4, 5, 5, 5]  # the three detail lines share Layer 5
