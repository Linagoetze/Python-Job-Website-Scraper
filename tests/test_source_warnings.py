"""SP7: warnings about *sources*, printed in the run summary.

Three questions the summary could not answer: which source raised (it was
counted with the config skips and named only in a log line), which source has
read one page on every run, and which configured source the tombstone bans.
Each block is built from this run's results or the store's history, never from
a log line, and none of them skips a source or changes what a run does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from job_scraper import pipeline as pipeline_mod
from job_scraper.page_sizes import ONE_PAGE_RUNS, TYPICAL_PAGE_SIZES, constant_page_size
from job_scraper.pipeline import FailedSource, RefusedSource, RunSummary, run_pipeline
from job_scraper.robots import RobotsVerdict
from job_scraper.run import format_summary
from job_scraper.storage.db import RUN_KIND_REFILTER, JobStore, OnePageSource

_LISTING = "https://acme.example/jobs"
_OTHER = "https://beta.example/jobs"


def _job(source: str, listing: str, slug: str) -> dict[str, str]:
    return {
        "source_name": source,
        "title": "Data Analyst",
        "company": "",
        "location": "Berlin",
        "listing_url": listing,
        "detail_url": f"{listing}/{slug}",
        "apply_url": "",
        "raw_snippet": "Data Analyst Berlin",
    }


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "sources.yaml").write_text(
        yaml.dump(
            {
                "sources": [
                    {"name": "acme", "url": _LISTING, "strategy": "static"},
                    {"name": "beta", "url": _OTHER, "strategy": "static"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "rules.json").write_text(json.dumps({"locations": ["Berlin"]}), encoding="utf-8")
    monkeypatch.setattr(pipeline_mod, "fetch_text", lambda url, *a, **k: "Entry level role. " * 60)
    monkeypatch.setattr(pipeline_mod, "fetch_rendered", lambda url, *a, **k: "")
    return tmp_path


def _run(tmp_path: Path, *, dry_run: bool = False, **kwargs: Any) -> RunSummary:
    return run_pipeline(
        sources_path=tmp_path / "sources.yaml",
        rules_path=tmp_path / "rules.json",
        out_db_path=tmp_path / "jobs.sqlite3",
        cache_path=tmp_path / "http_cache.sqlite3",
        dry_run=dry_run,
        check_robots=False,  # stubbed fetchers, and the hosts do not exist
        **kwargs,
    )


def _extractors(monkeypatch: pytest.MonkeyPatch, *, acme_raises: Exception | None = None) -> None:
    """acme reads one job, or raises; beta always reads one job."""

    def get_extractor(name: str) -> Any:
        def extract(url: str, fetch_fn: Any) -> list[dict[str, str]]:
            if name == "acme" and acme_raises is not None:
                raise acme_raises
            return [_job(name, url, "one")]

        return extract

    monkeypatch.setattr(pipeline_mod, "get_extractor", get_extractor)


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


# --- 1. failed sources ------------------------------------------------------


def test_a_dry_run_names_a_source_whose_extractor_raised(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case SP3b's dry run found: '5 / 6 processed (1 skipped)', nothing named."""
    _extractors(monkeypatch, acme_raises=ValueError("board states 2000 postings\nsecond line"))
    summary = _run(env, dry_run=True)

    assert summary.failed_sources == (FailedSource("acme", "board states 2000 postings"),)
    assert (summary.sources_processed, summary.sources_skipped) == (1, 0)
    text = format_summary(summary)
    assert "Sources           1 / 2 processed  (1 failed, 0 skipped)" in text
    assert "!  Failed sources: 1 source raised an error" in text
    assert "!  acme: board states 2000 postings — stored jobs kept, nothing delisted" in text
    assert "second line" not in text


