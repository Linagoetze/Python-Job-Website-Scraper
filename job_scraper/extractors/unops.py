"""Extractor for UNOPS career marketplace page.

HTML structure per listing:
    <article class="article article--result ...">
      <div class="article__header">
        <div class="article__header__text">
          <h3><a href="/careersmarketplace/JobDetail/slug/ID">Title</a></h3>
          <div class="article__header__text__subtitle">
            <span class="list-item-Duty Station">City</span>
            ...
          </div>
        </div>
      </div>
    </article>

Pagination: uses ?jobOffset=N with 6 results per page. The listing states its
own length — "1-6 of 74 results", and the same number in the control's
aria-label — and the walk is checked against it before it returns. See
`pagination.py`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from job_scraper.extractors import pagination

_PAGE_SIZE = 6
# "1-6 of 74 results" in the list controls, and `aria-label="74 results"` on the
# same element. Either spelling gives the same number; the text is tried first
# because an aria-label is the more likely of the two to be restyled away.
_TOTAL_PATTERN = re.compile(r"([\d\s,]+)\s*results\b", re.I)


def _paginate_url(base_url: str, offset: int) -> str:
    """Return *base_url* with jobOffset=*offset* set (replaces any existing value)."""
    parsed = urlparse(base_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params["jobOffset"] = [str(offset)]
    new_query = urlencode({k: v[0] for k, v in params.items()})
    return urlunparse(parsed._replace(query=new_query))


def _declared_total(soup: BeautifulSoup) -> int | None:
    """How many vacancies the marketplace says it is showing, if it says."""
    for candidate in (
        soup.get_text(" ", strip=True),
        *(str(tag.get("aria-label")) for tag in soup.select("[aria-label]")),
    ):
        match = _TOTAL_PATTERN.search(candidate)
        if match:
            return int(re.sub(r"[^\d]", "", match.group(1)))
    return None


def _parse_page(soup: BeautifulSoup, listing_url: str, source_name: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for article in soup.find_all("article"):
        title_a = article.find("a", href=lambda h: h and "JobDetail" in h)
        if not title_a:
            continue
        title = title_a.get_text(" ", strip=True)
        if not title:
            continue
        detail_url = urljoin(listing_url, str(title_a["href"]))
        loc_span = article.find("span", class_="list-item-Duty Station")
        location = loc_span.get_text(" ", strip=True) if loc_span else ""
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


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str = "unops",
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    offset = 0
    total: int | None = None

    while True:
        url = _paginate_url(listing_url, offset)
        html = fetch_text(url)
        soup = BeautifulSoup(html, "lxml")
        page_jobs = _parse_page(soup, listing_url, source_name)
        if not page_jobs:
            break

        # Read the total only from a page that parsed: a page that did not
        # render carries neither postings nor the count of them.
        total = _declared_total(soup) or total
        for job in page_jobs:
            key = job["detail_url"]
            if key not in seen:
                seen.add(key)
                out.append(job)
        if len(page_jobs) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE

    # However the loop ended — empty page, short page, or the last full one —
    # the marketplace stated its own length and this walk either matched it or
    # did not. A page that half-renders ends the walk short without ever coming
    # back empty, which is why this is checked here and not in the loop.
    pagination.reconcile(source_name, url, collected=len(out), total=total)
    return out
