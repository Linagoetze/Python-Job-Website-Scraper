"""WP3: `company` is stamped from `sources.yaml` onto rows the extractor left
blank, but never overwrites a value the extractor already set.

No network: `get_extractor`, `fetch_text` and `fetch_rendered` are all
replaced on the pipeline module.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from job_scraper import pipeline as pipeline_mod
from job_scraper.pipeline import run_pipeline
from job_scraper.storage.db import JobStore

_LISTING = "https://acme.example/jobs"

# One job with no company (single-employer source, extractor never sets it),
# one job where the extractor already set a company (as an aggregator would).
_EXTRACTED = [
    {
        "source_name": "acme",
        "title": "Data Analyst",
        "company": "",
        "location": "Berlin",
        "listing_url": _LISTING,
        "detail_url": f"{_LISTING}/blank",
        "apply_url": "",
        "raw_snippet": "Data Analyst Berlin",
    },
    {
        "source_name": "acme",
        "title": "Data Scientist",
        "company": "Extractor Co",
        "location": "Berlin",
        "listing_url": _LISTING,
        "detail_url": f"{_LISTING}/set",
        "apply_url": "",
        "raw_snippet": "Data Scientist Berlin",
    },
]

_RULES = {"locations": ["Berlin"]}


def _fake_fetch(url: str, *args: Any, **kwargs: Any) -> str:
    return "A great opportunity for someone early in their career."


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(
        yaml.dump(
            {
                "sources": [
                    {
                        "name": "acme",
                        "url": _LISTING,
                        "strategy": "static",
                        "company": "Configured Co",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps(_RULES), encoding="utf-8")

    monkeypatch.setattr(
        pipeline_mod, "get_extractor", lambda name: lambda url, fetch_fn: list(_EXTRACTED)
    )
    monkeypatch.setattr(pipeline_mod, "fetch_text", _fake_fetch)
    monkeypatch.setattr(pipeline_mod, "fetch_rendered", _fake_fetch)
    return tmp_path


def _rows(tmp_path: Path) -> list[dict[str, Any]]:
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        return store.all_jobs()


def test_configured_company_fills_in_where_extractor_left_it_blank(env: Path) -> None:
    run_pipeline(
        sources_path=env / "sources.yaml",
        rules_path=env / "rules.json",
        out_db_path=env / "jobs.sqlite3",
        cache_path=env / "http_cache.sqlite3",
        # No robots.txt lookup: these fetchers are stubs and the host does not
        # exist. WP10's robots check is covered against a real origin in
        # tests/test_politeness.py and tests/test_source_health.py.
        check_robots=False,
    )

    by_title = {r["title"]: r["company"] for r in _rows(env)}
    assert by_title["Data Analyst"] == "Configured Co"


def test_extractor_company_wins_over_configured_company(env: Path) -> None:
    run_pipeline(
        sources_path=env / "sources.yaml",
        rules_path=env / "rules.json",
        out_db_path=env / "jobs.sqlite3",
        cache_path=env / "http_cache.sqlite3",
        # No robots.txt lookup: these fetchers are stubs and the host does not
        # exist. WP10's robots check is covered against a real origin in
        # tests/test_politeness.py and tests/test_source_health.py.
        check_robots=False,
    )

    by_title = {r["title"]: r["company"] for r in _rows(env)}
    assert by_title["Data Scientist"] == "Extractor Co"
