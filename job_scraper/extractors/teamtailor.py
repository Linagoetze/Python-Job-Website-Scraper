"""Generic extractor for Teamtailor-hosted job listing pages.

Handles the common Teamtailor HTML patterns:
  - <a href="/jobs/ID-slug"><h3>Title</h3><p>Dept · Location</p></a>  (older markup)
  - <a href="/jobs/ID-slug"><div>Title</div><div>Dept · Location</div></a>  (Storytel, older markup)
  - <a href="/jobs/ID-slug"><span title="Title">Title</span><div>Dept · Location</div></a>
    (Storytel, redesigned 2026-08 markup: title and metadata both inside <a>)
  - <a href="/jobs/ID-slug">Title</a><div>[Org ·] [Dept ·] Location [· WorkType]</div>
    (Fjällräven, Founders Pledge, FutureLearn, Planted, Seven Perigee, redesigned
    2026-08 markup: bare text title inside <a>, metadata <div> is a *sibling* of
    <a>, not a child. Segment count varies per posting — some have no department,
    FutureLearn's board additionally prefixes a brand/org segment — so dept and
    location are read off the *end* of the non-worktype segments rather than
    assumed to be exactly two.)
  - <a href="/jobs/ID-slug"><h3>Title</h3></a><p>Org · Dept · Location</p>  (older markup)
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

_JOB_PATH = re.compile(r"/jobs/\d+")
_MIDDOT = "·"
_WORK_TYPES = frozenset({"hybrid", "remote", "on-site", "onsite", "on site"})


def _content_segments(meta_tag: Tag) -> tuple[list[str], str]:
    """Split a metadata block into (content segments, work_type).

    The redesigned markup wraps the work-type tag ("Hybrid", "Remote", "Fully
    Remote", ...) in its own <span> alongside an icon (<i class="...fa-wifi">),
    so it is picked out structurally rather than by matching known label
    strings — a label this doesn't recognise (a new one Teamtailor adds, or a
    language variant) would otherwise silently read as a location. The
    "·"-joined divider spans are dropped by the same structural pass. Falls
    back to splitting the tag's whole text on the middot and matching
    _WORK_TYPES for markup with no such spans (older shapes, none of which
    have a saved fixture to confirm against).
    """
    direct_spans = meta_tag.find_all("span", recursive=False)
    if direct_spans:
        segments: list[str] = []
        work_type = ""
        for span in direct_spans:
            text = span.get_text(" ", strip=True)
            if not text or text == _MIDDOT:
                continue
            if span.find("i"):
                work_type = text
            else:
                segments.append(text)
        return segments, work_type

    parts = [p.strip() for p in meta_tag.get_text(" ", strip=True).split(_MIDDOT) if p.strip()]
    work_types = [p for p in parts if p.lower() in _WORK_TYPES]
    segments = [p for p in parts if p.lower() not in _WORK_TYPES]
    return segments, (work_types[0] if work_types else "")


def _split_meta(meta_tag: Tag) -> tuple[str, str, str]:
    """Split '[Org ·] [Dept ·] Location [· WorkType]' into (dept, location, work_type).

    Dept and location are read off the end: the last segment is always the
    location, the one before it (if any) is the department, and anything
    earlier (FutureLearn's brand/org prefix) is discarded. A single remaining
    segment is a location with no department, not the other way round —
    Teamtailor's redesigned cards omit the department chip entirely for some
    postings, but never omit location.
    """
    segments, work_type = _content_segments(meta_tag)
    if len(segments) >= 2:
        dept, location = segments[-2], segments[-1]
    elif len(segments) == 1:
        dept, location = "", segments[0]
    else:
        dept, location = "", ""
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

        # Title: prefer <h3>, then a <span title="..."> (redesigned Storytel),
        # fall back to first direct child <div>, then the anchor's own bare text
        # (redesigned Fjällräven/Founders Pledge/FutureLearn/Planted/Seven Perigee)
        h3 = a.find("h3")
        title_span: Tag | None = None
        if h3:
            title = h3.get_text(" ", strip=True)
        else:
            title_span = a.find("span", title=True)
            if title_span:
                title = title_span.get("title", "").strip()
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

        # Metadata: inside <a> first (older shapes, redesigned Storytel), then a
        # sibling of <a> (redesigned markup used by most other Teamtailor sources)
        meta_tag: Tag | None = a.find("p")

        if not meta_tag:
            child_divs = [c for c in a.children if isinstance(c, Tag) and c.name == "div"]
            if title_span and len(child_divs) >= 1:
                # Redesigned Storytel: title is a <span>, metadata is the (only) <div>
                meta_tag = child_divs[-1]
            elif len(child_divs) >= 2:
                # Storytel (older markup): second direct child <div> holds metadata
                meta_tag = child_divs[-1]

        if not meta_tag:
            parent = a.parent
            if isinstance(parent, Tag):
                # Older markup: <p> is a sibling of <a> inside a container <div>
                meta_tag = parent.find("p")
                if not meta_tag:
                    # Redesigned markup: metadata <div> is a sibling of <a>, not a
                    # child. The only sibling <div> with text is the metadata block
                    # — a logo <div>, when present, holds only an <img>.
                    meta_tag = next(
                        (
                            d
                            for d in parent.find_all("div", recursive=False)
                            if d.get_text(strip=True)
                        ),
                        None,
                    )

        dept, location, work_type = "", "", ""
        if meta_tag and meta_tag.get_text(strip=True):
            dept, location, work_type = _split_meta(meta_tag)

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
