"""Generic extractor for Pinpoint-hosted job boards (<board>.pinpointhq.com).

Reads the board's public postings feed, the one its own career pages are built
on:
    GET https://<board>.pinpointhq.com/postings.json

One request returns every published posting under a `data` list, with no total,
cursor or page parameter beside it, so there is no walk to check. An employer's
own site that embeds the board (LEEP's Framer page does) fetches this same feed
in the browser, which is why a source points at the board and not at that page:
the feed is plain JSON to a static fetch, and the embedding page is a shell
until its script has run.

Each posting carries its full text, split into Pinpoint's fixed sections
(description, key responsibilities, skills and knowledge, benefits), each a
fragment of HTML with its own heading. They are joined in the order the posting
page shows them and supplied to Layer 5 as `description_text` (SP4d), so no
detail page is fetched. The skills section is where a posting states its years
of experience, so leaving any section out would hide what Layer 5 looks for.

`workplace_type` (onsite, hybrid or remote) goes into `raw_snippet`, as Ashby's
does (SP4f): Pinpoint's location is an office record, and a remote posting is
often filed under an office city it is not tied to (LEEP's are "Indonesia -
Remote", with Jakarta as the record's city). The location field is the record's
`name`, the label the board shows, rather than its city, for the same reason.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

# The workplace types that say something Layer 0 can read, in the words its
# remote keywords and hybrid gate look for. Onsite adds nothing to a location.
_WORKPLACE_WORDS = {"remote": "Remote", "hybrid": "Hybrid"}

# Pinpoint's text sections, in the posting page's order, with the key of the
# heading the employer gave each one. `description` has no heading of its own.
_SECTIONS = (
    ("description", None),
    ("key_responsibilities", "key_responsibilities_header"),
    ("skills_knowledge_expertise", "skills_knowledge_expertise_header"),
    ("benefits", "benefits_header"),
)

_POSTINGS_HOST_SUFFIX = ".pinpointhq.com"


def feed_url(listing_url: str) -> str:
    """`https://<board>.pinpointhq.com/postings.json` for any URL on the board."""
    host = urlsplit(listing_url).netloc.lower()
    if not host.endswith(_POSTINGS_HOST_SUFFIX) or host == _POSTINGS_HOST_SUFFIX[1:]:
        raise ValueError(f"not a Pinpoint board URL: {listing_url}")
    return f"https://{host}/postings.json"


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str,
) -> list[dict[str, Any]]:
    try:
        api_url = feed_url(listing_url)
    except ValueError as exc:
        raise ValueError(f"{source_name}: {exc}") from exc

    # A body this reader cannot read must fail, not return [], which reads as
    # "no vacancies" (priority 2). A board with nothing open answers with an
    # empty `data` list, which is a different thing.
    try:
        payload = json.loads(fetch_text(api_url))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source_name}: the Pinpoint feed at {api_url} is not JSON") from exc
    postings = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(postings, list):
        raise ValueError(f"{source_name}: no `data` list in the Pinpoint feed at {api_url}")

    out: list[dict[str, Any]] = []
    for job in postings:
        title = str(job.get("title") or "").strip() if isinstance(job, dict) else ""
        url = str(job.get("url") or "").strip() if isinstance(job, dict) else ""
        if not title or not url:
            # Skipping would turn a feed whose shape changed into a shorter
            # list that reads as fewer vacancies.
            raise ValueError(
                f"{source_name}: a posting in the Pinpoint feed at {api_url} has no title or "
                f"no url ({str(job)[:80]!r})"
            )
        location = _location(job)
        dept = str(((job.get("job") or {}).get("department") or {}).get("name") or "").strip()
        workplace = _WORKPLACE_WORDS.get(str(job.get("workplace_type") or "").strip().lower(), "")
        raw_snippet = " ".join(x for x in [title, dept, location, workplace] if x)
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


def _location(job: dict[str, Any]) -> str:
    """The location record's label, or its city and province when it has none."""
    loc = job.get("location") or {}
    name = str(loc.get("name") or "").strip()
    if name:
        return name
    parts = [str(loc.get(k) or "").strip() for k in ("city", "province")]
    return ", ".join(dict.fromkeys(p for p in parts if p))


def _description(job: dict[str, Any]) -> str:
    """Every text section as plain text, each after its heading."""
    chunks: list[str] = []
    for body_key, header_key in _SECTIONS:
        html = str(job.get(body_key) or "")
        text = BeautifulSoup(html, "lxml").get_text(" ", strip=True) if html else ""
        if not text:
            continue
        header = str(job.get(header_key) or "").strip() if header_key else ""
        chunks.append(f"{header}\n{text}" if header else text)
    return "\n\n".join(chunks)
