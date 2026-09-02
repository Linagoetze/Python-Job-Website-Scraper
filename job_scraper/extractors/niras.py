"""Extractor for NIRAS vacant positions (Playwright-rendered).

The listing page renders job cards client-side. Static HTML contains only
the filter shell, so this source is `strategy: dynamic` in sources.yaml and
the caller hands it a rendering fetcher; this module uses what it is given.

After JS execution each job appears as a single labelled card:
    <a href="/jobs/vacant-positions/cvtp-NNNN-slug/">
      <div class="box-content">
        <p class="headline">Title</p>
        <p class="list-tags">Country: <span>…</span></p>
        <p class="list-tags">Employment: <span>…</span></p>
        <p class="list-tags">Commencement: <span>…</span></p>
        <p class="list-tags">Position length: <span>…</span></p>
        <p class="list-tags">Deadline: <span>…</span></p>
      </div>
    </a>

Note the single wrapping <div>: the anchor has exactly one element child, so
"the first child's text" is the entire card, metadata included. Read the
labelled `p.headline` instead.

Pagination: ?pageSize=25&page=N — loop until a page returns no job links. The
filter bar heads the list "Vacant positions: N", and the walk is checked
against that count before it returns; see `pagination.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from job_scraper.extractors import pagination

_JOB_PATH = "/jobs/vacant-positions/cvtp"
# `<p>Vacant positions: <span class="filter-result-text">2</span></p>`. Rendered
# by the same JavaScript that builds the cards, so a page that never rendered
# has no count either — which is why it is only ever read from a page with jobs.
_TOTAL_SELECTOR = "span.filter-result-text"
_TITLE_SELECTOR = "p.headline"
_PAGE_SIZE = 25
_WAIT_SELECTOR = f'a[href*="{_JOB_PATH}"]'


def _paginate_url(base_url: str, page: int) -> str:
    sep = "&" if "?" in base_url else "?"
    return f"{base_url.rstrip('/')}/{sep}pageSize={_PAGE_SIZE}&page={page}"


def _declared_total(soup: BeautifulSoup) -> int | None:
    """How many vacancies the filter bar says there are, if it says."""
    element = soup.select_one(_TOTAL_SELECTOR)
    if element is None:
        return None
    digits = "".join(c for c in element.get_text(strip=True) if c.isdigit())
    return int(digits) if digits else None


def _parse_page(soup: BeautifulSoup, listing_url: str, source_name: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for a in soup.find_all("a", href=lambda h: h and _JOB_PATH in h):
        href = str(a["href"]).strip()
        detail_url = urljoin(listing_url, href)
        # The card labels its title `p.headline`. Prefer that over any
        # positional guess: the anchor's only element child is the wrapping
        # div, so "the first child's text" is title *and* every metadata line
        # concatenated ("… Employment: Temporary Deadline: Sep 1, 2026").
        headline = a.select_one(_TITLE_SELECTOR)
        if headline is not None:
            title = headline.get_text(" ", strip=True)
        else:
            children_text = [
                c.get_text(" ", strip=True)
                for c in a.children
                if hasattr(c, "get_text") and c.get_text(strip=True)
            ]
            title = children_text[0] if children_text else a.get_text(" ", strip=True)
        if not title:
            continue

        # Country appears after "Country:" label in a sibling element
        location = ""
        for tag in a.find_all(True):
            text = tag.get_text(" ", strip=True)
            if text.startswith("Country:"):
                location = text.replace("Country:", "").strip()
                break

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
    source_name: str = "niras",
) -> list[dict[str, Any]]:
    from job_scraper.http import is_rendering_fetcher  # avoid circular import

    # This page needs JavaScript, but choosing the fetcher is the caller's job:
    # niras is `strategy: dynamic` in sources.yaml, so what arrives here already
    # renders. Test the capability and wrap the callable we were given rather
    # than substituting fetch_rendered — the fixture capture script records the
    # URL through its own wrapper, and an extractor that fetches through a
    # private callable records nothing at all.
    fetch_fn: Callable[..., str] = fetch_text
    if is_rendering_fetcher(fetch_text):
        fetch_fn = partial(fetch_text, wait_for_selector=_WAIT_SELECTOR)

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    page = 1

    total: int | None = None

    while True:
        url = _paginate_url(listing_url, page)
        soup = BeautifulSoup(fetch_fn(url), "lxml")
        jobs = _parse_page(soup, listing_url, source_name)
        if not jobs:
            break

        # Only a page that rendered can be asked how long the list is.
        total = _declared_total(soup) or total
        for job in jobs:
            key = job["detail_url"]
            if key not in seen:
                seen.add(key)
                out.append(job)
        if len(jobs) < _PAGE_SIZE:
            break
        page += 1

    # The filter bar counted them; this walk either has them all or it does not.
    pagination.reconcile(source_name, url, collected=len(out), total=total)
    return out
