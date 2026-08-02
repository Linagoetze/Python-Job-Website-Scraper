"""Generic extractor for Breezy HR job boards.

Uses the public JSON endpoint: https://{company}.breezy.hr/json
The base URL is derived from the listing URL (strip trailing slash, append /json).
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
    base = listing_url.rstrip("/")
    api_url = f"{base}/json"
    data = json.loads(fetch_text(api_url))

    out: list[dict[str, Any]] = []
    for job in data:
        title = (job.get("name") or "").strip()
        if not title:
            continue
        url = job.get("url") or listing_url
        location = (job.get("location") or {}).get("name", "") or ""
        department = (job.get("department") or "").strip()
        raw_snippet = " ".join(x for x in [title, department, location] if x)
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": location,
                "department": department,
                "listing_url": listing_url,
                "detail_url": url,
                "apply_url": url,
                "raw_snippet": raw_snippet,
            }
        )
    return out
