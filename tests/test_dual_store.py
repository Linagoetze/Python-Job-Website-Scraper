"""WP4 dual write: after a fake run, the SQLite shadow store must agree with
the authoritative CSV.

The CSV is compared field by field against the database, with the CSV parsed
here by a local regex rather than through `read_store_rows`, so the test does
not validate the mirror using the same helper that fed it.

No network: `get_extractor`, `fetch_text` and `fetch_rendered` are replaced on
the pipeline module, exactly as in test_pipeline_funnel.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml

from job_scraper import pipeline as pipeline_mod
from job_scraper.pipeline import run_pipeline
from job_scraper.storage.db import JobStore

_SOURCE = "acme"
_LISTING = "https://acme.example/jobs"
_HYPERLINK = re.compile(r'^=HYPERLINK\("([^"]+)"\)$')


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


# Two jobs the filters keep, one Layer 0 drops. `extracted` is module-mutable
# so tests can make a job vanish between runs and watch the delisting mirror.
_KEPT_A = _job("Data Analyst", location="Berlin", slug="a")
_KEPT_B = _job("Research Analyst", location="Berlin", slug="b")
_DROPPED = _job("Data Analyst", location="Lisbon", slug="wrong-city")


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    (tmp_path / "sources.yaml").write_text(
        yaml.dump({"sources": [{"name": _SOURCE, "url": _LISTING, "strategy": "static"}]}),
        encoding="utf-8",
    )
    (tmp_path / "rules.json").write_text(json.dumps({"locations": ["Berlin"]}), encoding="utf-8")

    extracted: list[dict[str, str]] = [_KEPT_A, _KEPT_B, _DROPPED]
    monkeypatch.setattr(
        pipeline_mod, "get_extractor", lambda name: lambda url, fetch_fn: list(extracted)
    )
    monkeypatch.setattr(pipeline_mod, "load_blocklist_keys", lambda: set())
    fake_fetch = lambda url, *a, **k: "A great opportunity, no experience required."  # noqa: E731
    monkeypatch.setattr(pipeline_mod, "fetch_text", fake_fetch)
    monkeypatch.setattr(pipeline_mod, "fetch_rendered", fake_fetch)
    return {"tmp_path": tmp_path, "extracted": extracted}


def _run(tmp_path: Path) -> None:
    run_pipeline(
        sources_path=tmp_path / "sources.yaml",
        rules_path=tmp_path / "rules.json",
        out_csv_path=tmp_path / "jobs.csv",
        out_db_path=tmp_path / "jobs.sqlite3",
    )


def _csv_rows(tmp_path: Path) -> dict[str, dict[str, str]]:
    """Final CSV keyed by detail URL, parsed independently of the store code."""
    with (tmp_path / "jobs.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        m = _HYPERLINK.match(row["detail_hyperlink"])
        assert m, f"unparseable hyperlink formula: {row['detail_hyperlink']!r}"
        out[m.group(1)] = row
    return out


def _db_jobs(tmp_path: Path, *, active_only: bool = True) -> dict[str, dict[str, Any]]:
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        jobs = store.active_jobs() if active_only else store.all_jobs()
    return {j["dedupe_key"]: j for j in jobs}


def test_stores_agree_after_a_run(env: dict[str, Any]) -> None:
    tmp_path = env["tmp_path"]
    _run(tmp_path)

    csv_rows = _csv_rows(tmp_path)
    db_jobs = _db_jobs(tmp_path)
    assert set(csv_rows) == set(db_jobs) == {f"{_LISTING}/a", f"{_LISTING}/b"}

    for url, row in csv_rows.items():
        job = db_jobs[url]
        assert job["source_name"] == row["source_name"]
        assert job["company"] == row["company"]
        assert job["title"] == row["title"]
        assert job["location"] == row["location"]
        assert job["detail_url"] == url
        assert job["apply_url"] == ""
        assert job["status"] == "new"
        assert job["first_seen"] == job["last_seen"]
        # Layer 2 judged both jobs this run, so the level is recorded, and the
        # CSV holds no such column — this is data only the database keeps.
        assert job["experience_level"] == "unspecified"


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
    assert health == [(_SOURCE, run_id, 3, 1, None)], "rows_found counts extracted, not kept"


def test_second_run_keeps_stores_in_step(env: dict[str, Any]) -> None:
    tmp_path = env["tmp_path"]
    _run(tmp_path)
    first = _db_jobs(tmp_path)
    _run(tmp_path)

    csv_rows = _csv_rows(tmp_path)
    db_jobs = _db_jobs(tmp_path)
    assert set(csv_rows) == set(db_jobs)
    for url, job in db_jobs.items():
        assert job["first_seen"] == first[url]["first_seen"]
        assert job["last_run_id"] == 2
        # The cached path reports experience_level "cached", a placeholder the
        # mirror must not store over the real level from the first run.
        assert job["experience_level"] == "unspecified"


def test_job_delisted_from_csv_is_kept_in_db_as_delisted(env: dict[str, Any]) -> None:
    tmp_path = env["tmp_path"]
    _run(tmp_path)

    env["extracted"].remove(_KEPT_B)
    _run(tmp_path)

    csv_rows = _csv_rows(tmp_path)
    assert set(csv_rows) == {f"{_LISTING}/a"}, "CSV store deletes the delisted row"

    all_jobs = _db_jobs(tmp_path, active_only=False)
    assert set(all_jobs) == {f"{_LISTING}/a", f"{_LISTING}/b"}, "database deletes nothing"
    assert all_jobs[f"{_LISTING}/b"]["status"] == "delisted"
    assert set(_db_jobs(tmp_path)) == set(csv_rows), "active view still mirrors the CSV"

    # And when the posting returns, it is revived rather than duplicated.
    env["extracted"].append(_KEPT_B)
    _run(tmp_path)
    all_jobs = _db_jobs(tmp_path, active_only=False)
    assert len(all_jobs) == 2
    assert all_jobs[f"{_LISTING}/b"]["status"] == "seen"


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
    assert health[_SOURCE] == (_SOURCE, 3, 1, None)
    assert health["broken"] == ("broken", 0, 0, "selector drift")
