"""Postings a site lists with a blank title: skipped by the pipeline, and named
in the log only when the owner has not already rejected them.

Impactpool keeps blank-title postings up for weeks, and six of them were stored
as rejected after run 30 shifted their fields (docs/DECISIONS.md, 2026-09-25).
Naming those on every run said nothing new."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
import yaml

from job_scraper import pipeline as pipeline_mod
from job_scraper.pipeline import run_pipeline
from job_scraper.storage.db import JobStore

_REJECTED_URL = "https://acme.example/jobs/rejected-blank"
_NEW_BLANK_URL = "https://acme.example/jobs/new-blank"
_TITLED_URL = "https://acme.example/jobs/titled"


def _job(title: str, url: str) -> dict[str, str]:
    return {"source_name": "acme", "title": title, "company": "", "location": "", "detail_url": url}


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "sources.yaml").write_text(
        yaml.dump(
            {
                "sources": [
                    {"name": "acme", "url": "https://acme.example/jobs", "strategy": "static"}
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "rules.json").write_text(json.dumps({}), encoding="utf-8")
    db = tmp_path / "jobs.sqlite3"
    with JobStore(db) as store:
        run_id = store.begin_run()
        store.upsert_jobs(
            [
                {
                    "dedupe_key": _REJECTED_URL,
                    "source_name": "acme",
                    "title": "Example Org",
                    "detail_url": _REJECTED_URL,
                }
            ],
            run_id,
            initial_status="rejected",
        )
        store.finish_run(run_id)

    extracted = [_job("", _REJECTED_URL), _job(" ", _NEW_BLANK_URL), _job("Analyst", _TITLED_URL)]
    monkeypatch.setattr(
        pipeline_mod, "get_extractor", lambda name: lambda url, fetch_fn: list(extracted)
    )
    monkeypatch.setattr(pipeline_mod, "fetch_text", lambda url: "")
    return db


def _run(db_path: Path):
    return run_pipeline(
        sources_path=db_path.parent / "sources.yaml",
        rules_path=db_path.parent / "rules.json",
        out_db_path=db_path,
        cache_path=db_path.parent / "http_cache.sqlite3",
        check_robots=False,
    )


def test_only_blanks_not_already_rejected_are_named(
    db_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger="job_scraper.pipeline"):
        _run(db_path)
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    named = [m for m in infos if "blank title" in m]
    assert named == [f"acme: skipped 1 posting(s) listed with a blank title: {_NEW_BLANK_URL}"]
    assert "Skipped 1 blank-title posting(s) already stored as rejected" in caplog.text


def test_blanks_are_neither_counted_nor_stored(db_path: Path) -> None:
    summary = _run(db_path)
    assert summary.jobs_extracted == 1
    with JobStore(db_path) as store:
        rows = {r["dedupe_key"]: r for r in store.all_jobs()}
    assert _NEW_BLANK_URL not in rows
    # The rejected leftover is untouched: still rejected, still its old title.
    assert (rows[_REJECTED_URL]["status"], rows[_REJECTED_URL]["title"]) == (
        "rejected",
        "Example Org",
    )
