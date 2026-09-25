"""Tests for the Impactpool card parser: which field is which.

The saved page pins the common card (`tests/test_extractors_golden.py`). These
pin the shapes that page does not happen to contain, built from the markup the
site served on 2026-09-25 with the text replaced. The card format changed
that day: the title moved from a <div> into an <h3>, and a parser that read
fields by counting `ip-typography` elements stored the employer as the title
for every posting. See `impactpool._card_fields`.
"""

from __future__ import annotations

import logging

import pytest
from bs4 import BeautifulSoup

from job_scraper.extractors.impactpool import CardMarkupError, _parse_page

_LISTING = "https://www.impactpool.org/search"


def _card(
    title: str | None, *details: str, org: str = "Example Org", tag: str = "h3", job_id: int = 101
) -> str:
    title_el = (
        f'<{tag} class="ip-typography" type="cardTitle">{title}</{tag}>'
        if title is not None
        else ""
    )
    detail_els = "".join(
        f'<div class="ip-typography" type="bodyEmphasis">{d}</div>' for d in details
    )
    return (
        '<div class="job"><div class="extra"></div>'
        f'<a href="/jobs/{job_id}">'
        '<img alt="" src="/logo.png"/>'
        '<span class="visually-hidden">New job:</span>'
        f"{title_el}"
        '<div class="ip-layout">'
        f'<div class="ip-typography" type="bodyEmphasis">{org}<img src="/dot.svg"/></div>'
        f'<div class="ip-layout">{detail_els}</div>'
        "</div></a></div>"
    )


def _parse(*cards: str) -> list[dict]:
    html = f"<html><body>{''.join(cards)}</body></html>"
    return _parse_page(BeautifulSoup(html, "lxml"), _LISTING, "impactpool")


def test_card_with_location_and_grade() -> None:
    [job] = _parse(_card("Programme Officer", "Geneva", "P-3"))
    assert (job["title"], job["company"], job["location"]) == (
        "Programme Officer",
        "Example Org",
        "Geneva",
    )
    assert job["raw_snippet"] == "Programme Officer Geneva"


def test_card_without_a_location_leaves_location_empty_not_the_grade() -> None:
    """102 of 3,547 cards on 2026-09-25 named no location; the one field left is
    the grade. Reading it as the location put "Mid - Mid level" in the column."""
    [job] = _parse(_card("Programme Officer", "Mid - Mid level"))
    assert (job["title"], job["company"], job["location"]) == (
        "Programme Officer",
        "Example Org",
        "",
    )


def test_title_is_found_by_role_whatever_its_tag() -> None:
    """The <div> -> <h3> move is what broke the positional parser."""
    [job] = _parse(_card("Programme Officer", "Geneva", "P-3", tag="div"))
    assert job["title"] == "Programme Officer"


def test_card_with_no_title_element_fails_loudly() -> None:
    with pytest.raises(CardMarkupError, match=r"jobs/101 has no type=\"cardTitle\""):
        _parse(_card(None, "Geneva", "P-3"))


@pytest.mark.parametrize("details", [(), ("Geneva", "P-3", "Full time")])
def test_card_of_an_unknown_shape_fails_loudly(details: tuple[str, ...]) -> None:
    with pytest.raises(CardMarkupError, match="detail field"):
        _parse(_card("Programme Officer", *details))


def test_blank_title_is_skipped_and_logged_not_fatal(caplog: pytest.LogCaptureFixture) -> None:
    """8 postings on 2026-09-25 had an empty title element: the markup is intact,
    the poster left it blank, and that must not fail the other 3,539."""
    with caplog.at_level(logging.INFO, logger="job_scraper.extractors.impactpool"):
        jobs = _parse(_card("", "Mid"), _card("Programme Officer", "Geneva", "P-3", job_id=102))
    assert [j["title"] for j in jobs] == ["Programme Officer"]
    assert "skipped 1 posting(s) listed with a blank title" in caplog.text
