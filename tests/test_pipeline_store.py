"""The pipeline against the authoritative SQLite store (WP5).

What test_dual_store.py checked while the CSV led — run/health bookkeeping,
delisting that never deletes, revival on reappearance — plus the behaviour the
cutover makes possible: a persisted hybrid confirmation, so a stored
conditional-city job stops re-earning its exception with a detail fetch every
run.

No network: `get_extractor`, `fetch_text` and `fetch_rendered` are replaced on
the pipeline module, exactly as in test_pipeline_funnel.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import yaml

from job_scraper import pipeline as pipeline_mod
from job_scraper.pipeline import run_pipeline
from job_scraper.storage.db import JobStore

_SOURCE = "acme"
_LISTING = "https://acme.example/jobs"


def _job(title: str, *, location: str, slug: str) -> dict[str, str]:
    return {
        "source_name": _SOURCE,
        "title": title,
        "company": "Acme",
        "location": location,
        "listing_url": _LISTING,
        "detail_url": f"{_LISTING}/{slug}",
        "apply_url": "",
        "raw_snippet": f"{title} {location}",
    }


_KEPT_A = _job("Data Analyst", location="Berlin", slug="a")
_KEPT_B = _job("Research Analyst", location="Berlin", slug="b")


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    (tmp_path / "sources.yaml").write_text(
        yaml.dump({"sources": [{"name": _SOURCE, "url": _LISTING, "strategy": "static"}]}),
        encoding="utf-8",
    )
    (tmp_path / "rules.json").write_text(json.dumps({"locations": ["Berlin"]}), encoding="utf-8")

    extracted: list[dict[str, str]] = [_KEPT_A, _KEPT_B]
    fetches: Counter[str] = Counter()

    def fake_fetch(url: str, *a: Any, **k: Any) -> str:
        fetches[url] += 1
        return "A great opportunity, no experience required."

    monkeypatch.setattr(
        pipeline_mod, "get_extractor", lambda name: lambda url, fetch_fn: list(extracted)
    )
    monkeypatch.setattr(pipeline_mod, "fetch_text", fake_fetch)
    monkeypatch.setattr(pipeline_mod, "fetch_rendered", fake_fetch)
    return {"tmp_path": tmp_path, "extracted": extracted, "fetches": fetches}


def _run(tmp_path: Path):
    return run_pipeline(
        sources_path=tmp_path / "sources.yaml",
        rules_path=tmp_path / "rules.json",
        out_db_path=tmp_path / "jobs.sqlite3",
        cache_path=tmp_path / "http_cache.sqlite3",
        delist_after=1,
        # No robots.txt lookup: these fetchers are stubs and the host does not
        # exist. WP10's robots check is covered against a real origin in
        # tests/test_politeness.py and tests/test_source_health.py.
        check_robots=False,
    )


def _db_jobs(tmp_path: Path) -> dict[str, dict[str, Any]]:
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        return {j["dedupe_key"]: j for j in store.all_jobs()}


def test_first_run_stores_jobs_with_run_metadata(env: dict[str, Any]) -> None:
    tmp_path = env["tmp_path"]
    summary = _run(tmp_path)

    jobs = _db_jobs(tmp_path)
    assert set(jobs) == {f"{_LISTING}/a", f"{_LISTING}/b"}
    assert summary.rows_written == 2
    for job in jobs.values():
        assert job["status"] == "new"
        assert job["first_seen"] == job["last_seen"]
        # Layer 2 judged both jobs this run, so the level is recorded.
        assert job["experience_level"] == "unspecified"
        # WP6: the stripped description text Layer 2 fetched is kept too, not
        # discarded, along with when it was captured.
        assert job["description_text"] == "A great opportunity, no experience required."
        assert job["description_fetched_at"]


def test_second_run_performs_no_http_request_for_an_already_stored_job(
    env: dict[str, Any],
) -> None:
    """WP6: once a job's description is stored, a later run over the same job
    must not re-fetch its detail page."""
    tmp_path = env["tmp_path"]
    fetches = env["fetches"]

    _run(tmp_path)
    fetch_count_after_first_run = sum(fetches.values())
    assert fetch_count_after_first_run == 2  # one detail fetch per job

    _run(tmp_path)
    assert sum(fetches.values()) == fetch_count_after_first_run, (
        "second run over the same jobs fetched a detail page again"
    )


def test_layer2_rejected_job_is_stored_and_not_refetched(env: dict[str, Any]) -> None:
    """A job Layer 2 excludes (too senior) used to be discarded rather than
    stored, so nothing recorded that it had already been judged and its
    detail page was re-fetched on every run. WP6 stores it as 'rejected' with
    its description, so the second run skips the fetch entirely — via the
    same review-status check that already skips a manually rejected job."""
    tmp_path = env["tmp_path"]
    fetches = env["fetches"]
    senior_job = _job("Head Analyst", location="Berlin", slug="senior")
    env["extracted"][:] = [senior_job]
    url = senior_job["detail_url"]

    def senior_fetch(u: str, *a: Any, **k: Any) -> str:
        fetches[u] += 1
        return "We require 8+ years of experience in the field."

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pipeline_mod, "fetch_text", senior_fetch)
        mp.setattr(pipeline_mod, "fetch_rendered", senior_fetch)

        first = _run(tmp_path)
        assert first.jobs_detail_excluded == 1
        assert first.jobs_kept_new == 0
        assert fetches[url] == 1

        stored = _db_jobs(tmp_path)[url]
        assert stored["status"] == "rejected"
        assert "8+ years" in stored["description_text"]

        second = _run(tmp_path)

    assert second.jobs_detail_excluded == 0
    assert second.jobs_blocklist_excluded == 1, "caught by the review-status check instead"
    assert fetches[url] == 1, "no re-fetch: the rejection and its description were persisted"


def test_run_and_health_bookkeeping(env: dict[str, Any]) -> None:
    tmp_path = env["tmp_path"]
    _run(tmp_path)

    conn = sqlite3.connect(tmp_path / "jobs.sqlite3")
    try:
        runs = list(conn.execute("SELECT run_id, started_at, finished_at FROM runs"))
        health = list(
            conn.execute("SELECT source_name, run_id, rows_found, ok, error FROM source_health")
        )
    finally:
        conn.close()

    assert len(runs) == 1
    run_id, started_at, finished_at = runs[0]
    assert started_at and finished_at
    assert health == [(_SOURCE, run_id, 2, 1, None)], "rows_found counts extracted, not kept"


def test_second_run_updates_in_place(env: dict[str, Any]) -> None:
    tmp_path = env["tmp_path"]
    _run(tmp_path)
    first = _db_jobs(tmp_path)
    summary = _run(tmp_path)

    assert summary.rows_written == 0
    jobs = _db_jobs(tmp_path)
    assert set(jobs) == set(first)
    for url, job in jobs.items():
        assert job["first_seen"] == first[url]["first_seen"]
        assert job["last_run_id"] == 2
        # The cached path offers no experience level; the stored one survives.
        assert job["experience_level"] == "unspecified"


def test_delisted_job_is_kept_and_revived_as_seen(env: dict[str, Any]) -> None:
    tmp_path = env["tmp_path"]
    _run(tmp_path)

    env["extracted"].remove(_KEPT_B)
    summary = _run(tmp_path)
    assert summary.rows_delisted == 1

    jobs = _db_jobs(tmp_path)
    assert set(jobs) == {f"{_LISTING}/a", f"{_LISTING}/b"}, "the database deletes nothing"
    assert jobs[f"{_LISTING}/b"]["status"] == "delisted"

    # And when the posting returns, it is revived rather than duplicated.
    env["extracted"].append(_KEPT_B)
    _run(tmp_path)
    jobs = _db_jobs(tmp_path)
    assert len(jobs) == 2
    assert jobs[f"{_LISTING}/b"]["status"] == "seen"


def test_failed_source_is_recorded_in_source_health(env: dict[str, Any]) -> None:
    tmp_path = env["tmp_path"]
    (tmp_path / "sources.yaml").write_text(
        yaml.dump(
            {
                "sources": [
                    {"name": _SOURCE, "url": _LISTING, "strategy": "static"},
                    {"name": "broken", "url": "https://broken.example", "strategy": "static"},
                ]
            }
        ),
        encoding="utf-8",
    )

    def get_extractor(name: str):
        if name == "broken":

            def boom(url: str, fetch_fn: Any) -> list[dict[str, str]]:
                raise RuntimeError("selector drift")

            return boom
        return lambda url, fetch_fn: list(env["extracted"])

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pipeline_mod, "get_extractor", get_extractor)
        _run(tmp_path)

    conn = sqlite3.connect(tmp_path / "jobs.sqlite3")
    try:
        health = {
            row[0]: row
            for row in conn.execute("SELECT source_name, rows_found, ok, error FROM source_health")
        }
    finally:
        conn.close()
    assert health[_SOURCE] == (_SOURCE, 2, 1, None)
    assert health["broken"] == ("broken", 0, 0, "selector drift")


# --- the persisted hybrid confirmation (conditional locations) ---------------


_HYBRID_RULES = {
    "locations": ["Berlin"],
    "conditional_locations": ["Faraway"],
    "conditional_location_keywords": ["hybrid"],
}


def test_hybrid_confirmation_is_persisted_and_ends_the_refetch_loop(
    env: dict[str, Any],
) -> None:
    """A conditional-city job earns its hybrid exception from the detail page
    once; the confirmation is stored, so later runs skip the fetch entirely.
    Before WP5 the marker did not survive a run and the same page was
    re-fetched every time."""
    tmp_path = env["tmp_path"]
    (tmp_path / "rules.json").write_text(json.dumps(_HYBRID_RULES), encoding="utf-8")
    conditional = _job("Data Analyst", location="Faraway", slug="conditional")
    env["extracted"][:] = [conditional]
    url = conditional["detail_url"]

    def fake_fetch(u: str, *a: Any, **k: Any) -> str:
        env["fetches"][u] += 1
        return "This is a hybrid role, no experience required."

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pipeline_mod, "fetch_text", fake_fetch)
        mp.setattr(pipeline_mod, "fetch_rendered", fake_fetch)

        first = _run(tmp_path)
        assert first.jobs_kept_new == 1
        assert env["fetches"][url] == 1
        assert _db_jobs(tmp_path)[url]["hybrid_confirmed"] == 1

        second = _run(tmp_path)

    assert second.jobs_already_stored == 1
    assert second.jobs_stored_rechecked == 0
    assert env["fetches"][url] == 1, "no re-fetch: the confirmation was persisted"


def test_stored_unconfirmed_conditional_job_is_rechecked_once(
    env: dict[str, Any],
) -> None:
    """A conditional-city job stored before hybrid_confirmed existed (the
    column defaults to 0) is re-fetched once, and the confirmation is then
    persisted so the loop still converges."""
    tmp_path = env["tmp_path"]
    (tmp_path / "rules.json").write_text(json.dumps(_HYBRID_RULES), encoding="utf-8")
    conditional = _job("Data Analyst", location="Faraway", slug="conditional")
    env["extracted"][:] = [conditional]
    url = conditional["detail_url"]

    with JobStore(tmp_path / "jobs.sqlite3") as store:
        run_id = store.begin_run()
        store.upsert_jobs(
            [
                {
                    "dedupe_key": url,
                    "source_name": _SOURCE,
                    "title": "Data Analyst",
                    "location": "Faraway",
                    "detail_url": url,
                }
            ],
            run_id,
        )
        store.finish_run(run_id)

    def fake_fetch(u: str, *a: Any, **k: Any) -> str:
        env["fetches"][u] += 1
        return "This is a hybrid role, no experience required."

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pipeline_mod, "fetch_text", fake_fetch)
        mp.setattr(pipeline_mod, "fetch_rendered", fake_fetch)

        first = _run(tmp_path)
        assert first.jobs_stored_rechecked == 1
        assert first.rows_written == 0, "a re-check is not a new row"
        assert env["fetches"][url] == 1
        assert _db_jobs(tmp_path)[url]["hybrid_confirmed"] == 1

        second = _run(tmp_path)

    assert second.jobs_stored_rechecked == 0
    assert env["fetches"][url] == 1


def test_failed_hybrid_check_is_not_permanently_rejected(env: dict[str, Any]) -> None:
    """A network hiccup while checking a conditional-city job's hybrid
    arrangement must fail closed for that run only, not forever.

    Regression test: an earlier version of this change stored every job
    Layer 2 excludes as status='rejected' — including a conditional-location
    job excluded only because its detail page could not be read this run.
    Since 'rejected' is permanent (nothing automatic un-rejects a job, and
    rejected jobs never appear in jobs.xlsx), a single transient fetch
    failure would silently and permanently drop the job — violating
    CLAUDE.md's "never lose data" rule. The fix: such a job is excluded for
    the run (fail-closed is unchanged, deliberate behaviour) but never
    written to the store, so it gets a fresh detail fetch next run, exactly
    as before this package existed.
    """
    tmp_path = env["tmp_path"]
    fetches = env["fetches"]
    (tmp_path / "rules.json").write_text(json.dumps(_HYBRID_RULES), encoding="utf-8")
    conditional = _job("Data Analyst", location="Faraway", slug="conditional")
    env["extracted"][:] = [conditional]
    url = conditional["detail_url"]

    def failing_fetch(u: str, *a: Any, **k: Any) -> str:
        fetches[u] += 1
        raise TimeoutError("simulated network hiccup")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pipeline_mod, "fetch_text", failing_fetch)
        mp.setattr(pipeline_mod, "fetch_rendered", failing_fetch)

        first = _run(tmp_path)
        assert first.jobs_kept_new == 0, "fails closed for this run: not admitted"
        assert first.jobs_hybrid_excluded == 1
        assert fetches[url] == 1
        assert url not in _db_jobs(tmp_path), "not persisted as a permanent rejection"

        second = _run(tmp_path)

    assert fetches[url] == 2, "retried next run rather than silently dropped forever"
    assert second.jobs_kept_new == 0
    assert url not in _db_jobs(tmp_path)


# ---------------------------------------------------------------------------
# Unresolvable locations, end to end (WP8d)
# ---------------------------------------------------------------------------

_UNRESOLVABLE_RULES = {
    "locations": ["Berlin"],
    "non_place_locations": ["Worldwide"],
}


def test_resolved_unresolvable_location_is_not_refetched_next_run(
    env: dict[str, Any],
) -> None:
    """No new column for this state, and none needed.

    `hybrid_confirmed` exists to re-check rows written before it existed; a
    state introduced today has no such legacy population, so being in the store
    is enough to skip the fetch — and WP6 already persists the description that
    settled it.
    """
    tmp_path = env["tmp_path"]
    (tmp_path / "rules.json").write_text(json.dumps(_UNRESOLVABLE_RULES), encoding="utf-8")
    job = _job("Data Analyst", location="Home based - Worldwide", slug="unresolvable")
    env["extracted"][:] = [job]
    url = job["detail_url"]

    def fake_fetch(u: str, *a: Any, **k: Any) -> str:
        env["fetches"][u] += 1
        return "You will work from our Berlin office. No experience required."

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pipeline_mod, "fetch_text", fake_fetch)
        mp.setattr(pipeline_mod, "fetch_rendered", fake_fetch)

        first = _run(tmp_path)
        assert first.jobs_kept_new == 1, "the description named a listed place"
        assert env["fetches"][url] == 1

        second = _run(tmp_path)

    assert second.jobs_already_stored == 1
    assert env["fetches"][url] == 1, "no re-fetch: being stored is the whole mechanism"


def test_unresolvable_location_dropped_at_layer_2_is_not_refetched_either(
    env: dict[str, Any],
) -> None:
    """The other half of item 5: a fail-closed drop is stored as 'rejected',
    so Layer 1d catches it next run and Layer 2 never pays for it twice."""
    tmp_path = env["tmp_path"]
    (tmp_path / "rules.json").write_text(json.dumps(_UNRESOLVABLE_RULES), encoding="utf-8")
    job = _job("Data Analyst", location="Home based - Worldwide", slug="unresolvable")
    env["extracted"][:] = [job]
    url = job["detail_url"]

    def fake_fetch(u: str, *a: Any, **k: Any) -> str:
        env["fetches"][u] += 1
        return "You will work from our Nairobi office. No experience required."

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pipeline_mod, "fetch_text", fake_fetch)
        mp.setattr(pipeline_mod, "fetch_rendered", fake_fetch)

        first = _run(tmp_path)
        assert first.jobs_location_excluded == 1
        assert first.jobs_kept_new == 0
        assert _db_jobs(tmp_path)[url]["status"] == "rejected"

        second = _run(tmp_path)

    assert second.jobs_blocklist_excluded == 1
    assert env["fetches"][url] == 1, "judged once, then skipped as any rejected job is"


def test_unverified_unresolvable_location_is_retried_not_permanently_dropped(
    env: dict[str, Any],
) -> None:
    """A network hiccup must not read as "checked and found lacking"."""
    tmp_path = env["tmp_path"]
    (tmp_path / "rules.json").write_text(json.dumps(_UNRESOLVABLE_RULES), encoding="utf-8")
    job = _job("Data Analyst", location="Home based - Worldwide", slug="unresolvable")
    env["extracted"][:] = [job]
    url = job["detail_url"]
    fetches = env["fetches"]

    def failing_fetch(u: str, *a: Any, **k: Any) -> str:
        fetches[u] += 1
        raise TimeoutError("simulated network hiccup")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pipeline_mod, "fetch_text", failing_fetch)
        mp.setattr(pipeline_mod, "fetch_rendered", failing_fetch)

        first = _run(tmp_path)
        assert first.jobs_kept_new == 0, "fails closed for this run"
        assert first.jobs_location_excluded == 1
        assert url not in _db_jobs(tmp_path), "not persisted as a permanent rejection"

        second = _run(tmp_path)

    assert fetches[url] == 2, "retried next run rather than silently dropped forever"
    assert second.jobs_kept_new == 0
