from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from job_scraper.extractors import pagination

logger = logging.getLogger(__name__)

_BASE = "https://www.impactpool.org"
_MAX_PAGES = 200


def _links_to_page(soup: BeautifulSoup, page: int) -> bool:
    """True if this page offers a link to page *page*.

    The listing also heads itself "3973 jobs match your search.", and that count
    is deliberately *not* what the walk checks itself against: this is an
    aggregator, promoted postings repeat across pages, and the deduplicated
    result is legitimately smaller than the headline (3381 against 3973 on
    2026-09-02). The next-page link carries no such slack — it is there when
    there is another page and gone when there is not, which is the same signal
    J-PAL's pager gives.
    """
    return any(re.search(rf"[?&]page={page}\b", a.get("href", "")) for a in soup.select("a[href]"))


class CardMarkupError(ValueError):
    """A job card whose markup no longer has the shape this parser reads."""


def _card_fields(a: Tag, detail_url: str, source_name: str) -> tuple[str, str, str]:
    """Title, organisation and location from one card's link.

    Each field is found by its role in the card, never by counting
    `ip-typography` elements. Counting is what broke on 2026-09-25: the title
    moved from a <div> to an <h3>, every field slid one place left, and 3,547
    postings "parsed" with the employer as their title. The card is:

        <h3 type="cardTitle">title</h3>
        <div class="ip-layout">                  outer
          <div type="bodyEmphasis">organisation</div>
          <div class="ip-layout">                inner
            [<div type="bodyEmphasis">location</div>]
            <div type="bodyEmphasis">grade</div>

    The inner layout has two fields, or one when the posting names no location
    (102 of 3,547 cards on 2026-09-25). That single field is the grade, not a
    location — every one carried the grade's styling — so it leaves `location`
    empty rather than filling it with "P-3" or "Mid - Mid level". Any other
    shape raises: a card this parser does not recognise is a changed site, and
    guessing at it is how the employer ended up in the title column.

    A title element that is present but empty is a different thing: the
    poster left the title blank (8 cards on 2026-09-25). The markup is intact,
    so this returns an empty title for the caller to skip, not an error that
    would fail the whole source over one posting.
    """

    def text(el: Tag | None) -> str:
        return el.get_text(" ", strip=True) if el is not None else ""

    def fail(what: str) -> CardMarkupError:
        return CardMarkupError(
            f"{source_name}: the card for {detail_url} {what}. Impactpool's card markup "
            "has changed; refusing to guess which field is which."
        )

    title_el = a.find(attrs={"type": "cardTitle"})
    if title_el is None:
        raise fail('has no type="cardTitle" element')
    title = text(title_el)

    outer = a.find("div", class_="ip-layout", recursive=False)
    if outer is None:
        raise fail("has no ip-layout block under its title")
    orgs = outer.find_all(attrs={"type": "bodyEmphasis"}, recursive=False)
    inner = outer.find("div", class_="ip-layout", recursive=False)
    details = inner.find_all(attrs={"type": "bodyEmphasis"}, recursive=False) if inner else []
    if len(orgs) != 1 or len(details) not in (1, 2):
        raise fail(
            f"has {len(orgs)} organisation field(s) and {len(details)} detail field(s), "
            "expected 1 and 1-2"
        )

    company = text(orgs[0])
    location = text(details[0]) if len(details) == 2 else ""
    return title, company, location


def _parse_page(soup: BeautifulSoup, listing_url: str, source_name: str) -> list[dict[str, Any]]:
    """Every posting on one search page, in the order the page lists them.

    Separate from the walk because this listing is roughly a hundred pages long:
    storing the whole walk as a fixture the way J-PAL's is stored would be ten
    megabytes of third-party HTML. The saved page pins this parser instead, and
    the walk around it is covered by `tests/test_pagination.py`.
    """
    out: list[dict[str, Any]] = []
    on_this_page: set[str] = set()
    untitled: list[str] = []
    jobs = soup.find_all("div", class_="job")
    for job in jobs:
        a = job.find("a")
        if not a:
            continue
        href = a.get("href", "")
        if not href or not href.startswith("/jobs/"):
            continue

        # A posting shown twice on one page is one posting; a posting shown on
        # two pages is the walk's business, and that is what its `seen` is for.
        detail_url = urljoin(_BASE, href)
        if detail_url in on_this_page:
            continue
        on_this_page.add(detail_url)

        title, company, location = _card_fields(a, detail_url, source_name)
        if not title:
            untitled.append(detail_url)
            continue

        raw_snippet = " ".join(x for x in [title, location] if x)
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "company": company,
                "location": location,
                "department": "",
                "listing_url": listing_url,
                "detail_url": detail_url,
                "apply_url": detail_url,
                "raw_snippet": raw_snippet,
            }
        )

    if untitled:
        logger.info(
            "%s: skipped %d posting(s) listed with a blank title: %s",
            source_name,
            len(untitled),
            ", ".join(untitled),
        )
    return out


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    page = 1
    expects_more = False

    # No per_page param: the site 500s on ?page=1&per_page=40, and the default
    # page size is 40 anyway.
    while page <= _MAX_PAGES:
        url = f"{_BASE}/search?page={page}"
        html = fetch_text(url)
        soup = BeautifulSoup(html, "lxml")

        jobs = _parse_page(soup, listing_url, source_name)
        if not jobs:
            if expects_more:
                # The previous page offered this one. Impactpool 500s
                # intermittently (a 5xx is retried and then raises); this is the
                # quieter failure, where a 200 arrives with no listing in it.
                pagination.short_walk(
                    source_name,
                    url,
                    collected=len(out),
                    promised=f"page {page - 1} linked to it",
                )
            break

        # Only a page that parsed can be asked whether another one follows.
        expects_more = _links_to_page(soup, page + 1)

        for job in jobs:
            if job["detail_url"] not in seen:
                seen.add(job["detail_url"])
                out.append(job)

        page += 1

    if expects_more:
        # Fell out of the loop at the page cap with the listing still offering
        # another page. Whatever that is — a pager gone haywire, a board that
        # really has grown past 8,000 postings — it is not a finished walk, and
        # J-PAL's equivalent cap does not pass one off as one either.
        raise pagination.ShortWalkError(
            f"{source_name}: stopped at the {_MAX_PAGES}-page limit holding "
            f"{len(out)} posting(s), with page {page} still linked. Refusing a "
            "short list: raise the cap or find out why the listing is that long."
        )

    return out
