"""Extractor for Oatly Teamtailor careers job listing page."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_scraper.urlutil import oatly_canonical_job_url

_JOB_PATH = re.compile(r"/jobs/\d+")

_MIDDOT = ("\u00b7", "·")


def extract(listing_url: str, fetch_text: Callable[[str], str], source_name: str = "oatly") -> list[dict[str, Any]]:
    html = fetch_text(listing_url)
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    for a in soup.select('a[href*="careers.oatly.com/jobs/"], a[href^="/jobs/"]'):
        href = a.get("href")
        if not href or not isinstance(href, str):
            continue
        full = urljoin(listing_url, href)
        full = oatly_canonical_job_url(listing_url, full)
        path = urlparse(full).path
        if not _JOB_PATH.search(path):
            continue
        if full in seen:
            continue
        seen.add(full)

        title = a.get_text(" ", strip=True)
        department = ""
        location = ""
        parent = a.parent
        if parent:
            div = parent.find("div", class_=lambda c: bool(c and "mt-1" in c.split()))
            if div:
                parts: list[str] = []
                for span in div.find_all("span", recursive=False):
                    t = span.get_text(" ", strip=True)
                    if not t or t in _MIDDOT:
                        continue
                    parts.append(t)
                if len(parts) >= 2:
                    department = parts[0]
                    location = parts[-1]
                elif len(parts) == 1:
                    department = parts[0]

        raw_bits = [title, department, location]
        raw_snippet = " ".join(x for x in raw_bits if x).strip()
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": location,
                "department": department,
                "listing_url": listing_url,
                "detail_url": full,
                "apply_url": full,
                "raw_snippet": raw_snippet,
            }
        )
    return out
