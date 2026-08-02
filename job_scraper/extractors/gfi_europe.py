"""Extractor for Good Food Institute Europe careers page.

HTML structure (WordPress, static):
    <a href="/careers/[slug]">Job Title</a>
Jobs are linked as standard anchor tags with paths under /careers/.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

BASE_URL = "https://gfieurope.org"


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str = "gfi_europe",
) -> list[dict[str, Any]]:
    html = fetch_text(listing_url)
    soup = BeautifulSoup(html, "lxml")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        # Only internal /careers/ links; skip the listing page itself
        if not href.startswith("/careers/"):
            continue
        if href.rstrip("/") == "/careers":
            continue
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        detail_url = urljoin(BASE_URL, href)
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
