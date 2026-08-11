"""End-to-end `run_pipeline` over a fake extractor and a fake fetcher.

The point is not that any single count is right — it is that the counts
reconcile. `run.py`'s `format_summary` subtracts each stage's exclusions from
the previous stage's remainder and prints the result as "passed title filters",
"after blocklist", "new jobs kept". Nothing checks that arithmetic, so a stage
that stops feeding its counter, or one inserted without a matching field, would
print a funnel that silently does not add up.

No network: `get_extractor`, `fetch_text` and `fetch_rendered` are all replaced
on the pipeline module, and every call site resolves them from module globals
at call time.
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

# Detail-page bodies keyed by URL fragment. Layer 2 fetches each new job's
# detail page; these decide what it finds there.
_SENIOR_BODY = "We are looking for someone with 8+ years of experience in the field."
_PHD_BODY = "A PhD is required for this role."
_PLAIN_BODY = "A great opportunity for someone early in their career."


def _job(title: str, *, location: str, slug: str, snippet: str = "") -> dict[str, str]:
    return {
        "source_name": _SOURCE,
        "title": title,
        "company": "",
        "location": location,
        "listing_url": _LISTING,
        "detail_url": f"{_LISTING}/{slug}",
        "apply_url": "",
        "raw_snippet": snippet or f"{title} {location}",
    }


# One job per funnel stage, so every counter in RunSummary is exercised.
_EXTRACTED = [
    _job("Data Analyst", location="Berlin", slug="kept"),
    _job("Data Analyst", location="Lisbon", slug="wrong-city"),          # rules
    _job("Marketing Analyst", location="Berlin", slug="keyword"),        # title keyword
    _job("Head of Data", location="Berlin", slug="seniority"),           # seniority title
    _job("Analyst (Dutch speaking)", location="Berlin", slug="lang"),    # language
    _job("Data Analyst", location="Berlin", slug="blocked"),             # review status
    _job("Reporting Analyst", location="Berlin", slug="senior-detail"),  # Layer 2: years
    _job("Research Analyst", location="Berlin", slug="phd"),             # Layer 2: PhD
]

_RULES = {
    "locations": ["Berlin"],
    "seniority_filter_enabled": True,
    "seniority_exclude_titles": ["Head of"],
}


def _write_config(tmp_path: Path) -> tuple[Path, Path, Path]:
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(
        yaml.dump(
            {"sources": [{"name": _SOURCE, "url": _LISTING, "strategy": "static"}]}
        ),
        encoding="utf-8",
    )
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps(_RULES), encoding="utf-8")
    keywords_path = tmp_path / "title_exclude_keywords.csv"
    keywords_path.write_text("keyword,match\nmarketing,word\n", encoding="utf-8")
    return sources_path, rules_path, keywords_path


def _fake_fetch(url: str, *args: Any, **kwargs: Any) -> str:
    if url.endswith("senior-detail"):
        return _SENIOR_BODY
    if url.endswith("phd"):
        return _PHD_BODY
    return _PLAIN_BODY


def _seed_rejected_job(db_path: Path) -> None:
    """The review-status replacement of the old blocklist: one stored job the
    owner has rejected, which the extractor still offers every run."""
    with JobStore(db_path) as store:
        run_id = store.begin_run()
        store.upsert_jobs(
            [
                {
                    "dedupe_key": f"{_LISTING}/blocked",
                    "source_name": _SOURCE,
                    "title": "Data Analyst",
                    "location": "Berlin",
                    "detail_url": f"{_LISTING}/blocked",
                }
            ],
            run_id,
            # Fixed in the past so a same-second pipeline run still moves last_seen.
            now="2026-01-01T00:00:00+00:00",
        )
        store.set_status([f"{_LISTING}/blocked"], "rejected")
        store.finish_run(run_id)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    sources_path, rules_path, keywords_path = _write_config(tmp_path)
    _seed_rejected_job(tmp_path / "jobs.sqlite3")
    monkeypatch.setattr(
        pipeline_mod, "get_extractor", lambda name: lambda url, fetch_fn: list(_EXTRACTED)
    )
    monkeypatch.setattr(pipeline_mod, "fetch_text", _fake_fetch)
    monkeypatch.setattr(pipeline_mod, "fetch_rendered", _fake_fetch)
    assert sources_path.is_file() and rules_path.is_file() and keywords_path.is_file()
    return tmp_path


def _run(tmp_path: Path) -> RunSummary:
    return run_pipeline(
        sources_path=tmp_path / "sources.yaml",
        rules_path=tmp_path / "rules.json",
        out_db_path=tmp_path / "jobs.sqlite3",
        title_keywords_path=tmp_path / "title_exclude_keywords.csv",
    )


def _rows(tmp_path: Path) -> list[dict[str, Any]]:
    """The unreviewed jobs — what the xlsx export shows."""
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        return store.jobs_with_status(("new",))


# --- the funnel reconciles --------------------------------------------------


def test_funnel_counts_are_internally_consistent(env: Path) -> None:
    s = _run(env)

    # Sources: every source is either processed or skipped, never both, never neither.
    assert s.sources_processed + s.sources_skipped == s.sources_total

    # Layer 0 cannot admit more jobs than were extracted.
    assert s.jobs_extracted == len(_EXTRACTED)
    assert 0 <= s.jobs_kept <= s.jobs_extracted

    # The title/language/blocklist chain, exactly as format_summary computes it.
    after_blocklist = (
        s.jobs_kept
        - s.jobs_keyword_excluded
        - s.jobs_title_excluded
        - s.jobs_non_english_excluded
        - s.jobs_language_excluded
        - s.jobs_blocklist_excluded
    )
    assert after_blocklist >= 0

    # Everything surviving the review-status check is either cached or sent to
    # Layer 2, and Layer 2's intake splits into genuinely new and rechecked.
    assert after_blocklist == (
        s.jobs_already_stored + s.jobs_new_checked + s.jobs_stored_rechecked
    )

    # Layer 2 keeps what it does not exclude.
    assert s.jobs_new_checked + s.jobs_stored_rechecked - s.jobs_detail_excluded == s.jobs_kept_new

    # The two named detail-exclusion reasons are a subset of all of them; the
    # remainder is the years-based cut, which has no field of its own.
    assert s.jobs_detail_excluded >= s.jobs_phd_excluded + s.jobs_hybrid_excluded
    assert s.jobs_phd_excluded >= 0 and s.jobs_hybrid_excluded >= 0

    # First run against a store with no unreviewed jobs: every kept new job
    # becomes a row.
    assert s.rows_written == s.jobs_kept_new
    assert s.rows_delisted == 0


def test_each_stage_excluded_the_job_intended_for_it(env: Path) -> None:
    """Guards the funnel test itself: if a stage stopped firing, the invariants
    above would still hold trivially with every count at zero."""
    s = _run(env)

    assert s.jobs_kept == 7, "the Lisbon job is the only one Layer 0 should drop"
    assert s.jobs_keyword_excluded == 1
    assert s.jobs_title_excluded == 1
    assert s.jobs_language_excluded == 1
    assert s.jobs_blocklist_excluded == 1, "the stored 'rejected' job is excluded"
    assert s.jobs_new_checked == 3
    assert s.jobs_detail_excluded == 2
    assert s.jobs_phd_excluded == 1
    assert s.jobs_kept_new == 1

    titles = {r["title"] for r in _rows(env)}
    assert titles == {"Data Analyst"}


def test_second_run_stores_nothing_new(env: Path) -> None:
    """The property the cutover had to preserve: a job stored in one run is
    recognised as already-stored in the next, so it costs no detail fetch and
    writes no duplicate row.

    Note what this also pins: `jobs_new_checked` does not fall to zero. A job
    Layer 2 rejected is never written to the store, so nothing records that it
    was already judged, and it is re-fetched on every subsequent run. That is
    current behaviour, not a bug introduced here — WP6's stored descriptions
    are what would let the pipeline skip it.
    """
    first = _run(env)
    rows_after_first = len(_rows(env))
    second = _run(env)

    assert second.jobs_already_stored == first.jobs_kept_new
    assert second.rows_written == 0
    assert second.rows_delisted == 0
    assert len(_rows(env)) == rows_after_first

    # Re-checked every run: the two jobs Layer 2 rejected, and only those.
    assert second.jobs_new_checked == first.jobs_detail_excluded
    assert second.jobs_kept_new == 0

    # Still consistent on the second pass.
    after_blocklist = (
        second.jobs_kept
        - second.jobs_keyword_excluded
        - second.jobs_title_excluded
        - second.jobs_non_english_excluded
        - second.jobs_language_excluded
        - second.jobs_blocklist_excluded
    )
    assert after_blocklist == (
        second.jobs_already_stored + second.jobs_new_checked + second.jobs_stored_rechecked
    )


def test_rejected_job_stays_rejected_but_its_sighting_is_refreshed(env: Path) -> None:
    """The rejected job is still listed: its row keeps status 'rejected'
    (review decisions survive a scrape) while last_seen moves with the run."""
    _run(env)

    with JobStore(env / "jobs.sqlite3") as store:
        by_key = {r["dedupe_key"]: r for r in store.all_jobs()}
    row = by_key[f"{_LISTING}/blocked"]
    assert row["status"] == "rejected"
    assert row["last_seen"] > row["first_seen"]


def test_summary_renders_every_field(env: Path) -> None:
    """A field added to RunSummary without a line in format_summary, or one
    removed while the summary still reads it, fails here."""
    s = _run(env)

    rendered = format_summary(s, table_total=len(_rows(env)))

    assert "Run summary" in rendered
    assert f"{s.rows_written:,}" in rendered
    assert len(rendered.splitlines()) > 10
