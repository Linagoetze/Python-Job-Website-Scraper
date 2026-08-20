"""Extractor for BearingPoint Sweden open roles.

The Sweden career page (en-se) renders all jobs in static HTML with no
pagination. Each job is an anchor whose href contains '/open-roles/offer/':
    <a href="/en-se/careers/open-roles/offer/?id=T7634263">
      <h3>Global Process Owner - Record to Report</h3>
      <p>Stockholm</p>
    </a>

The page was redesigned (WP8e, 2026-08-20): the location moved from that <p>
into a sibling <div class="columns job-info">:
    <a href="...">
      <div class="row">
        <div class="columns job-title"><h3>...</h3></div>
        <div class="columns job-info">Stockholm</div>
      </div>
    </a>
Both are read for; the <p> is tried first in case an older-markup regional
page still uses it, since there is no fixture yet for any page but en-se.

To target other BearingPoint regional pages replace the listing URL with the
appropriate locale path (e.g. /en-gb/careers/open-roles/).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

_JOB_PATH = "/open-roles/offer/"


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str = "bearingpoint_sweden",
) -> list[dict[str, Any]]:
    html = fetch_text(listing_url)
    soup = BeautifulSoup(html, "lxml")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=lambda h: h and _JOB_PATH in h):
        href = str(a["href"]).strip()
        detail_url = urljoin(listing_url, href)
        if detail_url in seen:
            continue
        seen.add(detail_url)

        h3 = a.find("h3")
        title = h3.get_text(" ", strip=True) if h3 else a.get_text(" ", strip=True)
        if not title:
            continue

        location_tag = a.find("p") or a.find("div", class_="job-info")
        location = location_tag.get_text(" ", strip=True) if location_tag else ""

        raw_snippet = " ".join(x for x in [title, location] if x)
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": location,
                "department": "",
                "listing_url": listing_url,
                "detail_url": detail_url,
                "apply_url": detail_url,
                "raw_snippet": raw_snippet,
            }
        )

    return out
