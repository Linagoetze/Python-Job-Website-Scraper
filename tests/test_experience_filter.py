"""Tests for job_scraper.experience_filter."""


from job_scraper.experience_filter import (
    RULE_LOCATION_NOT_LISTED,
    RULE_LOCATION_UNVERIFIED,
    UNVERIFIED_KEY,
    _extract_min_years,
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

# ---------------------------------------------------------------------------
# _extract_min_years
# ---------------------------------------------------------------------------

class TestExtractMinYears:
    def test_years_of_experience(self):
        assert _extract_min_years("3+ years of experience required") == 3

    def test_minimum_years(self):
        assert _extract_min_years("minimum of 5 years in the field") == 5

    def test_minimum_without_of(self):
        assert _extract_min_years("minimum 2 years") == 2

    def test_at_least(self):
        assert _extract_min_years("at least 4 years of relevant work") == 4

    def test_range_extracts_lower_bound(self):
        assert _extract_min_years("1-3 years of experience") == 1

    def test_plus_years(self):
        assert _extract_min_years("5+ years in marketing") == 5

    def test_no_match(self):
        assert _extract_min_years("We are looking for a motivated individual") is None

    def test_multiple_takes_min(self):
        text = "at least 2 years of experience, ideally 5+ years"
        assert _extract_min_years(text) == 2

    def test_does_not_match_incidental_years(self):
        # "founded 15 years ago" should NOT be treated as a 15-year requirement
        assert _extract_min_years("Our company was founded 15 years ago") is None

    def test_zero_years(self):
        assert _extract_min_years("0-2 years of experience") == 0

    def test_one_year_of_experience(self):
        assert _extract_min_years("1 year of experience preferred") == 1


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
        return apply_detail_filter([job], lambda _url: html, hybrid_pattern=self._PATTERN)

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
        kept, _ = self._run(
            self._job([_HYBRID_PENDING_REASON]), "<p>Vi erbjuder hybridarbete.</p>"
        )
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
        return apply_detail_filter([job], lambda _url: html, location_pattern=self._PATTERN)

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
        _, excluded = self._run(
            self._job([_UNRESOLVED_PENDING_REASON]), "<p>Based in Nairobi.</p>"
        )
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
            lambda _url: "<p>Based in Lund.</p>",
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
            lambda _url: "<p>A hybrid role based in Lund.</p>",
            hybrid_pattern=build_hybrid_pattern({"conditional_location_keywords": ["hybrid"]}),
            location_pattern=self._PATTERN,
        )
        assert len(kept) == 1
        assert kept[0]["matched_reasons"] == [
            _HYBRID_CONFIRMED_REASON,
            _UNRESOLVED_CONFIRMED_REASON,
        ]
