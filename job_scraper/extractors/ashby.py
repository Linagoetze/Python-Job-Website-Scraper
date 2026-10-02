"""Generic extractor for Ashby-hosted job boards (jobs.ashbyhq.com).

Reads Ashby's public posting API, which Ashby publishes for career sites to
build on:
    GET https://api.ashbyhq.com/posting-api/job-board/{board}

One request returns every listed posting with its full description, which this
reader supplies to Layer 5 as plain text (SP4d, the owner's choice of route).
Until SP4d it read `window.__appData` from the board page, whose postings carry
no description, and every detail page is a JS shell to a static fetch, so no
Ashby posting was ever read (SP4b).

The detail URL is built from the board slug and the posting id, as it was from
the board page, rather than taken from the API's `jobUrl`. It is the dedupe key,
and the two agreed on every posting captured on 2026-10-02; building it keeps a
change to `jobUrl` from making every stored job look new.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

_API = "https://api.ashbyhq.com/posting-api/job-board/"


def board_slug(listing_url: str) -> str:
    """`kognity` from `https://jobs.ashbyhq.com/kognity/`, as the board is named."""
    return urlsplit(listing_url).path.strip("/").split("/")[0]


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str,
) -> list[dict[str, Any]]:
    slug = board_slug(listing_url)
    if not slug:
        raise ValueError(f"{source_name}: no Ashby board name in {listing_url}")
    api_url = _API + slug

    # A body this reader cannot read must fail, not return [], which reads as
    # "no vacancies" (priority 2; personio.py since SP4). A board with nothing
    # open answers with an empty `jobs` list, which is a different thing.
    try:
        data = json.loads(fetch_text(api_url))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source_name}: the Ashby posting API at {api_url} is not JSON") from exc
    postings = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(postings, list):
        raise ValueError(f"{source_name}: no `jobs` list in the Ashby posting API at {api_url}")

    out: list[dict[str, Any]] = []
    for job in postings:
        title = (job.get("title") or "").strip()
        job_id = (job.get("id") or "").strip()
        if not title or not job_id or job.get("isListed") is False:
            continue
        dept = (job.get("department") or job.get("team") or "").strip()
        location = (job.get("location") or "").strip()
        url = f"https://jobs.ashbyhq.com/{slug}/{job_id}"
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
                "description_text": (job.get("descriptionPlain") or "").strip(),
            }
        )
    return out
