"""extractor dict -> CSV -> `_dedupe_key` must be stable.

This is the property the whole "already stored" mechanism rests on: a job
written in one run has to produce the same key when read back in the next, or
it is stored again as a duplicate and re-fetched at Layer 2 forever.

It is not self-evidently true, because the key is computed from different
fields on each side. `_dedupe_key` reads `detail_url` / `apply_url` on a fresh
extractor dict, but a stored row has neither column — it has
`detail_hyperlink`, holding an Excel `=HYPERLINK("…")` formula the key function
has to parse back out. Between the two sits `_normalize_row_fields`, which runs
the URL through `canonical_detail_url` first. Any of those three can change the
string; only the key has to survive.

WP5 deletes `_excel_hyperlink_formula`, `_url_from_hyperlink_formula` and moves
formula generation into the xlsx writer. These tests are what says the
replacement still round-trips.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pytest

from job_scraper.storage.csv_store import (
    _dedupe_key,
    _read_existing_keys,
    append_jobs_csv,
)
from tests.fixture_cases import FIXTURE_CASES, FIXTURES_DIR, parse_fixture


def _stored_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _roundtrip(tmp_path: Path, jobs: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Write *jobs*, then read the rows back as CSV gives them to the next run."""
    path = tmp_path / "jobs.csv"
    written = append_jobs_csv(path, jobs)
    assert written == len(jobs), f"expected {len(jobs)} rows written, got {written}"
    return _stored_rows(path)


# Sources where `append_jobs_csv` also runs `_collapse_content_duplicates`,
# which can remove a row that was just written. Their round trip is not
# row-for-row; see test_content_dedupe_churns_a_row_on_every_run.
_CONTENT_DEDUPED = {"impactpool", "jobsinlund"}

_SIMPLE_FIXTURES = sorted(set(FIXTURE_CASES) - _CONTENT_DEDUPED)


def _fixture_jobs(name: str) -> list[dict[str, Any]]:
    filename = FIXTURE_CASES[name][0]
    if not (FIXTURES_DIR / filename).exists():
        pytest.skip(f"{filename} not captured yet — re-run scripts/capture_fixtures.py {name}")
    return parse_fixture(name)


# --- the six real fixtures --------------------------------------------------


@pytest.mark.parametrize("name", _SIMPLE_FIXTURES)
def test_fixture_job_keys_survive_the_csv(name: str, tmp_path: Path) -> None:
    jobs = _fixture_jobs(name)
    expected_keys = [_dedupe_key(j) for j in jobs]
    assert all(expected_keys), f"{name}: an extracted job produced no dedupe key at all"

    rows = _roundtrip(tmp_path, jobs)

    assert [_dedupe_key(r) for r in rows] == expected_keys


@pytest.mark.parametrize("name", _SIMPLE_FIXTURES)
def test_fixture_jobs_are_recognised_on_a_second_run(name: str, tmp_path: Path) -> None:
    """The same postings offered again must all be seen as already stored."""
    jobs = _fixture_jobs(name)
    path = tmp_path / "jobs.csv"
    append_jobs_csv(path, jobs)

    assert append_jobs_csv(path, jobs) == 0, f"{name}: re-offering the same jobs wrote rows"

    stored = _read_existing_keys(path)
    assert all(_dedupe_key(j) in stored for j in jobs)


@pytest.mark.parametrize("name", sorted(_CONTENT_DEDUPED & set(FIXTURE_CASES)))
def test_content_deduped_sources_still_round_trip_every_surviving_row(
    name: str, tmp_path: Path
) -> None:
    """Content dedup removes rows, but it must not corrupt the ones it keeps."""
    jobs = _fixture_jobs(name)
    extracted_keys = {_dedupe_key(j) for j in jobs}

    path = tmp_path / "jobs.csv"
    append_jobs_csv(path, jobs)
    rows = _stored_rows(path)

    stored_keys = {_dedupe_key(r) for r in rows}
    assert stored_keys, f"{name}: nothing survived the write"
    assert stored_keys <= extracted_keys
    assert all(_dedupe_key(r) for r in rows), "a stored row lost its key"


def test_content_dedupe_churns_a_row_on_every_run(tmp_path: Path) -> None:
    """Pinned as current behaviour, and it is a wart worth naming.

    `_collapse_content_duplicates` keys on source + company + title, so two
    genuinely distinct impactpool URLs advertising the same role under the same
    employer collapse into one row. The loser's URL key is then absent from the
    store, so the next run does not recognise it, appends it again, and
    collapses it again — every run, forever.

    Consequences: "New rows written" in the run summary never settles to zero
    for a content-deduped source, and the two postings alternate in the table.
    Not fixed here (WP2 observes; `_collapse_content_duplicates` is on WP5's
    deletion list). If WP5 makes this settle to zero, delete this test rather
    than weakening it — that is the fix landing, not a regression.
    """
    jobs = _fixture_jobs("impactpool")
    path = tmp_path / "jobs.csv"

    first = append_jobs_csv(path, jobs)
    settled = len(_stored_rows(path))
    assert first == len(jobs)
    assert settled < len(jobs), "expected content dedup to collapse at least one row"

    churn = [append_jobs_csv(path, jobs) for _ in range(3)]

    assert churn == [len(jobs) - settled] * 3, "the churn should be steady, not growing"
    assert len(_stored_rows(path)) == settled, "the table size must still be stable"


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

    rows = _roundtrip(tmp_path, [job])

    stored_url = rows[0]["detail_hyperlink"]
    assert "/en-GB/jobs/" in stored_url, "the locale prefix should have been applied on write"
    assert _dedupe_key(rows[0]) == _dedupe_key(job) == "oatly:job:4321"

    # The same posting seen again under its bare, locale-less URL is not new.
    assert append_jobs_csv(tmp_path / "jobs.csv", [job]) == 0


def test_apply_url_only_job_round_trips(tmp_path: Path) -> None:
    """Some extractors set only `apply_url`. `_dedupe_key` falls back to it, and
    `_normalize_row_fields` stores it as the detail hyperlink; the key has to
    come back the same rather than going empty."""
    job = {
        "source_name": "acme",
        "title": "Data Analyst",
        "company": "",
        "location": "Berlin",
        "listing_url": "https://acme.example/jobs",
        "detail_url": "",
        "apply_url": "https://acme.example/apply/99",
    }

    rows = _roundtrip(tmp_path, [job])

    assert _dedupe_key(rows[0]) == _dedupe_key(job) == "https://acme.example/apply/99"


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

    rows = _roundtrip(tmp_path, [job])

    assert _dedupe_key(rows[0]) == _dedupe_key(job) == "https://acme.example/jobs/7"


def test_a_job_with_no_url_is_dropped_rather_than_stored_keyless(tmp_path: Path) -> None:
    """A row with no usable URL has no key, so it could never be recognised
    again. `append_jobs_csv` skips it instead of writing an unmatchable row."""
    job = {
        "source_name": "acme",
        "title": "Data Analyst",
        "company": "",
        "location": "Berlin",
        "listing_url": "https://acme.example/jobs",
        "detail_url": "",
        "apply_url": "",
    }

    path = tmp_path / "jobs.csv"

    assert append_jobs_csv(path, [job]) == 0
    assert _read_existing_keys(path) == set()
