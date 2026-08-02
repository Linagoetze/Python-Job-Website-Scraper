"""Tests for the permanent job blocklist."""

from __future__ import annotations

import csv
from pathlib import Path

from job_scraper.blocklist import (
    append_to_blocklist,
    load_blocklist_keys,
)
from job_scraper.storage.csv_store import FIELDNAMES, clean_existing_rows


def _job_row(source: str, title: str, *, url: str, run_id: str = "1") -> dict[str, str]:
    return {
        "source_name": source,
        "title": title,
        "company": "",
        "location": "",
        "detail_hyperlink": f'=HYPERLINK("{url}")' if url else "",
        "apply_hyperlink": "",
        "run_id": run_id,
    }


def _write_jobs(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write("﻿")
        w = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _read_jobs(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def test_append_and_load_roundtrip(tmp_path: Path) -> None:
    bl = tmp_path / "blocklist.csv"
    rows = [
        _job_row("greenhouse", "Project Manager", url="https://x/jobs/1"),
        _job_row("axis", "Coordinator", url="https://x/jobs/2"),
    ]
    added = append_to_blocklist(bl, rows)
    assert added == 2
    keys = load_blocklist_keys(bl)
    assert keys == {"https://x/jobs/1", "https://x/jobs/2"}


def test_append_is_idempotent(tmp_path: Path) -> None:
    bl = tmp_path / "blocklist.csv"
    rows = [_job_row("greenhouse", "Project Manager", url="https://x/jobs/1")]
    assert append_to_blocklist(bl, rows) == 1
    # Re-adding the same posting adds nothing.
    assert append_to_blocklist(bl, rows) == 0
    assert len(load_blocklist_keys(bl)) == 1


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_blocklist_keys(tmp_path / "nope.csv") == set()


def test_oatly_slug_variants_share_one_key(tmp_path: Path) -> None:
    bl = tmp_path / "blocklist.csv"
    # Same numeric job id, different slug → one canonical key.
    append_to_blocklist(
        bl, [_job_row("oatly", "Brand Lead", url="https://careers.oatly.com/en-GB/jobs/12345-brand-lead")]
    )
    keys = load_blocklist_keys(bl)
    assert keys == {"oatly:job:12345"}


def test_clean_existing_rows_drops_blocklisted(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs.csv"
    _write_jobs(jobs, [
        _job_row("greenhouse", "Project Manager", url="https://x/jobs/1"),
        _job_row("axis", "Coordinator", url="https://x/jobs/2"),
    ])
    rules = {"locations": [], "include_keywords": [], "exclude_keywords": [],
             "match_in": "title_and_description", "seniority_filter_enabled": False}
    counts = clean_existing_rows(
        jobs, rules, [], blocklist_keys={"https://x/jobs/1"}
    )
    assert counts["blocklist"] == 1
    remaining = _read_jobs(jobs)
    assert len(remaining) == 1
    assert "jobs/2" in remaining[0]["detail_hyperlink"]
