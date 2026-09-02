"""Extractor for J-PAL (Poverty Action Lab) careers page.

HTML structure per listing (Drupal 10, server-rendered, paginated via ?page=N):
    <div class="node node--type-job node--view-mode-teaser">
        <h3 class="job-teaser-title"><a href="/careers/[slug]">Title</a></h3>
        <div class="job-teaser-country">Country</div>
    </div>

Pagination uses a zero-indexed ?page=N query parameter, and the pager on each
page lists every page that exists. That pager is the walk's authority: it says
how many pages there are, so a page inside that range which yields no postings
is a failure, not the end. See `pagination.py` for why that distinction is the
whole point of this module's loop.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from job_scraper.extractors import pagination

BASE_URL = "https://www.povertyactionlab.org"
# A sanity bound on the pager, not an expected length: about forty postings at
# nine to a page is five pages, so anything near this is a malformed pager.
_PAGE_LIMIT = 50


def _parse_jobs(soup: BeautifulSoup, listing_url: str, source_name: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in soup.select("div.node--type-job"):
        title_tag = node.select_one("h3.job-teaser-title a")
        if not title_tag:
            continue
        title = title_tag.get_text(" ", strip=True)
        if not title:
            continue

        href = title_tag.get("href", "")
        detail_url = urljoin(BASE_URL, href) if href else listing_url

        location_tag = node.select_one("div.job-teaser-country")
        location = location_tag.get_text(" ", strip=True) if location_tag else ""

        raw_snippet = " ".join(x for x in [title, location] if x)
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": location,
                "department": "",
                "listing_url": listing_url,
                "detail_url": detail_url,
                "apply_url": detail_url,
                "raw_snippet": raw_snippet,
            }
        )
    return out


def _last_page(soup: BeautifulSoup) -> int:
    """Return the highest page index found in pagination links (0-indexed).

    Zero means "this page shows no pager", which a genuine single-page listing
    and a page that failed to render both do. The caller keeps them apart by
    only ever asking this of a page that yielded postings.
    """
    max_page = 0
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        m = re.search(r"[?&]page=(\d+)", href)
        if m:
            max_page = max(max_page, int(m.group(1)))
    return max_page


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str = "jpal",
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    base = listing_url.rstrip("/").split("?")[0]

    page = 0
    last = 0
    while True:
        url = base if page == 0 else f"{base}?page={page}"
        html = fetch_text(url)
        soup = BeautifulSoup(html, "lxml")

        jobs = _parse_jobs(soup, listing_url, source_name)
        if not jobs and page > 0:
            # An earlier page's pager said this one exists and holds postings.
            # Every page in range carries at least one — J-PAL's out-of-range
            # response is the only empty one, and it lies past `last`.
            pagination.short_walk(
                source_name,
                url,
                collected=len(out),
                promised=f"the pager runs to page {last}",
            )
        out.extend(jobs)

        # Read the pager only from a page that parsed: a broken page shows no
        # pager, and taking its answer is exactly how the walk used to end early.
        # Never shrink the count either, so a page whose pager is truncated
        # cannot cut a walk that an earlier page said was longer.
        last = max(last, _last_page(soup))
        if page >= last:
            break
        if page >= _PAGE_LIMIT:
            # A pager pointing past this is not a listing, it is a bug or a
            # trap; either way, stop fetching and say so rather than hammer on.
            raise pagination.ShortWalkError(
                f"{source_name}: pager claims page {last}, past the {_PAGE_LIMIT}-page "
                f"limit; stopped after {len(out)} posting(s) rather than keep fetching"
            )
        page += 1

    return out
