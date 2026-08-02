"""Extractor for Sida (Swedish International Development Cooperation Agency).

Jobs are listed as anchors in the main content of the Swedish-language page:
    <a href="/jobba-med-bistand/jobba-pa-sida/lediga-tjanster/5621-...">
      Sida söker 1-2 controller…
    </a>

Applying redirects to an external ReachMee portal (login required there),
but browsing and title/location extraction work without authentication.

No pagination — Sida typically has only a handful of open positions.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

_JOB_PATH = "/lediga-tjanster/"


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str = "sida",
) -> list[dict[str, Any]]:
    html = fetch_text(listing_url)
    soup = BeautifulSoup(html, "lxml")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=lambda h: h and _JOB_PATH in h):
        href = str(a["href"]).strip()
        # Skip the breadcrumb link back to the listing page itself
        if href.rstrip("/") == listing_url.rstrip("/"):
            continue

        detail_url = urljoin(listing_url, href)
        if detail_url in seen:
            continue
        seen.add(detail_url)

        title = a.get_text(" ", strip=True)
        if not title:
            continue

        raw_snippet = title
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": "Stockholm, Sweden",  # Sida HQ; detail page has exact office
                "department": "",
                "listing_url": listing_url,
                "detail_url": detail_url,
                "apply_url": detail_url,
                "raw_snippet": raw_snippet,
            }
        )

    return out
