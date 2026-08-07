"""Generic extractor for Teamtailor-hosted job listing pages.

Handles the common Teamtailor HTML patterns:
  - <a href="/jobs/ID-slug"><h3>Title</h3><p>Dept · Location</p></a>  (Fjällräven, Planted, Lifesum)
  - <a href="/jobs/ID-slug"><div>Title</div><div>Dept · Location</div></a>  (Storytel)
  - <a href="/jobs/ID-slug"><h3>Title</h3></a><p>Org · Dept · Location</p>  (FutureLearn)
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

_JOB_PATH = re.compile(r"/jobs/\d+")
_MIDDOT = "\u00b7"
_WORK_TYPES = frozenset({"hybrid", "remote", "on-site", "onsite", "on site"})


def _split_meta(text: str, skip_first: bool = False) -> tuple[str, str, str]:
    """Split 'Dept · Location [· WorkType]' into (dept, location, work_type)."""
    parts = [p.strip() for p in text.split(_MIDDOT) if p.strip()]
    if skip_first and parts:
        parts = parts[1:]
    work_types = [p for p in parts if p.lower() in _WORK_TYPES]
    parts = [p for p in parts if p.lower() not in _WORK_TYPES]
    dept = parts[0] if len(parts) >= 1 else ""
    location = parts[1] if len(parts) >= 2 else ""
    work_type = work_types[0] if work_types else ""
    return dept, location, work_type


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str,
) -> list[dict[str, Any]]:
    html = fetch_text(listing_url)
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if not _JOB_PATH.search(href):
            continue
        full = urljoin(listing_url, href)
        if full in seen:
            continue
        seen.add(full)

        # Title: prefer <h3>, fall back to first direct child <div>
        h3 = a.find("h3")
        if h3:
            title = h3.get_text(" ", strip=True)
        else:
            first_div = next(
                (c for c in a.children if isinstance(c, Tag) and c.name == "div"),
                None,
            )
            title = (
                first_div.get_text(" ", strip=True)
                if first_div
                else a.get_text(" ", strip=True)
            )

        if not title:
            continue

        # Metadata (dept · location): inside <a> first, then sibling <p> in parent
        dept, location = "", ""

        meta_tag: Tag | None = a.find("p")
        skip_first = False

        if not meta_tag:
            # Storytel: second direct child <div> holds metadata
            child_divs = [c for c in a.children if isinstance(c, Tag) and c.name == "div"]
            if len(child_divs) >= 2:
                meta_tag = child_divs[-1]

        if not meta_tag:
            # FutureLearn: <p> is a sibling of <a> inside a container <div>
            parent = a.parent
            if isinstance(parent, Tag):
                meta_tag = parent.find("p")
                skip_first = True

        work_type = ""
        if meta_tag and _MIDDOT in meta_tag.get_text():
            dept, location, work_type = _split_meta(
                meta_tag.get_text(" ", strip=True), skip_first=skip_first
            )

        raw_snippet = " ".join(x for x in [title, dept, location, work_type] if x)
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": location,
                "department": dept,
                "listing_url": listing_url,
                "detail_url": full,
                "apply_url": full,
                "raw_snippet": raw_snippet,
            }
        )
    return out
