"""Load `sources.yaml` and `rules.json` from disk."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


def package_dir() -> Path:
    """Directory containing the `job_scraper` package."""
    return Path(__file__).resolve().parent


def default_project_root() -> Path:
    """Parent of the package (project root with `requirements.txt`, `data/`, etc.)."""
    return package_dir().parent


def default_sources_path() -> Path:
    return package_dir() / "config" / "sources.yaml"


def default_rules_path() -> Path:
    return package_dir() / "config" / "rules.json"


def default_title_keywords_path() -> Path:
    return package_dir() / "config" / "title_exclude_keywords.csv"


def default_data_dir() -> Path:
    """Generated output (jobs.csv/xlsx, jobs_sources.csv) — regenerated every run."""
    return default_project_root() / "data"


def default_curated_dir() -> Path:
    """Hand-maintained data that no run regenerates: the blocklist, excluded sources."""
    return default_data_dir() / "curated"


def default_jobs_csv_path() -> Path:
    return default_data_dir() / "jobs.csv"


def default_jobs_xlsx_path() -> Path:
    return default_data_dir() / "jobs.xlsx"


def _require(p: Path) -> Path:
    """Fail with a copy-pasteable fix when a config file hasn't been created yet.

    `sources.yaml` and `rules.json` are gitignored — a fresh clone gets the
    `.example` versions and is expected to copy them.
    """
    if not p.is_file():
        example = p.with_suffix(f".example{p.suffix}")
        if example.is_file():
            raise FileNotFoundError(
                f"{p} not found. Start from the shipped example:\n"
                f"    cp {example} {p}"
            )
        raise FileNotFoundError(f"{p} not found.")
    return p


def load_sources(path: Path | None = None) -> list[dict[str, Any]]:
    p = _require(path or default_sources_path())
    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "sources" not in data:
        raise ValueError(f"Invalid sources file: {p}")
    sources = data["sources"]
    if not isinstance(sources, list):
        raise ValueError(f"'sources' must be a list in {p}")
    return sources


def load_rules(path: Path | None = None) -> dict[str, Any]:
    p = _require(path or default_rules_path())
    with p.open(encoding="utf-8") as f:
        return json.load(f)
