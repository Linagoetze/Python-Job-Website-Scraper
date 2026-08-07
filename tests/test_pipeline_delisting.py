"""Tests for BUG 2: a source returning zero rows must not delist its stored jobs."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

import pytest
import yaml

from job_scraper import pipeline as pipeline_mod
from job_scraper.pipeline import run_pipeline
from job_scraper.storage.csv_store import FIELDNAMES, _rewrite_file


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


def _seed_existing_csv(path: Path, source: str, url: str) -> None:
    _rewrite_file(
        path,
        [
            {k: "" for k in FIELDNAMES}
            | {
                "source_name": source,
                "title": "Existing Job",
                "detail_hyperlink": f'=HYPERLINK("{url}")',
                "run_id": "1",
            }
        ],
    )


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    extractor_result: list[dict[str, str]],
    allow_empty_delist: bool = False,
):
    sources_path, rules_path = _write_config(tmp_path)
    out_csv = tmp_path / "jobs.csv"
    _seed_existing_csv(out_csv, "acme", "https://acme.example/jobs/existing")

    monkeypatch.setattr(
        pipeline_mod, "get_extractor", lambda name: lambda url, fetch_fn: extractor_result
    )
    monkeypatch.setattr(pipeline_mod, "load_blocklist_keys", lambda: set())
    monkeypatch.setattr(pipeline_mod, "fetch_text", lambda url: "")

    summary = run_pipeline(
        sources_path=sources_path,
        rules_path=rules_path,
        out_csv_path=out_csv,
        allow_empty_delist=allow_empty_delist,
    )
    with out_csv.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    return summary, rows


def test_empty_scrape_does_not_delist_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary, rows = _run(tmp_path, monkeypatch, extractor_result=[])
    assert summary.rows_delisted == 0
    assert len(rows) == 1
    assert rows[0]["title"] == "Existing Job"


def test_empty_scrape_logs_error_naming_the_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger=pipeline_mod.logger.name):
        _run(tmp_path, monkeypatch, extractor_result=[])
    assert any("acme" in r.message and r.levelno == logging.ERROR for r in caplog.records)


def test_empty_scrape_delists_when_flag_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary, rows = _run(tmp_path, monkeypatch, extractor_result=[], allow_empty_delist=True)
    assert summary.rows_delisted == 1
    assert rows == []


def test_nonempty_scrape_still_delists_missing_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fresh = [_job("acme", "New Job", "https://acme.example/jobs/new")]
    summary, rows = _run(tmp_path, monkeypatch, extractor_result=fresh)
    assert summary.rows_delisted == 1
    titles = {r["title"] for r in rows}
    assert titles == {"New Job"}
