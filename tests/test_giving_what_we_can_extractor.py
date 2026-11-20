"""Tests for the Giving What We Can reader: every way the page can fail to read raises.

The redesign of October 2026 removed the heading the old reader anchored on,
and it returned `[]` for four weeks while the page listed a role. The golden
pins the happy path; these pin the refusals. Each broken page is derived from
the saved fixture rather than hand-written, so it differs from the real page in
the one way the test names.
"""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from job_scraper.extractors.giving_what_we_can import UnreadablePageError, extract
from tests.fixture_cases import FIXTURE_CASES, FIXTURES_DIR, single_response_fetch

_LISTING_URL = FIXTURE_CASES["giving_what_we_can"][1]


def _fixture_soup() -> BeautifulSoup:
    path = FIXTURES_DIR / FIXTURE_CASES["giving_what_we_can"][0]
    if not path.exists():
        pytest.fail(f"{path.name} missing — run scripts/capture_fixtures.py giving_what_we_can")
    return BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")


def _extract(soup: BeautifulSoup) -> list[dict[str, str]]:
    return extract(_LISTING_URL, single_response_fetch(str(soup)))


def test_every_row_has_a_title_and_a_role_link() -> None:
    """The golden checks row 1 only (OP7); this checks them all."""
    jobs = _extract(_fixture_soup())
    assert jobs
    for job in jobs:
        assert job["title"]
        assert job["detail_url"].startswith("https://www.givingwhatwecan.org/careers/")
        # The first column also holds a mobile-only "Team · Type" line.
        assert "·" not in job["title"]


def test_a_page_without_the_roles_section_raises() -> None:
    """The failure that happened: the anchor is gone and the page still loads."""
    soup = _fixture_soup()
    soup.select_one("section#open-roles").decompose()
    with pytest.raises(UnreadablePageError, match="no section#open-roles"):
        _extract(soup)


def test_a_roles_section_with_no_rows_raises() -> None:
    """Empty is not proven by an empty list; the page's own wording is not known yet."""
    soup = _fixture_soup()
    for row in soup.select("section#open-roles li"):
        row.decompose()
    with pytest.raises(UnreadablePageError, match="lists no role links"):
        _extract(soup)


def test_a_row_with_no_title_raises_rather_than_being_skipped() -> None:
    soup = _fixture_soup()
    row = soup.select_one("section#open-roles li > a[href]")
    title = row.find("span", recursive=False)
    for span in title.find_all("span"):
        span.string = ""
    with pytest.raises(UnreadablePageError, match="has no title"):
        _extract(soup)
