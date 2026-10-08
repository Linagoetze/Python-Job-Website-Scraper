"""Extractor for Asana jobs page (Playwright-rendered).

HTML structure per listing:
    <a class="... e1ucmllm1" href="/jobs/apply/ID">
      <p>Job Title</p>
      <p>Location</p>
    </a>

The page also embeds the Greenhouse postings it was built from, in
`__NEXT_DATA__` (a `greenhouseJobsList` component), each with its full
`content`. The reader supplies that as `description_text`, so Layer 5 reads it
instead of rendering a detail page per job (SP6, 2026-10-08). A card with no
matching posting gets no description and Layer 5 fetches its page as before,
with a warning that counts such cards; the cards, not the JSON, remain the
list of jobs.
"""

from __future__ import annotations

import html
import json
import logging
import re
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

_JOB_ID = re.compile(r"/jobs/apply/(\d+)")


def _greenhouse_lists(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        if node.get("componentName") == "greenhouseJobsList":
            yield node
        for value in node.values():
            yield from _greenhouse_lists(value)
    elif isinstance(node, list):
        for value in node:
            yield from _greenhouse_lists(value)


def _descriptions(soup: BeautifulSoup) -> dict[str, str]:
    """Posting id -> plain-text description, from the page's embedded JSON.

    Greenhouse's `content` is HTML with its markup entity-escaped
    ("&lt;p&gt;"), so it is unescaped before the tags are stripped.
    """
    script = soup.find("script", id="__NEXT_DATA__")
    if not isinstance(script, Tag) or not script.string:
        return {}
    try:
        data = json.loads(script.string)
    except ValueError:
        return {}
    out: dict[str, str] = {}
    for listing in _greenhouse_lists(data):
        for job in listing.get("jobs") or []:
            if not isinstance(job, dict) or job.get("id") is None or not job.get("content"):
                continue
            markup = html.unescape(str(job["content"]))
            out[str(job["id"])] = BeautifulSoup(markup, "lxml").get_text(" ", strip=True)
    return out


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str = "asana",
) -> list[dict[str, Any]]:
    page = fetch_text(listing_url)
    soup = BeautifulSoup(page, "lxml")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    descriptions = _descriptions(soup)

    for a in soup.find_all("a", href=lambda h: h and "/jobs/apply/" in str(h)):
        href = str(a["href"])
        full = urljoin(listing_url, href)
        if full in seen:
            continue
        seen.add(full)

        paras = [c for c in a.children if isinstance(c, Tag) and c.name == "p"]
        title = paras[0].get_text(" ", strip=True) if len(paras) >= 1 else ""
        location = paras[1].get_text(" ", strip=True) if len(paras) >= 2 else ""

        if not title:
            continue

        raw_snippet = " ".join(x for x in [title, location] if x)
        job_id = _JOB_ID.search(href)
        description = descriptions.get(job_id.group(1), "") if job_id else ""
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": location,
                "department": "",
                "listing_url": listing_url,
                "detail_url": full,
                "apply_url": full,
                "raw_snippet": raw_snippet,
                "description_text": description,
            }
        )
    missing = sum(1 for job in out if not job["description_text"])
    if missing:
        # Not a failure: Layer 5 fetches those pages instead, as it did before,
        # but a page that stops embedding postings should be seen, not inferred.
        logger.warning(
            "%s: %d of %d postings on %s have no embedded description; "
            "Layer 5 will fetch their pages",
            source_name,
            missing,
            len(out),
            listing_url,
        )
    return out
