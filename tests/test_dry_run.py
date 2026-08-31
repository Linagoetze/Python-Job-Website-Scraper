"""WP10: `--dry-run` fetches and filters, then writes nothing.

"Nothing" is the whole assertion, and it is checked from both directions: the
store afterwards is byte-for-byte what it was, and the summary the run prints is
the same one a real run would have printed. A dry run that quietly wrote a run
row, a drop log or a sources csv would be worse than no dry run at all, since
the owner would trust it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from job_scraper import pipeline as pipeline_mod
from job_scraper.pipeline import RunSummary, run_pipeline
from job_scraper.run import format_summary
from job_scraper.storage.db import JobStore

_SOURCE = "acme"
_LISTING = "https://acme.example/jobs"

_EXTRACTED = [
    {
        "source_name": _SOURCE,
        "title": title,
        "company": "",
        "location": location,
        "listing_url": _LISTING,
        "detail_url": f"{_LISTING}/{slug}",
        "apply_url": "",
        "raw_snippet": f"{title} {location}",
    }
    for title, location, slug in [
        ("Data Analyst", "Berlin", "kept"),
        ("Data Analyst", "Lisbon", "wrong-city"),
    ]
]


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "sources.yaml").write_text(
        yaml.dump({"sources": [{"name": _SOURCE, "url": _LISTING, "strategy": "static"}]}),
        encoding="utf-8",
    )
    (tmp_path / "rules.json").write_text(json.dumps({"locations": ["Berlin"]}), encoding="utf-8")
    monkeypatch.setattr(
        pipeline_mod, "get_extractor", lambda name: lambda url, fetch_fn: list(_EXTRACTED)
    )
    monkeypatch.setattr(pipeline_mod, "fetch_text", lambda url, *a, **k: "Entry level role.")
    monkeypatch.setattr(pipeline_mod, "fetch_rendered", lambda url, *a, **k: "")
    return tmp_path


def _run(tmp_path: Path, *, dry_run: bool = False) -> RunSummary:
    return run_pipeline(
        sources_path=tmp_path / "sources.yaml",
        rules_path=tmp_path / "rules.json",
        out_db_path=tmp_path / "jobs.sqlite3",
        dry_run=dry_run,
        check_robots=False,  # stubbed fetchers, and the host does not exist
    )


def _counts(db_path: Path) -> dict[str, int]:
    with JobStore(db_path) as store:
        conn = store._c()
        return {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("jobs", "runs", "run_exclusions", "source_health")
        }


def test_a_dry_run_leaves_the_store_exactly_as_it_found_it(env: Path) -> None:
    _run(env)  # a real run first, so there is history a bug could damage
    before = _counts(env / "jobs.sqlite3")

    _run(env, dry_run=True)

    assert _counts(env / "jobs.sqlite3") == before


def test_a_dry_run_on_a_fresh_store_stores_no_job(env: Path) -> None:
    _run(env, dry_run=True)
    assert _counts(env / "jobs.sqlite3")["jobs"] == 0


def test_a_dry_run_writes_no_sources_csv(env: Path) -> None:
    _run(env, dry_run=True)
    assert not (env / "jobs_sources.csv").exists()


def test_the_counts_are_the_ones_a_real_run_would_have_reported(env: Path) -> None:
    """The point of the flag: see what a rules change does before it lands."""
    dry = _run(env, dry_run=True)
    real = _run(env)

    assert dry.dry_run and not real.dry_run
    assert (dry.jobs_extracted, dry.jobs_kept, dry.rows_written) == (
        real.jobs_extracted,
        real.jobs_kept,
        real.rows_written,
    )
    assert dry.exclusions_logged == real.exclusions_logged


def test_the_run_after_a_dry_run_behaves_as_if_it_never_happened(env: Path) -> None:
    """Nothing accrues: no run row means no miss counted towards delisting."""
    _run(env)
    _run(env, dry_run=True)
    with JobStore(env / "jobs.sqlite3") as store:
        assert [row["misses"] for row in store.jobs_with_status(("new",))] == [0]


def test_the_summary_says_so_and_only_then(env: Path) -> None:
    """A run that wrote nothing must not read like one that wrote something."""
    assert "DRY RUN" in format_summary(_run(env, dry_run=True))
    assert "DRY RUN" not in format_summary(_run(env))
