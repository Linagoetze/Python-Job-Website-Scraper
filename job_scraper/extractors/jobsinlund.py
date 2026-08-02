"""Extractor for jobsinlund.com (Jobsinnetwork platform).

Jobs are served by the central search API at search-api.jobsinnetwork.services,
not the local /api/jobs endpoint. The API requires provider UUIDs specific to
the Lund board (extracted from the page's globalVariables / queryParams) and
country filtering to avoid Norwegian Lund results.
Pagination follows hydra:view.hydra:next relative paths.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlencode

_STALE_DAYS = 30

_SEARCH_BASE = "https://search-api.jobsinnetwork.services"

# Provider UUIDs injected by the page as queryParams.providerUuid
_PROVIDER_UUIDS = [
    "36977fc0-ea73-41e4-b790-6cc27e48986d",
    "542aa111-ecb1-4d6f-acda-ae09b1e8a619",
    "1a17eb00-d5ec-4b18-a680-4f382d90e63b",
    "2d53d6a1-ec3a-44e9-8d67-5910c90ba1b7",
    "c292deec-ca24-48f0-9f5c-e61253a322b0",
    "3c7b0504-b2d8-4fc8-8092-36bcae739bab",
    "49a4195c-5130-40a5-8fca-68fb70bc36f4",
    "7afe4264-b157-4e32-b316-10dc6a081906",
    "883dd5cf-bca1-463b-9493-f9486c3c1903",
    "bf71f4ae-63ef-4643-9062-07ab407b261d",
    "485e43ba-ab3e-42e8-835c-e56db525603b",
    "b1520b44-98a3-47b7-bd8e-815d4bb5da92",
    "eef5be1c-72b7-4ff0-9535-9816931e9b88",
]

_PARAMS = (
    "published=true"
    "&location.address=Lund"
    "&location.countryCode%5B%5D=SE"   # avoid Norwegian Lund results
    "&language=en"
    "&limit=25"
    "&" + "&".join(f"providerUuid%5B%5D={u}" for u in _PROVIDER_UUIDS)
)
_FIRST_PAGE = f"{_SEARCH_BASE}/api/jobs.jsonld?page=1&{_PARAMS}"


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str = "jobsinlund",
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    next_url: str | None = _FIRST_PAGE

    while next_url:
        data = json.loads(fetch_text(next_url))
        for job in data.get("hydra:member", []):
            title = (job.get("title") or "").strip()
            if not title:
                continue

            detail_url = (job.get("view_url") or "").strip()
            if not detail_url or detail_url in seen:
                continue
            seen.add(detail_url)

            raw_date = (job.get("published_at") or "").strip()
            if raw_date:
                try:
                    posted = datetime.fromisoformat(raw_date.rstrip("Z")).replace(tzinfo=timezone.utc).date()
                    if (date.today() - posted).days > _STALE_DAYS:
                        continue
                except ValueError:
                    pass

            loc = job.get("location") or {}
            city = (loc.get("city") or "Lund").strip().title()
            country = (loc.get("country") or "").strip()
            location = f"{city}, {country}" if country else city

            dept = (job.get("functions") or "").strip()
            company = ((job.get("company") or {}).get("name") or "").strip()
            apply_url = (job.get("application_url") or job.get("url") or detail_url).strip()
            raw_snippet = " ".join(x for x in [title, dept, location] if x)

            out.append(
                {
                    "source_name": source_name,
                    "title": title,
                    "company": company,
                    "location": location,
                    "department": dept,
                    "listing_url": listing_url,
                    "detail_url": detail_url,
                    "apply_url": apply_url,
                    "raw_snippet": raw_snippet,
                }
            )

        raw_next = data.get("hydra:view", {}).get("hydra:next")
        next_url = (_SEARCH_BASE + raw_next) if raw_next else None

    return out
