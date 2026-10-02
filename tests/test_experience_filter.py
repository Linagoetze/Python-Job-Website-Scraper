"""Tests for job_scraper.experience_filter."""

import pytest

from job_scraper.experience_filter import (
    RULE_LOCATION_NOT_LISTED,
    RULE_LOCATION_UNVERIFIED,
    UNVERIFIED_KEY,
    _has_phd_required,
    _read_years_requirement,
    _strip_html,
    apply_detail_filter,
    apply_title_filter,
)
from job_scraper.filtering import (
    _HYBRID_CONFIRMED_REASON,
    _HYBRID_PENDING_REASON,
    _UNRESOLVED_CONFIRMED_REASON,
    _UNRESOLVED_PENDING_REASON,
    build_hybrid_pattern,
    build_location_pattern,
)
from tests.pages import posting

# ---------------------------------------------------------------------------
# _read_years_requirement
# ---------------------------------------------------------------------------


class TestReadYearsRequirement:
    def test_years_of_experience(self):
        assert _read_years_requirement("3+ years of experience required") == 3

    def test_minimum_years(self):
        assert _read_years_requirement("minimum of 5 years in the field") == 5

    def test_minimum_without_of(self):
        assert _read_years_requirement("minimum 2 years") == 2

    def test_at_least(self):
        assert _read_years_requirement("at least 4 years of relevant work") == 4

    def test_range_extracts_lower_bound(self):
        assert _read_years_requirement("1-3 years of experience") == 1

    def test_plus_years(self):
        assert _read_years_requirement("5+ years in marketing") == 5

    def test_no_match(self):
        assert _read_years_requirement("We are looking for a motivated individual") is None

    def test_an_ideal_does_not_raise_the_requirement(self):
        text = "at least 2 years of experience, ideally 5+ years"
        assert _read_years_requirement(text) == 2

    def test_does_not_match_incidental_years(self):
        # "founded 15 years ago" should NOT be treated as a 15-year requirement
        assert _read_years_requirement("Our company was founded 15 years ago") is None

    def test_zero_years(self):
        assert _read_years_requirement("0-2 years of experience") == 0

    def test_one_year_of_experience(self):
        assert _read_years_requirement("1 year of experience is needed") == 1


class TestWhatIsARequirement:
    """Step 2 of the reading (SP4e): which figures are requirements at all.

    Every text is invented. Each has the *shape* of a phrasing found in the
    stored descriptions during SP4e's measurement, never its words: the store
    is private (SP4b).
    """

    @pytest.mark.parametrize(
        ("text", "years"),
        [
            ("A minimum of five (5) years of relevant experience.", 5),
            ("2 to 4 years of experience in logistics.", 2),
            ("You have 3 yrs of hands-on experience.", 3),
            ("Approximately 4 years' experience as an analyst.", 4),
            ("3–5 years selling medical devices to hospitals.", 3),
            ("Who we are looking for: 4–6 years’ experience in marketing.", 4),
            ("Requirements:\n - 4+ years of experience in operations.", 4),
            ("Du har 4 års erfarenhet av redovisning.", 4),
            ("Du har mindst 3 års erfaring med kvalitetssikring.", 3),
            ("Du hast mindestens 3 Jahre Erfahrung im Controlling.", 3),
            ("18 months of experience in a similar role.", 1),
            ("Seven years of relevant professional experience.", 7),
        ],
    )
    def test_a_requirement_is_read(self, text, years):
        assert _read_years_requirement(text) == years

    @pytest.mark.parametrize(
        "text",
        [
            # A cap, not a floor.
            "Suited to people early in their career, with up to 6 years of experience.",
            # A contract's length, as a header.
            "Duration: 2–3 years, subject to funding.",
            "Contract length: 1–2 years of service.",
            # A window and a time ago.
            "Only open to people who finished their studies within the past 1-2 years.",
            "Someone who left university three years ago counts three years.",
            # An age, after the figure and as a header before it.
            "Applicants must be 18 years or older.",
            "Age requirement: volunteers must be at least 21 years on the first day.",
            # The employer's history, in the third person, past any plausible
            # requirement.
            "The charity has spent nearly 60 years working with families.",
            # A roadmap, not a career.
            "You will own the 2–4 year product roadmap.",
            # Years of study.
            "Du har en examen motsvarande minst två års högskolestudier.",
            # A preference, marked as such for the whole item.
            "Minimum 5 years of experience in emergency response (desirable).",
            "Applicants with 4 years or more of relevant experience in cloud "
            "migration projects are preferred.",
        ],
    )
    def test_a_figure_that_is_not_a_requirement(self, text):
        assert _read_years_requirement(text) is None

    def test_a_preferred_figure_is_not_a_requirement(self):
        # Kept as "unspecified" rather than "junior": nothing here is required.
        assert _read_years_requirement("1 year of experience preferred") is None

    def test_a_preference_for_something_else_leaves_the_figure_alone(self):
        text = (
            "5+ years of account management experience, ideally in logistics, "
            "and agency experience preferred."
        )
        assert _read_years_requirement(text) == 5

    def test_a_named_preference_in_brackets_leaves_the_figure_alone(self):
        text = "6+ years of experience in payments operations (fintech preferred)."
        assert _read_years_requirement(text) == 6

    def test_a_preference_in_the_next_list_item_is_not_this_ones(self):
        # A stripped list shows its items only as a capital after a lower-case word.
        text = "Minimum 3 years of experience in payroll administration Fluent in Spanish Desirable"
        assert _read_years_requirement(text) == 3


