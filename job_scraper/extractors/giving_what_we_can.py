"""Extractor for the Giving What We Can careers page.

HTML structure (Next.js, rows in the static HTML), as served since the redesign
that moved the page from /get-involved/careers to /careers (first seen empty in
run 40, 2026-10-27):

    <section id="open-roles">
      ...
      <ul>
        <li>
          <a href="/careers/[job-slug]">
            <span>                         # title block
              <span><span>Job Title</span></span>
              <span class="md:hidden">Team · Type</span>
            </span>
            <span>Team</span>              # desktop columns
            <span>Type</span>
            <span>Deadline</span>
          </a>
        </li>
      </ul>
    </section>

The page states no place for a role, so `location` stays empty and Layer 5
settles it from the posting page, as it did before the redesign.

The old reader found its list under `h2#current-openings` and returned `[]`
when that heading was missing. The redesign removed it, and for four weeks the
source read as a board with no vacancies while it listed one. So every way this
page can fail to read now raises: no `section#open-roles`, a section with no
rows, or a row with no title. A board that is genuinely empty raises too, until
its wording is known; its sentence can then be taught here, as Teamtailor's
"No open positions right now" could be.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

BASE_URL = "https://www.givingwhatwecan.org"

_SECTION_SELECTOR = "section#open-roles"
_ROW_SELECTOR = "li > a[href]"


class UnreadablePageError(ValueError):
    """The careers page did not have the shape this reader knows."""


def _title_of(row: Any) -> str:
    """The role's name: the innermost first span of the row's first column.

    The first column also holds a mobile-only "Team · Type" line, so the
    column's whole text is not the title.
    """
    node = row.find("span", recursive=False)
    while node is not None:
        inner = node.find("span", recursive=False)
        if inner is None:
            return node.get_text(" ", strip=True)
        node = inner
    return ""


def _team_of(row: Any) -> str:
    """The second column, the team ("Product Engineering"), or "" if absent."""
    columns = row.find_all("span", recursive=False)
    return columns[1].get_text(" ", strip=True) if len(columns) > 1 else ""


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str = "giving_what_we_can",
) -> list[dict[str, Any]]:
    html = fetch_text(listing_url)
    soup = BeautifulSoup(html, "lxml")

    section = soup.select_one(_SECTION_SELECTOR)
    if section is None:
        raise UnreadablePageError(
            f"{source_name}: {listing_url} has no {_SECTION_SELECTOR}, so its roles cannot "
            "be found. The page has changed shape; an empty list here would read as "
            "'no vacancies'."
        )

    rows = section.select(_ROW_SELECTOR)
    if not rows:
        raise UnreadablePageError(
            f"{source_name}: {listing_url}'s {_SECTION_SELECTOR} lists no role links. If the "
            "page says it has no openings, teach this reader that sentence; otherwise its "
            "markup has changed."
        )

    out: list[dict[str, Any]] = []
    for row in rows:
        title = _title_of(row)
        if not title:
            raise UnreadablePageError(
                f"{source_name}: a role row linking to {row['href']!r} has no title. "
                "Refusing to skip it: a row the reader cannot read is not a missing role."
            )
        detail_url = urljoin(BASE_URL, str(row["href"]).strip())
        department = _team_of(row)
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": "",
                "department": department,
                "listing_url": listing_url,
                "detail_url": detail_url,
                "apply_url": detail_url,
                "raw_snippet": " ".join(x for x in [title, department] if x),
            }
        )
    return out
