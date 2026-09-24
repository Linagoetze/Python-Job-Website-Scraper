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

import logging
import re
from typing import Any

import pytest

from job_scraper.extractors import (
    impactpool,
    jpal,
    niras,
    smartrecruiters,
    successfactors_html,
    unops,
    workday,
)
from job_scraper.extractors.pagination import ShortWalkError
from tests.fixture_cases import FIXTURES_DIR, parse_fixture

_LISTING_URL = "https://www.povertyactionlab.org/careers"


# --- J-PAL: a pager-driven walk ---------------------------------------------


def _job_node(slug: str) -> str:
    return (
        '<div class="node node--type-job">'
        f'<h3 class="job-teaser-title"><a href="/careers/{slug}">{slug}</a></h3>'
        '<div class="job-teaser-country">Kenya</div>'
        "</div>"
    )


def _page(jobs: int, last_page: int | None, offset: int = 0, rendered: bool = True) -> str:
    """A listing page in one of the jobs view's three states.

    `rendered=True` builds the view around whatever postings there are — with
    `.view-content` when there are some and `.view-empty` when there are none,
    which is what the site serves for a search that genuinely matches nothing.
    `rendered=False` leaves the jobs view off the page altogether: the failure
    that cost 35 postings. The office-contact blocks are included because they
    are `.view-empty` too, and a check that is not scoped to the jobs view would
    read them as an answer about vacancies.
    """
    contact = '<div class="view view-id-office_contact"><div class="view-empty">J-PAL</div></div>'
    if not rendered:
        return f"<html><body>{contact}</body></html>"

    nodes = "".join(_job_node(f"job-{offset + i}") for i in range(jobs))
    body = (
        f'<div class="view-content">{nodes}</div>'
        if jobs
        else '<div class="view-empty">Your search returned no results.</div>'
    )
    pager = ""
    if last_page is not None:
        links = "".join(f'<a href="?page={n}">Page {n + 1}</a>' for n in range(last_page + 1))
        pager = f'<nav class="pager">{links}</nav>'
    return f'<html><body><div class="view view-id-jobs">{body}{pager}</div>{contact}</body></html>'


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
            f"{_LISTING_URL}?page=1": _page(jobs=0, last_page=None, rendered=False),
        }
    )

    with pytest.raises(ShortWalkError) as excinfo:
        jpal.extract(_LISTING_URL, fetch)

    message = str(excinfo.value)
    assert "?page=1" in message
    assert "no jobs listing at all" in message
    # The count it refused to return, so the log says how much was at stake.
    assert "9 posting(s)" in message


