"""A paginated walk must not end quietly on a page that failed to parse.

WP11's bug: J-PAL's `?page=1` came back 200 with a full-size body, no job nodes
and no pager. The walk read "no jobs" as "no more jobs" and returned 9 of about
44 postings without failing, which is exactly the silent loss this project ranks
worst. These tests cover the guard from both sides — a broken page must raise,
and a genuinely single-page listing must still come back clean.

The J-PAL markup here is hand-written and deliberately minimal: `jpal.html` and
its four page files pin what the real site serves, and what these tests need is
the *shape* of a walk (pager says five pages, page two is broken), which cannot
be captured from a healthy site.
"""

from __future__ import annotations

from typing import Any

import pytest

from job_scraper.extractors import jpal, smartrecruiters, tetrapak
from job_scraper.extractors.pagination import ShortWalkError
from tests.fixture_cases import parse_fixture

_LISTING_URL = "https://www.povertyactionlab.org/careers"


# --- J-PAL: a pager-driven walk ---------------------------------------------


def _job_node(slug: str) -> str:
    return (
        '<div class="node node--type-job">'
        f'<h3 class="job-teaser-title"><a href="/careers/{slug}">{slug}</a></h3>'
        '<div class="job-teaser-country">Kenya</div>'
        "</div>"
    )


def _page(jobs: int, last_page: int | None, offset: int = 0) -> str:
    """A listing page holding *jobs* postings, with a pager up to *last_page*.

    `last_page=None` is the no-pager page: what a single-page listing serves,
    and also what the site served when its view failed to render.
    """
    nodes = "".join(_job_node(f"job-{offset + i}") for i in range(jobs))
    pager = ""
    if last_page is not None:
        links = "".join(f'<a href="?page={n}">Page {n + 1}</a>' for n in range(last_page + 1))
        pager = f'<nav class="pager">{links}</nav>'
    return f"<html><body>{nodes}{pager}</body></html>"


def _serving(pages: dict[str, str]) -> Any:
    """A fetcher over a URL -> body map, recording what was asked for."""
    asked: list[str] = []

    def fetch(url: str, *args: Any, **kwargs: Any) -> str:
        asked.append(url)
        return pages[url]

    fetch.asked = asked  # type: ignore[attr-defined]
    return fetch


def test_single_page_listing_needs_no_pager() -> None:
    """The case the guard must not break: one page, no pager, one fetch."""
    fetch = _serving({_LISTING_URL: _page(jobs=4, last_page=None)})

    jobs = jpal.extract(_LISTING_URL, fetch)

    assert len(jobs) == 4
    assert fetch.asked == [_LISTING_URL]


def test_walk_follows_the_pager_to_the_last_page() -> None:
    fetch = _serving(
        {
            _LISTING_URL: _page(jobs=9, last_page=2),
            f"{_LISTING_URL}?page=1": _page(jobs=9, last_page=2, offset=9),
            f"{_LISTING_URL}?page=2": _page(jobs=3, last_page=2, offset=18),
        }
    )

    jobs = jpal.extract(_LISTING_URL, fetch)

    assert len(jobs) == 21
    assert len(fetch.asked) == 3


def test_empty_page_mid_walk_raises_rather_than_shortening() -> None:
    """The WP11 bug: page 1 renders nothing, and the walk used to call it the end."""
    fetch = _serving(
        {
            _LISTING_URL: _page(jobs=9, last_page=4),
            f"{_LISTING_URL}?page=1": _page(jobs=0, last_page=None),
        }
    )

    with pytest.raises(ShortWalkError) as excinfo:
        jpal.extract(_LISTING_URL, fetch)

    message = str(excinfo.value)
    assert "?page=1" in message
    assert "pager runs to page 4" in message
    # The count it refused to return, so the log says how much was at stake.
    assert "9 posting(s)" in message


