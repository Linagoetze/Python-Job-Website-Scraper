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

import json
from typing import Any
from urllib.parse import urlparse

import pytest

from job_scraper.extractors import (
    asana,
    ashby,
    personio,
    sida,
    smartrecruiters,
    workable,
)
from job_scraper.filtering import _HYBRID_CONFIRMED_REASON, build_hybrid_pattern, matches_rules
from tests.fixture_cases import EMPTY_FIXTURES, FIXTURE_CASES, FIXTURES_DIR, parse_fixture

# source name -> expected job count and complete first-job dict.
#
# Five sources produce eight keys; impactpool produces nine, adding `company`,
# because it is an aggregator listing other organisations' vacancies rather
# than a single employer's career page. Do not "normalise" that away — the
# difference is real, and the company-stamping in pipeline.py relies on the
# extractor setting the field only when it genuinely knows the employer.
_GOLDEN: dict[str, dict[str, Any]] = {
    "airbus": {
        # SP3c (2026-09-24): one POST, with the listing's own facet query sent
        # as appliedFacets. The board is narrowed below Workday's 2000 cap to
        # the owner's chosen country; unnarrowed it states 2000 and is refused.
        # The response proved the filter applied (the country's own facet count
        # equals total). The query reaches no detail_url, and the one posting
        # also in the store has a byte-identical key there.
        "count": 16,
        "first_job": {
            "source_name": "airbus",
            "title": "Student Assistant HR",
            "department": "",
            "location": "CPO - Copenhagen Office",
            "listing_url": (
                "https://ag.wd3.myworkdayjobs.com/Airbus"
                "?locationCountry=49ab063f422741e2aef271de00efeac8"
            ),
            "detail_url": (
                "https://ag.wd3.myworkdayjobs.com/en-US/Airbus/job/CPO---Copenhagen-Office/"
                "Student-Assistant-HR_JR10443545"
            ),
            "apply_url": (
                "https://ag.wd3.myworkdayjobs.com/en-US/Airbus/job/CPO---Copenhagen-Office/"
                "Student-Assistant-HR_JR10443545"
            ),
            "raw_snippet": "Student Assistant HR CPO - Copenhagen Office",
        },
    },
    "busuu": {
        # SP3b (2026-09-23): re-captured as the board's JSON walk (one POST;
        # the board holds 5). The page this replaced was the rendered listing
        # (6 postings on 2026-08-07). Captured the same moment, the rendered
        # page and this JSON gave the same 5 postings with identical titles,
        # locations and detail URLs.
        "count": 5,
        "first_job": {
            "source_name": "busuu",
            "title": "Senior B2B Customer Marketing Manager",
            "department": "",
            "location": "London - Busuu",
            "listing_url": "https://osv-chegg.wd5.myworkdayjobs.com/Busuu",
            "detail_url": (
                "https://osv-chegg.wd5.myworkdayjobs.com/en-US/Busuu/job/London---Busuu/"
                "Senior-B2B-Customer-Marketing-Manager_R5387-1"
            ),
            "apply_url": (
                "https://osv-chegg.wd5.myworkdayjobs.com/en-US/Busuu/job/London---Busuu/"
                "Senior-B2B-Customer-Marketing-Manager_R5387-1"
            ),
            "raw_snippet": "Senior B2B Customer Marketing Manager London - Busuu",
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
    "novo_nordisk": {
        # WP11 review (2026-09-02): re-captured as its whole four-page walk, so
        # this is the board's real length rather than page one's 100. It is the
        # SuccessFactors instance that covers the shared walk end to end — the
        # other four pin their skins' parsing. Recapture with `--pages all`.
        "count": 329,
        "first_job": {
            "source_name": "novo_nordisk",
            "title": "Regulatory Affairs Specialist",
            "department": "Reg Affairs & Safety Pharmacovigilance",
            "location": "Søborg, Capital Region of Denmark, DK",
            "listing_url": "https://careers.novonordisk.com/search",
            "detail_url": (
                "https://careers.novonordisk.com/job/"
                "S%C3%B8borg-Regulatory-Affairs-Specialist-Capi/1432777733/"
            ),
            "apply_url": (
                "https://careers.novonordisk.com/job/"
                "S%C3%B8borg-Regulatory-Affairs-Specialist-Capi/1432777733/"
            ),
            "raw_snippet": (
                "Regulatory Affairs Specialist Reg Affairs & Safety Pharmacovigilance "
                "Søborg, Capital Region of Denmark, DK"
            ),
        },
    },
    "coloplast": {
        # WP8g follow-on (2026-08-21): the capture that disproved the package's
        # own reasoning. Two bugs, both invisible without a fixture.
        #
        # 1. Sub-brand postings were dropped. Coloplast hosts Kerecis and Atos,
        #    whose links are /Kerecis/job/… and /Atos/job/…, and the extractor
        #    matched only hrefs *starting* "/job/". 6 of 25 rows were lost
        #    silently — including this first_job. The count is 25, not 19.
        # 2. `department` is `span.jobDepartment` here, not the `span.jobFacility`
        #    DSV and Novo Nordisk use, so reading only jobFacility blanked all 19
        #    surviving rows.
        #
        # Two of the 25 rows still have an empty department. That is honest: the
        # markup is literally <span class="jobDepartment"></span> for those.
        "count": 25,
        "first_job": {
            "source_name": "coloplast",
            "title": "Regenerative Surgical Specialist - Western CT",
            "department": "Sales",
            "location": "Stamford, CT, US",
            "listing_url": "https://careers.coloplast.com/search/",
            "detail_url": (
                "https://careers.coloplast.com/Kerecis/job/Stamford-Regenerative-"
                "Surgical-Specialist-Western-CT-CT-06901/1418450133/"
            ),
            "apply_url": (
                "https://careers.coloplast.com/Kerecis/job/Stamford-Regenerative-"
                "Surgical-Specialist-Western-CT-CT-06901/1418450133/"
            ),
            "raw_snippet": ("Regenerative Surgical Specialist - Western CT Sales Stamford, CT, US"),
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
        # Refreshed 2026-09-25 from that morning's run (page 1 from the HTTP
        # cache, through capture_fixtures.sanitise_html). The title had moved
        # from a <div> to an <h3>, and the positional parser stored the
        # employer as every posting's title; `_card_fields` now reads fields
        # by role. The card shapes this page lacks (no location, blank title,
        # unknown markup) are pinned in `tests/test_impactpool_extractor.py`.
        "count": 40,
        "first_job": {
            "source_name": "impactpool",
            "title": "Senior Analyst of the International Accountability Platform for Belarus",
            "company": "DIGNITY - Danish Institute Against Torture",
            "department": "",
            "location": (
                "Remote | Copenhagen | Netherlands | Germany | France | Spain | Portugal"
                " | Au | United Kingdom | Switzerland"
            ),
            "listing_url": "https://www.impactpool.org/search",
            "detail_url": "https://www.impactpool.org/jobs/1238292",
            "apply_url": "https://www.impactpool.org/jobs/1238292",
            "raw_snippet": (
                "Senior Analyst of the International Accountability Platform for Belarus"
                " Remote | Copenhagen | Netherlands | Germany | France | Spain | Portugal"
                " | Au | United Kingdom | Switzerland"
            ),
        },
    },
    "kognity": {
        # SP4d (2026-10-02): the reader moved from the board page's
        # window.__appData to Ashby's public posting API (the owner's choice),
        # because the board page carries no description and the detail page is a
        # JS shell. The API's location matched the stored one on every posting
        # stored, and it adds a real department. The board page itself lives on
        # as kognity.listing.html, for the probe. 5 -> 4 is the board, which now
        # lists four postings, not the move. SP4f: Ashby's `workplaceType` joins
        # the snippet, where Layer 0's hybrid gate reads it ("Hybrid" here).
        "count": 4,
        "first_job": {
            "source_name": "kognity",
            "title": "VP of Customer Success",
            "location": "Stockholm",
            "department": "Commercial",
            "listing_url": "https://jobs.ashbyhq.com/kognity",
            "detail_url": ("https://jobs.ashbyhq.com/kognity/c4574356-f612-4edb-b66c-811cf198b79e"),
            "apply_url": ("https://jobs.ashbyhq.com/kognity/c4574356-f612-4edb-b66c-811cf198b79e"),
            "raw_snippet": "VP of Customer Success Commercial Stockholm Hybrid",
        },
        # Too long to inline, so pinned by length and opening words.
        "description": (3308, "This is not a typical Customer Success role."),
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
            "raw_snippet": ("Funds Program Manager Research New York, San Francisco Hybrid"),
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
                "Consulente commerciale - Vendita Educazione Admissions Spain (remote) Hybrid"
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
                "Produktionsmitarbeiter:in (f/m/d) - Memmingen Germany Production Memmingen"
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
    "gfi_europe": {
        # CU2 (2026-09-02): captured for the first time, after nineteen runs of
        # zero postings. The page had moved from root-relative hrefs to
        # absolute ones and the raw-string prefix test stopped matching; see
        # the extractor's module docstring. `location` and `department` are
        # empty for every row because the listing genuinely carries neither —
        # the place, where there is one, is inside the title.
        "count": 3,
        "first_job": {
            "source_name": "gfi_europe",
            "title": "Executive Director (remote)",
            "location": "",
            "department": "",
            "listing_url": "https://gfieurope.org/careers/",
            "detail_url": "https://gfieurope.org/careers/executive-director",
            "apply_url": "https://gfieurope.org/careers/executive-director",
            "raw_snippet": "Executive Director (remote)",
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
        # WP11 (2026-09-02): re-captured as the whole five-page walk, not just
        # page 0, so the count is the listing's real length. The old count of 9
        # was one page — and a golden test that only ever saw page 0 is why the
        # walk could stop after it without any test noticing.
        "count": 37,
        "first_job": {
            "source_name": "jpal",
            "title": "Training and Education Associate - J-PAL Latin America and the Caribbean",
            "department": "",
            "location": "Guatemala",
            "listing_url": "https://www.povertyactionlab.org/careers",
            "detail_url": (
                "https://www.povertyactionlab.org/careers/"
                "training-and-education-associate-j-pal-latin-america-and-caribbean-job-105605"
            ),
            "apply_url": (
                "https://www.povertyactionlab.org/careers/"
                "training-and-education-associate-j-pal-latin-america-and-caribbean-job-105605"
            ),
            "raw_snippet": (
                "Training and Education Associate - J-PAL Latin America and the Caribbean Guatemala"
            ),
        },
    },
    "niras": {
        # WP8g follow-on (2026-08-21): captured once the fetcher bypass was
        # fixed, and it immediately showed a second bug. `title` had been "the
        # first child's text", but the anchor's only element child is the
        # wrapping `div.box-content`, so every title arrived with the whole
        # card appended — "… Country: Tunisia Employment: Temporary
        # Commencement: 02/09/2024 Position length: 300 Deadline: Sep 1, 2026".
        # Now read from the labelled `p.headline`.
        #
        # Two jobs is correct, not a truncated capture: no filter input is
        # checked and the page's own counter reads "Vacant positions: 2".
        "count": 2,
        "first_job": {
            "source_name": "niras",
            "title": "7.004 Expert Communication institutionelle",
            "department": "",
            "location": "Tunisia",
            "listing_url": "https://www.niras.com/jobs/vacant-positions/",
            "detail_url": (
                "https://www.niras.com/jobs/vacant-positions/"
                "cvtp-8491-7004-expert-communication-institutionelle/"
            ),
            "apply_url": (
                "https://www.niras.com/jobs/vacant-positions/"
                "cvtp-8491-7004-expert-communication-institutionelle/"
            ),
            "raw_snippet": "7.004 Expert Communication institutionelle Tunisia",
        },
    },
    "jobsinlund": {
        # SP4d (2026-10-02): first fixture for this reader, page 1 of a 34-page
        # walk (see fixture_cases.py). It now supplies each posting's own
        # description to Layer 5, stripped of its HTML. This one is 464
        # characters, under the 500 a fetched page is held to, and is read.
        "count": 25,
        "first_job": {
            "source_name": "jobsinlund",
            "title": "Global Cleantech Marketing Coordinator (12-Month Temp)",
            "company": "Radeptus",
            "location": "Lund, Sweden",
            "department": "",
            "listing_url": "https://jobsinlund.com/?language[]=en&location.address=Lund",
            "detail_url": (
                "https://jobsinnetwork.com/jobs/global-cleantech-marketing-coordinator-"
                "12month-temp/ba0b629f47cabfb96a6af6ecf7d6fff5"
            ),
            "apply_url": (
                "https://click.appcast.io/t/"
                "GO16tXR_0PhMSkais7ddlbpRhxaRHaeY2cTyQcuiWD9Et5ORdzPM0eeLCHbhCIKo"
            ),
            "raw_snippet": "Global Cleantech Marketing Coordinator (12-Month Temp) Lund, Sweden",
            "description_text": (
                "Comsys AB in Lund, Sweden seeks a Marketing Coordinator for a 12\u2011month "
                "parental leave cover. This full\u2011time temporary role involves coordinating "
                "brand communication, creating content, and supporting product launches across "
                "digital channels. You will work with product management and sales, publish "
                "materials, manage WordPress, and help optimize campaigns using Google Ads and "
                "analytics. English proficiency and a marketing background are essential. "
                "#J-18808-Ljbffr"
            ),
        },
    },
    "unops": {
        # WP11 review (2026-09-02): UNOPS had no fixture at all, which is how a
        # crash in its total-reader survived — the reader was only ever run
        # against hand-written markup. Captured as the whole thirteen-page walk;
        # recapture with `--pages all`.
        "count": 74,
        "first_job": {
            "source_name": "unops",
            "title": "Architectural Engineering Technician - Senior Associate",
            "department": "",
            "location": "Addis Ababa",
            "listing_url": "https://careers.unops.org/careersmarketplace/SearchJobs",
            "detail_url": (
                "https://careers.unops.org/careersmarketplace/JobDetail/"
                "Architectural-Engineering-Technician-Senior-Associate/4215"
            ),
            "apply_url": (
                "https://careers.unops.org/careersmarketplace/JobDetail/"
                "Architectural-Engineering-Technician-Senior-Associate/4215"
            ),
            "raw_snippet": ("Architectural Engineering Technician - Senior Associate Addis Ababa"),
        },
    },
    "path": {
        # SP3b (2026-09-23): 20 -> 64, and the move is the bug fixed, not a
        # site change. The count was 20 because workday.py read the first
        # rendered page of a board that said "1 - 20 of 61 jobs", and this
        # golden pinned the short read as correct. The reader now walks the
        # board's JSON endpoint to its stated total, and this fixture holds
        # that whole walk (path.json + path.p1-p3.json, four POSTs); recapture
        # with `--pages all`. Every detail_url for a posting the old capture
        # also held is byte-identical, as the dedupe key requires.
        #
        # Still not a bug: 21 of these 64 rows carry an empty location, because
        # Workday gives those postings no location line at all — the rendered
        # card shows only the req ID. On the same day, all 20 postings on the
        # rendered first page matched this JSON's location, empties included.
        "count": 64,
        "first_job": {
            "source_name": "path",
            "title": "Global Procurement Officer, Asia",
            "department": "",
            "location": "Vietnam, Hanoi Regional Program Office",
            "listing_url": "https://path.wd1.myworkdayjobs.com/en-US/External",
            "detail_url": (
                "https://path.wd1.myworkdayjobs.com/en-US/External/job/"
                "Vietnam-Hanoi-Regional-Program-Office/Global-Procurement-Officer--Asia_JR2771"
            ),
            "apply_url": (
                "https://path.wd1.myworkdayjobs.com/en-US/External/job/"
                "Vietnam-Hanoi-Regional-Program-Office/Global-Procurement-Officer--Asia_JR2771"
            ),
            "raw_snippet": (
                "Global Procurement Officer, Asia Vietnam, Hanoi Regional Program Office"
            ),
        },
    },
    # --- SP4: the five generic ATS readers, captured for the first time ---
    "new_incentives": {
        # breezy.py. No bug: title, location, department and detail_url all
        # matched the captured JSON exactly.
        "count": 5,
        "first_job": {
            "source_name": "new_incentives",
            "title": "Field Officers (entry-level) - All Locations",
            "location": "NorthWest and NorthEast, NG",
            "department": "",
            "listing_url": "https://new-incentives.breezy.hr",
            "detail_url": (
                "https://new-incentives.breezy.hr/p/"
                "d0d0e6ea8f4f-field-officers-entry-level-all-locations"
            ),
            "apply_url": (
                "https://new-incentives.breezy.hr/p/"
                "d0d0e6ea8f4f-field-officers-entry-level-all-locations"
            ),
            "raw_snippet": (
                "Field Officers (entry-level) - All Locations NorthWest and NorthEast, NG"
            ),
        },
    },
    "wave": {
        # lever.py. No bug: `categories.department` ("Customer Experience")
        # is preferred over `categories.team` ("Wave Advisors") on this row,
        # as the extractor's `department or team` fallback intends.
        "count": 8,
        "first_job": {
            "source_name": "wave",
            "title": "Accounting Associate",
            "location": "Toronto, Ontario",
            "department": "Customer Experience",
            "listing_url": "https://www.waveapps.com/about-us/culture",
            "detail_url": "https://jobs.lever.co/waveapps/69616656-9b45-45e9-8170-9d4915a3fde0",
            "apply_url": (
                "https://jobs.lever.co/waveapps/69616656-9b45-45e9-8170-9d4915a3fde0/apply"
            ),
            "raw_snippet": "Accounting Associate Customer Experience Toronto, Ontario",
        },
    },
    "outdooractive": {
        # personio.py. Two bugs found by capturing this one, both fixed in
        # SP4, neither in the reader's field mapping:
        #  - capture_fixtures.py ran every non-JSON capture through an HTML
        #    parser (BeautifulSoup + lxml), which rewrites `<![CDATA[` as an
        #    HTML comment and closes tags it does not recognise. Run over
        #    real XML that corrupted the feed enough that ET.fromstring could
        #    not read it — the first capture parsed to 0 jobs, all 22 silently
        #    dropped. `_guess_extension` now recognises XML by its declaration
        #    and skips sanitisation for it, as it already did for JSON.
        #  - personio.py caught that same ET.ParseError and returned [], an
        #    empty list indistinguishable from "no vacancies" — the exact
        #    failure CLAUDE.md's priority 2 rules out. It now raises.
        # Against the raw (unsanitised) feed fetched directly, all 22 rows
        # matched this golden's field mapping exactly.
        "count": 22,
        "first_job": {
            "source_name": "outdooractive",
            "title": "Android Entwickler (w/m/d)",
            "location": "Immenstadt (Deutschland)",
            "department": "Development",
            "listing_url": "https://outdooractive.jobs.personio.de/?language=en",
            "detail_url": "https://outdooractive.jobs.personio.de/job/2040503?language=en",
            "apply_url": "https://outdooractive.jobs.personio.de/job/2040503?language=en",
            "raw_snippet": "Android Entwickler (w/m/d) Development Immenstadt (Deutschland)",
        },
    },
    "oecd": {
        # smartrecruiters.py. No bug: `totalFound` (14) matched `len(content)`
        # exactly, so the pagination guard never has to fire on this board.
        # `relativeUri` is null on every posting here, so every detail_url
        # comes from the `org_slug`/`job_id` fallback rather than the
        # relative-path branch — both are exercised elsewhere in the suite.
        "count": 14,
        "first_job": {
            "source_name": "oecd",
            "title": "Nurse – Temporary position",
            "location": "Paris, fr",
            "department": "Corporate Functions",
            "listing_url": "https://careers.smartrecruiters.com/OECD/oecd---en",
            "detail_url": "https://jobs.smartrecruiters.com/OECD/744000150054199",
            "apply_url": "https://jobs.smartrecruiters.com/OECD/744000150054199",
            "raw_snippet": "Nurse – Temporary position Corporate Functions Paris, fr",
        },
    },
    "wwf_us": {
        # smartrecruiters.py's third source. One page: `totalFound` (39) matched
        # the 39 postings read. Country is SmartRecruiters' lower-case ISO code,
        # as for OECD, and `relativeUri` is absent here too. The first posting
        # is flagged `remote` by the platform, so "Remote" ends its snippet.
        "count": 39,
        "first_job": {
            "source_name": "wwf_us",
            "title": "Cybersecurity Specialist - R4645",
            "location": "Quito, ec",
            "department": "Information Technology",
            "listing_url": "https://careers.smartrecruiters.com/WorldWildlifeFundInc1/wwfus",
            "detail_url": "https://jobs.smartrecruiters.com/WorldWildlifeFundInc1/744000153753919",
            "apply_url": "https://jobs.smartrecruiters.com/WorldWildlifeFundInc1/744000153753919",
            "raw_snippet": (
                "Cybersecurity Specialist - R4645 Information Technology Quito, ec Remote"
            ),
        },
    },
    "ramboll": {
        # smartrecruiters.py's fourth source, and the largest walk: 1,019
        # postings over 11 pages, all saved (a one-page replay would fake the
        # end of the board, WP11). `totalFound` agrees with the rows read; the
        # careers page's own "1,006" was out of date. 696 postings are flagged
        # hybrid and 10 remote, which land in the snippet.
        "count": 1019,
        "first_job": {
            "source_name": "ramboll",
            "title": "Lead Architect - Data Center",
            "location": "Mumbai, in",
            "department": "",
            "listing_url": "https://careers.smartrecruiters.com/Ramboll3",
            "detail_url": "https://jobs.smartrecruiters.com/Ramboll3/744000154005189",
            "apply_url": "https://jobs.smartrecruiters.com/Ramboll3/744000154005189",
            "raw_snippet": "Lead Architect - Data Center Mumbai, in Hybrid",
        },
    },
    "deloitte_nordic": {
        # smartrecruiters.py over two pages (136 postings at 100 a page), so the
        # whole walk is saved: deloitte_nordic.json + .p1.json. A one-page replay
        # would fake the end of the board (docs/DECISIONS.md, WP11). No posting
        # carries a department. The platform marks 33 of the 136 hybrid and 3
        # remote, and the reader carries both into the snippet.
        "count": 136,
        "first_job": {
            "source_name": "deloitte_nordic",
            "title": "Build your career in Transfer Pricing - Aarhus",
            "location": "Aarhus, dk",
            "department": "",
            "listing_url": "https://careers.smartrecruiters.com/DeloitteNordic",
            "detail_url": "https://jobs.smartrecruiters.com/DeloitteNordic/744000153731959",
            "apply_url": "https://jobs.smartrecruiters.com/DeloitteNordic/744000153731959",
            "raw_snippet": "Build your career in Transfer Pricing - Aarhus Aarhus, dk",
        },
    },
    "nutrition_international": {
        # workable.py, moved onto the fetcher's post_json in this same
        # package ahead of its first capture. No bug in the field mapping.
        "count": 10,
        "first_job": {
            "source_name": "nutrition_international",
            "title": "Program Assistant",
            "location": "Nairobi, Kenya",
            "department": "Programs",
            "listing_url": "https://apply.workable.com/nutritionintl/",
            "detail_url": "https://apply.workable.com/nutritionintl/j/A3711AA7FC/",
            "apply_url": "https://apply.workable.com/nutritionintl/j/A3711AA7FC/",
            "raw_snippet": "Program Assistant Programs Nairobi, Kenya",
        },
    },
    "simprints": {
        # workable.py's second source. No bug: this row has no city, and the
        # extractor's `", ".join(x for x in [city, country] if x)` correctly
        # drops the empty part rather than leaving a stray leading comma.
        # SP4f: `workplace` joins the snippet ("Remote"). Every location this
        # posting lists is marked hidden, so the field keeps Workable's single
        # `location`, as before.
        "count": 9,
        "first_job": {
            "source_name": "simprints",
            "title": "Director of Strategic Partnerships",
            "location": "Ghana",
            "department": "Partnerships",
            "listing_url": "https://apply.workable.com/simprints/",
            "detail_url": "https://apply.workable.com/simprints/j/3CAA06941B/",
            "apply_url": "https://apply.workable.com/simprints/j/3CAA06941B/",
            "raw_snippet": "Director of Strategic Partnerships Partnerships Ghana Remote",
        },
    },
    # --- SP6 (2026-10-08): the single-source readers ---
    "asana": {
        # asana.py. No bug: all 100 cards read title, location and detail_url
        # as the page shows them. The rendered page lagged the Greenhouse board
        # it is built from by about a day (one closed posting still listed,
        # four new ones missing; the board said 103), which is the site's, not
        # the reader's. The team heading above each group is not read, so
        # department is empty on every row. Every row carries its description,
        # read from the Greenhouse JSON the page embeds (SP6 follow-up).
        "count": 100,
        "first_job": {
            "source_name": "asana",
            "title": "Administrative Business Partner",
            "location": "Vancouver, BC",
            "department": "",
            "listing_url": "https://asana.com/jobs/all",
            "detail_url": "https://asana.com/jobs/apply/8165477",
            "apply_url": "https://asana.com/jobs/apply/8165477",
            "raw_snippet": "Administrative Business Partner Vancouver, BC",
        },
        "description": (7553, "The Administrative Business Partner (ABP) provides strategic"),
    },
    "coefficient_giving": {
        # ashby.py since SP6, reading the board's posting API: the WordPress
        # page's own reader is gone, and its roles were always links to this
        # board. The first capture through it (2026-10-08) holds one posting,
        # the standing "Expression of Interest" form, which the owner rejects
        # once in review rather than the reader learning to skip it. Its
        # secondaryLocations join the field as segments (SP4f).
        "count": 1,
        "first_job": {
            "source_name": "coefficient_giving",
            "title": "Expression of Interest",
            "location": "Remote - Global | San Francisco | Remote - USA",
            "department": "Future Openings",
            "listing_url": "https://jobs.ashbyhq.com/coefficientgiving",
            "detail_url": (
                "https://jobs.ashbyhq.com/coefficientgiving/2cc48fa8-97aa-47d8-b367-bf3b66cdba3f"
            ),
            "apply_url": (
                "https://jobs.ashbyhq.com/coefficientgiving/2cc48fa8-97aa-47d8-b367-bf3b66cdba3f"
            ),
            "raw_snippet": (
                "Expression of Interest Future Openings Remote - Global | San Francisco | "
                "Remote - USA"
            ),
        },
        "description": (1304, "Open Philanthropy is now Coefficient Giving."),
    },
    "mammut": {
        # mammut.py. No bug: all 17 rows match the page. The location is the
        # third labelled span (the second is the employment type). One title
        # names Hamburg while the posting's own location says Zweibrücken; that
        # is the site's data, and the field is kept as given.
        "count": 17,
        "first_job": {
            "source_name": "mammut",
            "title": "Intern Corporate Strategy (all, 80-100%), 6-12 months",
            "location": "Seon",
            "department": "",
            "listing_url": "https://recruiting.mammut.com/Jobs/All",
            "detail_url": "https://recruiting.mammut.com/Vacancies/1450/Description/2",
            "apply_url": "https://recruiting.mammut.com/Vacancies/1450/Description/2",
            "raw_snippet": "Intern Corporate Strategy (all, 80-100%), 6-12 months Seon",
        },
    },
    "norrsken": {
        # norrsken.py. No bug: the one card matches the page, and the Teamtailor
        # career site behind the widget listed the same single posting.
        "count": 1,
        "first_job": {
            "source_name": "norrsken",
            "title": "Membership Growth Associate",
            "location": "Barcelona, Spain",
            "department": "Norrsken House Barcelona",
            "listing_url": "https://www.norrsken.org/work-at-norrsken",
            "detail_url": (
                "https://careers.norrskenfoundation.org/jobs/8505941-membership-growth-associate"
            ),
            "apply_url": (
                "https://careers.norrskenfoundation.org/jobs/8505941-membership-growth-associate"
            ),
            "raw_snippet": "Membership Growth Associate Norrsken House Barcelona Barcelona, Spain",
        },
    },
    "oatly": {
        # teamtailor.py since SP6. Oatly's own reader returned an empty
        # location and department on all 15 rows: it looked for a `mt-1`
        # metadata block the page now calls `mt-4`, and its positional split
        # would have read "Onsite" as the location had it found one. The
        # generic reader reads every card. Its detail_url has no /en-GB/; the
        # store adds it, and the key is the posting id either way.
        "count": 15,
        "first_job": {
            "source_name": "oatly",
            "title": "Stage Chef de secteur Proximité Paris janvier-juin 2027",
            "location": "Paris",
            "department": "Sales & Commercial",
            "listing_url": "https://careers.oatly.com/en-GB/jobs",
            "detail_url": (
                "https://careers.oatly.com/jobs/8489573-stage-chef-de-secteur-proximite-paris-"
                "janvier-juin-2027"
            ),
            "apply_url": (
                "https://careers.oatly.com/jobs/8489573-stage-chef-de-secteur-proximite-paris-"
                "janvier-juin-2027"
            ),
            "raw_snippet": (
                "Stage Chef de secteur Proximité Paris janvier-juin 2027 Sales & Commercial Paris"
            ),
        },
    },
    "sida": {
        # sida.py. The reader wrote "Stockholm, Sweden" on every row; the
        # page labels each posting's place, and six of nine said Sundbyberg.
        # It now reads the "Plats:" label and checks its count against the
        # page's stated total (9).
        "count": 9,
        "first_job": {
            "source_name": "sida",
            "title": "Säkerhetsspecialist, person- och resesäkerhet",
            "location": "Sundbyberg",
            "department": "",
            "listing_url": "https://www.sida.se/jobba-med-bistand/jobba-pa-sida/lediga-tjanster/",
            "detail_url": (
                "https://www.sida.se/jobba-med-bistand/jobba-pa-sida/lediga-tjanster/"
                "5688-sakerhetsspecialist-person-och-resesakerhet"
            ),
            "apply_url": (
                "https://www.sida.se/jobba-med-bistand/jobba-pa-sida/lediga-tjanster/"
                "5688-sakerhetsspecialist-person-och-resesakerhet"
            ),
            "raw_snippet": "Säkerhetsspecialist, person- och resesäkerhet Sundbyberg",
        },
    },
    "wwf_sweden": {
        # teamtailor.py, image-grid cards (SP8). The title is a shortened
        # <span> with the whole title in its `title` attribute, and the
        # metadata <div> is its sibling: the reader used to take the wrapper
        # as the metadata and return every title as its own location. The page
        # states "7 jobs" and shows no pager or "show more" control.
        "count": 7,
        "first_job": {
            "source_name": "wwf_sweden",
            "title": (
                "Intern with the WWF Baltic Sea programme: project management and communication"
            ),
            "location": "Stockholm",
            "department": "Internship",
            "listing_url": "https://jobb.wwf.se/en-GB/jobs",
            "detail_url": (
                "https://jobb.wwf.se/en-GB/jobs/8395577-intern-with-the-wwf-baltic-sea-"
                "programme-project-management-and-communication"
            ),
            "apply_url": (
                "https://jobb.wwf.se/en-GB/jobs/8395577-intern-with-the-wwf-baltic-sea-"
                "programme-project-management-and-communication"
            ),
            "raw_snippet": (
                "Intern with the WWF Baltic Sea programme: project management and "
                "communication Internship Stockholm Hybrid"
            ),
        },
    },
}


def test_every_fixture_has_a_golden() -> None:
    """A newly captured fixture must not slip in unpinned."""
    assert set(_GOLDEN) == set(FIXTURE_CASES)


def test_only_a_declared_empty_fixture_is_pinned_at_zero() -> None:
    """A zero golden is a stated empty board, never a selector pinned as broken."""
    assert {name for name, golden in _GOLDEN.items() if not golden["count"]} == EMPTY_FIXTURES


@pytest.mark.parametrize("name", sorted(_GOLDEN))
def test_extractor_output_matches_golden(name: str) -> None:
    filename = FIXTURE_CASES[name][0]
    if not (FIXTURES_DIR / filename).exists():
        pytest.fail(f"{filename} not captured yet — re-run scripts/capture_fixtures.py {name}")

    jobs = parse_fixture(name)
    expected = _GOLDEN[name]

    assert len(jobs) == expected["count"], (
        f"{name}: expected {expected['count']} jobs, got {len(jobs)}. "
        "Either a selector drifted or the fixture was refreshed."
    )
    if not expected["count"]:
        return
    first = dict(jobs[0])
    if "description" in expected:
        chars, opening = expected["description"]
        text = first.pop("description_text")
        assert (len(text), text[: len(opening)]) == (chars, opening)
    assert first == expected["first_job"]


def test_coloplast_keeps_sub_brand_postings() -> None:
    """Pin the data loss found when this source was first captured.

    Coloplast hosts Kerecis and Atos vacancies, whose links carry a brand
    segment (/Kerecis/job/…). The extractor matched only hrefs starting "/job/",
    so those rows vanished — no error, no empty field, just six fewer jobs than
    the page had. Silent loss is the failure this project ranks worst, and it
    survived precisely because the source had no fixture.

    Checks the brand-prefixed rows are present and fully parsed, so a future
    narrowing of the href match fails here rather than in a quietly shorter run.
    """
    filename = FIXTURE_CASES["coloplast"][0]
    if not (FIXTURES_DIR / filename).exists():
        pytest.fail(f"{filename} not captured yet — re-run scripts/capture_fixtures.py coloplast")

    jobs = parse_fixture("coloplast")
    branded = [
        job
        for job in jobs
        if urlparse(job["detail_url"]).path.startswith("/")
        and not urlparse(job["detail_url"]).path.startswith("/job/")
    ]
    assert branded, (
        "no sub-brand postings parsed; the href match has narrowed back to "
        '"starts with /job/" and Kerecis/Atos rows are being dropped'
    )
    for job in branded:
        assert job["title"], f"sub-brand posting parsed without a title: {job['detail_url']}"
        assert job["location"], f"sub-brand posting parsed without a location: {job['title']!r}"


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
        pytest.fail(f"{filename} not captured yet — re-run scripts/capture_fixtures.py iss")

    jobs = parse_fixture("iss")
    assert jobs, "iss fixture parsed to zero jobs"

    offenders = [j for j in jobs if j["location"].strip().casefold() == "title"]
    assert not offenders, (
        f"{len(offenders)} of {len(jobs)} ISS rows have the column label 'Title' "
        "as their location; the sr-only guard in successfactors_html has regressed"
    )


def test_gfi_europe_matches_absolute_hrefs_and_skips_near_misses() -> None:
    """Pin CU2's bug: postings linked absolutely must still be found.

    The extractor tested `href.startswith("/careers/")` on the raw attribute.
    When the page began emitting `https://gfieurope.org/careers/[slug]` instead
    of the root-relative form, every posting stopped matching and the source
    returned zero rows for nineteen consecutive runs.

    The golden above pins the count. This checks the boundary the fix has to
    hold on both sides: the postings are matched however the href is written,
    and the four near misses on the same page — the listing itself, the German
    listing, the FAQ page whose path merely shares the prefix, and GFI's global
    board on another host — are still not mistaken for vacancies.
    """
    jobs = parse_fixture("gfi_europe")
    assert jobs, "gfi_europe fixture parsed to zero jobs"

    for job in jobs:
        parts = urlparse(job["detail_url"])
        assert parts.netloc == "gfieurope.org", (
            f"off-site link parsed as a posting: {parts.geturl()}"
        )
        assert parts.path.startswith("/careers/"), f"non-posting path parsed: {parts.path}"
        assert parts.path.rstrip("/") != "/careers", "the listing page parsed as a posting"
        assert not parts.path.startswith("/de/"), "the German listing parsed as a posting"


def test_personio_fails_loudly_on_a_malformed_feed() -> None:
    """Pin the bug found capturing outdooractive (SP4): a broken feed must not
    read as "no vacancies".

    personio.py used to catch `ET.ParseError` and return `[]`, which is exactly
    the failure CLAUDE.md's priority 2 rules out — indistinguishable from a
    board that genuinely has nothing open. It now raises. This does not need a
    captured fixture: any XML a real feed could never serve says the same
    thing, and the point is the reader's own behaviour, not this board's data.
    """
    with pytest.raises(ValueError, match="could not parse the XML feed"):
        personio.extract(
            "https://outdooractive.jobs.personio.de/?language=en",
            lambda url, *a, **k: "<workzag-jobs><position>",
            "outdooractive",
        )


def test_workable_refuses_a_fetcher_that_cannot_post() -> None:
    """Refused, not bypassed (SP4, matching workday.py's rule in test_pagination.py):
    falling back to http.post_json would reach the network from a test, or from a
    capture that would then record nothing — which is exactly what happened before
    this reader was moved onto the fetcher's post_json.
    """
    with pytest.raises(TypeError, match="no post_json"):
        workable.extract(
            "https://apply.workable.com/simprints/", lambda url, *a, **k: "", "simprints"
        )


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("<html><body>Something went wrong</body></html>", id="not-json"),
        pytest.param('{"apiVersion": "1"}', id="no-jobs-list"),
        pytest.param("[]", id="not-an-object"),
    ],
)
def test_ashby_fails_loudly_on_a_body_it_cannot_read(body: str) -> None:
    """A body without a `jobs` list is a broken read, not an empty board (SP4d,
    found in SP4b). ashby.py returned `[]` when the board page had no readable
    `window.__appData`, the silent "no vacancies" personio.py was cured of in
    SP4. It now reads the posting API, and keeps the rule.
    """
    with pytest.raises(ValueError, match="Ashby posting API"):
        ashby.extract("https://jobs.ashbyhq.com/kognity", lambda url, *a, **k: body, "kognity")


def test_ashby_reads_an_empty_board_as_empty() -> None:
    """The other half: a board with nothing open says so, and that is not an error."""
    assert (
        ashby.extract(
            "https://jobs.ashbyhq.com/kognity", lambda url, *a, **k: '{"jobs": []}', "kognity"
        )
        == []
    )


def test_ashby_detail_urls_are_the_stored_keys() -> None:
    """The dedupe-key rule: built from the board and id, and equal to the API's
    own jobUrl on every captured posting, so no stored row looks new.
    """
    raw = json.loads((FIXTURES_DIR / "kognity.json").read_text(encoding="utf-8"))
    built = [j["detail_url"] for j in parse_fixture("kognity")]
    assert built == [j["jobUrl"] for j in raw["jobs"]]


# --- the platform's own workplace fields (SP4f) -------------------------------
#
# Invented postings. The readers carry the platform's statement of how and where
# a job is worked into the fields Layer 0 already reads: the workplace into
# `raw_snippet` (the remote keywords and the hybrid gate), every shown location
# into `location`. Run 34 rejected an Ashby posting marked Hybrid as non-hybrid,
# because its prose never said the word.


def _ashby(*postings: dict[str, Any]) -> list[dict[str, Any]]:
    body = json.dumps({"jobs": [{"isListed": True, **p} for p in postings]})
    return ashby.extract("https://jobs.ashbyhq.com/contoso", lambda url, *a, **k: body, "contoso")


def test_ashby_carries_the_workplace_type_into_the_snippet() -> None:
    jobs = _ashby(
        {"id": "1", "title": "Analyst", "location": "Fabrikam City", "workplaceType": "Hybrid"},
        {"id": "2", "title": "Analyst", "location": "Fabrikam City", "workplaceType": "Remote"},
        {"id": "3", "title": "Analyst", "location": "Fabrikam City", "workplaceType": "OnSite"},
    )
    assert [j["raw_snippet"] for j in jobs] == [
        "Analyst Fabrikam City Hybrid",
        "Analyst Fabrikam City Remote",
        "Analyst Fabrikam City",
    ]


def test_ashby_does_not_read_is_remote() -> None:
    # True on every Hybrid posting captured: it says remote is allowed, not that
    # the job is remote, so reading it would admit hybrid office jobs as remote.
    [job] = _ashby(
        {"id": "1", "title": "Analyst", "location": "Fabrikam City", "isRemote": True},
    )
    assert job["raw_snippet"] == "Analyst Fabrikam City"


def test_ashby_lists_secondary_locations_as_segments() -> None:
    [job] = _ashby(
        {
            "id": "1",
            "title": "Analyst",
            "location": "Fabrikam City",
            "secondaryLocations": [
                {"location": "Northwind"},
                {"location": "Fabrikam City"},
                {"location": ""},
            ],
        }
    )
    assert job["location"] == "Fabrikam City | Northwind"


def test_an_ashby_hybrid_posting_is_confirmed_at_layer_0() -> None:
    rules = {
        "locations": ["Northwind"],
        "conditional_locations": ["Fabrikam City"],
        "conditional_location_keywords": ["hybrid"],
    }
    [job] = _ashby(
        {"id": "1", "title": "Analyst", "location": "Fabrikam City", "workplaceType": "Hybrid"}
    )
    ok, reasons = matches_rules(job, rules, build_hybrid_pattern(rules))
    assert ok
    assert reasons == [_HYBRID_CONFIRMED_REASON]


def _smartrecruiters(*locations: dict[str, Any]) -> list[dict[str, Any]]:
    postings = [
        {
            "id": str(i),
            "name": "Analyst",
            "location": {"city": "Fabrikam City", "country": "cn", **loc},
        }
        for i, loc in enumerate(locations)
    ]
    body = json.dumps({"totalFound": len(postings), "content": postings})
    return smartrecruiters.extract(
        "https://careers.smartrecruiters.com/Contoso", lambda url: body, "contoso", "Contoso"
    )


def test_smartrecruiters_carries_the_workplace_flags_into_the_snippet() -> None:
    jobs = _smartrecruiters(
        {"remote": True, "hybrid": False},
        {"remote": False, "hybrid": True},
        {"remote": False, "hybrid": False},
        {"remote": True, "hybrid": True},
        {},
    )
    assert [j["raw_snippet"] for j in jobs] == [
        "Analyst Fabrikam City, cn Remote",
        "Analyst Fabrikam City, cn Hybrid",
        "Analyst Fabrikam City, cn",
        "Analyst Fabrikam City, cn Hybrid",
        "Analyst Fabrikam City, cn",
    ]


def test_smartrecruiters_leaves_the_location_field_alone() -> None:
    (job,) = _smartrecruiters({"hybrid": True})
    assert job["location"] == "Fabrikam City, cn"


def _workable(*postings: dict[str, Any]) -> list[dict[str, Any]]:
    def fetch(url: str, *a: Any, **k: Any) -> str:
        return ""

    fetch.post_json = lambda url, body, headers=None: {
        "results": [{"title": "Analyst", "shortcode": str(i), **p} for i, p in enumerate(postings)]
    }
    return workable.extract("https://apply.workable.com/contoso/", fetch, "contoso")


def test_workable_carries_the_workplace_into_the_snippet() -> None:
    place = {"location": {"city": "Fabrikam City", "country": "Contoso"}}
    jobs = _workable(
        {**place, "workplace": "remote", "remote": True},
        {**place, "workplace": "hybrid", "remote": False},
        {**place, "workplace": "on_site", "remote": False},
    )
    assert [j["raw_snippet"] for j in jobs] == [
        "Analyst Fabrikam City, Contoso Remote",
        "Analyst Fabrikam City, Contoso Hybrid",
        "Analyst Fabrikam City, Contoso",
    ]


def test_workable_lists_every_shown_location_and_none_that_are_hidden() -> None:
    [job] = _workable(
        {
            "location": {"city": "Fabrikam City", "country": "Contoso"},
            "locations": [
                {"city": "Fabrikam City", "country": "Contoso", "hidden": False},
                {"city": "", "country": "Litware", "hidden": True},
                {"city": "Northwind", "country": "Contoso", "hidden": False},
            ],
        }
    )
    assert job["location"] == "Fabrikam City, Contoso | Northwind, Contoso"


def test_workable_keeps_its_single_location_when_every_location_is_hidden() -> None:
    [job] = _workable(
        {
            "location": {"city": "", "country": "Litware"},
            "locations": [{"city": "", "country": "Litware", "hidden": True}],
        }
    )
    assert job["location"] == "Litware"


# --- SP6 -------------------------------------------------------------------


def _sida_page() -> str:
    return (FIXTURES_DIR / FIXTURE_CASES["sida"][0]).read_text(encoding="utf-8")


def _sida(page: str) -> list[dict[str, Any]]:
    return sida.extract(FIXTURE_CASES["sida"][1], lambda url: page, "sida")


def test_sida_reads_each_posting_s_own_place() -> None:
    """Not the head office for every row, which is what the reader used to write."""
    places = sorted(job["location"] for job in parse_fixture("sida"))
    assert places == ["Stockholm"] * 3 + ["Sundbyberg"] * 6


def test_sida_fails_loudly_when_the_count_disagrees_with_the_total() -> None:
    page = _sida_page().replace(">9<", ">10<")
    with pytest.raises(ValueError, match="states 10 vacancies but 9 were read"):
        _sida(page)


def test_sida_fails_loudly_without_a_stated_total() -> None:
    page = _sida_page().replace("lediga tjänster", "")
    with pytest.raises(ValueError, match="no stated total"):
        _sida(page)


def test_oatly_reads_each_card_shape_the_old_reader_could_not() -> None:
    """A workplace chip goes to the snippet, and a card with one segment is a place."""
    jobs = {job["title"]: job for job in parse_fixture("oatly")}
    onsite = jobs["Maintenance Engineer"]
    assert (onsite["location"], onsite["department"]) == ("Vlissingen", "Site Manufacturing")
    assert onsite["raw_snippet"].endswith("Onsite")
    no_department = jobs["Logistics Project Manager & Analytics"]
    assert (no_department["location"], no_department["department"]) == (
        "United States - Remote",
        "",
    )
    assert all(job["location"] for job in jobs.values())


def test_asana_supplies_every_description_as_plain_text() -> None:
    """Greenhouse escapes its markup; what reaches Layer 5 is the text, not tags."""
    jobs = parse_fixture("asana")
    texts = [job["description_text"] for job in jobs]
    assert all(texts)
    assert not any("<p" in text or "&lt;" in text for text in texts)
    assert all("hybrid" in text.lower() for text in texts)


def test_asana_without_embedded_postings_still_lists_its_cards() -> None:
    """The cards are the list; a missing description only means Layer 5 fetches."""
    page = (
        '<html><body><a href="/jobs/apply/1"><p>Analyst</p><p>Fabrikam City</p></a></body></html>'
    )
    [job] = asana.extract("https://asana.com/jobs/all", lambda url: page, "asana")
    assert (job["title"], job["location"], job["description_text"]) == (
        "Analyst",
        "Fabrikam City",
        "",
    )


def test_sida_reads_a_stated_zero_as_an_empty_board() -> None:
    """Handwritten: no zero-vacancy page has been captured yet."""
    page = (
        '<html><body><div class="job-listing__pagination-div"><p>Totalt '
        '<span class="semi-bold">0</span> lediga tjänster</p></div></body></html>'
    )
    assert _sida(page) == []


def test_sida_says_an_unread_empty_page_may_be_a_day_without_vacancies() -> None:
    with pytest.raises(ValueError, match="may be a day with no vacancies"):
        _sida("<html><body><p>Inga lediga jobb just nu.</p></body></html>")


def test_sida_fails_loudly_when_no_posting_carries_the_place_label() -> None:
    page = _sida_page().replace("Plats:", "Ort:")
    with pytest.raises(ValueError, match="may have been renamed"):
        _sida(page)


def test_sida_warns_about_one_posting_without_the_place_label(
    caplog: pytest.LogCaptureFixture,
) -> None:
    page = _sida_page().replace("Plats:", "Ort:", 1)
    jobs = _sida(page)
    assert [job["location"] for job in jobs].count("") == 1
    assert "1 posting(s) with no 'Plats:' label" in caplog.text


def test_asana_warns_when_some_postings_have_no_embedded_description(
    caplog: pytest.LogCaptureFixture,
) -> None:
    page = (FIXTURES_DIR / FIXTURE_CASES["asana"][0]).read_text(encoding="utf-8")
    page = page.replace('"id":8165477', '"id":1', 1)
    jobs = asana.extract(FIXTURE_CASES["asana"][1], lambda url: page, "asana")
    assert sum(not job["description_text"] for job in jobs) == 1
    assert "1 of 100 postings" in caplog.text
