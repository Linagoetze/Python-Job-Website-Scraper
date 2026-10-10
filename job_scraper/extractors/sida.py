"""Extractor for Sida (Swedish International Development Cooperation Agency).

The listing is a Nuxt page, and its postings are read from the data the page
embeds for itself (`<script id="__NUXT_DATA__">`), not from its cards. The cards
show ten at a time, behind a pager made of buttons ("Sida 1 av 2"), and that
pager is a slice the browser makes of the same data: on 2026-10-09 the page held
eleven ads and showed ten, and a reader of the cards read ten of eleven (run 38).
Every ad is in the one response this reader fetches, so no second page exists to
walk.

Each ad carries its title, its place (`Area2`, the value the card shows under
"Plats:") and its ReachMee job number (`rmjob=` in its `url`). It does not carry
its address on sida.se, which is `<listing>/<rmjob>-<slug of the title>`. That
address is the dedupe key, so a posting the page links is keyed on the link
itself, and the slug rule is checked against every such link on every run. A
posting only the data holds is keyed on the built address. The rule was checked
on 31 of 31 addresses (21 stored, 10 linked, 2026-10-10), all of them ASCII and
å, ä, ö; a title with any other letter is refused rather than keyed by a guess.

The page states its own total ("Totalt 9 lediga tjänster"). A count of ads that
disagrees with it, or a page with no total, raises: a layout this reader no
longer reads must not pass for a short list. A day with no vacancies has not
been seen. "Totalt 0 lediga tjänster" reads as empty, and any other empty page
raises with a message saying it may be one, so that its wording can be captured
and taught here rather than guessed.

A renamed `Area2` would blank every location without failing, so a page where
no ad carries a place raises. One ad without it only logs a warning, since a
posting may simply have no place. The reader used to write "Stockholm, Sweden"
on every row, and the first capture showed six of nine postings placed in
Sundbyberg (SP6).

Applying redirects to an external ReachMee portal (login required there),
but browsing and title/location extraction work without authentication.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_JOB_PATH = "/lediga-tjanster/"
_TOTAL = re.compile(r"Totalt\s+(\d+)\s+lediga\s+tjänster")
_LINKED_ID = re.compile(r"/lediga-tjanster/(\d+)-")
_RMJOB = re.compile(r"rmjob=(\d+)")
# The letters the slug rule was checked on; see the module docstring.
_SPELLABLE = re.compile(r"[\x00-\x7fåäöÅÄÖ]*")
_FOLD = str.maketrans("åäö", "aao")


def _stated_total(soup: BeautifulSoup) -> int | None:
    for p in soup.select(".job-listing__pagination-div p"):
        match = _TOTAL.search(p.get_text(" ", strip=True))
        if match:
            return int(match.group(1))
    return None


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower().translate(_FOLD)).strip("-")


def _embedded_ads(soup: BeautifulSoup, source_name: str, listing_url: str) -> list[dict[str, str]]:
    """Each ad in the page's own data, as {"id", "title", "place"}, in page order.

    Nuxt serialises its state as one flat array in which an object's fields are
    indices into that array. Only the three fields read here are resolved, and
    each must resolve to a string: anything else means the format moved.
    """
    script = soup.find("script", id="__NUXT_DATA__")
    if script is None:
        return []
    try:
        payload = json.loads(script.get_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source_name}: the page's data at {listing_url} is not JSON") from exc
    if not isinstance(payload, list):
        raise ValueError(f"{source_name}: the page's data at {listing_url} is not Nuxt's array")

    def resolve(ad: dict[str, Any], field: str) -> str | None:
        if field not in ad:
            return None
        index = ad[field]
        value = payload[index] if isinstance(index, int) and 0 <= index < len(payload) else None
        if not isinstance(value, str):
            raise ValueError(
                f"{source_name}: an ad's {field!r} at {listing_url} is not a string; "
                "the page's data format may have changed"
            )
        return value

    ads: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in payload:
        if not (isinstance(item, dict) and "projectNr" in item):
            continue
        rmjob = _RMJOB.search(resolve(item, "url") or "")
        title = (resolve(item, "title") or "").strip()
        if rmjob is None or not title:
            raise ValueError(
                f"{source_name}: an ad at {listing_url} has no job number or no title; "
                "the page's data format may have changed"
            )
        if rmjob.group(1) in seen:
            continue
        seen.add(rmjob.group(1))
        place = (resolve(item, "Area2") or "").strip()
        ads.append({"id": rmjob.group(1), "title": title, "place": place})
    return ads


def _linked(soup: BeautifulSoup, listing_url: str) -> dict[str, str]:
    """The postings the cards link, by job number, as absolute addresses."""
    out: dict[str, str] = {}
    for a in soup.find_all("a", href=lambda h: h and _JOB_PATH in h):
        href = str(a["href"]).strip()
        match = _LINKED_ID.search(href)
        # The breadcrumb back to the listing itself carries no job number.
        if match:
            out.setdefault(match.group(1), urljoin(listing_url, href))
    return out


def _address(ad: dict[str, str], linked: dict[str, str], source_name: str, listing_url: str) -> str:
    built = urljoin(listing_url, f"{ad['id']}-{_slug(ad['title'])}")
    spellable = _SPELLABLE.fullmatch(ad["title"]) is not None
    link = linked.get(ad["id"])
    if link is not None:
        if spellable and link != built:
            raise ValueError(
                f"{source_name}: the slug rule does not build the address the page links "
                f"for {ad['title']!r} ({link}, built {built}); every posting the page "
                "does not show would be keyed wrongly"
            )
        return link
    if not spellable:
        raise ValueError(
            f"{source_name}: cannot build the address of {ad['title']!r}, which the page "
            "does not link: its title has a letter the slug rule was not checked on"
        )
    return built


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str = "sida",
) -> list[dict[str, Any]]:
    html = fetch_text(listing_url)
    soup = BeautifulSoup(html, "lxml")
    ads = _embedded_ads(soup, source_name, listing_url)
    linked = _linked(soup, listing_url)

    missing = sorted(set(linked) - {ad["id"] for ad in ads})
    if missing and ads:
        raise ValueError(
            f"{source_name}: the page links posting(s) {', '.join(missing)} that are not in "
            f"the page's data at {listing_url}"
        )

    total = _stated_total(soup)
    if total is None and not ads and not linked:
        raise ValueError(
            f"{source_name}: no postings and no stated total at {listing_url}. This may be "
            "a day with no vacancies in a layout not seen yet: capture the page and teach "
            "the reader its wording"
        )
    if total is None:
        raise ValueError(f"{source_name}: no stated total of vacancies at {listing_url}")
    if total and not ads:
        raise ValueError(
            f"{source_name}: the page states {total} vacancies and holds no embedded "
            f"postings at {listing_url}; the page's data may have moved"
        )
    if total != len(ads):
        raise ValueError(
            f"{source_name}: the page states {total} vacancies but {len(ads)} were read "
            f"at {listing_url}"
        )

    out: list[dict[str, Any]] = []
    for ad in ads:
        detail_url = _address(ad, linked, source_name, listing_url)
        out.append(
            {
                "source_name": source_name,
                "title": ad["title"],
                "location": ad["place"],
                "department": "",
                "listing_url": listing_url,
                "detail_url": detail_url,
                "apply_url": detail_url,
                "raw_snippet": " ".join(x for x in [ad["title"], ad["place"]] if x),
            }
        )

    unplaced = [job["title"] for job in out if not job["location"]]
    if out and len(unplaced) == len(out):
        raise ValueError(
            f"{source_name}: no posting at {listing_url} carries a place ('Area2' in the "
            "page's data); the field may have been renamed"
        )
    if unplaced:
        logger.warning(
            "%s: %d posting(s) with no place: %s", source_name, len(unplaced), ", ".join(unplaced)
        )
    return out
