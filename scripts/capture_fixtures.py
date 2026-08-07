"""Capture real career-page responses as test fixtures.

Saves the response the source's *extractor* actually consumes, not whatever
happens to live at the listing URL. Several extractors (Greenhouse, Lever,
SmartRecruiters) ignore the listing page entirely and call a JSON API instead,
and paginating extractors ask for `?page=1` or `?startrow=0` rather than the
bare URL. Rather than duplicating that URL-building here, the capture runs the
real extractor with a recording fetcher and keeps the first response it asks
for; see `capture_one`.

Captured HTML is sanitised before it is written: third-party inline scripts
(analytics, front-end config) are stripped, and only scripts carrying a job-data
payload are kept. See `sanitise_html` for why that distinction matters.

Run this by hand whenever a fixture goes stale; see docs/REFACTOR-PLAN.md, WP0,
for the how-to.

Usage:
    python scripts/capture_fixtures.py <source_name> [<source_name> ...]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_scraper.config_loader import default_project_root, load_sources
from job_scraper.extractors.registry import get_extractor
from job_scraper.http import fetch_rendered, fetch_text

FIXTURES_DIR = default_project_root() / "tests" / "fixtures"
PAUSE_SECONDS = 3

logger = logging.getLogger(__name__)

# Script elements whose `type` is a data MIME type are payloads, never
# executable third-party code, so they are always safe to keep.
_KEEP_SCRIPT_TYPES = frozenset({"application/ld+json", "application/json"})

# Substrings that mark a script as carrying the page's own job data. Matched
# against both the script body and its `id`, because Next.js puts the marker in
# the attribute (`<script id="__NEXT_DATA__" type="application/json">`) while
# Ashby puts it in the body (`window.__appData = {…}`).
#
# This list is the reason the sanitiser is not simply "remove every script":
# ashby.py finds jobs by searching the raw HTML for `window.__appData` and
# decoding the JSON that follows, so stripping all scripts would silently
# reduce every Ashby fixture to zero jobs.
_DATA_MARKERS = (
    "window.__appData",
    "__NEXT_DATA__",
    "__NUXT__",
    "__INITIAL_STATE__",
    "__APOLLO_STATE__",
)


def _carries_job_data(tag: Any) -> bool:
    """True if this <script> holds a data payload rather than executable code."""
    if (tag.get("type") or "").strip().lower() in _KEEP_SCRIPT_TYPES:
        return True
    haystack = f"{tag.get('id') or ''}\n{tag.string or tag.get_text()}"
    return any(marker in haystack for marker in _DATA_MARKERS)


def sanitise_html(html: str) -> str:
    """Strip everything from a captured page that is not job data.

    Career pages embed third-party front-end config, and that config contains
    public API keys which secret scanners flag on sight. Nothing in this project
    parses those scripts, so they are dead weight in a fixture — but scripts
    holding the page's own job data are load-bearing and must survive.

    Hidden CSRF token inputs get the same treatment: they look exactly like
    credentials to a scanner, no extractor reads form inputs, and a token
    captured months ago is meaningless anyway.
    """
    soup = BeautifulSoup(html, "lxml")

    for tag in soup.find_all("script"):
        if not _carries_job_data(tag):
            tag.decompose()

    for tag in soup.find_all("input", attrs={"type": "hidden"}):
        identifier = f"{tag.get('name') or ''} {tag.get('id') or ''}".lower()
        if "token" in identifier and tag.has_attr("value"):
            tag["value"] = ""

    return str(soup)


def single_response_fetch(text: str) -> Callable[..., str]:
    """A fetcher that serves *text* once, then empty responses.

    A fixture is a single response, so replaying it for every page a paginating
    extractor asks for would loop until the extractor's own page cap. Returning
    an empty body for later pages ends the loop the way a real last page does.
    """
    served = False

    def fetch(url: str, *args: Any, **kwargs: Any) -> str:
        nonlocal served
        if served:
            return ""
        served = True
        return text

    return fetch


class _CaptureComplete(Exception):
    """Raised to stop an extractor once its first request has been recorded."""


def capture_one(source: dict[str, str]) -> tuple[bool, str]:
    """Fetch and save the fixture for one source. Returns (ok, message)."""
    name = source["name"]
    listing_url = source["url"]
    strategy = source.get("strategy", "static")
    fetch = fetch_rendered if strategy == "dynamic" else fetch_text
    extractor = get_extractor(name)

    recorded: list[tuple[str, str]] = []

    def recording_fetch(url: str, *args: Any, **kwargs: Any) -> str:
        recorded.append((url, fetch(url, *args, **kwargs)))
        raise _CaptureComplete

    # Carry the rendering mark through the wrapper, so extractors that add a
    # selector wait for JS-heavy pages still do so during capture.
    recording_fetch.renders = getattr(fetch, "renders", False)  # type: ignore[attr-defined]

    try:
        if extractor is None:
            # No registry entry: fall back to the listing URL as-is.
            recorded.append((listing_url, fetch(listing_url)))
        else:
            try:
                extractor(listing_url, recording_fetch)
            except _CaptureComplete:
                pass
    except Exception as exc:  # noqa: BLE001 — any fetch failure is reported, not raised
        return False, f"{name}: FAILED ({exc})"

    if not recorded:
        return False, f"{name}: FAILED (extractor made no request)"

    fetched_url, text = recorded[0]
    ext = _guess_extension(text)
    if ext == "html":
        text = sanitise_html(text)

    dest = FIXTURES_DIR / f"{name}.{ext}"
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(dest)

    stale = _remove_stale_sibling(name, ext)

    size = dest.stat().st_size
    jobs = _verify(extractor, listing_url, text)
    rel = dest.relative_to(default_project_root())
    message = f"{name}: saved {rel} ({size:,} bytes, {jobs} jobs) from {fetched_url}"
    if stale:
        message += f"; removed stale {stale}"
    return True, message


def _remove_stale_sibling(name: str, ext: str) -> str | None:
    """Delete a previous fixture for *name* saved under the other extension.

    A source changes artefact type when its extractor is corrected — givewell
    went from listing HTML to the Greenhouse API's JSON. Leaving the old file
    behind is a trap: it looks like a valid fixture and nothing parses it.
    Only ever removes the sibling this script itself would have written.
    """
    other = "json" if ext == "html" else "html"
    sibling = FIXTURES_DIR / f"{name}.{other}"
    if not sibling.exists():
        return None
    sibling.unlink()
    return sibling.name


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


def _verify(extractor: Any, listing_url: str, text: str) -> str:
    """Re-parse what was just saved, so an empty or wrong capture is visible now.

    A cookie wall or an unrendered dynamic page is a successful HTTP response of
    a plausible size; only running the extractor over it shows it is useless.
    """
    if extractor is None:
        return "?"
    try:
        return str(len(extractor(listing_url, single_response_fetch(text))))
    except Exception as exc:  # noqa: BLE001 — a parse failure is a result, not a crash
        return f"UNPARSEABLE: {exc}"


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
