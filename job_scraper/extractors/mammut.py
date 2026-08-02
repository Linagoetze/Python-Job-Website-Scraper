"""Extractor for Mammut recruiting page.

HTML structure:
    <tr>
      <td><img /></td>
      <td>
        <span class="table-as-list__subtitle"><a href="/Vacancies/ID/...">Job Title</a></span>
        <span class="table-as-list__subtitle">| Employment Type</span>
        <span class="table-as-list__subtitle">|  Location</span>
      </td>
    </tr>
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

_VACANCY_HREF = re.compile(r"/Vacancies/\d+")


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str = "mammut",
) -> list[dict[str, Any]]:
    html = fetch_text(listing_url)
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    for a in soup.find_all("a", href=_VACANCY_HREF):
        full = urljoin(listing_url, str(a["href"]))
        if full in seen:
            continue
        seen.add(full)

        # Title: text of the link, excluding any nested <img> alt text
        for img in a.find_all("img"):
            img.decompose()
        title = a.get_text(" ", strip=True)
        if not title:
            continue

        # Location: 3rd table-as-list__subtitle span in the parent <td>, strip leading "|"
        location = ""
        td = a.find_parent("td")
        if isinstance(td, Tag):
            subtitles = td.find_all("span", class_="table-as-list__subtitle")
            if len(subtitles) >= 3:
                location = subtitles[2].get_text(" ", strip=True).lstrip("|").strip()

        raw_snippet = " ".join(x for x in [title, location] if x)
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": location,
                "department": "",
                "listing_url": listing_url,
                "detail_url": full,
                "apply_url": full,
                "raw_snippet": raw_snippet,
            }
        )
    return out
