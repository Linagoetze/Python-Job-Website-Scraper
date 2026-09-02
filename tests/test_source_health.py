"""WP10: warning about a source whose row count collapses.

A source that breaks loudly is already handled — the extractor raises and the
run says so. This is the other case: a selector that still matches *something*
returns a shorter list, nothing fails, and the missing postings are simply never
seen. `source_health` has recorded the row counts since WP4; nothing read them
back until now.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from job_scraper import pipeline as pipeline_mod
from job_scraper.pipeline import RunSummary, run_pipeline
from job_scraper.run import format_summary
from job_scraper.storage.db import JobStore

_SOURCE = "acme"
_LISTING = "https://acme.example/jobs"


# --- the query --------------------------------------------------------------


def _record(store: JobStore, rows: int, ok: bool = True) -> int:
    run_id = store.begin_run()
    store.record_source_health(run_id, _SOURCE, rows, ok, None if ok else "boom")
    store.finish_run(run_id)
    return run_id


def test_a_source_that_more_than_halves_is_reported(tmp_path: Path) -> None:
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        _record(store, 100)
        run_id = _record(store, 40)
        (drop,) = store.source_health_regressions(run_id)
    assert (drop.source_name, drop.previous_rows, drop.current_rows) == (_SOURCE, 100, 40)


def test_exactly_half_is_not_a_collapse(tmp_path: Path) -> None:
    """ "More than 50%" is the threshold, so the boundary case stays quiet."""
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        _record(store, 100)
        run_id = _record(store, 50)
        assert store.source_health_regressions(run_id) == []


def test_a_first_scrape_has_nothing_to_be_compared_against(tmp_path: Path) -> None:
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        run_id = _record(store, 3)
        assert store.source_health_regressions(run_id) == []


def test_the_comparison_skips_runs_where_the_source_failed(tmp_path: Path) -> None:
    """A failed run's zero is not a row count, and recovering from one is not a drop."""
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        _record(store, 100)
        _record(store, 0, ok=False)
        run_id = _record(store, 90)
        assert store.source_health_regressions(run_id) == []


def test_a_failing_source_is_not_reported_as_a_collapse(tmp_path: Path) -> None:
    """It already failed loudly; two warnings for one event would be noise."""
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        _record(store, 100)
        run_id = _record(store, 0, ok=False)
        assert store.source_health_regressions(run_id) == []


# --- through a run ----------------------------------------------------------


def _job(slug: str) -> dict[str, str]:
    return {
        "source_name": _SOURCE,
        "title": "Data Analyst",
        "company": "",
        "location": "Berlin",
        "listing_url": _LISTING,
        "detail_url": f"{_LISTING}/{slug}",
        "apply_url": "",
        "raw_snippet": "Data Analyst Berlin",
    }


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "sources.yaml").write_text(
        yaml.dump({"sources": [{"name": _SOURCE, "url": _LISTING, "strategy": "static"}]}),
        encoding="utf-8",
    )
    (tmp_path / "rules.json").write_text(json.dumps({"locations": ["Berlin"]}), encoding="utf-8")
    monkeypatch.setattr(pipeline_mod, "fetch_text", lambda url, *a, **k: "Entry level role.")
    monkeypatch.setattr(pipeline_mod, "fetch_rendered", lambda url, *a, **k: "")
    return tmp_path


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rows: int) -> RunSummary:
    jobs = [_job(f"job-{i}") for i in range(rows)]
    monkeypatch.setattr(
        pipeline_mod, "get_extractor", lambda name: lambda url, fetch_fn: list(jobs)
    )
    return run_pipeline(
        sources_path=tmp_path / "sources.yaml",
        rules_path=tmp_path / "rules.json",
        out_db_path=tmp_path / "jobs.sqlite3",
        cache_path=tmp_path / "http_cache.sqlite3",
        check_robots=False,  # stubbed fetchers, and the host does not exist
    )


