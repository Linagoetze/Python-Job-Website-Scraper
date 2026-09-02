"""Extractor for Good Food Institute Europe careers page.

HTML structure (WordPress, static):
    <a href="https://gfieurope.org/careers/[slug]">Job Title</a>
Jobs are linked as standard anchor tags under the site's own `/careers/` path.

The links are matched on their *resolved* path, not on the raw `href` string.
The page moved from root-relative hrefs (`/careers/[slug]`) to absolute ones
(`https://gfieurope.org/careers/[slug]`) at some point before 2026-09, and a
prefix test against the raw string silently stopped matching anything —
nineteen runs of zero postings. Resolving first also keeps three near misses
out: the German listing (`/de/careers/`), the FAQ page (`/careers-at-…`, which
shares the prefix but is not a posting), and GFI's global board on the separate
`gfi.org` host.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

_CAREERS_PREFIX = "/careers/"


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str = "gfi_europe",
) -> list[dict[str, Any]]:
    html = fetch_text(listing_url)
    soup = BeautifulSoup(html, "lxml")
    listing = urlparse(listing_url)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        detail_url = urljoin(listing_url, str(a["href"]))
        parts = urlparse(detail_url)
        # Same host only: the page also links GFI's global board on gfi.org.
        if parts.netloc != listing.netloc:
            continue
        path = parts.path
        # A posting lives *under* /careers/; the listing itself is not one.
        if not path.startswith(_CAREERS_PREFIX) or path.rstrip("/") == _CAREERS_PREFIX.rstrip("/"):
            continue
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        if detail_url in seen:
            continue
        seen.add(detail_url)
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": "",
                "department": "",
                "listing_url": listing_url,
                "detail_url": detail_url,
                "apply_url": detail_url,
                "raw_snippet": title,
            }
        )
    return out
