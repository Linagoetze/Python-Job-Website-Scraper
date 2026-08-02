"""Extractor for NIRAS vacant positions (dynamic / Playwright-rendered).

The listing page renders job cards client-side. Static HTML contains only
the filter shell. After JS execution each job appears as:
    <a href="/jobs/vacant-positions/cvtp-NNNN-slug/">
      <generic>Title</generic>
      <generic>Country: …</generic>
      <generic>Employment: …</generic>
      <generic>Deadline: …</generic>
    </a>

Pagination: ?pageSize=25&page=N — loop until a page returns no job links.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

_JOB_PATH = "/jobs/vacant-positions/cvtp"
_PAGE_SIZE = 25
_WAIT_SELECTOR = f'a[href*="{_JOB_PATH}"]'


def _paginate_url(base_url: str, page: int) -> str:
    sep = "&" if "?" in base_url else "?"
    return f"{base_url.rstrip('/')}/{sep}pageSize={_PAGE_SIZE}&page={page}"


def _parse_page(html: str, listing_url: str, source_name: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    out: list[dict[str, Any]] = []
    for a in soup.find_all("a", href=lambda h: h and _JOB_PATH in h):
        href = str(a["href"]).strip()
        detail_url = urljoin(listing_url, href)
        title_tag = a.find(string=True, recursive=False) or a.get_text(" ", strip=True)
        # Job cards wrap text in <generic> tags; grab the first non-empty child text
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
    from job_scraper.http import fetch_rendered  # avoid circular import

    # Always use Playwright; upgrade with selector-based wait for reliability
    if fetch_text is fetch_rendered:
        fetch_fn = partial(fetch_rendered, wait_for_selector=_WAIT_SELECTOR)
    else:
        fetch_fn = partial(fetch_rendered, wait_for_selector=_WAIT_SELECTOR)

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    page = 1

    while True:
        url = _paginate_url(listing_url, page)
        html = fetch_fn(url)
        jobs = _parse_page(html, listing_url, source_name)
        if not jobs:
            break
        for job in jobs:
            key = job["detail_url"]
            if key not in seen:
                seen.add(key)
                out.append(job)
        if len(jobs) < _PAGE_SIZE:
            break
        page += 1

    return out
