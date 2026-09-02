"""Generic extractor for Workable-hosted job boards (apply.workable.com).

Uses the Workable public jobs API (POST):
    POST https://apply.workable.com/api/v3/accounts/{slug}/jobs
    Body: {"query":"","location":[],"department":[],"worktype":[],"remote":[]}

Returns JSON with a "results" array of job objects.
Detail URL pattern: https://apply.workable.com/{slug}/j/{shortcode}/
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from job_scraper.http import post_json

_API_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}
_EMPTY_BODY = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str,
) -> list[dict[str, Any]]:
    slug = listing_url.rstrip("/").split("/")[-1]
    api_url = f"https://apply.workable.com/api/v3/accounts/{slug}/jobs"

    # Through http.post_json, not requests directly: that is what carries the
    # owner's contact details, the robots.txt check and the per-host spacing.
    data = post_json(api_url, _EMPTY_BODY, headers=_API_HEADERS)

    out: list[dict[str, Any]] = []
    for job in data.get("results") or []:
        title = (job.get("title") or "").strip()
        if not title:
            continue

        shortcode = (job.get("shortcode") or "").strip()
        depts = job.get("department") or []
        dept = depts[0].strip() if depts else ""

        loc_obj = job.get("location") or {}
        city = (loc_obj.get("city") or "").strip()
        country = (loc_obj.get("country") or "").strip()
        location = ", ".join(x for x in [city, country] if x)

        detail_url = (
            f"https://apply.workable.com/{slug}/j/{shortcode}/"
            if shortcode
            else listing_url
        )

        raw_snippet = " ".join(x for x in [title, dept, location] if x)
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": location,
                "department": dept,
                "listing_url": listing_url,
                "detail_url": detail_url,
                "apply_url": detail_url,
                "raw_snippet": raw_snippet,
            }
        )
    return out
