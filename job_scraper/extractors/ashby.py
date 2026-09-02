"""Generic extractor for Ashby-hosted job board pages (jobs.ashbyhq.com).

Parses the JSON embedded in the page as window.__appData, which contains
the full list of job postings without requiring JavaScript execution.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

_APP_DATA_RE = re.compile(r"window\.__appData\s*=\s*")


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str,
) -> list[dict[str, Any]]:
    html = fetch_text(listing_url)

    m = _APP_DATA_RE.search(html)
    if not m:
        return []

    try:
        data, _ = json.JSONDecoder().raw_decode(html, m.end())
    except json.JSONDecodeError:
        return []

    org_slug = (data.get("organization") or {}).get("hostedJobsPageSlug", "") or ""

    # Job postings may be at different paths depending on Ashby version
    postings: list[dict[str, Any]] = (
        data.get("jobs") or (data.get("jobBoard") or {}).get("jobPostings") or []
    )

    out: list[dict[str, Any]] = []
    for job in postings:
        title = (job.get("title") or "").strip()
        if not title:
            continue
        job_id = job.get("id") or ""
        dept = (job.get("departmentName") or job.get("teamName") or "").strip()
        location = (job.get("locationName") or "").strip()
        url = (
            f"https://jobs.ashbyhq.com/{org_slug}/{job_id}" if org_slug and job_id else listing_url
        )
        raw_snippet = " ".join(x for x in [title, dept, location] if x)
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": location,
                "department": dept,
                "listing_url": listing_url,
                "detail_url": url,
                "apply_url": url,
                "raw_snippet": raw_snippet,
            }
        )
    return out
