"""`--dry-run` in `job_scraper.run.main` — the half that guards the spreadsheet.

tests/test_dry_run.py proves the pipeline writes no row. That is the larger
half, but not the half that protects `jobs.xlsx`: the export and the paid
scoring call live in `main`, after `run_pipeline` returns, and nothing pinned
them. Reorder those two lines and a dry run silently overwrites a spreadsheet
and spends tokens, with every database test still green.

Same shape as tests/test_run_scoring_gate.py next door: `run_pipeline`,
`write_xlsx` and `score_new_jobs` are stubbed, so this exercises only the
decisions `main` itself makes.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from job_scraper import run as run_module
from job_scraper.storage.db import SourceDrop
from tests.test_run_scoring_gate import _EMPTY_SUMMARY


def _run_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    dry_run: bool,
    scoring_enabled: bool = True,
) -> dict[str, Any]:
    """Run `main` and report what it asked the outside world to do."""
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps({"scoring_enabled": scoring_enabled}), encoding="utf-8")
    xlsx_path = tmp_path / "jobs.xlsx"
    xlsx_path.write_text("the spreadsheet you have open", encoding="utf-8")

    seen: dict[str, Any] = {"xlsx": [], "scored": [], "pipeline": []}

    def fake_pipeline(**kwargs: Any) -> Any:
        seen["pipeline"].append(kwargs)
        return replace(_EMPTY_SUMMARY, dry_run=kwargs.get("dry_run", False))

    def fake_write_xlsx(db: Path, out: Path, **kwargs: Any) -> int:
        seen["xlsx"].append(out)
        out.write_text("overwritten", encoding="utf-8")  # what the real one would do
        return 0

    def fake_score(db: Path, **kwargs: Any) -> Any:
        from job_scraper.scoring import ScoringSummary

        seen["scored"].append(db)
        return ScoringSummary(0, 0, 0, 0.0)

    monkeypatch.setattr(run_module, "run_pipeline", fake_pipeline)
    monkeypatch.setattr(run_module, "write_xlsx", fake_write_xlsx)
    monkeypatch.setattr(run_module, "score_new_jobs", fake_score)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run.py",
            "--sources", str(tmp_path / "sources.yaml"),
            "--rules", str(rules_path),
            "--output-db", str(tmp_path / "jobs.sqlite3"),
            "--output-xlsx", str(xlsx_path),
            *(["--dry-run"] if dry_run else []),
        ],
    )
    run_module.main()
    seen["xlsx_content"] = xlsx_path.read_text(encoding="utf-8")
    return seen


def test_a_dry_run_does_not_touch_the_spreadsheet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one that matters: jobs.xlsx is what the owner may have open."""
    seen = _run_main(tmp_path, monkeypatch, dry_run=True)
    assert seen["xlsx"] == []
    assert seen["xlsx_content"] == "the spreadsheet you have open"


def test_a_real_run_still_writes_the_spreadsheet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard must not have turned the export off for everyone."""
    seen = _run_main(tmp_path, monkeypatch, dry_run=False)
    assert len(seen["xlsx"]) == 1
    assert seen["xlsx_content"] == "overwritten"


def test_a_dry_run_spends_no_scoring_credits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scoring writes verdicts this run would discard, and bills for them anyway."""
    assert _run_main(tmp_path, monkeypatch, dry_run=True, scoring_enabled=True)["scored"] == []


def test_a_real_run_with_scoring_on_still_scores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _run_main(tmp_path, monkeypatch, dry_run=False, scoring_enabled=True)
    assert len(seen["scored"]) == 1


def test_the_flag_reaches_the_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _run_main(tmp_path, monkeypatch, dry_run=True)["pipeline"][0]["dry_run"] is True
    assert _run_main(tmp_path, monkeypatch, dry_run=False)["pipeline"][0]["dry_run"] is False


def test_a_dry_run_does_not_offer_the_drop_log_of_a_run_it_rolled_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Those exclusions went back with the transaction; the log holds the last real run's."""
    monkeypatch.setattr(
        run_module,
        "run_pipeline",
        lambda **kwargs: replace(_EMPTY_SUMMARY, exclusions_logged=42, dry_run=True),
    )
    monkeypatch.setattr(run_module, "write_xlsx", lambda *a, **k: 0)
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps({"scoring_enabled": False}), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run.py",
            "--rules", str(rules_path),
            "--output-db", str(tmp_path / "jobs.sqlite3"),
            "--output-xlsx", str(tmp_path / "jobs.xlsx"),
            "--dry-run",
        ],
    )
    run_module.main()
    out = capsys.readouterr()
    assert "job_scraper.drops" not in out.out
    assert "Output:" not in out.out
    assert "DRY RUN" in out.err  # the summary still says what happened


def test_the_health_block_survives_the_trip_through_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A warning the pipeline raised must reach stderr, not stop at the summary object."""
    monkeypatch.setattr(
        run_module,
        "run_pipeline",
        lambda **kwargs: replace(
            _EMPTY_SUMMARY, health_warnings=(SourceDrop("impactpool", 120, 4),)
        ),
    )
    monkeypatch.setattr(run_module, "write_xlsx", lambda *a, **k: 0)
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps({"scoring_enabled": False}), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run.py",
            "--rules", str(rules_path),
            "--output-db", str(tmp_path / "jobs.sqlite3"),
            "--output-xlsx", str(tmp_path / "jobs.xlsx"),
        ],
    )
    run_module.main()
    assert "!  impactpool: 4 rows this run, was 120" in capsys.readouterr().err
