"""Characterisation tests from the SP4b filter-ladder audit (2026-10-01).

Every string here is invented. None is taken from the store, the HTTP cache or
the labelled set, which are private (docs/SOURCES-PLAN.md, SP4b's prompt); each
one reproduces the *shape* of a phrasing the audit found, not its words.

Two kinds of test, kept apart on purpose:

- `xfail(strict=True)`: a decision the audit found wrong. Each fails today and
  is the red-to-green target its follow-up package inherits. `strict` makes a
  fix that lands without removing the marker fail the suite, so the marker
  cannot outlive the bug.
- Plain tests: behaviour that is the owner's policy, not a correctness bug.
  The owner answered SP4b's questions on 2026-10-01 (docs/DECISIONS.md). One
  of these pins an answer that matches today. The other pins today's
  behaviour that SP4f is to change, because it needs a config key that does
  not exist yet.

The audit's vocabulary (docs/DECISIONS.md, SP4b): a filter is STARVED when it
is handed input that cannot support a decision, and WRONG when the input is
fine and the rule mis-decides. The first section is starved, the rest wrong.
"""

from __future__ import annotations

import pytest

from job_scraper.experience_filter import (
    EXPERIENCE_UNREADABLE,
    UNVERIFIED_KEY,
    _extract_min_years,
    _has_phd_required,
    apply_detail_filter,
)
from job_scraper.filtering import (
    _HYBRID_PENDING_REASON,
    _UNRESOLVED_PENDING_REASON,
    build_hybrid_pattern,
    build_location_pattern,
    build_non_place_pattern,
    matches_rules,
)

# A client-rendered detail page as a static fetch sees it: a title, a
# noscript notice and an empty mount point. The posting itself arrives later
# by JavaScript, so nothing below the title is ever text.
_JS_SHELL = (
    "<html><head><title>Data Analyst @ Contoso</title></head>"
    "<body><noscript>You need to enable JavaScript to run this app.</noscript>"
    '<div id="root"></div><script src="/app.js"></script></body></html>'
)

_RULES = {
    "locations": ["Northwind"],
    "conditional_locations": ["Fabrikam City"],
    "conditional_location_keywords": ["hybrid"],
    "non_place_locations": ["EMEA", "Worldwide"],
    "remote_keywords": ["remote", "anywhere"],
    "match_in": "title_and_description",
}


def _job(**extra: object) -> dict[str, object]:
    return {
        "source_name": "contoso",
        "title": "Data Analyst",
        "detail_url": "https://jobs.example.test/contoso/1",
        **extra,
    }


def _detail(job: dict[str, object], page: str) -> list[dict[str, object]]:
    kept, excluded = apply_detail_filter(
        [job],
        lambda _url: page,
        hybrid_pattern=build_hybrid_pattern(_RULES),
        location_pattern=build_location_pattern(_RULES),
    )
    return [*kept, *excluded]


# ---------------------------------------------------------------------------
# Layer 5, starved: a page that renders nothing is read as a page that says
# nothing. Found on jobsinlund, undp, kognity, monday_com and simprints.
# ---------------------------------------------------------------------------


def test_a_js_shell_is_not_reported_as_no_requirement() -> None:
    [job] = _detail(_job(), _JS_SHELL)
    assert job["experience_level"] == EXPERIENCE_UNREADABLE


@pytest.mark.parametrize(
    ("location", "pending"),
    [
        ("2 Locations", _UNRESOLVED_PENDING_REASON),
        ("Fabrikam City", _HYBRID_PENDING_REASON),
    ],
)
def test_a_js_shell_cannot_settle_a_deferred_location(location: str, pending: str) -> None:
    [job] = _detail(_job(location=location, matched_reasons=[pending]), _JS_SHELL)
    # Unverified is the state that keeps a fail-closed drop out of the store
    # as 'rejected'. Reading a shell is no more a verification than a timeout.
    assert job.get(UNVERIFIED_KEY) is True


# ---------------------------------------------------------------------------
# Layer 5, wrong: phrasings the years patterns miss
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="SP4b: a phrasing the years patterns do not read")
@pytest.mark.parametrize(
    ("text", "years"),
    [
        # A word between the number and "experience".
        ("You bring 3 years of relevant experience in data analysis.", 3),
        # The possessive form, with no "of".
        ("You bring 5 years' experience in finance.", 5),
        # A number word with a word between it and "experience".
        ("You have five years of professional experience.", 5),
        # Swedish.
        ("Du har minst 3 års erfarenhet av ekonomiarbete.", 3),
    ],
)
def test_missed_phrasing(text: str, years: int) -> None:
    assert _extract_min_years(text) == years


