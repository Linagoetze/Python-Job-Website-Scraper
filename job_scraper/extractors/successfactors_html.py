"""Generic extractor for SAP SuccessFactors hosted career sites (HTML variant).

These portals serve job listings as server-rendered HTML at a /search/ or
/search URL. Job links follow the pattern:
    <a href="/job/{Location}-{Title}/{ID}/">…</a>

Pagination uses a ?startrow=N query parameter. Loop until a page returns no
job links or the page_step would exceed a reasonable upper bound.

Supports two modes selected via the `dynamic` flag:
  static (default) — fetch with requests (fetch_text); works for DSV, Novo
      Nordisk, Coloplast where static HTML is fully server-rendered.
  dynamic — fetch with Playwright (fetch_rendered); needed for ISS which uses
      AJAX infinite scroll (the ?start=N parameter alone is not enough).

Known instances:
  DSV        https://jobs.dsv.com/search/           page_step=10
  Novo Nordisk https://careers.novonordisk.com/search  page_step=100
  Coloplast  https://careers.coloplast.com/search/  page_step=25
  ISS        https://jobs.issworld.com/search/      dynamic=True
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

_WAIT_SELECTOR = 'a[href^="/job/"]'


def _set_startrow(base_url: str, startrow: int) -> str:
    parsed = urlparse(base_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params["startrow"] = [str(startrow)]
    # preserve other params (q, sortColumn, sortDirection …)
    new_query = urlencode({k: v[0] for k, v in params.items()})
    return urlunparse(parsed._replace(query=new_query))


def _parse_page(
    html: str,
    base_search_url: str,
    source_name: str,
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    out: list[dict[str, Any]] = []

    # Derive the host root from the search URL so relative hrefs resolve correctly
    parsed = urlparse(base_search_url)
    host_root = f"{parsed.scheme}://{parsed.netloc}"

    for a in soup.find_all("a", href=lambda h: h and h.startswith("/job/")):
        href = str(a["href"]).strip()
        detail_url = urljoin(host_root, href)

        title = a.get_text(" ", strip=True)
        if not title:
            continue

        # Location and metadata sit in sibling/parent elements.
        # SuccessFactors wraps each row in a <li> or <tr>; look for the
        # nearest container that has location-like text.
        location = ""
        department = ""
        container = a.find_parent(["li", "tr", "div"])
        if container:
            texts = [
                t.strip()
                for t in container.stripped_strings
                if t.strip() and t.strip() != title
            ]
            # Heuristic: first non-title text is usually location or job family
            if texts:
                location = texts[0]
            if len(texts) >= 2:
                department = texts[1]

        raw_snippet = " ".join(x for x in [title, department, location] if x)
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": location,
                "department": department,
                "listing_url": base_search_url,
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
    page_step: int,
    base_search_url: str,
    dynamic: bool = False,
) -> list[dict[str, Any]]:
    from job_scraper.http import fetch_rendered  # avoid circular import

    if dynamic:
        fetch_fn: Callable[[str], str] = partial(
            fetch_rendered, wait_for_selector=_WAIT_SELECTOR
        )
    else:
        fetch_fn = fetch_text

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    startrow = 0

    while True:
        url = _set_startrow(base_search_url, startrow)
        html = fetch_fn(url)
        jobs = _parse_page(html, base_search_url, source_name)
        if not jobs:
            break
        new_jobs = 0
        for job in jobs:
            key = job["detail_url"]
            if key not in seen:
                seen.add(key)
                out.append(job)
                new_jobs += 1
        if new_jobs == 0:
            break  # all duplicates — we've lapped the list
        if len(jobs) < page_step:
            break
        startrow += page_step

    return out
