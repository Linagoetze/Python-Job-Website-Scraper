"""Delisting through the pipeline: N consecutive misses, and the WP1 guard
that a zero-row scrape (indistinguishable from a broken selector) must not
touch stored history."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
import yaml

from job_scraper import pipeline as pipeline_mod
from job_scraper.pipeline import run_pipeline
from job_scraper.storage.db import JobStore

_EXISTING_URL = "https://acme.example/jobs/existing"


def _job(source: str, title: str, url: str) -> dict[str, str]:
    return {
        "source_name": source,
        "title": title,
        "company": "",
        "location": "",
        "detail_url": url,
    }


def _write_config(tmp_path: Path) -> tuple[Path, Path]:
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(
        yaml.dump(
            {
                "sources": [
                    {"name": "acme", "url": "https://acme.example/jobs", "strategy": "static"}
                ]
            }
        ),
        encoding="utf-8",
    )
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps({}), encoding="utf-8")
    return sources_path, rules_path


def _seed_existing_job(db_path: Path) -> None:
    with JobStore(db_path) as store:
        run_id = store.begin_run()
        store.upsert_jobs(
            [
                {
                    "dedupe_key": _EXISTING_URL,
                    "source_name": "acme",
                    "title": "Existing Job",
                    "detail_url": _EXISTING_URL,
                }
            ],
            run_id,
        )
        store.finish_run(run_id)


def _stored(db_path: Path) -> dict[str, dict]:
    with JobStore(db_path) as store:
        return {r["dedupe_key"]: r for r in store.all_jobs()}


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    extractor_result: list[dict[str, str]],
    allow_empty_delist: bool = False,
    delist_after: int = 2,
    seed: bool = True,
):
    sources_path, rules_path = _write_config(tmp_path)
    db_path = tmp_path / "jobs.sqlite3"
    if seed and not db_path.exists():
        _seed_existing_job(db_path)

    monkeypatch.setattr(
        pipeline_mod, "get_extractor", lambda name: lambda url, fetch_fn: list(extractor_result)
    )
    monkeypatch.setattr(pipeline_mod, "fetch_text", lambda url: "")

    summary = run_pipeline(
        sources_path=sources_path,
        rules_path=rules_path,
        out_db_path=db_path,
        cache_path=db_path.parent / "http_cache.sqlite3",
        allow_empty_delist=allow_empty_delist,
        delist_after=delist_after,
        # No robots.txt lookup: these fetchers are stubs and the host does not
        # exist. WP10's robots check is covered against a real origin in
        # tests/test_politeness.py and tests/test_source_health.py.
        check_robots=False,
    )
    return summary, _stored(db_path)


def test_empty_scrape_does_not_delist_or_count_a_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary, stored = _run(tmp_path, monkeypatch, extractor_result=[])
    assert summary.rows_delisted == 0
    assert stored[_EXISTING_URL]["status"] == "new"
    assert stored[_EXISTING_URL]["misses"] == 0, "an untrusted zero-row scrape is not a miss"


def test_empty_scrape_logs_error_naming_the_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger=pipeline_mod.logger.name):
        _run(tmp_path, monkeypatch, extractor_result=[])
    assert any("acme" in r.message and r.levelno == logging.ERROR for r in caplog.records)


def test_empty_scrape_with_flag_delists_immediately_but_keeps_the_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary, stored = _run(tmp_path, monkeypatch, extractor_result=[], allow_empty_delist=True)
    assert summary.rows_delisted == 1
    assert stored[_EXISTING_URL]["status"] == "delisted", "marked, not deleted"


def test_missing_job_survives_the_first_run_and_delists_on_the_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fresh = [_job("acme", "New Job", "https://acme.example/jobs/new")]

    first, stored = _run(tmp_path, monkeypatch, extractor_result=fresh)
    assert first.rows_delisted == 0, "one miss could be a listing hiccup"
    assert stored[_EXISTING_URL]["status"] == "new"
    assert stored[_EXISTING_URL]["misses"] == 1

    second, stored = _run(tmp_path, monkeypatch, extractor_result=fresh)
    assert second.rows_delisted == 1
    assert stored[_EXISTING_URL]["status"] == "delisted"
    assert stored["https://acme.example/jobs/new"]["status"] == "new"


def test_delist_after_is_configurable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fresh = [_job("acme", "New Job", "https://acme.example/jobs/new")]
    summary, stored = _run(tmp_path, monkeypatch, extractor_result=fresh, delist_after=1)
    assert summary.rows_delisted == 1
    assert stored[_EXISTING_URL]["status"] == "delisted"


def test_reappearance_between_misses_resets_the_streak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gone = [_job("acme", "New Job", "https://acme.example/jobs/new")]
    back = gone + [_job("acme", "Existing Job", _EXISTING_URL)]

    _run(tmp_path, monkeypatch, extractor_result=gone)  # miss 1
    _run(tmp_path, monkeypatch, extractor_result=back)  # sighted again
    summary, stored = _run(tmp_path, monkeypatch, extractor_result=gone)  # miss 1 again

    assert summary.rows_delisted == 0
    assert stored[_EXISTING_URL]["status"] == "new"
    assert stored[_EXISTING_URL]["misses"] == 1
