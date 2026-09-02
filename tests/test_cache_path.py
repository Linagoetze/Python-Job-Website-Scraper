"""Where a run's response cache goes, and the net that keeps it out of `data/`.

WP10 found that `run_pipeline` opened `http_cache()` with no path, so every
pipeline test in the suite shared the owner's live cache — writing to it, and
pruning it on the way out. `cache_path` is the fix; these two tests pin both
halves of it, the parameter and the guard in `conftest.py` that catches the
next call site to forget it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from job_scraper import pipeline as pipeline_mod
from job_scraper.pipeline import run_pipeline

_SOURCE = "acme"
_LISTING = "https://acme.example/jobs"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "sources.yaml").write_text(
        yaml.dump({"sources": [{"name": _SOURCE, "url": _LISTING, "strategy": "static"}]}),
        encoding="utf-8",
    )
    (tmp_path / "rules.json").write_text(json.dumps({"locations": ["Berlin"]}), encoding="utf-8")

    job = {
        "source_name": _SOURCE,
        "title": "Data Analyst",
        "company": "Acme",
        "location": "Berlin",
        "listing_url": _LISTING,
        "detail_url": f"{_LISTING}/a",
        "apply_url": "",
        "raw_snippet": "Data Analyst Berlin",
    }

    def fake_fetch(url: str, *a: Any, **k: Any) -> str:
        return "A great opportunity, no experience required."

    monkeypatch.setattr(
        pipeline_mod, "get_extractor", lambda name: lambda url, fetch_fn: [dict(job)]
    )
    monkeypatch.setattr(pipeline_mod, "fetch_text", fake_fetch)
    monkeypatch.setattr(pipeline_mod, "fetch_rendered", fake_fetch)
    return tmp_path


def test_the_run_caches_where_it_was_told_to(env: Path) -> None:
    cache_path = env / "elsewhere" / "http_cache.sqlite3"
    run_pipeline(
        sources_path=env / "sources.yaml",
        rules_path=env / "rules.json",
        out_db_path=env / "jobs.sqlite3",
        cache_path=cache_path,
        check_robots=False,
    )
    assert cache_path.exists()


def test_a_run_that_names_no_cache_path_is_refused_under_test(env: Path) -> None:
    """The guard, not the pipeline: a real run may of course use the real cache.

    Without this the parameter above is a convention, and a convention is what
    the suite had before it emptied 37 MB of somebody's cache.
    """
    with pytest.raises(AssertionError, match="without a path"):
        run_pipeline(
            sources_path=env / "sources.yaml",
            rules_path=env / "rules.json",
            out_db_path=env / "jobs.sqlite3",
            check_robots=False,
        )
