"""Tests for BUG 1: full-file rewrites in csv_store.py must be atomic."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from job_scraper.storage.csv_store import FIELDNAMES, _rewrite_file


def _row(title: str) -> dict[str, str]:
    return {k: "" for k in FIELDNAMES} | {"title": title, "run_id": "1"}


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def test_rewrite_file_no_temp_left_behind(tmp_path: Path) -> None:
    p = tmp_path / "jobs.csv"
    _rewrite_file(p, [_row("A"), _row("B")])
    leftovers = [f for f in os.listdir(tmp_path) if f != "jobs.csv"]
    assert leftovers == []
    assert [r["title"] for r in _read(p)] == ["A", "B"]


def test_rewrite_file_survives_a_failed_write(tmp_path: Path) -> None:
    """A crash mid-write must leave the original file untouched, not truncated."""
    p = tmp_path / "jobs.csv"
    _rewrite_file(p, [_row("Original")])

    with patch("csv.DictWriter.writerows", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            _rewrite_file(p, [_row("New")])

    # The live file must still hold the pre-crash content, never truncated.
    assert [r["title"] for r in _read(p)] == ["Original"]
    # And no stray temp file left in the directory.
    leftovers = [f for f in os.listdir(tmp_path) if f != "jobs.csv"]
    assert leftovers == []


def test_rewrite_file_uses_os_replace(tmp_path: Path) -> None:
    p = tmp_path / "jobs.csv"
    _rewrite_file(p, [_row("A")])
    with patch("os.replace", side_effect=os.replace) as mock_replace:
        _rewrite_file(p, [_row("B")])
    assert mock_replace.called
    src_arg = mock_replace.call_args[0][0]
    assert str(src_arg).startswith(str(p)) and str(src_arg) != str(p)
