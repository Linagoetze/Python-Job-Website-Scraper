"""Tests for the fixture capture script and for the fixtures themselves.

Nothing here touches the network. The fixtures on disk are the input.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

from capture_fixtures import sanitise_html, single_response_fetch  # noqa: E402

from job_scraper.extractors import (  # noqa: E402
    ashby,
    greenhouse,
    impactpool,
    successfactors_html,
    teamtailor,
    workday,
)

FIXTURES_DIR = _PROJECT_ROOT / "tests" / "fixtures"


# --- the sanitiser ----------------------------------------------------------


def test_sanitiser_drops_tracking_but_keeps_app_data() -> None:
    """Only scripts without a job-data payload are removed.

    The kept case is not hypothetical: ashby.py locates jobs by searching the
    raw HTML for window.__appData, so a sanitiser that stripped every script
    would silently empty the kognity fixture.
    """
    html = """<html><head>
      <script>ga('send','pageview');window.ENV={"KEY":"public-value"};</script>
      <script>window.__appData = {"jobs":[{"title":"Engineer"}]};</script>
    </head><body><a href="/jobs/1">Engineer</a></body></html>"""

    out = sanitise_html(html)

    assert "ga('send','pageview')" not in out
    assert "window.ENV" not in out
    assert 'window.__appData = {"jobs":[{"title":"Engineer"}]};' in out
    assert '<a href="/jobs/1">' in out


# --- no secrets in fixtures -------------------------------------------------

# A bare mention of "api_key" in page copy is not a leak; an opaque value
# assigned to one is. These patterns look for the value, not the word.
_SECRET_PATTERNS = (
    re.compile(r"AIza[A-Za-z0-9_-]{35}"),
    re.compile(r"""api[_-]?key["'\s:=]{0,10}["']?[A-Za-z0-9_-]{16,}""", re.IGNORECASE),
    re.compile(r"""_token["'\s:=]{0,10}["']?[A-Za-z0-9_-]{16,}""", re.IGNORECASE),
)


@pytest.mark.parametrize("path", sorted(FIXTURES_DIR.iterdir()), ids=lambda p: p.name)
def test_fixture_contains_no_secret_shaped_values(path: Path) -> None:
    """Guards the GitHub secret-scanning incident that prompted the sanitiser."""
    text = path.read_text(encoding="utf-8", errors="replace")
    for pattern in _SECRET_PATTERNS:
        match = pattern.search(text)
        assert match is None, f"{path.name} matches {pattern.pattern}: {match.group(0)[:40]!r}"


# --- fixtures still parse ---------------------------------------------------
#
# This is the real guard on the sanitiser. Asserting that fixtures contain no
# <script> would be wrong: kognity's job data lives inside one.

_Extractor = Callable[[str, Callable[..., str]], list[dict[str, Any]]]

# source name -> (fixture filename, listing URL, extractor bound to its args)
_FIXTURE_CASES: dict[str, tuple[str, str, _Extractor]] = {
    "givewell": (
        "givewell.json",
        "https://job-boards.greenhouse.io/givewell",
        lambda url, fetch: greenhouse.extract(url, fetch, source_name="givewell"),
    ),
    "kognity": (
        "kognity.html",
        "https://jobs.ashbyhq.com/kognity",
        lambda url, fetch: ashby.extract(url, fetch, source_name="kognity"),
    ),
    "storytel": (
        "storytel.html",
        "https://jobs.storytel.com/jobs",
        lambda url, fetch: teamtailor.extract(url, fetch, source_name="storytel"),
    ),
    "busuu": (
        "busuu.html",
        "https://osv-chegg.wd5.myworkdayjobs.com/Busuu",
        lambda url, fetch: workday.extract(url, fetch, source_name="busuu"),
    ),
    "dsv": (
        "dsv.html",
        "https://jobs.dsv.com/search/",
        lambda url, fetch: successfactors_html.extract(
            url,
            fetch,
            source_name="dsv",
            page_step=10,
            base_search_url="https://jobs.dsv.com/search/",
        ),
    ),
    "impactpool": (
        "impactpool.html",
        "https://www.impactpool.org/search",
        lambda url, fetch: impactpool.extract(url, fetch, source_name="impactpool"),
    ),
}


@pytest.mark.parametrize("name", sorted(_FIXTURE_CASES))
def test_fixture_still_parses(name: str) -> None:
    filename, listing_url, extractor = _FIXTURE_CASES[name]
    path = FIXTURES_DIR / filename
    if not path.exists():
        # givewell's captured HTML is the wrong artefact: greenhouse.py reads
        # the boards API, not the listing page. The capture script now saves
        # what the extractor asks for, so this skip clears on the next run.
        pytest.skip(f"{filename} not captured yet — re-run scripts/capture_fixtures.py {name}")

    jobs = extractor(listing_url, single_response_fetch(path.read_text(encoding="utf-8")))

    assert len(jobs) > 0, f"{filename} parsed to zero jobs"
    assert all(job["title"] for job in jobs)
