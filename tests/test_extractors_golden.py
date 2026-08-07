"""Golden-file tests: what each extractor produces from its saved fixture.

`test_fixture_still_parses` asserts only that a fixture yields more than zero
jobs, which a drifted selector can satisfy while returning half the postings
with empty locations. These tests pin the exact output instead: the job count,
and the complete first-job dict. A career site redesign then fails here, at
test time, rather than showing up as a quietly shorter run months later.

The expectations below are the extractors' *current* output, warts included —
see the quirks noted at the bottom. A golden file records what the code does,
not what it ought to do; fixing a quirk is a change to the extractor, and the
expectation moves with it.

Nothing here touches the network: the fixtures are bytes already on disk.

When a fixture is legitimately refreshed (scripts/capture_fixtures.py, see
docs/REFACTOR-PLAN.md, WP0), these will fail. Read the assertion diff, confirm
the change matches what the site now serves, and paste the new values in.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.fixture_cases import FIXTURE_CASES, FIXTURES_DIR, parse_fixture

# source name -> expected job count and complete first-job dict.
#
# Five sources produce eight keys; impactpool produces nine, adding `company`,
# because it is an aggregator listing other organisations' vacancies rather
# than a single employer's career page. Do not "normalise" that away — the
# difference is real and _content_key in csv_store.py depends on it.
_GOLDEN: dict[str, dict[str, Any]] = {
    "busuu": {
        "count": 6,
        "first_job": {
            "source_name": "busuu",
            "title": "Senior Machine Learning / AI Engineer",
            "department": "",
            "location": "Madrid - Busuu",
            "listing_url": "https://osv-chegg.wd5.myworkdayjobs.com/Busuu",
            "detail_url": (
                "https://osv-chegg.wd5.myworkdayjobs.com/en-US/Busuu/job/Madrid---Busuu/"
                "Senior-Machine-Learning---AI-Engineer_R5379-1"
            ),
            "apply_url": (
                "https://osv-chegg.wd5.myworkdayjobs.com/en-US/Busuu/job/Madrid---Busuu/"
                "Senior-Machine-Learning---AI-Engineer_R5379-1"
            ),
            "raw_snippet": "Senior Machine Learning / AI Engineer Madrid - Busuu",
        },
    },
    "dsv": {
        "count": 10,
        "first_job": {
            "source_name": "dsv",
            "title": "Manager - Air Import",
            "department": "7 Aug 2026",
            "location": "Chester, PA, US, 19013",
            "listing_url": "https://jobs.dsv.com/search/",
            "detail_url": (
                "https://jobs.dsv.com/job/Chester-Manager-Air-Import-PA-19013/1402649033/"
            ),
            "apply_url": (
                "https://jobs.dsv.com/job/Chester-Manager-Air-Import-PA-19013/1402649033/"
            ),
            "raw_snippet": "Manager - Air Import 7 Aug 2026 Chester, PA, US, 19013",
        },
    },
    "givewell": {
        "count": 20,
        "first_job": {
            "source_name": "givewell",
            "title": "Program Officer",
            "department": "",
            "location": "United States + International (Remote)",
            "listing_url": "https://job-boards.greenhouse.io/givewell",
            "detail_url": "https://job-boards.greenhouse.io/givewell/jobs/5263759008",
            "apply_url": "https://job-boards.greenhouse.io/givewell/jobs/5263759008",
            "raw_snippet": "Program Officer United States + International (Remote)",
        },
    },
    "impactpool": {
        "count": 40,
        "first_job": {
            "source_name": "impactpool",
            "title": "Director of Programmes",
            "company": "Resource justice Network",
            "department": "",
            "location": "Remote",
            "listing_url": "https://www.impactpool.org/search",
            "detail_url": "https://www.impactpool.org/jobs/1229365",
            "apply_url": "https://www.impactpool.org/jobs/1229365",
            "raw_snippet": "Director of Programmes Remote",
        },
    },
    "kognity": {
        "count": 5,
        "first_job": {
            "source_name": "kognity",
            "title": "Delivery Manager - 12 months fixed-term contract",
            "department": "",
            "location": "Sweden",
            "listing_url": "https://jobs.ashbyhq.com/kognity",
            "detail_url": (
                "https://jobs.ashbyhq.com/kognity/bc514f8b-3ee3-4b5b-8917-2166fdf769fd"
            ),
            "apply_url": (
                "https://jobs.ashbyhq.com/kognity/bc514f8b-3ee3-4b5b-8917-2166fdf769fd"
            ),
            "raw_snippet": "Delivery Manager - 12 months fixed-term contract Sweden",
        },
    },
    "storytel": {
        "count": 6,
        "first_job": {
            "source_name": "storytel",
            # Quirk, pinned deliberately: teamtailor.py's first match on this
            # page is a department heading, so the title and raw_snippet are
            # "Product & Tech · Stockholm" and location comes back empty. Layer
            # 0 drops it later. Fixing the selector is an extractor change, not
            # a test change — if a later package fixes it, this moves.
            "title": "Product & Tech · Stockholm",
            "department": "",
            "location": "",
            "listing_url": "https://jobs.storytel.com/jobs",
            "detail_url": "https://jobs.storytel.com/jobs/8090473-senior-data-engineer",
            "apply_url": "https://jobs.storytel.com/jobs/8090473-senior-data-engineer",
            "raw_snippet": "Product & Tech · Stockholm",
        },
    },
}


def test_every_fixture_has_a_golden() -> None:
    """A newly captured fixture must not slip in unpinned."""
    assert set(_GOLDEN) == set(FIXTURE_CASES)


@pytest.mark.parametrize("name", sorted(_GOLDEN))
def test_extractor_output_matches_golden(name: str) -> None:
    filename = FIXTURE_CASES[name][0]
    if not (FIXTURES_DIR / filename).exists():
        pytest.skip(f"{filename} not captured yet — re-run scripts/capture_fixtures.py {name}")

    jobs = parse_fixture(name)
    expected = _GOLDEN[name]

    assert len(jobs) == expected["count"], (
        f"{name}: expected {expected['count']} jobs, got {len(jobs)}. "
        "Either a selector drifted or the fixture was refreshed."
    )
    assert jobs[0] == expected["first_job"]
