"""Generic extractor for SmartRecruiters-hosted job boards.

Uses the public SmartRecruiters Jobs API:
  GET https://api.smartrecruiters.com/v1/companies/{org_slug}/postings?limit=100&offset=0

The org_slug is passed explicitly (not derived from the listing URL) because
SmartRecruiters slugs are case-sensitive and not always predictable from the
hosted career-site URL.

Pagination: the API returns a JSON envelope with `totalFound` and `content`.
Loop using offset until all postings are fetched. `totalFound` is authoritative,
so an empty `content` before it is reached is a failure rather than the end of
the board; see `pagination.py`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from job_scraper.extractors import pagination

_API_BASE = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
_PAGE_SIZE = 100


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str,
    org_slug: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    offset = 0
    base = _API_BASE.format(slug=org_slug)

    while True:
        api_url = f"{base}?limit={_PAGE_SIZE}&offset={offset}"
        data = json.loads(fetch_text(api_url))

        total = data.get("totalFound", 0)
        content = data.get("content", [])
        if not content:
            if offset < total:
                pagination.short_walk(
                    source_name,
                    api_url,
                    collected=len(out),
                    promised=f"the API reports {total} posting(s)",
                )
            break

        for posting in content:
            job_id = posting.get("id", "")
            title = (posting.get("name") or "").strip()
            if not title or not job_id:
                continue

            # Build detail URL from relativeUri (always present) or id
            relative = (posting.get("relativeUri") or "").lstrip("/")
            detail_url = (
                f"https://jobs.smartrecruiters.com/{relative}"
                if relative
                else f"https://jobs.smartrecruiters.com/{org_slug}/{job_id}"
            )
            if detail_url in seen:
                continue
            seen.add(detail_url)

            loc = posting.get("location") or {}
            city = (loc.get("city") or "").strip()
            country = (loc.get("country") or "").strip()
            location = ", ".join(x for x in [city, country] if x)

            dept_obj = posting.get("department") or {}
            department = (dept_obj.get("label") or "").strip()

            apply_url = (posting.get("applyUrl") or detail_url).strip()

            raw_snippet = " ".join(x for x in [title, department, location] if x)
            out.append(
                {
                    "source_name": source_name,
                    "title": title,
                    "location": location,
                    "department": department,
                    "listing_url": listing_url,
                    "detail_url": detail_url,
                    "apply_url": apply_url,
                    "raw_snippet": raw_snippet,
                }
            )

        offset += _PAGE_SIZE
        if offset >= total:
            break

    return out
