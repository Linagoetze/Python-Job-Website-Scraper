"""Extractor for Asana jobs page (Playwright-rendered).

HTML structure per listing:
    <a class="... e1ucmllm1" href="/jobs/apply/ID">
      <p>Job Title</p>
      <p>Location</p>
    </a>
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str = "asana",
) -> list[dict[str, Any]]:
    html = fetch_text(listing_url)
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    for a in soup.find_all("a", href=lambda h: h and "/jobs/apply/" in str(h)):
        href = str(a["href"])
        full = urljoin(listing_url, href)
        if full in seen:
            continue
        seen.add(full)

        paras = [c for c in a.children if isinstance(c, Tag) and c.name == "p"]
        title = paras[0].get_text(" ", strip=True) if len(paras) >= 1 else ""
        location = paras[1].get_text(" ", strip=True) if len(paras) >= 2 else ""

        if not title:
            continue

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
