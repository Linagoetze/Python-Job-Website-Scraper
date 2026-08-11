"""extractor dict -> store -> `dedupe_key_for_job` must be stable.

This is the property the whole "already stored" mechanism rests on: a job
written in one run has to be recognised by its key in the next, or it is
stored again as a duplicate and re-fetched at Layer 2 forever.

With SQLite the key is stored explicitly (the CSV store had to re-derive it
from an Excel formula on every read), so the risk has moved: `job_to_row`
canonicalises the detail URL on the way in (`canonical_detail_url` can add a
locale prefix), and the key must be computed so that the raw extracted URL and
the canonical stored one still land on the same row.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from job_scraper.storage.db import JobStore, dedupe_key_for_job, job_to_row
from tests.fixture_cases import FIXTURE_CASES, FIXTURES_DIR, parse_fixture


def _store_and_reload(tmp_path: Path, jobs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Upsert *jobs* into a fresh store and return the rows keyed by dedupe key."""
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        run_id = store.begin_run()
        rows = [r for j in jobs if (r := job_to_row(j))]
        inserted, updated = store.upsert_jobs(rows, run_id)
        assert (inserted, updated) == (len(rows), 0)
        store.finish_run(run_id)
        return {r["dedupe_key"]: r for r in store.all_jobs()}


def _fixture_jobs(name: str) -> list[dict[str, Any]]:
    filename = FIXTURE_CASES[name][0]
    if not (FIXTURES_DIR / filename).exists():
        pytest.skip(f"{filename} not captured yet — re-run scripts/capture_fixtures.py {name}")
    return parse_fixture(name)


# --- the six real fixtures --------------------------------------------------


@pytest.mark.parametrize("name", sorted(FIXTURE_CASES))
def test_fixture_job_keys_survive_the_store(name: str, tmp_path: Path) -> None:
    jobs = _fixture_jobs(name)
    expected_keys = {dedupe_key_for_job(j) for j in jobs}
    assert all(expected_keys), f"{name}: an extracted job produced no dedupe key at all"

    stored = _store_and_reload(tmp_path, jobs)

    assert set(stored) == expected_keys


@pytest.mark.parametrize("name", sorted(FIXTURE_CASES))
def test_fixture_jobs_are_recognised_on_a_second_run(name: str, tmp_path: Path) -> None:
    """The same postings offered again must all be seen as already stored."""
    jobs = _fixture_jobs(name)
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        run1 = store.begin_run()
        rows = [r for j in jobs if (r := job_to_row(j))]
        store.upsert_jobs(rows, run1)

        run2 = store.begin_run()
        inserted, updated = store.upsert_jobs(rows, run2)
        assert inserted == 0, f"{name}: re-offering the same jobs inserted rows"
        assert updated == len(rows)

        index = store.job_index()
    assert all(dedupe_key_for_job(j) in index for j in jobs)


# --- the cases the fixtures do not reach ------------------------------------


def test_oatly_locale_variants_collapse_to_one_key(tmp_path: Path) -> None:
    """Oatly is the one source where the stored URL is deliberately *not* the
    extracted one: `canonical_detail_url` adds the listing page's locale prefix
    on the way in, while `dedupe_key_from_url` folds every slug and locale
    variant to `oatly:job:<id>`. So the strings differ and only the key matches
    — exactly the case a naive rewrite of the store would break.
    """
    job = {
        "source_name": "oatly",
        "title": "Data Analyst",
        "company": "",
        "location": "Malmö",
        "listing_url": "https://careers.oatly.com/en-GB/jobs",
        "detail_url": "https://careers.oatly.com/jobs/4321-data-analyst",
        "apply_url": "",
    }

    stored = _store_and_reload(tmp_path, [job])

    (key,) = stored
    assert key == dedupe_key_for_job(job) == "oatly:job:4321"
    assert "/en-GB/jobs/" in stored[key]["detail_url"], "locale prefix applied on write"

    # The same posting seen again under its bare, locale-less URL is not new.
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        run_id = store.begin_run()
        inserted, _ = store.upsert_jobs([job_to_row(job)], run_id)
    assert inserted == 0


def test_apply_url_only_job_round_trips(tmp_path: Path) -> None:
    """Some extractors set only `apply_url`. The key falls back to it, and the
    row stores it as the detail URL; the key has to come back the same rather
    than going empty."""
    job = {
        "source_name": "acme",
        "title": "Data Analyst",
        "company": "",
        "location": "Berlin",
        "listing_url": "https://acme.example/jobs",
        "detail_url": "",
        "apply_url": "https://acme.example/apply/99",
    }

    stored = _store_and_reload(tmp_path, [job])

    (key,) = stored
    assert key == dedupe_key_for_job(job) == "https://acme.example/apply/99"
    assert stored[key]["detail_url"] == "https://acme.example/apply/99"
    assert stored[key]["apply_url"] == "", "not duplicated into apply_url"


def test_http_url_is_upgraded_consistently_on_both_sides(tmp_path: Path) -> None:
    """`normalize_http_url` upgrades http→https. It runs on the extracted dict
    and again on the stored row, so a job listed over http must not read back
    as a different job."""
    job = {
        "source_name": "acme",
        "title": "Data Analyst",
        "company": "",
        "location": "Berlin",
        "listing_url": "http://acme.example/jobs",
        "detail_url": "http://acme.example/jobs/7",
        "apply_url": "",
    }

    stored = _store_and_reload(tmp_path, [job])

    assert set(stored) == {dedupe_key_for_job(job)} == {"https://acme.example/jobs/7"}


def test_a_job_with_no_url_is_dropped_rather_than_stored_keyless(tmp_path: Path) -> None:
    """A row with no usable URL has no key, so it could never be recognised
    again. `job_to_row` returns None instead of producing an unmatchable row."""
    job = {
        "source_name": "acme",
        "title": "Data Analyst",
        "company": "",
        "location": "Berlin",
        "listing_url": "https://acme.example/jobs",
        "detail_url": "",
        "apply_url": "",
    }

    assert job_to_row(job) is None
    assert _store_and_reload(tmp_path, [job]) == {}
