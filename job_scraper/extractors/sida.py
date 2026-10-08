"""Extractor for Sida (Swedish International Development Cooperation Agency).

Jobs are listed as cards in the main content of the Swedish-language page. Each
card links its title and labels its own fields (captured 2026-10-08):
    <a href="/jobba-med-bistand/jobba-pa-sida/lediga-tjanster/5688-...">Title</a>
    <h3>Sista ansökningsdag:</h3> <p>2026-10-25 CET</p>
    <h3>Plats:</h3> <p>Sundbyberg</p>

The location is read from the "Plats:" label, never assumed. This reader used
to write "Stockholm, Sweden" on every row, and the first capture showed six of
nine postings placed in Sundbyberg (SP6).

The page states its own total ("Totalt 9 lediga tjänster") in a block that
would hold a pager, though none has been seen. A count that disagrees with the
total, or a page with no total, raises: a second page this reader does not
walk, or a layout it no longer reads, must not pass for a short list.

Applying redirects to an external ReachMee portal (login required there),
but browsing and title/location extraction work without authentication.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

_JOB_PATH = "/lediga-tjanster/"
_LOCATION_LABEL = "Plats:"
_TOTAL = re.compile(r"Totalt\s+(\d+)\s+lediga\s+tjänster")


def _stated_total(soup: BeautifulSoup) -> int | None:
    for p in soup.select(".job-listing__pagination-div p"):
        match = _TOTAL.search(p.get_text(" ", strip=True))
        if match:
            return int(match.group(1))
    return None


def _labelled_value(card: Tag, label: str) -> str:
    """The text of the <p> that follows the <h3> reading exactly *label*."""
    for heading in card.find_all("h3"):
        if heading.get_text(" ", strip=True) == label:
            value = heading.find_next_sibling("p")
            return value.get_text(" ", strip=True) if value else ""
    return ""


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str = "sida",
) -> list[dict[str, Any]]:
    html = fetch_text(listing_url)
    soup = BeautifulSoup(html, "lxml")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=lambda h: h and _JOB_PATH in h):
        href = str(a["href"]).strip()
        # Skip the breadcrumb link back to the listing page itself
        if href.rstrip("/") == listing_url.rstrip("/"):
            continue

        detail_url = urljoin(listing_url, href)
        if detail_url in seen:
            continue
        seen.add(detail_url)

        title = a.get_text(" ", strip=True)
        if not title:
            continue

        card = a.find_parent(class_="list-card-text-container")
        location = _labelled_value(card, _LOCATION_LABEL) if isinstance(card, Tag) else ""

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

    total = _stated_total(soup)
    if total is None:
        raise ValueError(f"{source_name}: no stated total of vacancies at {listing_url}")
    if total != len(out):
        raise ValueError(
            f"{source_name}: the page states {total} vacancies but {len(out)} were read "
            f"at {listing_url}"
        )
    return out
