"""Generic extractor for Workday-hosted job boards (Playwright-rendered).

Workday renders job cards as:
    <li>
      <h3><a data-automation-id="jobTitle" href="/en-US/{board}/job/...">Title</a></h3>
      <ul data-automation-id="subtitle">
        <li>Primary location</li>
        <li>All locations</li>
        <li>Req ID</li>
      </ul>
    </li>
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import partial
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

_REQ_ID = re.compile(r"^[A-Z0-9]+-?\d+$")  # e.g. R5135, JR-1234
_SELECTOR = '[data-automation-id="jobTitle"]'


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str,
) -> list[dict[str, Any]]:
    from job_scraper.http import is_rendering_fetcher  # local import to avoid circular deps

    # When running under the dynamic pipeline, upgrade the fetcher with a
    # selector-based wait so Playwright blocks until job cards are in the DOM
    # rather than relying on a fixed sleep (Airbus's Workday instance is slower).
    # Test by capability, not identity, and wrap the fetcher we were given
    # rather than substituting fetch_rendered: the caller's wrapper may be
    # doing something (the fixture capture script records the URL through it).
    if is_rendering_fetcher(fetch_text):
        fetch_text = partial(fetch_text, wait_for_selector=_SELECTOR)

    html = fetch_text(listing_url)
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    for a in soup.find_all("a", attrs={"data-automation-id": "jobTitle"}):
        href = str(a.get("href", ""))
        if not href:
            continue
        full = urljoin(listing_url, href)
        if full in seen:
            continue
        seen.add(full)

        title = a.get_text(" ", strip=True)
        if not title:
            continue

        # Location: Workday uses two different UI layouts.
        # Modern  (e.g. Airbus): <div data-automation-id="locations"><dl><dd>…</dd></dl></div>
        # Classic (e.g. Busuu):  <ul data-automation-id="subtitle"><li>…</li></ul>
        # Prefer the dedicated locations element when present; fall back to subtitle.
        location = ""
        li_card = a.find_parent("li")
        if li_card:
            loc_div = li_card.find(attrs={"data-automation-id": "locations"})
            if loc_div:
                dd = loc_div.find("dd")
                location = dd.get_text(" ", strip=True) if dd else ""
            else:
                subtitle = li_card.find("ul", attrs={"data-automation-id": "subtitle"})
                if subtitle:
                    parts = [
                        li.get_text(" ", strip=True)
                        for li in subtitle.find_all("li")
                        if not _REQ_ID.match(li.get_text(strip=True))
                    ]
                    location = ", ".join(parts) if parts else ""

        raw_snippet = " ".join(x for x in [title, location] if x)
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
            }
        )
    return out
