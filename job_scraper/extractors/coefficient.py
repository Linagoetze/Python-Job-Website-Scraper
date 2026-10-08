"""Extractor for Coefficient Giving careers page (WordPress + Ashby Apply links).

The roles sit in a table under the "Open Roles" heading. When there are none,
the same table holds one row saying so ("There are no open roles at this
time.", captured 2026-10-08), and only that statement makes an empty result
an answer. A missing heading, or a section with neither a readable role nor
that statement, is a page this reader no longer understands, and it raises
rather than returning the empty list that would read as "no vacancies".
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from job_scraper.urlutil import normalize_http_url

_ASHBY_JOB = re.compile(
    r"^https://jobs\.ashbyhq\.com/coefficientgiving/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_NO_OPEN_ROLES = re.compile(r"\bno open roles\b", re.IGNORECASE)


def _section_text(heading: Tag) -> str:
    """The text between *heading* and the next heading of its rank."""
    parts: list[str] = []
    for el in heading.next_elements:
        if isinstance(el, Tag) and el.name == heading.name:
            break
        if isinstance(el, NavigableString) and not isinstance(el, Comment):
            parts.append(str(el))
    return " ".join(" ".join(parts).split())


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str = "coefficient_giving",
) -> list[dict[str, Any]]:
    html = fetch_text(listing_url)
    soup = BeautifulSoup(html, "lxml")
    h2 = soup.find(id="0-open-roles")
    if not isinstance(h2, Tag):
        raise ValueError(f"{source_name}: no Open Roles heading at {listing_url}")
    table = h2.find_next("table")
    # A table further down the page (the FAQ, say) is not the roles table.
    if table is not None and table.find_previous(h2.name) is not h2:
        table = None
    tbody = (table.find("tbody") or table) if table is not None else None
    rows = tbody.find_all("tr", recursive=False) if tbody is not None else []
    out: list[dict[str, Any]] = []
    for tr in rows:
        apply_a = None
        for a in tr.find_all("a", class_="content-button", href=True):
            href = str(a.get("href", "")).strip()
            if "/form/" in href:
                continue
            if _ASHBY_JOB.match(href):
                apply_a = a
                break
        if apply_a is None:
            continue
        apply_url = normalize_http_url(str(apply_a["href"]))
        first_td = tr.find_all("td", recursive=False)
        if not first_td:
            continue
        cell = first_td[0]
        strong = cell.find("strong")
        title = strong.get_text(" ", strip=True) if strong else ""
        em = cell.find("em")
        location = em.get_text(" ", strip=True) if em else ""
        if not title:
            title = cell.get_text(" ", strip=True)
        raw_snippet = " ".join(x for x in (title, location) if x).strip()
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": location,
                "department": "",
                "listing_url": listing_url,
                "detail_url": apply_url,
                "apply_url": apply_url,
                "raw_snippet": raw_snippet,
            }
        )
    if not out and not _NO_OPEN_ROLES.search(_section_text(h2)):
        raise ValueError(
            f"{source_name}: the Open Roles section at {listing_url} lists no role this "
            "reader can read and does not say there are none"
        )
    return out
