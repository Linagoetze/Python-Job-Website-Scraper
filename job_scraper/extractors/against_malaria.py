"""Extractor for Against Malaria Foundation vacancies page.

HTML structure:
    <h3>Job Title - posted 12th March</h3>
    <p>Description... <a href="newsitem.aspx?newsitem=...">More details</a></p>
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

_POSTED_SUFFIX = re.compile(r"\s*-\s*posted\s+.*$", re.IGNORECASE)


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str = "against_malaria_foundation",
) -> list[dict[str, Any]]:
    html = fetch_text(listing_url)
    soup = BeautifulSoup(html, "lxml")
    out: list[dict[str, Any]] = []

    for h3 in soup.find_all("h3"):
        raw = h3.get_text(" ", strip=True)
        title = _POSTED_SUFFIX.sub("", raw).strip()
        if not title:
            continue

        # Find the nearest "More details" link in following siblings
        detail_url = listing_url
        for sib in h3.find_next_siblings():
            a = sib.find("a", href=True)
            if a:
                detail_url = urljoin(listing_url, str(a["href"]))
                break

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