def test_a_failed_source_is_not_counted_as_a_config_skip(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (env / "sources.yaml").write_text(
        yaml.dump(
            {
                "sources": [
                    {"name": "acme", "url": _LISTING, "strategy": "static"},
                    {"name": "gamma", "url": _OTHER, "strategy": "carrier-pigeon"},
                ]
            }
        ),
        encoding="utf-8",
    )
    _extractors(monkeypatch, acme_raises=RuntimeError("boom"))
    summary = _run(env, dry_run=True)
    assert len(summary.failed_sources) == 1
    assert summary.sources_skipped == 1
    assert "(1 failed, 1 skipped)" in format_summary(summary)


def test_a_failure_with_no_message_still_names_what_it_was(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _extractors(monkeypatch, acme_raises=AssertionError())
    assert _run(env, dry_run=True).failed_sources == (FailedSource("acme", "AssertionError"),)


def test_a_long_first_line_is_cut(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _extractors(monkeypatch, acme_raises=ValueError("<html>" + "x" * 5000))
    (failed,) = _run(env, dry_run=True).failed_sources
    assert len(failed.error) <= 200 and failed.error.endswith("…")


def test_what_the_block_says_about_stored_jobs_is_true(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kept, and not delisted — even with the threshold at its lowest."""
    _extractors(monkeypatch)
    _run(env)
    _extractors(monkeypatch, acme_raises=ValueError("boom"))
    for _ in range(3):
        summary = _run(env, delist_after=1)
        assert summary.rows_delisted == 0
    with JobStore(env / "jobs.sqlite3") as store:
        statuses = {
            row["source_name"]: row["status"]
            for row in store._c().execute("SELECT source_name, status FROM jobs")
        }
    assert statuses["acme"] != "delisted"


def test_a_real_run_records_the_failure_in_source_health_as_before(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _extractors(monkeypatch, acme_raises=ValueError("boom"))
    _run(env)
    with JobStore(env / "jobs.sqlite3") as store:
        row = (
            store._c()
            .execute("SELECT rows_found, ok, error FROM source_health WHERE source_name = 'acme'")
            .fetchone()
        )
    assert tuple(row) == (0, 0, "boom")


def test_a_healthy_run_adds_no_failure_lines(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _extractors(monkeypatch)
    text = format_summary(_run(env, dry_run=True))
    assert "!" not in text
    assert "Sources           2 / 2 processed  (0 skipped)" in text


# --- 1b. robots.txt refusals ------------------------------------------------


class _RefusingPolicy:
    """Refuses acme's listing, allows everything else."""

    def allows(self, url: str) -> bool:
        return "acme" not in url

    def explain(self, url: str) -> RobotsVerdict:
        return RobotsVerdict(
            url=url,
            robots_url="https://acme.example/robots.txt",
            user_agent="test",
            allowed=False,
            reason="the first matching line decides",
            rule="Disallow: /",
            group="User-agent: *",
        )


def test_a_refused_source_is_named_apart_from_the_skips(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _extractors(monkeypatch)
    monkeypatch.setattr(pipeline_mod, "current_robots_policy", lambda: _RefusingPolicy())
    summary = _run(env, dry_run=True)

    assert summary.refused_sources == (
        RefusedSource("acme", "https://acme.example/robots.txt", "Disallow: /"),
    )
    assert (summary.sources_processed, summary.sources_skipped, summary.failed_sources) == (
        1,
        0,
        (),
    )
    text = format_summary(summary)
    assert "(1 refused by robots.txt, 0 skipped)" in text
    assert "!  Refused by robots.txt: 1 source not read this run" in text
    assert "!  acme: https://acme.example/robots.txt says `Disallow: /` — stored jobs kept" in text


def test_a_refusal_writes_no_source_health_row(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The owner's call (SP7): source_health stays 'a reader ran'."""
    _extractors(monkeypatch)
    monkeypatch.setattr(pipeline_mod, "current_robots_policy", lambda: _RefusingPolicy())
    _run(env)
    with JobStore(env / "jobs.sqlite3") as store:
        names = {r[0] for r in store._c().execute("SELECT source_name FROM source_health")}
    assert names == {"beta"}


def test_a_refusal_with_no_identifiable_line_says_why(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Unidentified(_RefusingPolicy):
        def explain(self, url: str) -> RobotsVerdict:
            return RobotsVerdict(
                url=url,
                robots_url="https://acme.example/robots.txt",
                user_agent="test",
                allowed=False,
                reason="the deciding line could not be identified",
            )

    _extractors(monkeypatch)
    monkeypatch.setattr(pipeline_mod, "current_robots_policy", lambda: Unidentified())
    (refused,) = _run(env, dry_run=True).refused_sources
    assert refused.rule == "the deciding line could not be identified"


# --- rendering --------------------------------------------------------------


def test_the_blocks_use_the_marker_and_never_a_ladder_ordinal() -> None:
    text = format_summary(
        _summary(
            sources_processed=27,
            failed_sources=(FailedSource("airbus", "boom"), FailedSource("irc", "bang")),
            refused_sources=(RefusedSource("undp", "https://u.example/robots.txt", "Disallow: /"),),
        )
    )
    assert "2 failed, 1 refused by robots.txt, 0 skipped" in text
    marked = [ln for ln in text.splitlines() if ln.startswith("!")]
    assert len(marked) == 5  # two headings, three sources
    assert all("− " not in ln for ln in marked)
    assert text.index("Failed sources") < text.index("Refused by robots.txt")


# --- 2. one page, every run --------------------------------------------------


def test_the_typical_sizes_are_one_list_shared_with_the_probe() -> None:
    from job_scraper import probe

    assert probe.TYPICAL_PAGE_SIZES is TYPICAL_PAGE_SIZES
    assert ONE_PAGE_RUNS == 5


def test_five_equal_typical_counts_are_suspected() -> None:
    assert constant_page_size([20, 20, 20, 20, 20]) == 20


def test_a_streak_broken_by_one_different_run_is_not() -> None:
    assert constant_page_size([20, 20, 19, 20, 20]) is None
    assert constant_page_size([21, 20, 20, 20, 20]) is None


def test_a_constant_count_that_is_no_page_size_is_not() -> None:
    assert 17 not in TYPICAL_PAGE_SIZES
    assert constant_page_size([17] * 5) is None
    assert constant_page_size([0] * 5) is None


def test_fewer_than_n_runs_say_nothing() -> None:
    assert constant_page_size([20] * (ONE_PAGE_RUNS - 1)) is None
    assert constant_page_size([]) is None


def test_only_the_newest_n_are_read() -> None:
    assert constant_page_size([20] * 5 + [3, 99]) == 20
    assert constant_page_size([20, 20, 20, 20, 7, 20]) is None


def test_the_rule_is_platform_blind() -> None:
    """The owner declined the guarded-reader exemption (SP7): 100 warns like 20."""
    assert constant_page_size([100] * 5) == 100
    assert constant_page_size([16] * 5) == 16


def _record(store: JobStore, rows: int, *, source: str = "acme", ok: bool = True) -> int:
    run_id = store.begin_run()
    store.record_source_health(run_id, source, rows, ok, None if ok else "boom")
    store.finish_run(run_id)
    return run_id


def _counts(store: JobStore, run_id: int, source: str = "acme") -> list[int]:
    return store.recent_successful_row_counts(run_id, ONE_PAGE_RUNS).get(source, [])


def test_the_store_returns_the_newest_counts_first(tmp_path: Path) -> None:
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        ids = [_record(store, n) for n in (1, 2, 3, 4, 5, 6, 7)]
        assert _counts(store, ids[-1]) == [7, 6, 5, 4, 3]
        assert _counts(store, ids[2]) == [3, 2, 1]  # as of an earlier run


def test_a_failed_run_is_skipped_not_counted(tmp_path: Path) -> None:
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        for _ in range(3):
            _record(store, 20)
        _record(store, 0, ok=False)
        _record(store, 20)
        last = _record(store, 20)
        assert constant_page_size(_counts(store, last)) == 20


def test_a_source_that_failed_this_run_is_not_judged(tmp_path: Path) -> None:
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        for _ in range(5):
            _record(store, 20)
        last = _record(store, 0, ok=False)
        assert store.recent_successful_row_counts(last, ONE_PAGE_RUNS) == {}


def test_a_maintenance_run_between_two_identical_runs_reads_as_two_runs(tmp_path: Path) -> None:
    """SP4g: retrofilter's run has no health rows, and must neither break nor pad."""
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        _record(store, 20)
        refilter = store.begin_run(kind=RUN_KIND_REFILTER)
        store.finish_run(refilter)
        last = _record(store, 20)
        assert _counts(store, last) == [20, 20]
        # Not five, so a pass cannot pad a streak up to the threshold either.
        assert constant_page_size(_counts(store, last)) is None


def test_a_refilter_run_cannot_break_a_streak_even_with_a_health_row(tmp_path: Path) -> None:
    """The kind filter, tested directly: today no such run has a row; one day it may."""
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        for _ in range(2):
            _record(store, 20)
        refilter = store.begin_run(kind=RUN_KIND_REFILTER)
        store.record_source_health(refilter, "acme", 3, True, None)
        store.finish_run(refilter)
        for _ in range(3):
            last = _record(store, 20)
        assert constant_page_size(_counts(store, last)) == 20


def test_a_pre_sp4g_store_reads_every_old_run_as_a_scrape(tmp_path: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    with JobStore(db) as store:
        for _ in range(5):
            last = _record(store, 20)
        conn = store._c()
        conn.execute("UPDATE runs SET kind = 'scrape'")  # what the column default gave them
    with JobStore(db) as store:
        assert constant_page_size(_counts(store, last)) == 20


def _stub_rows(monkeypatch: pytest.MonkeyPatch, counts: dict[str, int]) -> None:
    def get_extractor(name: str) -> Any:
        return lambda url, fetch_fn: [_job(name, url, f"job-{i}") for i in range(counts[name])]

    monkeypatch.setattr(pipeline_mod, "get_extractor", get_extractor)


def test_five_identical_runs_warn_through_a_real_run(
    env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _stub_rows(monkeypatch, {"acme": 20, "beta": 7})
    for _ in range(ONE_PAGE_RUNS - 1):
        assert _run(env).one_page_sources == ()
    with caplog.at_level("WARNING"):
        summary = _run(env)
    # beta's constant 7 is no page size, so only acme is named.
    assert summary.one_page_sources == (OnePageSource("acme", 20, ONE_PAGE_RUNS),)
    assert "may be reading only its first page" in caplog.text
    text = format_summary(summary)
    assert "!  One page, every run: 1 source returned the same typical page size" in text
    assert (
        "!  acme: 20 rows on each of its last 5 runs — may be reading only its first page" in text
    )


def test_a_dry_run_counts_itself_as_the_newest_run(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_rows(monkeypatch, {"acme": 20, "beta": 7})
    for _ in range(ONE_PAGE_RUNS - 1):
        _run(env)
    summary = _run(env, dry_run=True)
    assert [p.source_name for p in summary.one_page_sources] == ["acme"]
    with JobStore(env / "jobs.sqlite3") as store:
        assert store._c().execute("SELECT count(*) FROM runs").fetchone()[0] == ONE_PAGE_RUNS - 1


def test_one_different_run_ends_the_warning_through_a_real_run(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_rows(monkeypatch, {"acme": 20, "beta": 7})
    for _ in range(ONE_PAGE_RUNS):
        _run(env)
    _stub_rows(monkeypatch, {"acme": 19, "beta": 7})
    assert _run(env).one_page_sources == ()
    _stub_rows(monkeypatch, {"acme": 20, "beta": 7})
    assert _run(env).one_page_sources == ()  # the streak starts again from one


def test_the_one_page_block_renders_its_own_marker() -> None:
    text = format_summary(
        _summary(one_page_sources=(OnePageSource("airbus", 20, 5), OnePageSource("irc", 20, 5)))
    )
    assert "!  One page, every run: 2 sources returned the same typical page size" in text
    marked = [ln for ln in text.splitlines() if ln.startswith("!")]
    assert len(marked) == 3
    assert all("− " not in ln for ln in marked)