def test_a_shrinking_source_reaches_the_run_summary(
    env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _run(env, monkeypatch, rows=10)
    with caplog.at_level("WARNING"):
        summary = _run(env, monkeypatch, rows=2)
    assert [(d.source_name, d.previous_rows, d.current_rows) for d in summary.health_warnings] == [
        (_SOURCE, 10, 2)
    ]
    assert "down from 10" in caplog.text


def test_a_steady_source_says_nothing(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _run(env, monkeypatch, rows=10)
    assert _run(env, monkeypatch, rows=9).health_warnings == ()


# --- how it renders ---------------------------------------------------------


def _summary(**overrides: Any) -> RunSummary:
    fields: dict[str, Any] = dict(
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


def _drop(name: str, previous: int, current: int) -> Any:
    from job_scraper.storage.db import SourceDrop

    return SourceDrop(name, previous, current)


def test_a_healthy_run_adds_no_lines_at_all() -> None:
    """Which is why tests/test_run_summary.py's golden layout is untouched by WP10."""
    assert "!" not in format_summary(_summary())


def test_a_warning_does_not_borrow_a_ladder_ordinal() -> None:
    """A health warning is not a filter layer, and must not read like one (WP8h)."""
    text = format_summary(_summary(health_warnings=(_drop("impactpool", 120, 4),)))
    warnings = [ln for ln in text.splitlines() if ln.startswith("!")]
    assert len(warnings) == 2  # the heading and the one source
    assert "impactpool: 4 rows this run, was 120 (-97%)" in warnings[1]
    for line in warnings:
        assert "− " not in line  # not a drop
        assert not any(f"L{n}" in line for n in range(1, 6))


def test_the_heading_counts_the_sources_it_names() -> None:
    text = format_summary(_summary(health_warnings=(_drop("one", 10, 1), _drop("two", 80, 2))))
    assert "2 sources returned far fewer rows than last time" in text
    assert "1 source returned" in format_summary(_summary(health_warnings=(_drop("one", 10, 1),)))


# --- zero rows on this run, whatever the history (CU2) -----------------------


def test_a_source_that_returns_nothing_is_named(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run(env, monkeypatch, rows=0).empty_sources == (_SOURCE,)


def test_a_source_that_has_never_worked_is_named_on_every_run(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The blind spot this check exists to close (docs/AUDIT.md §7A).

    `source_health_regressions` compares a source against its own last
    successful run, so a source that has never returned a row has nothing to
    collapse from and can never be reported there. Three sources sat at zero
    for nineteen consecutive runs on that technicality. The zero-row check asks
    the current run instead, so the first run and the nineteenth both say so.
    """
    first = _run(env, monkeypatch, rows=0)
    nineteenth = _run(env, monkeypatch, rows=0)
    assert first.empty_sources == nineteenth.empty_sources == (_SOURCE,)
    assert first.health_warnings == nineteenth.health_warnings == ()


def test_a_source_with_rows_is_not_named(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run(env, monkeypatch, rows=3).empty_sources == ()


def test_a_failing_source_is_not_named_as_empty(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """It raised, which is reported on its own; one event must not warn twice."""

    def boom(name: str) -> Any:
        def extractor(url: str, fetch_fn: Any) -> list[dict[str, str]]:
            raise RuntimeError("selector gone")

        return extractor

    monkeypatch.setattr(pipeline_mod, "get_extractor", boom)
    summary = run_pipeline(
        sources_path=env / "sources.yaml",
        rules_path=env / "rules.json",
        out_db_path=env / "jobs.sqlite3",
        cache_path=env / "http_cache.sqlite3",
        check_robots=False,
    )
    assert summary.empty_sources == ()


def test_the_empty_block_has_its_own_heading_and_no_ladder_ordinal() -> None:
    text = format_summary(_summary(empty_sources=("gfi_europe", "lifesum")))
    warnings = [ln for ln in text.splitlines() if ln.startswith("!")]
    assert len(warnings) == 3  # the heading and the two sources
    assert "2 sources returned zero rows this run" in warnings[0]
    assert "gfi_europe: 0 rows" in warnings[1]
    for line in warnings:
        assert "− " not in line
        assert not any(f"L{n}" in line for n in range(1, 6))


def test_the_empty_heading_counts_the_sources_it_names() -> None:
    assert "1 source returned zero rows" in format_summary(_summary(empty_sources=("lifesum",)))


def test_a_healthy_run_adds_no_empty_block() -> None:
    assert "zero rows this run" not in format_summary(_summary())


def test_shrinking_and_empty_are_reported_separately() -> None:
    """A source can be in both blocks; each answers its own question."""
    text = format_summary(
        _summary(health_warnings=(_drop("acme", 40, 0),), empty_sources=("acme",))
    )
    assert "acme: 0 rows this run, was 40 (-100%)" in text
    assert "acme: 0 rows — nothing delisted" in text


def test_the_empty_line_says_nothing_was_delisted_by_default() -> None:
    text = format_summary(_summary(empty_sources=("lifesum",)))
    assert "lifesum: 0 rows — nothing delisted; check its extractor" in text


def test_the_empty_line_admits_the_delisting_under_allow_empty_delist() -> None:
    """The one run where "nothing delisted" would be a lie.

    `--allow-empty-delist` means "this source genuinely emptied", and
    `note_misses_and_delist` then delists its unreviewed jobs immediately,
    bypassing the miss threshold. A block that still reported nothing had
    happened would be reassuring the reader about the only run where the
    reassurance is untrue.
    """
    text = format_summary(_summary(empty_sources=("lifesum",), allow_empty_delist=True))
    assert "lifesum: 0 rows — its stored jobs were delisted (--allow-empty-delist)" in text
    assert "nothing delisted" not in text


def test_the_flag_alone_adds_no_block() -> None:
    """No empty source, nothing to say — the flag is not news on its own."""
    assert "!" not in format_summary(_summary(allow_empty_delist=True))


def test_the_run_reports_the_flag_it_was_given(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    jobs: list[dict[str, str]] = []
    monkeypatch.setattr(
        pipeline_mod, "get_extractor", lambda name: lambda url, fetch_fn: list(jobs)
    )
    summary = run_pipeline(
        sources_path=env / "sources.yaml",
        rules_path=env / "rules.json",
        out_db_path=env / "jobs.sqlite3",
        cache_path=env / "http_cache.sqlite3",
        check_robots=False,
        allow_empty_delist=True,
    )
    assert summary.empty_sources == (_SOURCE,)
    assert summary.allow_empty_delist is True
