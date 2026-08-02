"""Generic extractor for Lever-hosted job boards.

Uses the public Lever postings API (no auth required):
    https://api.lever.co/v0/postings/{org}

The org slug is derived from the listing URL's hostname or last path segment.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str,
    org_slug: str,
) -> list[dict[str, Any]]:
    api_url = f"https://api.lever.co/v0/postings/{org_slug}"
    data = json.loads(fetch_text(api_url))

    out: list[dict[str, Any]] = []
    for job in data:
        title = (job.get("text") or "").strip()
        if not title:
            continue
        cats = job.get("categories") or {}
        location = (cats.get("location") or "").strip()
        dept = (cats.get("department") or cats.get("team") or "").strip()
        url = job.get("hostedUrl") or listing_url
        apply_url = job.get("applyUrl") or url
        raw_snippet = " ".join(x for x in [title, dept, location] if x)
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": location,
                "department": dept,
                "listing_url": listing_url,
                "detail_url": url,
                "apply_url": apply_url,
                "raw_snippet": raw_snippet,
            }
        )
    return out
