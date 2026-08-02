"""Extractor for Giving What We Can careers page.

HTML structure (Next.js, jobs visible in static HTML):
    <h2 id="current-openings">Current openings</h2>
    <ul class="list-disc ...">
      <li><a href="/[job-slug]">Job Title</a> (deadline info)</li>
    </ul>
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

BASE_URL = "https://www.givingwhatwecan.org"


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str = "giving_what_we_can",
) -> list[dict[str, Any]]:
    html = fetch_text(listing_url)
    soup = BeautifulSoup(html, "lxml")
    out: list[dict[str, Any]] = []

    heading = soup.find("h2", id="current-openings")
    if heading is None:
        return out

    ul = heading.find_next_sibling("ul")
    if ul is None:
        return out

    for li in ul.find_all("li"):
        a = li.find("a", href=True)
        if not a:
            continue
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        href = a["href"]
        detail_url = urljoin(BASE_URL, href) if href.startswith("/") else href
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
