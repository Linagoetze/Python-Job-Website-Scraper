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

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

import capture_fixtures  # noqa: E402
from capture_fixtures import single_response_fetch  # noqa: E402

from job_scraper.extractors import (  # noqa: E402
    ashby,
    greenhouse,
    impactpool,
    successfactors_html,
    teamtailor,
    workday,
)

__all__ = [
    "FIXTURES_DIR",
    "FIXTURE_CASES",
    "capture_fixtures",
    "parse_fixture",
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
        lambda url, fetch: successfactors_html.extract(
            url,
            fetch,
            source_name="dsv",
            page_step=10,
            base_search_url="https://jobs.dsv.com/search/",
        ),
    ),
    "impactpool": (
        "impactpool.html",
        "https://www.impactpool.org/search",
        lambda url, fetch: impactpool.extract(url, fetch, source_name="impactpool"),
    ),
}


def parse_fixture(name: str) -> list[dict[str, Any]]:
    """Run *name*'s extractor over its saved fixture. Never touches the network."""
    filename, listing_url, extractor = FIXTURE_CASES[name]
    path = FIXTURES_DIR / filename
    return extractor(listing_url, single_response_fetch(path.read_text(encoding="utf-8")))
