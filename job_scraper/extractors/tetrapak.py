"""Extractor for Tetra Pak (SAP SuccessFactors platform).

Uses the internal career site REST API (POST, JSON body) at
/services/recruiting/v1/jobs. No authentication required.
Paginates until all jobs are fetched. Location filtering is
left to the pipeline's rules (fetching all jobs avoids
discrepancies with the website's location filter).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from job_scraper.http import post_json

_API_URL = "https://jobs.tetrapak.com/services/recruiting/v1/jobs"
_DETAIL_BASE = "https://jobs.tetrapak.com/job-detail"
# The API always returns 10 results per page regardless of pageSize.
_ACTUAL_PAGE_SIZE = 10


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str = "tetrapak",
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    page = 0

    while True:
        # Through http.post_json, not requests directly. This loop walks the
        # whole board ten postings at a time and used to do it as fast as the
        # API would answer; it now takes its turn at the host like everything
        # else, which is the point of the package that added it.
        data = post_json(
            _API_URL,
            {"q": "", "locale": "en_GB", "location": "Sweden", "pageNum": page},
        )

        results = data.get("jobSearchResult", [])
        if not results:
            break

        for item in results:
            job = item.get("response", {})
            job_id = str(job.get("id") or "").strip()
            url_title = (job.get("urlTitle") or job.get("unifiedUrlTitle") or "").strip()
            if not job_id or not url_title:
                continue

            detail_url = f"{_DETAIL_BASE}/{job_id}/{url_title}"
            if detail_url in seen:
                continue
            seen.add(detail_url)

            title = (job.get("unifiedStandardTitle") or url_title).strip()
            locations = [loc.strip() for loc in (job.get("jobLocationShort") or [])]
            location = ", ".join(loc for loc in locations if loc)
            dept_list = job.get("filter2") or []
            dept = ", ".join(dept_list) if dept_list else ""
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

        total = data.get("totalJobs", 0)
        if (page + 1) * _ACTUAL_PAGE_SIZE >= total:
            break
        page += 1

    return out
