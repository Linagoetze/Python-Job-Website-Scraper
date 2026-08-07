from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

_BASE = "https://www.impactpool.org"
_MAX_PAGES = 200


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    page = 1

    # No per_page param: the site 500s on ?page=1&per_page=40, and the default
    # page size is 40 anyway.
    while page <= _MAX_PAGES:
        url = f"{_BASE}/search?page={page}"
        html = fetch_text(url)
        soup = BeautifulSoup(html, "lxml")

        jobs = soup.find_all("div", class_="job")
        if not jobs:
            break

        for job in jobs:
            a = job.find("a")
            if not a:
                continue
            href = a.get("href", "")
            if not href or not href.startswith("/jobs/"):
                continue

            detail_url = urljoin(_BASE, href)
            if detail_url in seen:
                continue
            seen.add(detail_url)

            divs = [d.get_text(" ", strip=True) for d in a.find_all("div", class_="ip-typography")]
            title = divs[0] if len(divs) > 0 else ""
            company = divs[1] if len(divs) > 1 else ""
            location = divs[2] if len(divs) > 2 else ""

            if not title:
                continue

            raw_snippet = " ".join(x for x in [title, location] if x)
            out.append({
                "source_name": source_name,
                "title": title,
                "company": company,
                "location": location,
                "department": "",
                "listing_url": listing_url,
                "detail_url": detail_url,
                "apply_url": detail_url,
                "raw_snippet": raw_snippet,
            })

        page += 1

    return out