class TestHowRequirementsCombine:
    """Step 3 of the reading (SP4e): the largest requirement, the lowest route."""

    def test_separate_requirements_take_the_largest(self):
        text = (
            "At least 5 years of experience in programme management. "
            "At least 2 years of experience in people management."
        )
        assert _read_years_requirement(text) == 5

    def test_a_subset_does_not_lower_the_whole(self):
        text = "At least 6 years of relevant experience, including at least 1 year leading a team."
        assert _read_years_requirement(text) == 6

    def test_routes_by_degree_take_the_lowest(self):
        text = (
            "A Master's degree with 2 years of relevant experience; or a Bachelor's "
            "degree with 4 years of relevant experience; or a secondary school "
            "diploma with 6 years of relevant experience."
        )
        assert _read_years_requirement(text) == 2

    def test_routes_tagged_after_the_figure(self):
        text = (
            "At least 6 years (with a secondary school diploma) or 3 years "
            "(with a bachelor's degree) of relevant experience."
        )
        assert _read_years_requirement(text) == 3

    def test_routes_by_level_take_the_lowest(self):
        text = "At least two years for level C, five years for level B and eight years for level A."
        assert _read_years_requirement(text) == 2

    def test_routes_joined_by_or(self):
        text = "At least 4 years of relevant experience or 8 years of military experience."
        assert _read_years_requirement(text) == 4

    def test_a_route_still_meets_every_other_requirement(self):
        text = (
            "A Master's degree with 3 years of experience, or a Bachelor's degree "
            "with 5 years of experience. At least 2 years in a supervisory role."
        )
        assert _read_years_requirement(text) == 3

    def test_a_route_in_lieu_of_the_main_requirement_can_only_lower_it(self):
        text = (
            "Advanced university degree in economics. Minimum 2 years of relevant "
            "experience. A bachelor's degree combined with a minimum of 4 years of "
            "relevant experience will be considered in lieu of the advanced degree."
        )
        assert _read_years_requirement(text) == 2

    def test_a_degree_in_lieu_of_years_takes_them_off(self):
        text = (
            "At least five years of experience are needed. A relevant degree can "
            "count in place of two years of experience."
        )
        assert _read_years_requirement(text) == 3


class TestDoctorate:
    """The PhD rule (SP4e): required in its own clause, and not offered beside another degree."""

    @pytest.mark.parametrize(
        "text",
        [
            "Qualifications required: a PhD in chemistry.",
            "Applicants should hold a PhD in geology.",
            "A doctorate is a prerequisite for this post.",
            "The post requires a PhD, or a foreign degree judged equivalent to a PhD.",
        ],
    )
    def test_required(self, text):
        assert _has_phd_required(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            # The job is the doctorate.
            "The programme has compulsory PhD courses in the first year.",
            "You must be used to supervising PhD-students.",
            # Offered beside another way in.
            "You hold a doctoral degree or have comparable research merits.",
            "A doctoral degree (or equivalent) in law is required.",
            # Preferred, not required.
            "A PhD is preferred but not required.",
        ],
    )
    def test_not_required(self, text):
        assert _has_phd_required(text) is False


# ---------------------------------------------------------------------------
# _strip_html
# ---------------------------------------------------------------------------


class TestStripHtml:
    def test_strips_tags(self):
        result = _strip_html("<p>Hello <b>world</b></p>")
        assert "Hello" in result
        assert "world" in result
        assert "<" not in result

    def test_plain_text_passthrough(self):
        result = _strip_html("no tags here")
        assert "no tags here" in result


