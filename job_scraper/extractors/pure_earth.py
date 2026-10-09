"""Extractor for Pure Earth's own careers page (static, no ATS).

The page has no structured data for its postings: the one JSON-LD block is a
WordPress `WebPage`/`WebSite` graph and the only other `application/json`
script is the emoji loader's config (SP9 looked, 2026-10-09). The postings are
markup, a Bootstrap accordion under the "Open Positions" heading:

    <div id="accordion">
      <div class="card">
        <div class="card-header"><h5><a ...>HQ: Chief Impact Officer</a></h5></div>
        <div class="collapse" id="collapse1">
          <div class="card-body">
            <p><strong>Position:</strong> <a href="...pdf">Chief Impact Officer</a></p>
            <p><strong>Location</strong>: New York City ... remote considered</p>
            <p><strong><a href="https://app.trinethire.com/...">APPLY NOW</a></strong></p>
            ...the full description...

Every card is in the HTML whether it is expanded or not, so the page needs no
browser. The accordion is the whole list: the page states no total and has no
pager, no "load more" control and no `rel=next` (checked on the capture), so one
fetch is the board. If the accordion goes missing the reader raises rather than
returning an empty list that reads as "no vacancies".

Card shapes seen on the capture (17 cards, all of them postings):

- the common one: `Unit: Title` header, a "Location" paragraph, an "APPLY NOW"
  link to the employer's Trinet Hire board, usually a job-description PDF linked
  from the "Position" paragraph;
- the same without a PDF (the "Position" paragraph is plain text);
- an "APPLY NOW" link to a third-party recruiter's site instead of Trinet Hire;
- a Portuguese consultancy notice (labels "Vaga" and "Localização") that has no
  "APPLY NOW" link, only a terms-of-reference PDF and an obfuscated e-mail
  address to apply through.

Nothing on the page is a featured card, an open-application card or a closed
notice. A card the reader cannot read raises, naming its position, instead of
being dropped or half-read.

The header's text before the first colon ("HQ", "Brazil") is the office or
country unit, not a department; the page gives no department. It goes into
`raw_snippet`. The card body is the posting's full text, supplied as
`description_text`, so Layer 5 reads it and fetches nothing (SP4d). The apply
pages are a third party's (Trinet Hire) and are static too, with the same text;
supplying it from the card saves one request per new posting and keeps Layer 5
off a host that is not the employer's.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from bs4 import BeautifulSoup, Tag

from job_scraper.extractors.rowcheck import title_read_as_location

# English and Portuguese label of the location paragraph, matched on the
# paragraph's own text so that the label may sit in a <strong>, a <b> or a
# <span>, with or without its colon inside the tag.
_LOCATION_LABEL = re.compile(r"^(?:location|localização)\s*:?\s*(.*)$", re.IGNORECASE)
_UNIT_PREFIX = re.compile(r"^([^:]{1,40}):\s+(\S.*)$")


def _clean(text: str) -> str:
    """Collapse whitespace, including the no-break spaces the CMS pastes in."""
    return " ".join(text.split())


def _card_label(card: Tag, position: int) -> str:
    return f"card {position} ({_clean(card.get_text(' ', strip=True))[:60]!r})"


def _location(body: Tag) -> str | None:
    for p in body.find_all("p"):
        match = _LOCATION_LABEL.match(_clean(p.get_text(" ", strip=True)))
        if match:
            return match.group(1).lstrip(": ").strip()
    return None


def _apply_link(body: Tag) -> str | None:
    for a in body.find_all("a", href=True):
        if _clean(a.get_text(" ", strip=True)).casefold() == "apply now":
            return str(a["href"])
    return None


def _document_link(body: Tag) -> str | None:
    for a in body.find_all("a", href=True):
        if str(a["href"]).split("?")[0].lower().endswith(".pdf"):
            return str(a["href"])
    return None


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str = "pure_earth",
) -> list[dict[str, Any]]:
    html = fetch_text(listing_url)
    soup = BeautifulSoup(html, "lxml")
    accordion = soup.find(id="accordion")
    if not isinstance(accordion, Tag):
        raise ValueError(f"{source_name}: no #accordion on {listing_url}; the page has changed")
    cards = [c for c in accordion.find_all("div", class_="card") if isinstance(c, Tag)]
    if not cards:
        raise ValueError(f"{source_name}: the accordion on {listing_url} holds no cards")

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, card in enumerate(cards, start=1):
        header = card.find("div", class_="card-header")
        body = card.find("div", class_="card-body")
        if not isinstance(header, Tag) or not isinstance(body, Tag):
            raise ValueError(f"{source_name}: {_card_label(card, position)} has no header or body")
        heading = _clean(header.get_text(" ", strip=True))
        unit = ""
        title = heading
        prefixed = _UNIT_PREFIX.match(heading)
        if prefixed:
            unit, title = prefixed.group(1).strip(), prefixed.group(2).strip()
        location = _location(body)
        if location is None:
            raise ValueError(
                f"{source_name}: {_card_label(card, position)} has no Location paragraph"
            )
        if title_read_as_location(title, location):
            raise ValueError(
                f"{source_name}: {_card_label(card, position)} read its title as location"
            )
        apply_link = _apply_link(body)
        detail_link = apply_link or _document_link(body)
        if not title or detail_link is None:
            raise ValueError(
                f"{source_name}: {_card_label(card, position)} has no title or no link to key it on"
            )
        if detail_link in seen:
            continue
        seen.add(detail_link)
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": location,
                "department": "",
                "listing_url": listing_url,
                "detail_url": detail_link,
                "apply_url": apply_link or detail_link,
                "raw_snippet": " | ".join(part for part in (unit, location) if part),
                "description_text": _clean(body.get_text(" ", strip=True)),
            }
        )
    return out
