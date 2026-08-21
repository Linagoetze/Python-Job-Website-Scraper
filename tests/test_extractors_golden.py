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
# difference is real, and the company-stamping in pipeline.py relies on the
# extractor setting the field only when it genuinely knows the employer.
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
        # WP8g (2026-08-21): `department` was "7 Aug 2026" — the posting date,
        # picked up by the positional heuristic as "the second text that is not
        # the title". The classic layout labels this cell `span.jobFacility`
        # ("Managerial", "Freight Forwarding"), so it is now read structurally
        # like the tile layout's. `location` is unchanged: the heuristic already
        # landed on it, and `span.jobLocation` holds the same string.
        "count": 10,
        "first_job": {
            "source_name": "dsv",
            "title": "Manager - Air Import",
            "department": "Managerial",
            "location": "Chester, PA, US, 19013",
            "listing_url": "https://jobs.dsv.com/search/",
            "detail_url": (
                "https://jobs.dsv.com/job/Chester-Manager-Air-Import-PA-19013/1402649033/"
            ),
            "apply_url": (
                "https://jobs.dsv.com/job/Chester-Manager-Air-Import-PA-19013/1402649033/"
            ),
            "raw_snippet": "Manager - Air Import Managerial Chester, PA, US, 19013",
        },
    },
    "iss": {
        # WP8g (2026-08-21): the source that put the literal word "Title" in all
        # 33 of its gold-set locations. ISS serves the modern SuccessFactors
        # tile layout, where `span.sr-only` labels each field — and the label
        # for the title block sits inside the title's own container, so the old
        # "first text that is not the title" heuristic read the label as data.
        # Now read from the labelled `section-field` blocks; all 20 rows carry a
        # real place. See test_iss_location_is_never_the_field_label below.
        "count": 20,
        "first_job": {
            "source_name": "iss",
            "title": (
                "ISS søger en handyman til praktiske ejendomsservice opgaver "
                "hos vores kunde i København K"
            ),
            "department": "Property Services",
            "location": "København K, DK, 1402",
            "listing_url": "https://jobs.issworld.com/search/",
            "detail_url": (
                "https://jobs.issworld.com/job/K%C3%B8benhavn-K-ISS-s%C3%B8ger-en-handyman"
                "-til-praktiske-ejendomsservice-opgaver-hos-vores-kunde-i-K%C3%B8benhavn-K"
                "-1402/1364641957/"
            ),
            "apply_url": (
                "https://jobs.issworld.com/job/K%C3%B8benhavn-K-ISS-s%C3%B8ger-en-handyman"
                "-til-praktiske-ejendomsservice-opgaver-hos-vores-kunde-i-K%C3%B8benhavn-K"
                "-1402/1364641957/"
            ),
            "raw_snippet": (
                "ISS søger en handyman til praktiske ejendomsservice opgaver "
                "hos vores kunde i København K Property Services "
                "København K, DK, 1402"
            ),
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
        # WP8e (2026-08-20): fixed. The page had been redesigned since WP2 pinned
        # this fixture — titles moved from a <div> into a <span title="...">, and
        # teamtailor.py's div-based fallback then picked up the metadata <div> as
        # the title for every row, not just one. What WP2 read as "a department
        # heading scraped as a posting" was this bug on row 1, not a real
        # department heading: teamtailor.py now reads the <span title> when
        # present, so all 6 rows get their real title and location. Count is
        # unchanged at 6 (no phantom row existed to remove); every row's title
        # and location go from wrong/empty to correct.
        "count": 6,
        "first_job": {
            "source_name": "storytel",
            "title": "Senior Data Engineer",
            "department": "Product & Tech",
            "location": "Stockholm",
            "listing_url": "https://jobs.storytel.com/jobs",
            "detail_url": "https://jobs.storytel.com/jobs/8090473-senior-data-engineer",
            "apply_url": "https://jobs.storytel.com/jobs/8090473-senior-data-engineer",
            "raw_snippet": "Senior Data Engineer Product & Tech Stockholm",
        },
    },
    # --- WP8e (2026-08-20): fixtures captured to investigate "no location given"
    # drops. Five of these (fjallraven, founders_pledge, futurelearn, planted,
    # seven_perigee) share teamtailor.py's fix above but needed a second one:
    # their redesigned markup puts the metadata <div> as a *sibling* of <a>, not
    # a child, with a varying number of "·"-joined segments (sometimes no
    # department at all) and a structurally-detected work-type tag ("Hybrid",
    # "Fully Remote", ...) rather than a fixed vocabulary. bearingpoint_sweden
    # needed its own fix: the same site redesign moved its location from a <p>
    # into a sibling <div class="job-info">. against_malaria_foundation,
    # giving_what_we_can, jpal and path are pinned with an empty location as-is
    # — confirmed genuinely absent from the page, not an extractor gap; see the
    # WP8e Result section in docs/REFACTOR-PLAN.md.
    "fjallraven": {
        "count": 3,
        "first_job": {
            "source_name": "fjallraven",
            "title": "Administrator",
            "department": "",
            "location": "Solna, Sweden",
            "listing_url": "https://career.fjallraven.com/jobs",
            "detail_url": "https://career.fjallraven.com/jobs/8044352-administrator",
            "apply_url": "https://career.fjallraven.com/jobs/8044352-administrator",
            "raw_snippet": "Administrator Solna, Sweden",
        },
    },
    "founders_pledge": {
        "count": 6,
        "first_job": {
            "source_name": "founders_pledge",
            "title": "Funds Program Manager",
            "department": "Research",
            "location": "New York, San Francisco",
            "listing_url": "https://careers.founderspledge.com/jobs",
            "detail_url": "https://careers.founderspledge.com/jobs/8230675-funds-program-manager",
            "apply_url": "https://careers.founderspledge.com/jobs/8230675-funds-program-manager",
            "raw_snippet": (
                "Funds Program Manager Research New York, San Francisco Hybrid"
            ),
        },
    },
    "futurelearn": {
        "count": 3,
        "first_job": {
            "source_name": "futurelearn",
            "title": "Consulente commerciale - Vendita Educazione",
            "department": "Admissions",
            "location": "Spain (remote)",
            "listing_url": "https://gusglobaluniversitysystems-futurelearn.teamtailor.com/",
            "detail_url": (
                "https://gusglobaluniversitysystems-futurelearn.teamtailor.com/jobs/"
                "8191472-consulente-commerciale-vendita-educazione"
            ),
            "apply_url": (
                "https://gusglobaluniversitysystems-futurelearn.teamtailor.com/jobs/"
                "8191472-consulente-commerciale-vendita-educazione"
            ),
            "raw_snippet": (
                "Consulente commerciale - Vendita Educazione Admissions "
                "Spain (remote) Hybrid"
            ),
        },
    },
    "planted": {
        "count": 16,
        "first_job": {
            "source_name": "planted",
            "title": "Produktionsmitarbeiter:in (f/m/d) - Memmingen Germany",
            "department": "Production",
            "location": "Memmingen",
            "listing_url": "https://careers.eatplanted.com/jobs",
            "detail_url": (
                "https://careers.eatplanted.com/de-inf/jobs/"
                "5548595-produktionsmitarbeiter-in-f-m-d-memmingen-germany"
            ),
            "apply_url": (
                "https://careers.eatplanted.com/de-inf/jobs/"
                "5548595-produktionsmitarbeiter-in-f-m-d-memmingen-germany"
            ),
            "raw_snippet": (
                "Produktionsmitarbeiter:in (f/m/d) - Memmingen Germany "
                "Production Memmingen"
            ),
        },
    },
    "seven_perigee": {
        "count": 1,
        "first_job": {
            "source_name": "seven_perigee",
            "title": "iOS Developer",
            "department": "",
            "location": "Malmö",
            "listing_url": "https://careers.perigee.se",
            "detail_url": "https://careers.perigee.se/jobs/3401069-ios-developer",
            "apply_url": "https://careers.perigee.se/jobs/3401069-ios-developer",
            "raw_snippet": "iOS Developer Malmö",
        },
    },
    "bearingpoint_sweden": {
        "count": 6,
        "first_job": {
            "source_name": "bearingpoint_sweden",
            "title": "Manager – Strategy & Operations, Retail and Manufacturing (Malmö)",
            "department": "",
            "location": "Malmö",
            "listing_url": "https://www.bearingpoint.com/en-se/careers/open-roles/",
            "detail_url": (
                "https://www.bearingpoint.com/en-se/careers/open-roles/offer/?id=T7972813"
            ),
            "apply_url": (
                "https://www.bearingpoint.com/en-se/careers/open-roles/offer/?id=T7972813"
            ),
            "raw_snippet": (
                "Manager – Strategy & Operations, Retail and Manufacturing (Malmö) Malmö"
            ),
        },
    },
    "against_malaria_foundation": {
        # Not a bug: this page has no location field at all, structured or
        # otherwise (free-text blurbs mention e.g. "UK-based" in prose, which
        # this extractor correctly does not attempt to mine). Pinned empty.
        "count": 2,
        "first_job": {
            "source_name": "against_malaria_foundation",
            "title": "Senior Software Engineer",
            "department": "",
            "location": "",
            "listing_url": "https://www.againstmalaria.com/Vacancies.aspx",
            "detail_url": (
                "https://www.againstmalaria.com/NewsItem.aspx?"
                "newsitem=AMF-is-hiring-Senior-Software-Engineer"
            ),
            "apply_url": (
                "https://www.againstmalaria.com/NewsItem.aspx?"
                "newsitem=AMF-is-hiring-Senior-Software-Engineer"
            ),
            "raw_snippet": "Senior Software Engineer",
        },
    },
    "giving_what_we_can": {
        # Not a bug: the only listing on this page has no location anywhere in
        # its markup, just a closed-applications note. Pinned empty.
        "count": 1,
        "first_job": {
            "source_name": "giving_what_we_can",
            "title": "Head of Marketing",
            "department": "",
            "location": "",
            "listing_url": "https://www.givingwhatwecan.org/get-involved/careers",
            "detail_url": "https://www.givingwhatwecan.org/head-of-marketing",
            "apply_url": "https://www.givingwhatwecan.org/head-of-marketing",
            "raw_snippet": "Head of Marketing",
        },
    },
    "jpal": {
        "count": 9,
        "first_job": {
            "source_name": "jpal",
            "title": "Field-Based Research Associate - Democratic Republic of Congo",
            "department": "",
            "location": "Democratic Republic of the Congo",
            "listing_url": "https://www.povertyactionlab.org/careers",
            "detail_url": (
                "https://www.povertyactionlab.org/careers/"
                "field-based-research-associate-democratic-republic-congo-job-105604"
            ),
            "apply_url": (
                "https://www.povertyactionlab.org/careers/"
                "field-based-research-associate-democratic-republic-congo-job-105604"
            ),
            "raw_snippet": (
                "Field-Based Research Associate - Democratic Republic of Congo "
                "Democratic Republic of the Congo"
            ),
        },
    },
    "path": {
        # Not a bug: workday.py's location selectors both work here (the busuu
        # golden above pins the same code succeeding). 9 of these 20 rows carry
        # an empty location because their Workday subtitle list holds only the
        # req ID with no location line at all — confirmed against the raw HTML,
        # not assumed.
        "count": 20,
        "first_job": {
            "source_name": "path",
            "title": "Consultant – Planning and Finance Management, Technical Support Unit",
            "department": "",
            "location": "India, New Delhi Country Program Office",
            "listing_url": "https://path.wd1.myworkdayjobs.com/en-US/External",
            "detail_url": (
                "https://path.wd1.myworkdayjobs.com/en-US/External/job/"
                "India-New-Delhi-Country-Program-Office/"
                "Consultant---Planning-and-Finance-Management--Technical-Support-Unit_JR2742"
            ),
            "apply_url": (
                "https://path.wd1.myworkdayjobs.com/en-US/External/job/"
                "India-New-Delhi-Country-Program-Office/"
                "Consultant---Planning-and-Finance-Management--Technical-Support-Unit_JR2742"
            ),
            "raw_snippet": (
                "Consultant – Planning and Finance Management, Technical Support Unit "
                "India, New Delhi Country Program Office"
            ),
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


def test_iss_location_is_never_the_field_label() -> None:
    """Pin WP8g's bug: a screen-reader label must never reach the location field.

    The golden above would catch this on row 1 alone. This one is deliberately
    separate and checks every row, because the failure was uniform — all 33 ISS
    rows in the 2026-08-18 gold set carried "Title" — and a heuristic that
    regressed for rows 2..n while row 1 stayed correct would slip past a
    first-job assertion. Layer 0 reads `location` as a city name, so a label
    landing here costs the source every posting it has.
    """
    filename = FIXTURE_CASES["iss"][0]
    if not (FIXTURES_DIR / filename).exists():
        pytest.skip(f"{filename} not captured yet — re-run scripts/capture_fixtures.py iss")

    jobs = parse_fixture("iss")
    assert jobs, "iss fixture parsed to zero jobs"

    offenders = [j for j in jobs if j["location"].strip().casefold() == "title"]
    assert not offenders, (
        f"{len(offenders)} of {len(jobs)} ISS rows have the column label 'Title' "
        "as their location; the sr-only guard in successfactors_html has regressed"
    )
