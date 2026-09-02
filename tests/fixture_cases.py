"""The saved fixtures, and the extractor call that reads each one.

One table, several consumers: the capture-script tests, the golden-file
extractor tests, and the CSV round-trip test all need to know which extractor
belongs to which fixture, and with which arguments. Keeping that in one place
means a newly captured fixture is wired into every test at once.

`capture_fixtures` lives in `scripts/`, outside the package, so importing it
needs a `sys.path` amendment. That hack lives here and nowhere else. The module
itself and `single_response_fetch` are both re-exported, so no other test needs
to touch `sys.path` — and, just as important, no other test ends up with two
imports that must stay in a particular order. An import-sorter will happily
hoist `import capture_fixtures` above the line that made it importable; there
is nothing to hoist if there is only one import.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

import capture_fixtures  # noqa: E402
from capture_fixtures import recorded_pages_fetch, single_response_fetch  # noqa: E402

from job_scraper.extractors import (  # noqa: E402
    against_malaria,
    ashby,
    bearingpoint,
    giving_what_we_can,
    greenhouse,
    impactpool,
    jpal,
    niras,
    successfactors_html,
    teamtailor,
    unops,
    workday,
)

__all__ = [
    "FIXTURES_DIR",
    "FIXTURE_CASES",
    "capture_fixtures",
    "fixture_pages",
    "parse_fixture",
    "recorded_pages_fetch",
    "single_response_fetch",
]

FIXTURES_DIR = _PROJECT_ROOT / "tests" / "fixtures"

Extractor = Callable[[str, Callable[..., str]], list[dict[str, Any]]]

# source name -> (fixture filename, listing URL, extractor bound to its args)
FIXTURE_CASES: dict[str, tuple[str, str, Extractor]] = {
    "givewell": (
        "givewell.json",
        "https://job-boards.greenhouse.io/givewell",
        lambda url, fetch: greenhouse.extract(url, fetch, source_name="givewell"),
    ),
    "kognity": (
        "kognity.html",
        "https://jobs.ashbyhq.com/kognity",
        lambda url, fetch: ashby.extract(url, fetch, source_name="kognity"),
    ),
    "storytel": (
        "storytel.html",
        "https://jobs.storytel.com/jobs",
        lambda url, fetch: teamtailor.extract(url, fetch, source_name="storytel"),
    ),
    "busuu": (
        "busuu.html",
        "https://osv-chegg.wd5.myworkdayjobs.com/Busuu",
        lambda url, fetch: workday.extract(url, fetch, source_name="busuu"),
    ),
    "dsv": (
        "dsv.html",
        "https://jobs.dsv.com/search/",
        # The parser, not the walk: 201 pages, which is far too much markup to
        # store. The walk itself is `novo_nordisk`'s to cover.
        lambda url, fetch: successfactors_html._parse_page(
            BeautifulSoup(fetch(url), "lxml"), "https://jobs.dsv.com/search/", "dsv"
        ),
    ),
    "iss": (
        "iss.html",
        "https://jobs.issworld.com/search/",
        # The parser, not the walk. This board is only four pages and could
        # have been captured whole; it is not, because the walk is one shared
        # module and `novo_nordisk` already exercises it against real markup.
        # What ISS is here for is its tile skin, which is the layout the other
        # three instances do not have.
        lambda url, fetch: successfactors_html._parse_page(
            BeautifulSoup(fetch(url), "lxml"), "https://jobs.issworld.com/search/", "iss"
        ),
    ),
    "novo_nordisk": (
        "novo_nordisk.html",
        "https://careers.novonordisk.com/search",
        # The one SuccessFactors instance captured as its whole walk (four
        # pages). The walk is a single shared module, so capturing all five
        # instances' walks would test the same loop five times over; this one
        # exercises it against real markup — pagination, the last short page,
        # and the reconciliation against the board's own total — while the other
        # four pin their skins' parsing, which is what actually differs between
        # them.
        lambda url, fetch: successfactors_html.extract(
            url,
            fetch,
            source_name="novo_nordisk",
            page_step=100,
            base_search_url="https://careers.novonordisk.com/search",
        ),
    ),
    "coloplast": (
        "coloplast.html",
        "https://careers.coloplast.com/search/",
        # The parser, not the walk. Fourteen pages could have been captured;
        # this one is here for its sub-brand postings (/Kerecis/job/…), pinned
        # by `test_coloplast_keeps_sub_brand_postings`, and the walk is
        # `novo_nordisk`'s to cover.
        lambda url, fetch: successfactors_html._parse_page(
            BeautifulSoup(fetch(url), "lxml"), "https://careers.coloplast.com/search/", "coloplast"
        ),
    ),
    "impactpool": (
        # The parser, not the walk: roughly a hundred pages, which would be
        # ten megabytes of someone else's HTML. This walk is not shared with
        # anything, so it is covered synthetically in `tests/test_pagination.py`
        # — the one source here whose walk no captured markup exercises.
        # Replaying a single page through `extract` would now raise, and rightly:
        # page 1 links to page 2 and the harness has no page 2 to give it.
        "impactpool.html",
        "https://www.impactpool.org/search",
        lambda url, fetch: impactpool._parse_page(
            BeautifulSoup(fetch(url), "lxml"), url, "impactpool"
        ),
    ),
    "against_malaria_foundation": (
        "against_malaria_foundation.html",
        "https://www.againstmalaria.com/Vacancies.aspx",
        lambda url, fetch: against_malaria.extract(
            url, fetch, source_name="against_malaria_foundation"
        ),
    ),
    "bearingpoint_sweden": (
        "bearingpoint_sweden.html",
        "https://www.bearingpoint.com/en-se/careers/open-roles/",
        lambda url, fetch: bearingpoint.extract(url, fetch, source_name="bearingpoint_sweden"),
    ),
    "fjallraven": (
        "fjallraven.html",
        "https://career.fjallraven.com/jobs",
        lambda url, fetch: teamtailor.extract(url, fetch, source_name="fjallraven"),
    ),
    "founders_pledge": (
        "founders_pledge.html",
        "https://careers.founderspledge.com/jobs",
        lambda url, fetch: teamtailor.extract(url, fetch, source_name="founders_pledge"),
    ),
    "futurelearn": (
        "futurelearn.html",
        "https://gusglobaluniversitysystems-futurelearn.teamtailor.com/",
        lambda url, fetch: teamtailor.extract(url, fetch, source_name="futurelearn"),
    ),
    "giving_what_we_can": (
        "giving_what_we_can.html",
        "https://www.givingwhatwecan.org/get-involved/careers",
        lambda url, fetch: giving_what_we_can.extract(url, fetch, source_name="giving_what_we_can"),
    ),
    "jpal": (
        "jpal.html",
        "https://www.povertyactionlab.org/careers",
        lambda url, fetch: jpal.extract(url, fetch, source_name="jpal"),
    ),
    "niras": (
        "niras.html",
        "https://www.niras.com/jobs/vacant-positions/",
        lambda url, fetch: niras.extract(url, fetch, source_name="niras"),
    ),
    "path": (
        "path.html",
        "https://path.wd1.myworkdayjobs.com/en-US/External",
        lambda url, fetch: workday.extract(url, fetch, source_name="path"),
    ),
    "planted": (
        "planted.html",
        "https://careers.eatplanted.com/jobs",
        lambda url, fetch: teamtailor.extract(url, fetch, source_name="planted"),
    ),
    "unops": (
        # Captured as the whole thirteen-page walk. UNOPS had no fixture at all
        # until WP11's review pointed out that the one source with a hand-rolled
        # total-reader was also the one with no real markup behind it.
        "unops.html",
        "https://careers.unops.org/careersmarketplace/SearchJobs",
        lambda url, fetch: unops.extract(url, fetch, source_name="unops"),
    ),
    "seven_perigee": (
        "seven_perigee.html",
        "https://careers.perigee.se",
        lambda url, fetch: teamtailor.extract(url, fetch, source_name="seven_perigee"),
    ),
}


def fixture_pages(name: str) -> list[Path]:
    """Every saved page of *name*'s fixture, in the order the extractor asked.

    A single-page fixture is one file, `name.ext`. A paginated one adds
    `name.p1.ext`, `name.p2.ext`, … (see scripts/capture_fixtures.py). Sorting
    numerically rather than by filename matters once a walk passes ten pages.
    """
    filename = FIXTURE_CASES[name][0]
    first = FIXTURES_DIR / filename
    stem, _, ext = filename.rpartition(".")
    page_file = re.compile(rf"^{re.escape(stem)}\.p(\d+)\.{re.escape(ext)}$")
    numbered = [
        (int(match.group(1)), path)
        for path in FIXTURES_DIR.glob(f"{stem}.p*.{ext}")
        if (match := page_file.match(path.name))
    ]
    return [first, *(path for _, path in sorted(numbered))]


def parse_fixture(name: str) -> list[dict[str, Any]]:
    """Run *name*'s extractor over its saved fixture. Never touches the network."""
    _, listing_url, extractor = FIXTURE_CASES[name]
    pages = [path.read_text(encoding="utf-8") for path in fixture_pages(name)]
    return extractor(listing_url, recorded_pages_fetch(pages))
