"""Generic extractor for HiBob career sites ({company}.careers.hibob.com).

A HiBob career page is a client-rendered shell: the static HTML holds no text
and the cards the rendered page shows are not links, which is why the probe
found nothing on it (SP5). The page fills itself from the platform's own JSON
endpoint, on the board's host:

    GET https://{company}.careers.hibob.com/api/job-ad
    Header: companyIdentifier: {company}

The endpoint is public. Without the header it answers 401, which looks like a
token wall and is not one: the page's script adds the header itself, copying the
first label of its own hostname (SP16 read this in the page's bundle and
confirmed it with a plain request). The value is the subdomain, a name and not
a secret, so nothing here is stored or fixture-worthy.

One response holds every posting, `jobAdDetails`, each with its full HTML
description, which this reader supplies to Layer 5 as plain text; the posting
page itself is a shell to a static fetch. The response states no total (only
`filterGroups`, the filter menus), so there is nothing to reconcile a count
against. A body that is not the expected shape fails the source rather than
returning `[]`, which would read as "no vacancies"; a board with nothing open
answers with an empty `jobAdDetails` list, which is a different thing.

The posting names a `site` ("Head Office", "Flagship Store") and a `country`.
The site is a label the employer chose, not always a place, so the location is
the country, and the site goes into `raw_snippet` beside it where a city in its
name can still be read. `workspaceTypeId`
(on_site, hybrid or remote) goes into `raw_snippet` as a word Layer 0's remote
keywords and hybrid gate already look for (SP4f); on_site adds nothing.

The detail URL is the board's `/jobs/{id}` route, which the shell renders.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

# The workplace types that say something Layer 0 can read, in the words its
# remote keywords and hybrid gate look for.
_WORKPLACE_WORDS = {"remote": "Remote", "hybrid": "Hybrid"}


def company_identifier(listing_url: str) -> str:
    """`acme` from `https://acme.careers.hibob.com/jobs`, as the page's script sends it."""
    host = urlsplit(listing_url).hostname or ""
    return host.split(".")[0] if host.count(".") >= 3 else ""


def extract(
    listing_url: str,
    fetch_text: Callable[..., str],
    source_name: str,
) -> list[dict[str, Any]]:
    parts = urlsplit(listing_url)
    identifier = company_identifier(listing_url)
    if not parts.scheme or not parts.netloc or not identifier:
        raise ValueError(
            f"{source_name}: no HiBob company in {listing_url}; expected "
            "https://{company}.careers.hibob.com/jobs"
        )
    origin = f"{parts.scheme}://{parts.netloc}"
    api_url = f"{origin}/api/job-ad"

    try:
        data = json.loads(fetch_text(api_url, headers={"companyIdentifier": identifier}))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source_name}: the HiBob job-ad API at {api_url} is not JSON") from exc
    postings = data.get("jobAdDetails") if isinstance(data, dict) else None
    if not isinstance(postings, list):
        raise ValueError(
            f"{source_name}: no `jobAdDetails` list in the HiBob job-ad API at {api_url}"
        )

    out: list[dict[str, Any]] = []
    for job in postings:
        title = (job.get("title") or "").strip()
        job_id = str(job.get("id") or "").strip()
        if not title or not job_id:
            continue
        dept = (job.get("department") or "").strip()
        site = (job.get("site") or "").strip()
        location = (job.get("country") or "").strip()
        workplace = _WORKPLACE_WORDS.get(str(job.get("workspaceTypeId") or "").strip().lower(), "")
        url = f"{origin}/jobs/{job_id}"
        raw_snippet = " ".join(x for x in [title, dept, site, location, workplace] if x)
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
                "description_text": _description(job),
            }
        )
    return out


def _description(job: dict[str, Any]) -> str:
    """The posting's sections as plain text; the ones a posting leaves null are skipped."""
    sections = (job.get(k) for k in ("description", "responsibilities", "requirements", "benefits"))
    html = " ".join(s for s in sections if isinstance(s, str) and s.strip())
    return BeautifulSoup(html, "lxml").get_text(" ", strip=True) if html else ""
