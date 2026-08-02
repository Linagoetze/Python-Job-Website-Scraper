"""Extractor for J-PAL (Poverty Action Lab) careers page.

HTML structure per listing (Drupal 10, server-rendered, paginated via ?page=N):
    <div class="node node--type-job node--view-mode-teaser">
        <h3 class="job-teaser-title"><a href="/careers/[slug]">Title</a></h3>
        <div class="job-teaser-country">Country</div>
    </div>

Pagination uses zero-indexed ?page=N query parameter. The last page is
determined from the highest page number found in pagination links.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

BASE_URL = "https://www.povertyactionlab.org"


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
    """Return the highest page index found in pagination links (0-indexed)."""
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
    while True:
        url = base if page == 0 else f"{base}?page={page}"
        html = fetch_text(url)
        soup = BeautifulSoup(html, "lxml")

        jobs = _parse_jobs(soup, listing_url, source_name)
        out.extend(jobs)

        last = _last_page(soup)
        if page >= last:
            break
        page += 1

    return out
