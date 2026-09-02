from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from job_scraper.extractors import pagination

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


def _parse_page(soup: BeautifulSoup, listing_url: str, source_name: str) -> list[dict[str, Any]]:
    """Every posting on one search page, in the order the page lists them.

    Separate from the walk because this listing is roughly a hundred pages long:
    storing the whole walk as a fixture the way J-PAL's is stored would be ten
    megabytes of third-party HTML. The saved page pins this parser instead, and
    the walk around it is covered by `tests/test_pagination.py`.
    """
    out: list[dict[str, Any]] = []
    on_this_page: set[str] = set()
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

        divs = [d.get_text(" ", strip=True) for d in a.find_all("div", class_="ip-typography")]
        title = divs[0] if len(divs) > 0 else ""
        company = divs[1] if len(divs) > 1 else ""
        location = divs[2] if len(divs) > 2 else ""

        if not title:
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