# ---------------------------------------------------------------------------
# apply_title_filter
# ---------------------------------------------------------------------------


class TestApplyTitleFilter:
    def _job(self, title):
        return {"title": title, "source_name": "test", "location": ""}

    def test_excludes_senior(self):
        rules = {"seniority_filter_enabled": True, "seniority_exclude_titles": ["Senior", "Lead"]}
        kept, excluded = apply_title_filter([self._job("Senior Analyst")], rules)
        assert len(excluded) == 1

    def test_keeps_junior(self):
        rules = {"seniority_filter_enabled": True, "seniority_exclude_titles": ["Senior", "Lead"]}
        kept, excluded = apply_title_filter([self._job("Junior Analyst")], rules)
        assert len(kept) == 1

    def test_disabled_keeps_all(self):
        rules = {"seniority_filter_enabled": False, "seniority_exclude_titles": ["Senior"]}
        kept, excluded = apply_title_filter([self._job("Senior Analyst")], rules)
        assert len(kept) == 1
        assert len(excluded) == 0

    def test_empty_terms_keeps_all(self):
        rules = {"seniority_filter_enabled": True, "seniority_exclude_titles": []}
        kept, _ = apply_title_filter([self._job("Senior Analyst")], rules)
        assert len(kept) == 1

    def test_excludes_director(self):
        rules = {"seniority_filter_enabled": True, "seniority_exclude_titles": ["Director"]}
        _, excluded = apply_title_filter([self._job("Director of Marketing")], rules)
        assert len(excluded) == 1


# ---------------------------------------------------------------------------
# apply_detail_filter — hybrid resolution for conditional locations
# ---------------------------------------------------------------------------


class TestHybridResolution:
    _PATTERN = build_hybrid_pattern({"conditional_location_keywords": ["hybrid"]})

    @staticmethod
    def _job(reasons, url="https://example.com/job"):
        return {
            "source_name": "test",
            "title": "Analyst",
            "location": "Stockholm",
            "detail_url": url,
            "apply_url": "",
            "raw_snippet": "Analyst Stockholm",
            "matched_reasons": list(reasons),
        }

    def _run(self, job, html="<p>A great role.</p>"):
        return apply_detail_filter([job], lambda _url: posting(html), hybrid_pattern=self._PATTERN)

    def test_hybrid_in_description_confirms(self):
        kept, excluded = self._run(
            self._job([_HYBRID_PENDING_REASON]),
            "<p>This is a hybrid role, two days on site.</p>",
        )
        assert len(kept) == 1
        assert not excluded
        assert kept[0]["matched_reasons"] == [_HYBRID_CONFIRMED_REASON]

    def test_no_hybrid_in_description_excludes(self):
        kept, excluded = self._run(self._job([_HYBRID_PENDING_REASON]))
        assert not kept
        assert len(excluded) == 1

    def test_swedish_compound_in_description_confirms(self):
        kept, _ = self._run(self._job([_HYBRID_PENDING_REASON]), "<p>Vi erbjuder hybridarbete.</p>")
        assert len(kept) == 1

    def test_pending_job_fails_closed_on_fetch_error(self):
        def boom(_url):
            raise RuntimeError("network down")

        kept, excluded = apply_detail_filter(
            [self._job([_HYBRID_PENDING_REASON])], boom, hybrid_pattern=self._PATTERN
        )
        assert not kept
        assert len(excluded) == 1

    def test_pending_job_fails_closed_without_url(self):
        kept, excluded = self._run(self._job([_HYBRID_PENDING_REASON], url=""))
        assert not kept
        assert len(excluded) == 1

    def test_non_pending_job_still_fails_open_on_fetch_error(self):
        def boom(_url):
            raise RuntimeError("network down")

        kept, excluded = apply_detail_filter(
            [self._job(["locations: matched"])], boom, hybrid_pattern=self._PATTERN
        )
        assert len(kept) == 1
        assert not excluded

    def test_non_pending_job_untouched_by_hybrid_check(self):
        kept, _ = self._run(self._job(["locations: matched"]))
        assert len(kept) == 1
        assert kept[0]["matched_reasons"] == ["locations: matched"]

    def test_confirmed_hybrid_job_still_experience_filtered(self):
        kept, excluded = self._run(
            self._job([_HYBRID_PENDING_REASON]),
            "<p>Hybrid role. Requires 8 years of experience.</p>",
        )
        assert not kept
        assert excluded[0]["experience_level"] == "senior (8+yr)"


# ---------------------------------------------------------------------------
# apply_detail_filter — unresolvable locations (WP8d)
# ---------------------------------------------------------------------------


