"""Generic extractor for Greenhouse job board pages.

Uses the public Greenhouse Jobs API:
  https://boards-api.greenhouse.io/v1/boards/{board}/jobs

The board slug is derived from the listing URL's last path segment.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str,
) -> list[dict[str, Any]]:
    board = listing_url.rstrip("/").split("/")[-1]
    api_url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?per_page=500"

    data = json.loads(fetch_text(api_url))
    out: list[dict[str, Any]] = []

    for job in data.get("jobs", []):
        title = (job.get("title") or "").strip()
        if not title:
            continue
        location = (job.get("location") or {}).get("name", "") or ""
        url = job.get("absolute_url") or listing_url
        raw_snippet = " ".join(x for x in [title, location] if x)
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": location,
                "department": "",
                "listing_url": listing_url,
                "detail_url": url,
                "apply_url": url,
                "raw_snippet": raw_snippet,
            }
        )
    return out