def test_a_broken_pages_missing_pager_cannot_end_the_walk() -> None:
    """A page that failed to render is never asked how many pages there are.

    This is the mechanism of the original bug rather than its symptom: the walk
    asked the page that had just failed, and a page that renders nothing renders
    no pager either. `_last_page` now says None for "no pager here" rather than
    0, and the walk reaches the state check before it would read the pager.
    """
    fetch = _serving(
        {
            _LISTING_URL: _page(jobs=9, last_page=4),
            f"{_LISTING_URL}?page=1": _page(jobs=0, last_page=None, rendered=False),
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


def test_a_listing_that_genuinely_has_no_vacancies_is_not_a_failure() -> None:
    """The view rendered and said there are none. That is an answer, not a fault."""
    fetch = _serving({_LISTING_URL: _page(jobs=0, last_page=None)})

    assert jpal.extract(_LISTING_URL, fetch) == []


def test_a_missing_jobs_view_on_the_first_page_raises() -> None:
    """The state that used to be indistinguishable from "no vacancies".

    Page 0 was previously covered only by the pipeline's zero-row guard, which
    keeps the stored jobs but reports the source as healthy with nothing on it.
    The view being absent is not a count of zero, and it is now said out loud.
    """
    fetch = _serving({_LISTING_URL: _page(jobs=0, last_page=None, rendered=False)})

    with pytest.raises(ShortWalkError, match="no jobs listing at all"):
        jpal.extract(_LISTING_URL, fetch)


def test_the_captured_walk_covers_every_page() -> None:
    """The fixture holds the whole listing, so the golden count is not one page."""
    assert len(parse_fixture("jpal")) > 9


# --- SmartRecruiters: a total from an API -----------------------------------
#
# Tetra Pak used to be tested here too, against the same shape. Its extractor is
# gone: the JSON API it called is disallowed by robots.txt, and the source now
# reads the HTML search page through `successfactors_html`.


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


# --- the four listings that state a total in their own markup ---------------
#
# None of these can store its walk as a fixture — UNOPS is 13 pages, DSV 201,
# Impactpool about a hundred — so the walk is pinned here and the saved page
# pins the parser. Each site's total is quoted from what it really serves; the
# fixtures and the live pages the totals were read from are named in the plan.


def _unops_page(jobs: int, total: int | None, offset: int = 0) -> str:
    articles = "".join(
        f'<article class="article article--result">'
        f'<h3><a href="/careersmarketplace/JobDetail/role-{offset + i}/{offset + i}">'
        f"Role {offset + i}</a></h3>"
        f'<span class="list-item-Duty Station">Copenhagen</span></article>'
        for i in range(jobs)
    )
    legend = ""
    if total is not None:
        legend = (
            f'<span class="list-controls__text__legend" aria-label="{total} results">'
            f"1-{jobs} of {total} results</span>"
        )
    return f"<html><body>{legend}{articles}</body></html>"


def test_unops_empty_page_before_its_stated_total_raises() -> None:
    """A page that came back empty part-way through the marketplace."""
    pages = {
        0: _unops_page(6, total=74),
        6: _unops_page(0, total=None),
    }

    def fetch(url: str, *args: Any, **kwargs: Any) -> str:
        offset = int(url.rsplit("jobOffset=", 1)[1])
        return pages[offset]

    with pytest.raises(ShortWalkError, match="says it has 74"):
        unops.extract("https://careers.unops.org/careersmarketplace/SearchJobs", fetch)


def test_unops_without_a_readable_total_says_it_could_not_check(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No total is not an error, but it is not silence either."""
    pages = {0: _unops_page(6, total=None), 6: _unops_page(0, total=None)}

    def fetch(url: str, *args: Any, **kwargs: Any) -> str:
        return pages[int(url.rsplit("jobOffset=", 1)[1])]

    with caplog.at_level(logging.WARNING):
        jobs = unops.extract("https://careers.unops.org/careersmarketplace/SearchJobs", fetch)

    assert len(jobs) == 6
    assert "publishes no total" in caplog.text


def _impactpool_page(jobs: int, next_page: int | None, offset: int = 0) -> str:
    rows = "".join(
        f'<div class="job"><a href="/jobs/role-{offset + i}">'
        f'<div class="ip-typography">Role {offset + i}</div>'
        f'<div class="ip-typography">Org</div>'
        f'<div class="ip-typography">Geneva</div></a></div>'
        for i in range(jobs)
    )
    nxt = f'<a href="/search?page={next_page}">Next</a>' if next_page else ""
    return f"<html><body>{rows}{nxt}</body></html>"


def test_impactpool_page_that_was_linked_to_must_not_come_back_empty() -> None:
    pages = {1: _impactpool_page(40, next_page=2), 2: _impactpool_page(0, next_page=None)}

    def fetch(url: str, *args: Any, **kwargs: Any) -> str:
        return pages[int(url.rsplit("page=", 1)[1])]

    with pytest.raises(ShortWalkError, match="page 1 linked to it"):
        impactpool.extract("https://www.impactpool.org/search", fetch, "impactpool")


def test_impactpool_stops_where_the_links_stop() -> None:
    """The count in the header is deliberately not the test: it runs ahead of a
    deduplicated walk on an aggregator, and using it would fail every run.

    The walk still asks for the page after the last one — the absent link makes
    that page's emptiness acceptable rather than skipping the request. Trusting
    the link to end the walk would mean a restyled pager silently truncating it,
    which is the failure this whole package is about.
    """
    pages = {1: _impactpool_page(40, next_page=None), 2: _impactpool_page(0, next_page=None)}

    def fetch(url: str, *args: Any, **kwargs: Any) -> str:
        return pages[int(url.rsplit("page=", 1)[1])]

    assert len(impactpool.extract("https://www.impactpool.org/search", fetch, "impactpool")) == 40


def _niras_page(jobs: int, total: int | None, offset: int = 0) -> str:
    cards = "".join(
        f'<a href="/jobs/vacant-positions/cvtp-{offset + i}-role/">'
        f'<div class="box-content"><p class="headline">Role {offset + i}</p>'
        f'<p class="list-tags">Country: <span>Denmark</span></p></div></a>'
        for i in range(jobs)
    )
    bar = f'<p>Vacant positions: <span class="filter-result-text">{total}</span></p>'
    return f"<html><body>{bar if total is not None else ''}{cards}</body></html>"


def test_niras_empty_page_before_its_stated_total_raises() -> None:
    pages = {1: _niras_page(25, total=60), 2: _niras_page(0, total=None)}

    def fetch(url: str, *args: Any, **kwargs: Any) -> str:
        return pages[int(url.rsplit("page=", 1)[1])]

    with pytest.raises(ShortWalkError, match="says it has 60"):
        niras.extract("https://www.niras.com/jobs/vacant-positions/", fetch)


def _sf_page(jobs: int, total: int | None, offset: int = 0) -> str:
    rows = "".join(
        f'<tr><td><a href="/job/City-Role-{offset + i}/{offset + i}/">Role {offset + i}</a></td>'
        f'<td class="colLocation"><span class="jobLocation">Copenhagen</span></td></tr>'
        for i in range(jobs)
    )
    label = ""
    if total is not None:
        label = (
            f'<span class="paginationLabel" aria-label="Search results for . Page 1 of 9, '
            f'Results 1 to {jobs} of {total}">Results</span>'
        )
    return f"<html><body>{label}<table>{rows}</table></body></html>"


def test_successfactors_empty_page_before_its_stated_total_raises() -> None:
    pages = {0: _sf_page(10, total=2010), 10: _sf_page(0, total=None)}

    def fetch(url: str, *args: Any, **kwargs: Any) -> str:
        return pages[int(url.rsplit("startrow=", 1)[1])]

    with pytest.raises(ShortWalkError, match="says it has 2010"):
        successfactors_html.extract(
            "https://jobs.dsv.com/search/",
            fetch,
            source_name="dsv",
            page_step=10,
            base_search_url="https://jobs.dsv.com/search/",
        )


def test_successfactors_lapping_the_list_early_raises() -> None:
    """A board that serves page one again has not ended, it has stuck."""
    page = _sf_page(10, total=2010)

    def fetch(url: str, *args: Any, **kwargs: Any) -> str:
        return page

    with pytest.raises(ShortWalkError, match="says it has 2010"):
        successfactors_html.extract(
            "https://jobs.dsv.com/search/",
            fetch,
            source_name="dsv",
            page_step=10,
            base_search_url="https://jobs.dsv.com/search/",
        )


def test_successfactors_stops_cleanly_once_it_has_them_all() -> None:
    pages = {0: _sf_page(10, total=15), 10: _sf_page(5, total=15, offset=10)}

    def fetch(url: str, *args: Any, **kwargs: Any) -> str:
        return pages[int(url.rsplit("startrow=", 1)[1])]

    jobs = successfactors_html.extract(
        "https://jobs.dsv.com/search/",
        fetch,
        source_name="dsv",
        page_step=10,
        base_search_url="https://jobs.dsv.com/search/",
    )
    assert len(jobs) == 15


# --- the totals, read from the real saved pages -----------------------------


@pytest.mark.parametrize(
    ("name", "reader", "expected"),
    [
        ("dsv", successfactors_html._declared_total, 2010),
        ("iss", successfactors_html._declared_total, 62),
        ("novo_nordisk", successfactors_html._declared_total, 329),
        ("coloplast", successfactors_html._declared_total, 331),
        ("niras", niras._declared_total, 2),
        ("unops", unops._declared_total, 74),
    ],
)
def test_the_saved_page_still_states_its_total(name: str, reader: Any, expected: int) -> None:
    """The guard is only as good as its ability to read the number.

    A restyled pagination label would not fail any other test: the postings
    would still parse, the golden would still match, and the walk would go back
    to ending on an empty page with nothing to check it against — quietly, in
    `unverifiable_end`. Pinning the number against the captured markup is what
    makes that drift visible at test time.
    """
    from bs4 import BeautifulSoup

    path = FIXTURES_DIR / f"{name}.html"
    if not path.exists():
        pytest.skip(f"{name}.html not captured yet")

    assert reader(BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")) == expected


def test_the_saved_impactpool_page_still_links_to_the_next_one() -> None:
    """Impactpool's guard is the link, not the count; it has to be findable."""
    from bs4 import BeautifulSoup

    path = FIXTURES_DIR / "impactpool.html"
    if not path.exists():
        pytest.skip("impactpool.html not captured yet")

    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")
    assert impactpool._links_to_page(soup, 2)
    assert not impactpool._links_to_page(soup, 3)


@pytest.mark.parametrize(
    ("name", "per_page"),
    [("dsv", 10), ("iss", 20), ("coloplast", 25)],
)
def test_successfactors_parses_exactly_what_its_label_counts(name: str, per_page: int) -> None:
    """The invariant the total guard rests on, checked against real markup.

    "Results 1 to 10 of 2010" is only a safe thing to compare a finished walk
    against if this extractor sees the same postings the site is counting. It
    does: on every captured instance, the rows parsed off a page equal the
    upper bound the page's own label states for it. If a skin appears where
    that stops being true — a row the parser drops, or one the site counts
    twice — the walk would end short of the total and raise on the boards whose
    length happens to divide evenly by the page size. This is where that shows.
    """
    from bs4 import BeautifulSoup

    path = FIXTURES_DIR / f"{name}.html"
    if not path.exists():
        pytest.skip(f"{name}.html not captured yet")

    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")
    labels = [soup.get_text(" ", strip=True)] + [
        str(tag.get("aria-label")) for tag in soup.select("[aria-label]")
    ]
    stated = next(
        int(m.group(1))
        for label in labels
        if (
            m := re.search(
                r"(?:results|showing)\s+[\d,]+\s*(?:to|-|–)\s*([\d,]+)\s+of", label, re.I
            )
        )
    )

    assert stated == per_page
    assert len(parse_fixture(name)) == stated


# --- a page that half-renders is short, not empty ---------------------------
#
# The narrower guard these replaced only fired on a page with nothing at all on
# it. A page that renders three of its six rows ends the walk just as quietly,
# through the "this page was short, so it must be the last" exit, and is the
# more likely failure of the two: it needs only part of a page to go wrong.


def test_unops_half_rendered_page_does_not_pass_as_the_last_one() -> None:
    pages = {0: _unops_page(6, total=74), 6: _unops_page(3, total=74, offset=6)}

    def fetch(url: str, *args: Any, **kwargs: Any) -> str:
        return pages[int(url.rsplit("jobOffset=", 1)[1])]

    with pytest.raises(ShortWalkError, match="holding 9 posting"):
        unops.extract("https://careers.unops.org/careersmarketplace/SearchJobs", fetch)


def test_unops_genuinely_short_last_page_is_fine() -> None:
    """The same exit, taken honestly: 8 of 8, the last page simply not full."""
    pages = {0: _unops_page(6, total=8), 6: _unops_page(2, total=8, offset=6)}

    def fetch(url: str, *args: Any, **kwargs: Any) -> str:
        return pages[int(url.rsplit("jobOffset=", 1)[1])]

    assert len(unops.extract("https://careers.unops.org/careersmarketplace/SearchJobs", fetch)) == 8


def test_successfactors_half_rendered_page_does_not_pass_as_the_last_one() -> None:
    pages = {0: _sf_page(10, total=25), 10: _sf_page(4, total=25, offset=10)}

    def fetch(url: str, *args: Any, **kwargs: Any) -> str:
        return pages[int(url.rsplit("startrow=", 1)[1])]

    with pytest.raises(ShortWalkError, match="holding 14 posting"):
        successfactors_html.extract(
            "https://jobs.dsv.com/search/",
            fetch,
            source_name="dsv",
            page_step=10,
            base_search_url="https://jobs.dsv.com/search/",
        )


def test_niras_half_rendered_page_does_not_pass_as_the_last_one() -> None:
    pages = {1: _niras_page(25, total=40), 2: _niras_page(5, total=40, offset=25)}

    def fetch(url: str, *args: Any, **kwargs: Any) -> str:
        return pages[int(url.rsplit("page=", 1)[1])]

    with pytest.raises(ShortWalkError, match="holding 30 posting"):
        niras.extract("https://www.niras.com/jobs/vacant-positions/", fetch)


# --- the three holes a second review found ----------------------------------


def test_jpal_page_that_renders_an_empty_listing_mid_walk_raises() -> None:
    """Rendered, saying there are none, and listed in its own pager.

    Distinct from the missing-view case: this page worked, and its answer is
    "no vacancies" — but its own pager still lists it, so the answer is to the
    wrong question and a page of postings has gone missing.
    """
    fetch = _serving(
        {
            _LISTING_URL: _page(jobs=9, last_page=4),
            f"{_LISTING_URL}?page=1": _page(jobs=0, last_page=4),
        }
    )

    with pytest.raises(ShortWalkError, match="its own pager still lists it"):
        jpal.extract(_LISTING_URL, fetch)


def test_impactpool_hitting_its_page_cap_with_more_to_come_raises() -> None:
    """The one exit from that loop that used to return quietly."""

    def fetch(url: str, *args: Any, **kwargs: Any) -> str:
        page = int(url.rsplit("page=", 1)[1])
        return _impactpool_page(40, next_page=page + 1, offset=page * 40)

    with pytest.raises(ShortWalkError, match="page limit"):
        impactpool.extract("https://www.impactpool.org/search", fetch, "impactpool")


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<h1>Search results</h1>", None),
        ("<p>No results found</p>", None),
        # A real number that is not the total. Read as one, it would be smaller
        # than any walk and switch the guard off while leaving it looking on.
        ("<p>10 results per page</p><span>1-6 of 74 results</span>", 74),
        ('<span aria-label="74 results">1-6 of 74 results</span>', 74),
        ('<span aria-label="74 results">Vacancies</span>', 74),
        ("<p>1-6 of 1,234 results</p>", 1234),
    ],
)
def test_unops_reads_only_a_number_that_is_actually_the_total(
    html: str, expected: int | None
) -> None:
    """The first two of these crashed the reader: matched, captured nothing."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(f"<html><body>{html}</body></html>", "lxml")
    assert unops._declared_total(soup) == expected


def test_jpal_tolerates_a_pager_that_over_claims_by_a_page() -> None:
    """The false alarm that the previous version of this guard would have raised.

    J-PAL's pages are edge-cached separately, so page 0 can be a copy from when
    the board was one page longer. Observed live on 2026-09-02: page 0 said the
    listing ran to page 4, page 3's pager said 3, and page 4 rendered "Your
    search returned no results". The walk must read that as the end — the empty
    page agrees with its own pager — and return the 36 postings it has, not fail
    a healthy source.
    """
    fetch = _serving(
        {
            _LISTING_URL: _page(jobs=9, last_page=4),
            f"{_LISTING_URL}?page=1": _page(jobs=9, last_page=3, offset=9),
            f"{_LISTING_URL}?page=2": _page(jobs=9, last_page=3, offset=18),
            f"{_LISTING_URL}?page=3": _page(jobs=9, last_page=3, offset=27),
            f"{_LISTING_URL}?page=4": _page(jobs=0, last_page=3),
        }
    )

    assert len(jpal.extract(_LISTING_URL, fetch)) == 36


# --- Workday: a walk of the board's JSON endpoint (SP3b) ----------------

_WORKDAY_URL = "https://tenant.wd1.myworkdayjobs.com/Board"
_WORKDAY_API = "https://tenant.wd1.myworkdayjobs.com/wday/cxs/tenant/Board/jobs"


def _workday_postings(start: int, count: int) -> list[dict[str, Any]]:
    return [
        {
            "title": f"Role {n}",
            "externalPath": f"/job/Geneva/Role-{n}_R{n}",
            "locationsText": "Geneva",
            "bulletFields": [f"R{n}"],
        }
        for n in range(start, start + count)
    ]


class _WorkdayBoard:
    """A fetcher whose `post_json` serves a board of *size* postings, 20 a page.

    *total* is what the first response states; *later_total* what every other
    response states — by default the same, as path's real walk did; 0 is the
    case some tenants are said to send. *broken* maps an
    offset to the number of postings that page actually returns.
    """

    def __init__(
        self,
        size: int,
        *,
        total: int | None = None,
        later_total: int | None | str = "same",
        broken: dict[int, int] | None = None,
    ) -> None:
        self.size, self.total, self.later_total = size, total, later_total
        self.broken = broken or {}
        self.asked: list[tuple[str, int]] = []

    def __call__(self, url: str, *args: Any, **kwargs: Any) -> str:
        raise AssertionError("the Workday reader must not GET anything")

    def post_json(self, url: str, payload: dict[str, Any], **kwargs: Any) -> Any:
        offset, limit = payload["offset"], payload["limit"]
        self.asked.append((url, offset))
        count = self.broken.get(offset, max(0, min(limit, self.size - offset)))
        body: dict[str, Any] = {"jobPostings": _workday_postings(offset, count)}
        stated = self.total if offset == 0 or self.later_total == "same" else self.later_total
        if stated is not None:
            body["total"] = stated
        return body


def test_workday_walks_every_page_to_the_stated_total() -> None:
    board = _WorkdayBoard(45, total=45)
    jobs = workday.extract(_WORKDAY_URL, board, "tenant")

    assert len(jobs) == 45
    assert board.asked == [(_WORKDAY_API, 0), (_WORKDAY_API, 20), (_WORKDAY_API, 40)]
    assert jobs[44]["detail_url"] == (
        "https://tenant.wd1.myworkdayjobs.com/en-US/Board/job/Geneva/Role-44_R44"
    )


def test_workday_stops_at_the_total_without_asking_for_an_empty_page() -> None:
    board = _WorkdayBoard(40, total=40)
    assert len(workday.extract(_WORKDAY_URL, board, "tenant")) == 40
    assert [offset for _, offset in board.asked] == [0, 20]


def test_workday_a_later_zero_total_does_not_replace_the_first() -> None:
    """The first response's total is the one the walk is held to."""
    board = _WorkdayBoard(45, total=46, later_total=0)
    with pytest.raises(ShortWalkError, match="says it has 46"):
        workday.extract(_WORKDAY_URL, board, "tenant")


def test_workday_empty_page_before_the_total_raises() -> None:
    board = _WorkdayBoard(61, total=61, broken={20: 0})
    with pytest.raises(ShortWalkError, match="holding 20 posting.*says it has 61"):
        workday.extract(_WORKDAY_URL, board, "tenant")


def test_workday_half_answered_page_does_not_pass_as_the_last_one() -> None:
    board = _WorkdayBoard(61, total=61, broken={20: 7})
    with pytest.raises(ShortWalkError, match="holding 27 posting.*says it has 61"):
        workday.extract(_WORKDAY_URL, board, "tenant")


def test_workday_a_zero_total_beside_postings_does_not_end_the_walk() -> None:
    """0 is not a total when the same response holds postings; the pages decide."""
    board = _WorkdayBoard(45, total=0)
    assert len(workday.extract(_WORKDAY_URL, board, "tenant")) == 45


def test_workday_empty_board_is_not_a_failure() -> None:
    board = _WorkdayBoard(0, total=0)
    assert workday.extract(_WORKDAY_URL, board, "tenant") == []


def test_workday_without_a_readable_total_says_it_could_not_check(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No total is not an error, but it is not silence either."""
    board = _WorkdayBoard(26, total=None, later_total=None)
    with caplog.at_level(logging.WARNING):
        jobs = workday.extract(_WORKDAY_URL, board, "tenant")
    assert len(jobs) == 26
    assert "publishes no total" in caplog.text


def test_workday_a_capped_total_fails_before_the_walk() -> None:
    """Workday reports at most 2000; at that number the count is a floor.

    Airbus stated 2000 with about 2,940 postings behind it. Walking to 2000 and
    reconciling would pass as whole; refusing after one request is the only
    honest answer, and the cheaper one for the site.
    """
    board = _WorkdayBoard(2937, total=2000)
    with pytest.raises(ShortWalkError, match="states 2000 postings"):
        workday.extract(_WORKDAY_URL, board, "tenant")
    assert [offset for _, offset in board.asked] == [0]


def test_workday_a_total_just_under_the_cap_is_walked() -> None:
    board = _WorkdayBoard(1999, total=1999)
    assert len(workday.extract(_WORKDAY_URL, board, "tenant")) == 1999


def test_workday_runaway_walk_stops_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workday, "_MAX_PAGES", 3)
    board = _WorkdayBoard(1000, total=None, later_total=None)
    with pytest.raises(ShortWalkError, match="3-page limit holding 60"):
        workday.extract(_WORKDAY_URL, board, "tenant")


@pytest.mark.parametrize(
    ("listing", "api", "prefix"),
    [
        (
            "https://osv-chegg.wd5.myworkdayjobs.com/Busuu",
            # The tenant id has an underscore that the hostname cannot carry:
            # the page's own config says `tenant: "osv_chegg"`, and the host's
            # spelling answered 422.
            "https://osv-chegg.wd5.myworkdayjobs.com/wday/cxs/osv_chegg/Busuu/jobs",
            "https://osv-chegg.wd5.myworkdayjobs.com/en-US/Busuu",
        ),
        (
            "https://path.wd1.myworkdayjobs.com/en-US/External",
            "https://path.wd1.myworkdayjobs.com/wday/cxs/path/External/jobs",
            "https://path.wd1.myworkdayjobs.com/en-US/External",
        ),
        (
            "https://tenant.wd3.myworkdayjobs.com/de-DE/Karriere/",
            "https://tenant.wd3.myworkdayjobs.com/wday/cxs/tenant/Karriere/jobs",
            "https://tenant.wd3.myworkdayjobs.com/de-DE/Karriere",
        ),
    ],
)
def test_workday_detail_urls_keep_the_shape_the_rendered_page_linked(
    listing: str, api: str, prefix: str
) -> None:
    """Stored jobs are keyed on detail_url, so this shape is a contract.

    With no locale in the listing URL the rendered page still linked under
    /en-US/, and every stored Workday row has that form (SP3b, DECISIONS.md).
    """
    assert workday._endpoints(listing) == (api, prefix, {})


@pytest.mark.parametrize(
    "listing",
    [
        "https://tenant.wd1.myworkdayjobs.com/en-US/Board/details/x",
        "https://tenant.wd1.myworkdayjobs.com/",
        "https://careers.example.org/Board",
    ],
)
def test_workday_refuses_a_listing_it_cannot_map_exactly(listing: str) -> None:
    board = _WorkdayBoard(5, total=5)
    with pytest.raises(ValueError):
        workday.extract(listing, board, "tenant")
    assert board.asked == []


# --- Workday: the listing's own filter query (SP3c) ---------------------
#
# Invented ids and places throughout: the real board is narrowed to a country
# that stays out of tracked files (docs/DECISIONS.md, SP3c).

_FACET_ID = "0a1b2c3d4e5f60718293a4b5c6d7e8f9"
_OTHER_ID = "ffeeddccbbaa99887766554433221100"
_FACETED_URL = f"{_WORKDAY_URL}?locationCountry={_FACET_ID}"


class _FacetedBoard(_WorkdayBoard):
    """A board of *whole* postings, *size* of them in the invented country.

    The response is shaped like the real one: `locationCountry` nested under
    `locationMainGroup`, and — Workday's facets being disjunctive — the applied
    parameter's own list keeping whole-board counts. *ignores* makes Workday
    drop the filter and serve the whole board; *facets* replaces the facet
    list outright.
    """

    def __init__(
        self,
        size: int,
        whole: int = 900,
        *,
        ignores: bool = False,
        facets: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> None:
        self.filtered = size
        self.ignores = ignores
        self.payloads: list[dict[str, Any]] = []
        self.facets = facets
        super().__init__(whole if ignores else size, total=whole if ignores else size, **kwargs)
        self.whole = whole

    def post_json(self, url: str, payload: dict[str, Any], **kwargs: Any) -> Any:
        self.payloads.append(payload)
        body = super().post_json(url, payload, **kwargs)
        country = {
            "facetParameter": "locationCountry",
            "descriptor": "Country",
            "values": [
                {"descriptor": "Ruritania", "id": _FACET_ID, "count": self.filtered},
                {"descriptor": "Elbonia", "id": _OTHER_ID, "count": self.whole - self.filtered},
            ],
        }
        body["facets"] = (
            self.facets
            if self.facets is not None
            else [
                {"facetParameter": "timeType", "values": [{"id": "x", "count": body.get("total")}]},
                {"facetParameter": "locationMainGroup", "values": [country]},
            ]
        )
        return body


def test_workday_translates_the_listing_query_into_applied_facets() -> None:
    """Each key a facet parameter, a repeated key a list of ids, the rest untouched."""
    listing = (
        "https://tenant.wd1.myworkdayjobs.com/de-DE/Board"
        f"?locationCountry={_FACET_ID}&jobFamilyGroup=abc&locationCountry={_OTHER_ID}"
    )
    assert workday._endpoints(listing) == (
        "https://tenant.wd1.myworkdayjobs.com/wday/cxs/tenant/Board/jobs",
        "https://tenant.wd1.myworkdayjobs.com/de-DE/Board",
        {"locationCountry": [_FACET_ID, _OTHER_ID], "jobFamilyGroup": ["abc"]},
    )


def test_workday_a_repeated_id_is_sent_once() -> None:
    listing = f"{_FACETED_URL}&locationCountry={_FACET_ID}"
    assert workday._endpoints(listing)[2] == {"locationCountry": [_FACET_ID]}


def test_workday_keeps_the_tenant_rule_with_a_query() -> None:
    api, _, _ = workday._endpoints(f"https://osv-chegg.wd5.myworkdayjobs.com/Busuu?a={_FACET_ID}")
    assert api == "https://osv-chegg.wd5.myworkdayjobs.com/wday/cxs/osv_chegg/Busuu/jobs"


def test_workday_walks_a_filtered_board_with_its_facets_on_every_page() -> None:
    board = _FacetedBoard(45)
    jobs = workday.extract(_FACETED_URL, board, "tenant")

    assert len(jobs) == 45
    assert [p["offset"] for p in board.payloads] == [0, 20, 40]
    assert all(p["appliedFacets"] == {"locationCountry": [_FACET_ID]} for p in board.payloads)


def test_workday_the_query_never_reaches_a_detail_url() -> None:
    """Stored jobs are keyed on detail_url; the rendered page's hrefs carry the
    query, so a key built from them would never match a stored one again."""
    jobs = workday.extract(_FACETED_URL, _FacetedBoard(3), "tenant")

    assert [j["detail_url"] for j in jobs] == [
        f"https://tenant.wd1.myworkdayjobs.com/en-US/Board/job/Geneva/Role-{n}_R{n}"
        for n in range(3)
    ]
    assert all(j["apply_url"] == j["detail_url"] for j in jobs)
    assert {j["listing_url"] for j in jobs} == {_FACETED_URL}


@pytest.mark.parametrize(
    ("query", "complaint"),
    [
        ("locationCountry", "not key=value pairs"),
        (f"locationCountry={_FACET_ID}&&a=b", "not key=value pairs"),
        ("locationCountry=", "empty facet name or id"),
        (f"={_FACET_ID}", "empty facet name or id"),
        ("locationCountry=%20", "empty facet name or id"),
    ],
)
def test_workday_refuses_a_query_it_cannot_translate(query: str, complaint: str) -> None:
    board = _FacetedBoard(5)
    with pytest.raises(ValueError, match=complaint):
        workday.extract(f"{_WORKDAY_URL}?{query}", board, "tenant")
    assert board.asked == []


def test_workday_an_ignored_filter_fails_the_source() -> None:
    """Workday answered with the whole board: a different board from the one
    configured, even though it is under the cap and would walk cleanly."""
    board = _FacetedBoard(16, whole=900, ignores=True)
    with pytest.raises(
        workday.FacetNotAppliedError, match="states 900 postings.*gives the filter's own value"
    ) as caught:
        workday.extract(_FACETED_URL, board, "tenant")
    assert [offset for _, offset in board.asked] == [0]
    # Fields, not only words: the probe builds its verdict from these.
    assert caught.value.endpoint == _WORKDAY_API
    assert caught.value.facets == {"locationCountry": [_FACET_ID]}


def test_workday_a_facet_missing_from_the_response_fails_the_source() -> None:
    board = _FacetedBoard(16, facets=[{"facetParameter": "timeType", "values": []}])
    with pytest.raises(ValueError, match="lists no 'locationCountry' facet"):
        workday.extract(_FACETED_URL, board, "tenant")


def test_workday_an_id_missing_from_its_facet_fails_the_source() -> None:
    """A mistyped id, or a country with nothing posted: either way unproven."""
    board = _FacetedBoard(16)
    with pytest.raises(ValueError, match="no 'locationCountry' value counted under id deadbeef"):
        workday.extract(f"{_WORKDAY_URL}?locationCountry=deadbeef", board, "tenant")


def test_workday_a_filtered_board_without_a_total_fails_the_source() -> None:
    board = _FacetedBoard(16)
    board.total = None
    with pytest.raises(ValueError, match="states no total"):
        workday.extract(_FACETED_URL, board, "tenant")


def test_workday_several_ids_need_only_reach_the_total() -> None:
    """A posting listed in two of the chosen countries counts under both."""
    both = f"{_FACETED_URL}&locationCountry={_OTHER_ID}"
    board = _FacetedBoard(30, whole=50)
    board.size = board.total = 45  # 30 + 20 counted, 45 distinct postings
    assert len(workday.extract(both, board, "tenant")) == 45

    short = _FacetedBoard(30, whole=50)
    short.size = short.total = 51
    with pytest.raises(ValueError, match="gives the filter's own value"):
        workday.extract(both, short, "tenant")


def test_workday_an_unfiltered_board_needs_no_facets_in_its_response() -> None:
    """SP3b's boards: no query, nothing to prove, the response's facets unread."""
    board = _FacetedBoard(5, facets=[])
    assert len(workday.extract(_WORKDAY_URL, board, "tenant")) == 5
    assert board.payloads[0]["appliedFacets"] == {}


def test_workday_a_narrowed_board_still_at_the_cap_fails() -> None:
    board = _FacetedBoard(2400, whole=2900)
    board.total = 2000
    with pytest.raises(ShortWalkError, match="states 2000 postings"):
        workday.extract(_FACETED_URL, board, "tenant")
    assert [offset for _, offset in board.asked] == [0]


def test_workday_refuses_a_fetcher_that_cannot_post() -> None:
    """Refused, not bypassed: falling back to http.post_json would reach the
    network from a test, or from a capture that would then record nothing."""
    with pytest.raises(TypeError, match="no post_json"):
        workday.extract(_WORKDAY_URL, lambda url, *a, **k: "", "tenant")


def test_the_pipelines_fetchers_can_post() -> None:
    from job_scraper import http

    assert http.fetch_text.post_json is http.post_json  # type: ignore[attr-defined]
    assert http.fetch_rendered.post_json is http.post_json  # type: ignore[attr-defined]
