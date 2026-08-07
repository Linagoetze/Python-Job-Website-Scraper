"""Capture real career-page responses as test fixtures.

Fetches the listing URL for named sources with the project's own fetchers
(`fetch_text` for static sources, `fetch_rendered` for dynamic ones) and saves
the raw response under `tests/fixtures/`. Run this by hand whenever a fixture
goes stale; see docs/REFACTOR-PLAN.md, WP0, for the how-to.

Usage:
    python scripts/capture_fixtures.py <source_name> [<source_name> ...]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_scraper.config_loader import default_project_root, load_sources
from job_scraper.http import fetch_rendered, fetch_text

FIXTURES_DIR = default_project_root() / "tests" / "fixtures"
PAUSE_SECONDS = 3

logger = logging.getLogger(__name__)


def _guess_extension(text: str) -> str:
    """Decide whether *text* is JSON or HTML by attempting to parse it as JSON."""
    stripped = text.lstrip()
    if stripped[:1] in ("{", "["):
        try:
            json.loads(stripped)
            return "json"
        except json.JSONDecodeError:
            pass
    return "html"


def capture_one(source: dict[str, str]) -> tuple[bool, str]:
    """Fetch and save the fixture for one source. Returns (ok, message)."""
    name = source["name"]
    url = source["url"]
    strategy = source.get("strategy", "static")
    fetch = fetch_rendered if strategy == "dynamic" else fetch_text
    try:
        text = fetch(url)
    except Exception as exc:  # noqa: BLE001 — any fetch failure is reported, not raised
        return False, f"{name}: FAILED ({exc})"

    ext = _guess_extension(text)
    dest = FIXTURES_DIR / f"{name}.{ext}"
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(dest)

    size = dest.stat().st_size
    return True, f"{name}: saved {dest.relative_to(default_project_root())} ({size:,} bytes)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="+", help="source names from sources.yaml")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    sources_by_name = {s["name"]: s for s in load_sources()}
    unknown = [n for n in args.names if n not in sources_by_name]
    if unknown:
        parser.error(f"not in sources.yaml: {', '.join(unknown)}")

    exit_code = 0
    for i, name in enumerate(args.names):
        ok, message = capture_one(sources_by_name[name])
        print(message)
        if not ok:
            exit_code = 1
        if i < len(args.names) - 1:
            time.sleep(PAUSE_SECONDS)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
