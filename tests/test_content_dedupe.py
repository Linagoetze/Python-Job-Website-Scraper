"""Tests for content-based duplicate collapse (impactpool / jobsinlund)."""

from __future__ import annotations

import csv
from pathlib import Path

from job_scraper.storage.csv_store import (
    FIELDNAMES,
    _collapse_content_duplicates,
    _content_key,
    _normalize_text,
)


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write("﻿")
        w = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _row(source: str, title: str, *, company: str = "", location: str = "",
         url: str = "", run_id: str = "1") -> dict[str, str]:
    return {
        "source_name": source,
        "title": title,
        "company": company,
        "location": location,
        "detail_hyperlink": f'=HYPERLINK("{url}")' if url else "",
        "apply_hyperlink": "",
        "run_id": run_id,
    }


def test_same_title_different_url_keeps_newest(tmp_path: Path) -> None:
    p = tmp_path / "jobs.csv"
    _write(p, [
        _row("jobsinlund", "Shipping Coordinator", url="https://x/jobs/a", run_id="11"),
        _row("jobsinlund", "Shipping Coordinator", url="https://x/jobs/b", run_id="15"),
    ])
    removed = _collapse_content_duplicates(p)
    rows = _read(p)
    assert removed == 1
    assert len(rows) == 1
    assert rows[0]["run_id"] == "15"
    assert "jobs/b" in rows[0]["detail_hyperlink"]


def test_location_only_difference_collapses(tmp_path: Path) -> None:
    p = tmp_path / "jobs.csv"
    _write(p, [
        _row("jobsinlund", "Study Coordinator", location="Lund, se",
             url="https://x/jobs/a", run_id="11"),
        _row("jobsinlund", "Study Coordinator", location="Lunds Kommun, Sweden",
             url="https://x/jobs/b", run_id="11"),
    ])
    removed = _collapse_content_duplicates(p)
    assert removed == 1
    assert len(_read(p)) == 1


def test_different_company_not_collapsed(tmp_path: Path) -> None:
    p = tmp_path / "jobs.csv"
    _write(p, [
        _row("jobsinlund", "Product Manager", company="Acme",
             url="https://x/jobs/a", run_id="11"),
        _row("jobsinlund", "Product Manager", company="Globex",
             url="https://x/jobs/b", run_id="12"),
    ])
    removed = _collapse_content_duplicates(p)
    assert removed == 0
    assert len(_read(p)) == 2


def test_non_allowlisted_source_untouched(tmp_path: Path) -> None:
    p = tmp_path / "jobs.csv"
    _write(p, [
        _row("oatly", "Engineer", url="https://x/jobs/a", run_id="1"),
        _row("oatly", "Engineer", url="https://x/jobs/b", run_id="2"),
    ])
    removed = _collapse_content_duplicates(p)
    assert removed == 0
    assert len(_read(p)) == 2


def test_normalization_equivalence() -> None:
    assert _normalize_text("Division Manager Food & Pharma") == "division manager food pharma"
    assert (
        _normalize_text("M365 Copilot &amp; Compliance konsult.")
        == "m365 copilot compliance konsult"
    )
    assert _normalize_text("Elektronikingenjör") == "elektronikingenjör"


def test_html_entity_and_punctuation_collapse(tmp_path: Path) -> None:
    p = tmp_path / "jobs.csv"
    _write(p, [
        _row("jobsinlund", "Division Manager Food & Pharma",
             url="https://x/jobs/a", run_id="15"),
        _row("jobsinlund", "Division Manager Food &amp; Pharma",
             url="https://x/jobs/b", run_id="16"),
    ])
    removed = _collapse_content_duplicates(p)
    rows = _read(p)
    assert removed == 1
    assert rows[0]["run_id"] == "16"


def test_content_key_scoping() -> None:
    assert _content_key(_row("oatly", "X", url="u")) == ""
    assert _content_key(_row("jobsinlund", "")) == ""
    assert _content_key(_row("jobsinlund", "Shipping Coordinator")) != ""