def test_a_broken_pages_missing_pager_cannot_end_the_walk() -> None:
    """`_last_page` returning 0 from a broken page must not be believed.

    This is the mechanism of the original bug rather than its symptom: the walk
    asked the page that had just failed how many pages there were, and a page
    that renders nothing renders no pager either.
    """
    fetch = _serving(
        {
            _LISTING_URL: _page(jobs=9, last_page=4),
            f"{_LISTING_URL}?page=1": _page(jobs=0, last_page=None),
        }
    )

    with pytest.raises(ShortWalkError):
        jpal.extract(_LISTING_URL, fetch)


def test_a_pager_that_grows_mid_walk_is_followed() -> None:
    """A posting added while the walk runs lengthens it; it never shortens it."""
    fetch = _serving(
        {
            _LISTING_URL: _page(jobs=9, last_page=1),
            f"{_LISTING_URL}?page=1": _page(jobs=9, last_page=2, offset=9),
            f"{_LISTING_URL}?page=2": _page(jobs=1, last_page=1, offset=18),
        }
    )

    jobs = jpal.extract(_LISTING_URL, fetch)

    assert len(jobs) == 19


def test_a_runaway_pager_stops_and_says_so() -> None:
    """A pager pointing past any plausible listing is a bug, not a long board."""

    def fetch(url: str, *args: Any, **kwargs: Any) -> str:
        return _page(jobs=1, last_page=10_000)

    with pytest.raises(ShortWalkError, match="limit"):
        jpal.extract(_LISTING_URL, fetch)


def test_the_captured_walk_covers_every_page() -> None:
    """The fixture holds the whole listing, so the golden count is not one page."""
    assert len(parse_fixture("jpal")) > 9


# --- SmartRecruiters and Tetra Pak: totals from an API ----------------------


def _smartrecruiters_pages(pages: list[dict[str, Any]]) -> Any:
    import json

    bodies = [json.dumps(page) for page in pages]

    def fetch(url: str, *args: Any, **kwargs: Any) -> str:
        return bodies.pop(0)

    return fetch


def _posting(index: int) -> dict[str, Any]:
    return {"id": f"id-{index}", "name": f"Job {index}", "relativeUri": f"org/{index}"}


def test_smartrecruiters_empty_page_before_the_total_raises() -> None:
    fetch = _smartrecruiters_pages(
        [
            {"totalFound": 250, "content": [_posting(i) for i in range(100)]},
            {"totalFound": 250, "content": []},
        ]
    )

    with pytest.raises(ShortWalkError, match="250 posting"):
        smartrecruiters.extract("https://example.test", fetch, "acme", "acme")


def test_smartrecruiters_stops_cleanly_at_the_total() -> None:
    fetch = _smartrecruiters_pages([{"totalFound": 3, "content": [_posting(i) for i in range(3)]}])

    assert len(smartrecruiters.extract("https://example.test", fetch, "acme", "acme")) == 3


def test_smartrecruiters_empty_board_is_not_a_failure() -> None:
    """No postings at all is a fact about the board, and the pipeline's job."""
    fetch = _smartrecruiters_pages([{"totalFound": 0, "content": []}])

    assert smartrecruiters.extract("https://example.test", fetch, "acme", "acme") == []


def _tetrapak_result(index: int) -> dict[str, Any]:
    # Ids start at one: the extractor reads `id or ""`, so a zero id is dropped.
    return {
        "response": {
            "id": index + 1,
            "urlTitle": f"job-{index + 1}",
            "jobLocationShort": ["Lund"],
        }
    }


def test_tetrapak_empty_page_before_the_total_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        {"totalJobs": 40, "jobSearchResult": [_tetrapak_result(i) for i in range(10)]},
        {"totalJobs": 40, "jobSearchResult": []},
    ]
    monkeypatch.setattr(tetrapak, "post_json", lambda *a, **k: responses.pop(0))

    with pytest.raises(ShortWalkError, match="40 job"):
        tetrapak.extract("https://example.test", lambda url: "")


def test_tetrapak_stops_cleanly_at_the_total(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [{"totalJobs": 2, "jobSearchResult": [_tetrapak_result(i) for i in range(2)]}]
    monkeypatch.setattr(tetrapak, "post_json", lambda *a, **k: responses.pop(0))

    assert len(tetrapak.extract("https://example.test", lambda url: "")) == 2
