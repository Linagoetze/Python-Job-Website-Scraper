"""rules.json's scoring_enabled gates the LLM stage; --score forces it on.

score_new_jobs must not even be called when scoring is off, since a call is
what previously produced an ERROR log for a stage the owner deliberately
turned off (WP7 follow-up). run_pipeline and write_xlsx are stubbed out so
this only exercises the gating logic in job_scraper.run.main.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from job_scraper import run as run_module
from job_scraper.pipeline import RunSummary

_EMPTY_SUMMARY = RunSummary(
    sources_total=0,
    sources_skipped=0,
    sources_processed=0,
    jobs_extracted=0,
    jobs_kept=0,
    jobs_keyword_excluded=0,
    jobs_language_excluded=0,
    jobs_non_english_excluded=0,
    jobs_title_excluded=0,
    jobs_blocklist_excluded=0,
    jobs_already_stored=0,
    jobs_new_checked=0,
    jobs_stored_rechecked=0,
    jobs_detail_excluded=0,
    jobs_phd_excluded=0,
    jobs_hybrid_excluded=0,
    jobs_kept_new=0,
    rows_written=0,
    rows_delisted=0,
)


def _run_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    scoring_enabled: bool,
    extra_args: list[str] | None = None,
) -> list[Path]:
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps({"scoring_enabled": scoring_enabled}), encoding="utf-8")
    db_path = tmp_path / "jobs.sqlite3"
    xlsx_path = tmp_path / "jobs.xlsx"

    score_calls: list[Path] = []

    monkeypatch.setattr(run_module, "run_pipeline", lambda **kwargs: _EMPTY_SUMMARY)
    monkeypatch.setattr(run_module, "write_xlsx", lambda *a, **k: 0)

    def fake_score_new_jobs(db, **kwargs):
        score_calls.append(db)
        from job_scraper.scoring import ScoringSummary

        return ScoringSummary(0, 0, 0, 0.0)

    monkeypatch.setattr(run_module, "score_new_jobs", fake_score_new_jobs)

    argv = [
        "run.py",
        "--sources",
        str(tmp_path / "sources.yaml"),
        "--rules",
        str(rules_path),
        "--output-db",
        str(db_path),
        "--output-xlsx",
        str(xlsx_path),
        *(extra_args or []),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    run_module.main()
    return score_calls


def test_scoring_off_by_default_never_calls_score_new_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _run_main(tmp_path, monkeypatch, scoring_enabled=False)
    assert calls == []


def test_scoring_enabled_in_rules_json_calls_score_new_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _run_main(tmp_path, monkeypatch, scoring_enabled=True)
    assert len(calls) == 1


def test_score_flag_forces_scoring_on_despite_rules_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _run_main(tmp_path, monkeypatch, scoring_enabled=False, extra_args=["--score"])
    assert len(calls) == 1
