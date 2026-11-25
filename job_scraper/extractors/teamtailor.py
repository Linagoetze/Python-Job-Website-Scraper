"""Generic extractor for Teamtailor-hosted job listing pages.

Handles the common Teamtailor HTML patterns:
  - <a href="/jobs/ID-slug"><h3>Title</h3><p>Dept · Location</p></a>  (older markup)
  - <a href="/jobs/ID-slug"><div>Title</div><div>Dept · Location</div></a>  (Storytel, older markup)
  - <a href="/jobs/ID-slug"><span title="Title">Title</span><div>Dept · Location</div></a>
    (Storytel, redesigned 2026-08 markup: title and metadata both inside <a>)
  - <a href="/jobs/ID-slug">Title</a><div>[Org ·] [Dept ·] Location [· WorkType]</div>
    (Founders Pledge, FutureLearn, Planted, Seven Perigee, redesigned
    2026-08 markup: bare text title inside <a>, metadata <div> is a *sibling* of
    <a>, not a child. Segment count varies per posting — some have no department,
    FutureLearn's board additionally prefixes a brand/org segment — so dept and
    location are read off the *end* of the non-worktype segments rather than
    assumed to be exactly two.)
  - <a href="/jobs/ID-slug"><h3>Title</h3></a><p>Org · Dept · Location</p>  (older markup)
  - <a href="/jobs/ID-slug"><div><img></div><div><span title="Title">Titl...</span>
    <div>Dept · Location · WorkType</div></div></a>  (image-grid cards, SP8: the title
    span and the metadata <div> are siblings inside a wrapper <div>, the anchor's last
    child. The visible text is shortened with "..."; the title attribute is whole.)

A row whose location is its title (or the title shortened) is a layout the reader
has misread, not a posting: `extract` raises rather than return it (SP8).

A board of more than 20 postings shows 20 and a "Show more" link,
`<div id="show_more_button"><a href="/jobs/show_more?page=2">`. That link answers
with a Turbo Stream, not a page: each posting is an `append` to
`jobs_list_container` wrapped in a `<template>`, and an `update` of
`show_more_button` carries the next link, or nothing on the last page. The walk
follows it to the end. BeautifulSoup keeps a template's text out of
`get_text()`, so each template's markup is parsed again as a fragment of its own
before its cards are read; read in place, every title came back empty and every
card was skipped. Until 2026-11-25 the reader read the first 20 only, and oatly
returned 20 of 23 in every run.

The list states its length ("23 jobs", "1 job") above the cards, and a walk is
checked against that count when there is one (`pagination.reconcile`). Three
older layouts state none; for them a single page is accepted as before, and a
walk that followed the link says it could not be checked.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from job_scraper.extractors import pagination
from job_scraper.extractors.rowcheck import title_read_as_location

_JOB_PATH = re.compile(r"/jobs/\d+")
# The list's own count, above the cards: "23 jobs", "1 job". English only,
# because every board followed shows it in English; a board whose count reads
# otherwise is read as stating none.
_STATED_COUNT = re.compile(r"^\s*(\d+)\s+jobs?\s*$", re.I)
_LIST_CONTAINER = {"data-blocks--jobs-target": "jobsListContainer"}
_SHOW_MORE_ID = "show_more_button"
# A board of 1,000 postings is 50 pages; the cap stops a link that never ends.
_MAX_PAGES = 50
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


def _check_row(source_name: str, row: dict[str, Any]) -> None:
    """Raise if the reader took the title for the location.

    Seven goldens passed while this reader returned every title as its own
    location on a layout it did not know (SP5, SP8): plausible rows, wrong
    element. An empty location is a real state and is not checked.
    """
    if title_read_as_location(row["title"], row["location"]):
        raise ValueError(
            f"{source_name}: teamtailor card {row['detail_url']!r} has its title "
            f"{row['title']!r} as its location {row['location']!r}; the markup is a "
            "layout teamtailor.py does not know"
        )


def _stated_count(soup: BeautifulSoup) -> int | None:
    """The count the list states above its cards, or None if it states none."""
    container = soup.find(attrs=_LIST_CONTAINER)
    if not isinstance(container, Tag):
        return None
    for text in container.find_all(string=_STATED_COUNT):
        if text.find_parent(id="jobs_list_container") is None:
            match = _STATED_COUNT.match(str(text))
            if match:
                return int(match.group(1))
    return None


def _show_more_href(scope: Tag | None) -> str | None:
    """The next page's link inside *scope*, the show-more block or its update."""
    if scope is None:
        return None
    link = scope.find("a", href=True)
    return str(link["href"]) if isinstance(link, Tag) else None


def _stream_parts(body: str) -> tuple[BeautifulSoup, str | None]:
    """A show-more answer: its appended cards as one fragment, and the next link.

    Each `<template>`'s markup is re-parsed, because BeautifulSoup files a
    template's text as `TemplateString`, which `get_text()` leaves out.
    """
    stream = BeautifulSoup(body, "lxml")
    cards: list[str] = []
    next_href: str | None = None
    for part in stream.find_all("turbo-stream"):
        template = part.find("template")
        if not isinstance(template, Tag):
            continue
        fragment = BeautifulSoup(template.decode_contents(), "lxml")
        if part.get("target") == _SHOW_MORE_ID:
            next_href = _show_more_href(fragment)
        elif part.get("action") == "append":
            cards.append(template.decode_contents())
    return BeautifulSoup("".join(cards), "lxml"), next_href


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str,
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(fetch_text(listing_url), "lxml")
    seen: set[str] = set()
    out = _read_cards(soup, listing_url, source_name, seen)
    total = _stated_count(soup)
    next_href = _show_more_href(soup.find(id=_SHOW_MORE_ID))

    url = listing_url
    pages = 1
    while next_href:
        if pages >= _MAX_PAGES:
            raise pagination.ShortWalkError(
                f"{source_name}: still offered a next page after {_MAX_PAGES} pages "
                f"holding {len(out)} posting(s); refusing a list that may be short"
            )
        url = urljoin(listing_url, next_href)
        cards, next_href = _stream_parts(fetch_text(url))
        pages += 1
        added = _read_cards(cards, listing_url, source_name, seen)
        if not added:
            break  # settled below: the end of the list only if the count agrees
        out.extend(added)

    if total is not None:
        pagination.reconcile(source_name, url, collected=len(out), total=total)
    elif pages > 1:
        pagination.unverifiable_end(source_name, url, collected=len(out))
    return out


def _read_cards(
    soup: BeautifulSoup,
    listing_url: str,
    source_name: str,
    seen: set[str],
) -> list[dict[str, Any]]:
    """Every posting card in *soup* not already in *seen*, which it extends."""
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
        # (redesigned Founders Pledge/FutureLearn/Planted/Seven Perigee)
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

        if not meta_tag and title_span and title_span.parent is not a:
            # Image-grid cards (SP8): title span and metadata <div> are siblings
            # inside a wrapper, so the wrapper itself is not the metadata block.
            meta_tag = title_span.find_next_sibling("div")

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
        row = {
            "source_name": source_name,
            "title": title,
            "location": location,
            "department": dept,
            "listing_url": listing_url,
            "detail_url": full,
            "apply_url": full,
            "raw_snippet": raw_snippet,
        }
        _check_row(source_name, row)
        out.append(row)
    return out