class TestUnresolvableLocationResolution:
    """The second deferred state, settled against the same fetched description.

    Mirrors TestHybridResolution deliberately: same two-stage contract, same
    fail-closed direction. What differs is the question — "does this page name
    a place I would actually commute to?" — and that these jobs are new load,
    since before WP8d they died at Layer 0 and never reached this layer.
    """

    _PATTERN = build_location_pattern({"locations": ["Malmö", "Lund", "Copenhagen"]})

    @staticmethod
    def _job(reasons, url="https://example.com/job"):
        return {
            "source_name": "test",
            "title": "Analyst",
            "location": "2 Locations",
            "detail_url": url,
            "apply_url": "",
            "raw_snippet": "Analyst",
            "matched_reasons": list(reasons),
        }

    def _run(self, job, html="<p>A great role.</p>"):
        return apply_detail_filter(
            [job], lambda _url: posting(html), location_pattern=self._PATTERN
        )

    def test_listed_place_in_description_confirms(self):
        kept, excluded = self._run(
            self._job([_UNRESOLVED_PENDING_REASON]),
            "<p>You will be based in our Lund office.</p>",
        )
        assert len(kept) == 1
        assert not excluded
        assert kept[0]["matched_reasons"] == [_UNRESOLVED_CONFIRMED_REASON]

    def test_no_listed_place_in_description_excludes(self):
        kept, excluded = self._run(
            self._job([_UNRESOLVED_PENDING_REASON]),
            "<p>The role is based in Nairobi.</p>",
        )
        assert not kept
        assert excluded[0]["experience_level"] == "unresolvable_location"
        assert excluded[0]["drop_rule"] == RULE_LOCATION_NOT_LISTED

    def test_a_read_rejection_keeps_its_description_so_it_is_not_refetched(self):
        _, excluded = self._run(self._job([_UNRESOLVED_PENDING_REASON]), "<p>Based in Nairobi.</p>")
        assert "Nairobi" in excluded[0]["description_text"]
        assert excluded[0]["description_fetched_at"]
        assert not excluded[0].get(UNVERIFIED_KEY)

    def test_fails_closed_on_fetch_error_but_marks_it_unverified(self):
        def boom(_url):
            raise RuntimeError("network down")

        kept, excluded = apply_detail_filter(
            [self._job([_UNRESOLVED_PENDING_REASON])], boom, location_pattern=self._PATTERN
        )
        assert not kept
        assert excluded[0][UNVERIFIED_KEY] is True
        assert excluded[0]["drop_rule"] == RULE_LOCATION_UNVERIFIED
        # Nothing durable may be written from a network hiccup.
        assert excluded[0]["description_text"] == ""

    def test_fails_closed_without_url(self):
        kept, excluded = self._run(self._job([_UNRESOLVED_PENDING_REASON], url=""))
        assert not kept
        assert excluded[0][UNVERIFIED_KEY] is True

    def test_fails_closed_when_no_locations_are_configured(self):
        kept, excluded = apply_detail_filter(
            [self._job([_UNRESOLVED_PENDING_REASON])],
            lambda _url: posting("Based in Lund."),
            location_pattern=None,
        )
        assert not kept
        assert excluded[0][UNVERIFIED_KEY] is True

    def test_non_pending_job_is_untouched(self):
        kept, _ = self._run(self._job(["locations: matched"]), "<p>Based in Nairobi.</p>")
        assert len(kept) == 1
        assert kept[0]["matched_reasons"] == ["locations: matched"]

    def test_confirmed_job_is_still_experience_filtered(self):
        kept, excluded = self._run(
            self._job([_UNRESOLVED_PENDING_REASON]),
            "<p>Based in Lund. Requires 8 years of experience.</p>",
        )
        assert not kept
        assert excluded[0]["experience_level"] == "senior (8+yr)"

    def test_both_deferred_states_resolve_from_one_fetch(self):
        # A job can carry both markers; one page answers both questions, so
        # neither state costs an HTTP request the other did not already make.
        job = self._job([_HYBRID_PENDING_REASON, _UNRESOLVED_PENDING_REASON])
        kept, _ = apply_detail_filter(
            [job],
            lambda _url: posting("A hybrid role based in Lund."),
            hybrid_pattern=build_hybrid_pattern({"conditional_location_keywords": ["hybrid"]}),
            location_pattern=self._PATTERN,
        )
        assert len(kept) == 1
        assert kept[0]["matched_reasons"] == [
            _HYBRID_CONFIRMED_REASON,
            _UNRESOLVED_CONFIRMED_REASON,
        ]
