"""Generic extractor for Workable-hosted job boards (apply.workable.com).

Uses the Workable public jobs API (POST):
    POST https://apply.workable.com/api/v3/accounts/{slug}/jobs
    Body: {"query":"","location":[],"department":[],"worktype":[],"remote":[]}

Returns JSON with a "results" array of job objects.
Detail URL pattern: https://apply.workable.com/{slug}/j/{shortcode}/

The POST goes through the fetcher this reader is handed, as workday.py's does
(SP3b): `http.fetch_text` and `http.fetch_rendered` carry `http.post_json` as
their `post_json`, and the fixture capture script and the probe hand in
fetchers that record or replay it. Until SP4 this module called
`http.post_json` directly, which meant the capture script recorded nothing for
it ("extractor made no request") and the probe stubbed it at the module
instead of through its own fetcher. A fetcher that cannot POST is refused
rather than bypassed, so no caller reaches the network by a route it did not
choose.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_API_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}
_EMPTY_BODY = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}


def extract(
    listing_url: str,
    fetch_text: Callable[..., str],
    source_name: str,
) -> list[dict[str, Any]]:
    post_json = getattr(fetch_text, "post_json", None)
    if post_json is None:
        raise TypeError(
            f"{source_name}: workable.py reads the board's JSON API and was handed a "
            "fetcher with no post_json; pass http.fetch_text or http.fetch_rendered, "
            "or a wrapper that carries theirs"
        )
    slug = listing_url.rstrip("/").split("/")[-1]
    api_url = f"https://apply.workable.com/api/v3/accounts/{slug}/jobs"

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
            f"https://apply.workable.com/{slug}/j/{shortcode}/" if shortcode else listing_url
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
