"""Extractor for Coefficient Giving careers page (WordPress + Ashby Apply links)."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from bs4 import BeautifulSoup

from job_scraper.urlutil import normalize_http_url

_ASHBY_JOB = re.compile(
    r"^https://jobs\.ashbyhq\.com/coefficientgiving/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def extract(listing_url: str, fetch_text: Callable[[str], str], source_name: str = "coefficient_giving") -> list[dict[str, Any]]:
    html = fetch_text(listing_url)
    soup = BeautifulSoup(html, "lxml")
    h2 = soup.find(id="0-open-roles")
    if not h2:
        return []
    table = h2.find_next("table")
    if not table:
        return []
    tbody = table.find("tbody") or table
    out: list[dict[str, Any]] = []
    for tr in tbody.find_all("tr", recursive=False):
        apply_a = None
        for a in tr.find_all("a", class_="content-button", href=True):
            href = str(a.get("href", "")).strip()
            if "/form/" in href:
                continue
            if _ASHBY_JOB.match(href):
                apply_a = a
                break
        if apply_a is None:
            continue
        apply_url = normalize_http_url(str(apply_a["href"]))
        first_td = tr.find_all("td", recursive=False)
        if not first_td:
            continue
        cell = first_td[0]
        strong = cell.find("strong")
        title = strong.get_text(" ", strip=True) if strong else ""
        em = cell.find("em")
        location = em.get_text(" ", strip=True) if em else ""
        if not title:
            title = cell.get_text(" ", strip=True)
        raw_snippet = " ".join(x for x in (title, location) if x).strip()
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": location,
                "department": "",
                "listing_url": listing_url,
                "detail_url": apply_url,
                "apply_url": apply_url,
                "raw_snippet": raw_snippet,
            }
        )
    return out
