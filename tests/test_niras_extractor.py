"""Tests for the NIRAS extractor — title/metadata separation on the job card.

The golden file pins the happy path. These cover the two things it cannot: that
*every* row's title is the headline rather than the whole card, and what happens
to a card that omits the `Country:` line.

Both cases are derived from `tests/fixtures/niras.html` rather than hand-written
markup. The extractor's docstring was wrong about this page's shape once already
— it described `<generic>` children that do not exist — so a test written from
the same assumption would have agreed with the bug.
"""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from job_scraper.extractors.niras import extract
from tests.fixture_cases import FIXTURE_CASES, FIXTURES_DIR, parse_fixture, single_response_fetch

_LISTING_URL = "https://www.niras.com/jobs/vacant-positions/"

# Labels the card prints beside its metadata. None may appear in a title: their
# presence means the parser has swallowed the card body along with the headline.
_METADATA_LABELS = (
    "Country:",
    "Employment:",
    "Commencement:",
    "Position length:",
    "Deadline:",
)


def _fixture_html() -> str:
    path = FIXTURES_DIR / FIXTURE_CASES["niras"][0]
    if not path.exists():
        pytest.skip(f"{path.name} not captured yet — re-run scripts/capture_fixtures.py niras")
    return path.read_text(encoding="utf-8")


def test_title_is_the_headline_not_the_whole_card() -> None:
    """Pin the bug found when this source was first captured.

    `title` was 'the anchor's first child text', but the anchor's only element
    child is the wrapping `div.box-content` — so every title arrived as the
    headline followed by all five metadata lines. Checked across every row
    rather than the first, which is all the golden covers.
    """
    jobs = parse_fixture("niras")
    assert jobs, "niras fixture parsed to zero jobs"

    for job in jobs:
        leaked = [label for label in _METADATA_LABELS if label in job["title"]]
        assert not leaked, (
            f"title carries card metadata {leaked}: {job['title']!r}. "
            "The parser is reading the card body, not p.headline."
        )


def test_card_without_a_country_line_yields_an_empty_location() -> None:
    """A card missing `Country:` must give an honest blank, not a wrong value.

    WP8f admits an empty location at Layer 0, so "" is an acceptable answer and
    a neighbouring metadata value (`Temporary`, a date) is not. Built by
    deleting the real `Country:` element from the captured markup, so the rest
    of the card stays exactly as the site serves it.
    """
    soup = BeautifulSoup(_fixture_html(), "lxml")
    removed = 0
    for anchor in soup.find_all("a", href=lambda h: h and "/cvtp" in h):
        for para in anchor.select("p"):
            if para.get_text(" ", strip=True).startswith("Country:"):
                para.decompose()
                removed += 1
    assert removed, "no Country: line found to remove — the fixture's shape has changed"

    jobs = extract(_LISTING_URL, single_response_fetch(str(soup)), source_name="niras")
    assert jobs, "removing the country line dropped the postings entirely"

    for job in jobs:
        assert job["location"] == "", (
            f"expected an empty location, got {job['location']!r} — "
            "the scan has fallen through to another metadata field"
        )
        assert job["title"], "title should survive a missing country line"
        assert not any(label in job["title"] for label in _METADATA_LABELS)


def test_department_is_empty_because_the_card_has_no_such_field() -> None:
    """Not a gap: NIRAS cards carry no department, so "" is the honest answer.

    Pinned so that a future card gaining one is a visible test failure rather
    than a field that silently stays blank.
    """
    soup = BeautifulSoup(_fixture_html(), "lxml")
    labels = {
        para.get_text(" ", strip=True).split(":")[0]
        for anchor in soup.find_all("a", href=lambda h: h and "/cvtp" in h)
        for para in anchor.select("p.list-tags")
    }
    assert labels == {"Country", "Employment", "Commencement", "Position length", "Deadline"}, (
        f"the card's metadata fields have changed: {sorted(labels)}"
    )
    assert all(job["department"] == "" for job in parse_fixture("niras"))
