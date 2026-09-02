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

The listing is one Drupal view among several on the page, `div.view-id-jobs`,
and it says which of three things happened:

    .view-id-jobs .view-content   postings rendered
    .view-id-jobs .view-empty     rendered, and there are genuinely none
    neither                       the view did not render at all

That third state is what the site served when this extractor lost 35 postings,
and reading it directly is what lets the walk tell an empty listing from a
broken one — rather than inferring it from a missing pager, which both states
share. The office-contact blocks lower down the page are also Drupal views with
their own `.view-empty`, so the check is scoped to the jobs view, not the page.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from job_scraper.extractors import pagination

BASE_URL = "https://www.povertyactionlab.org"
_JOBS_VIEW = "div.view-id-jobs"
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


def _listing_rendered(soup: BeautifulSoup) -> bool:
    """True if the jobs view produced a listing — with postings or without.

    False means the view is not on the page at all, which is not an answer
    about vacancies; it is the absence of one, and the walk must not read it as
    zero. See this module's docstring for the three states.
    """
    return soup.select_one(f"{_JOBS_VIEW} .view-content, {_JOBS_VIEW} .view-empty") is not None


def _last_page(soup: BeautifulSoup) -> int | None:
    """The highest page index in the pager (0-indexed), or None if there is none.

    None is now distinct from 0: 0 is a pager whose only page is the first,
    None is a page showing no pager at all. The caller pairs it with
    `_listing_rendered`, which is what says whether that absence means "one page
    of results" or "this page is not a listing".
    """
    pages = [
        int(m.group(1))
        for a in soup.select("a[href]")
        if (m := re.search(r"[?&]page=(\d+)", a.get("href", "")))
    ]
    return max(pages) if pages else None


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

        if not _listing_rendered(soup):
            # No jobs view on the page at all. This is the failure that cost 35
            # postings, and it is caught here rather than deduced from an empty
            # result later — including on page 0, where an unrendered view used
            # to look exactly like a career page with no vacancies.
            pagination.short_walk(
                source_name,
                url,
                collected=len(out),
                promised="the page carries no jobs listing at all, rendered or empty",
            )

        jobs = _parse_jobs(soup, listing_url, source_name)
        # Every page carries a pager, and the one on *this* page is the freshest
        # statement of where the listing ends. That matters: J-PAL's pages are
        # edge-cached separately, so page 0 can be a copy from when the board was
        # a page longer, and following it walks one page past the end. Observed
        # live on 2026-09-02, when the board went from 37 postings to 36 between
        # two fetches of the same walk.
        this_page_last = _last_page(soup)

        if not jobs:
            if this_page_last is not None and page <= this_page_last:
                # The page lists itself in its own pager and still shows nothing.
                # That is not the end of the listing; it is a page of it missing.
                pagination.short_walk(
                    source_name,
                    url,
                    collected=len(out),
                    promised=f"its own pager still lists it, and runs to page {this_page_last}",
                )
            # Otherwise this page is past the end — which is what J-PAL's
            # "Your search returned no results" state means when the pager
            # agrees — or page 0 of a board with no vacancies at all. Both are
            # answers, and the second is the pipeline's zero-row guard to judge.
            break

        out.extend(jobs)

        # Never shrink the count: a pager that under-claims (the same caching,
        # the other way round) must not cut a walk an earlier page said was
        # longer. Over-claiming is handled above, by the empty page itself.
        if this_page_last is not None:
            last = max(last, this_page_last)
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