# ---------------------------------------------------------------------------
# Layer 5, wrong: numbers that are not a requirement, read as one
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="SP4b: a number that is not an experience requirement decides the verdict; "
    "12 stored rows were rejected this way",
)
@pytest.mark.parametrize(
    "text",
    [
        # A minimum age, on an internship.
        "Applicants must be at least 18 years old.",
        # The employer's own history, phrased as experience.
        "With more than 40 years of experience in public health, our organisation "
        "works in many countries.",
    ],
)
def test_not_a_requirement(text: str) -> None:
    assert _extract_min_years(text) is None


@pytest.mark.xfail(
    strict=True,
    reason="SP4b: an 'additional N years' equivalence clause is read as the whole "
    "requirement, and the lowest number wins",
)
def test_an_additional_years_clause_is_not_the_requirement() -> None:
    text = (
        "Minimum 5 years of experience in programme management. A first-level "
        "degree with an additional 2 years of experience may be accepted in lieu "
        "of the advanced degree."
    )
    assert _extract_min_years(text) == 5


@pytest.mark.xfail(
    strict=True,
    reason="SP4b: lowest-number-wins lets a narrow secondary skill override the "
    "role's own requirement",
)
def test_a_narrow_skill_does_not_override_the_requirement() -> None:
    text = (
        "Minimum 5 years of experience in programme management. "
        "At least 2 years of experience with survey software."
    )
    assert _extract_min_years(text) == 5


def test_tiered_routes_take_the_lowest_today() -> None:
    """Policy, decided: where a posting offers alternative routes, the lowest decides.

    The owner's answer to SP4b Q1 (2026-10-01): they qualify through that
    route, so it is the honest reading. SP4e redesigns the years reading and
    must keep this green. Lowest-wins is right across routes, and still wrong
    across unrelated figures (the xfails above).
    """
    text = (
        "An advanced degree and 2 years of experience, or a first-level degree "
        "and 4 years of experience."
    )
    assert _extract_min_years(text) == 2


# ---------------------------------------------------------------------------
# Layer 5, wrong: the PhD rule. It has never fired on a stored description, so
# these are the shapes the regexes get wrong rather than observed losses.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True, reason="SP4b: a PhD offered as one of two degrees is read as required"
)
def test_a_phd_as_an_alternative_is_not_required() -> None:
    assert _has_phd_required("The role requires a Master's degree or PhD in economics.") is False


@pytest.mark.xfail(strict=True, reason="SP4b: 'doctoral degree' is not recognised")
def test_a_required_doctoral_degree_is_required() -> None:
    assert _has_phd_required("A doctoral degree in statistics is required.") is True


# ---------------------------------------------------------------------------
# Layer 0: home-based and worldwide fields. Policy, decided 2026-10-01 (SP4b Q3).
# ---------------------------------------------------------------------------


def _layer0(location: str) -> tuple[bool, list[str]]:
    return matches_rules(
        _job(location=location),
        _RULES,
        build_hybrid_pattern(_RULES),
        non_place_pattern=build_non_place_pattern(_RULES),
    )


@pytest.mark.xfail(
    strict=True,
    reason="SP4b Q3a: a home-based or worldwide role is remote, but is deferred to "
    "Layer 5 and fails closed there",
)
@pytest.mark.parametrize("location", ["Home based - Worldwide", "Home Based"])
def test_a_home_based_or_worldwide_field_is_admitted_as_remote(location: str) -> None:
    """The owner decided (2026-10-01) that these count as remote.

    Today they are deferred to Layer 5, which needs the description to name a
    listed place. A role that is not in any place rarely does: 83 stored rows
    of this shape were rejected that way, 16 of them against a JS shell.
    SP4f turns this green.
    """
    ok, reasons = _layer0(location)
    assert ok is True
    assert _UNRESOLVED_PENDING_REASON not in reasons


def test_a_home_based_region_beside_an_office_city_is_dropped() -> None:
    """Today one named city decides a field that also offers home-based work.

    `;` is not a segment separator, but splitting on it would not change this:
    the office segment names a place, so the field is a city not on the list.
    The owner decided (SP4b Q3b and Q3c, 2026-10-01) that a home-based option
    across a region they live in should admit the job. Nothing tells the
    filter which regions those are yet. That is a private config key SP4f
    adds, and this test then flips to expect an admission.
    """
    ok, _ = _layer0("Home based - EMEA; Office Based - Fabrikam Harbour")
    assert ok is False
