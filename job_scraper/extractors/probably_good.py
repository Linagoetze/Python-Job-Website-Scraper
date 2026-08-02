"""Extractor for Probably Good job listings page.

HTML structure per listing:
    <div>
      <img ...>
      <h4>Job Title</h4>
      <p>Organization</p>
      <p>Added Apr 3</p>
      <p>Location</p>
      <p>Job Type</p>
      ...
      <a href="https://...">Job Details</a>
    </div>
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bs4 import BeautifulSoup


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str = "probably_good",
) -> list[dict[str, Any]]:
    html = fetch_text(listing_url)
    soup = BeautifulSoup(html, "lxml")
    out: list[dict[str, Any]] = []

    for h4 in soup.find_all("h4"):
        title = h4.get_text(" ", strip=True)
        if not title:
            continue

        container = h4.parent
        if container is None:
            continue

        # Detail link
        detail_a = container.find("a", href=True)
        detail_url = str(detail_a["href"]) if detail_a else listing_url

        # <p> elements: [0]=org, [1]=date added, [2]=location, ...
        paras = container.find_all("p")
        org = paras[0].get_text(" ", strip=True) if len(paras) > 0 else ""
        location = paras[2].get_text(" ", strip=True) if len(paras) > 2 else ""

        raw_snippet = " ".join(x for x in [title, org, location] if x)
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": location,
                "department": org,
                "listing_url": listing_url,
                "detail_url": detail_url,
                "apply_url": detail_url,
                "raw_snippet": raw_snippet,
            }
        )
    return out
