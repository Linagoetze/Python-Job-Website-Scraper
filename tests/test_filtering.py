"""Tests for job_scraper.filtering."""

from job_scraper.filtering import (
    _HYBRID_CONFIRMED_REASON,
    _HYBRID_PENDING_REASON,
    _LOCATION_EMPTY_ADMITTED_REASON,
    _UNRESOLVED_PENDING_REASON,
    apply_title_keyword_filter,
    build_hybrid_pattern,
    build_location_pattern,
    build_non_place_pattern,
    matches_rules,
)


def _job(title="Analyst", location="Berlin", source_name="test", **kw):
    return {
        "source_name": source_name,
        "title": title,
        "location": location,
        "department": kw.get("department", ""),
        "listing_url": "",
        "detail_url": "",
        "apply_url": "",
        "raw_snippet": f"{title} {location}",
        **kw,
    }


# Rules with hybrid-gated conditional locations, mirroring config/rules.json.
_COND = {
    "locations": ["Malmö", "Lund"],
    "conditional_locations": ["Karlskrona", "Gothenburg", "Göteborg", "Goteborg", "Stockholm"],
    "conditional_location_keywords": ["hybrid"],
    "remote_keywords": [],
}
# Built once, mirroring how the real pipeline compiles it once per run and
# passes it down rather than rebuilding it inside matches_rules.
_COND_HYBRID_PATTERN = build_hybrid_pattern(_COND)

# Rules for WP8d's third location state. The non-place list is deliberately
# short: the shapes recognised in code ("2 Locations", "Home based") must work
# without it, and the list only adds the regions and countries no code list
# could guess.
_UNRESOLVABLE = {
    "locations": ["Malmö", "Lund", "Copenhagen"],
    "conditional_locations": [],
    "remote_keywords": ["remote", "anywhere"],
    "non_place_locations": ["EMEA", "Worldwide", "home base", "Sweden", "United Kingdom"],
}
_NON_PLACE_PATTERN = build_non_place_pattern(_UNRESOLVABLE)


def _unresolvable(job):
    return matches_rules(job, _UNRESOLVABLE, None, non_place_pattern=_NON_PLACE_PATTERN)


# ---------------------------------------------------------------------------
# matches_rules
# ---------------------------------------------------------------------------

class TestMatchesRules:
    def test_empty_rules_passes_everything(self):
        rules = {
            "include_keywords": [],
            "exclude_keywords": [],
            "locations": [],
            "remote_keywords": [],
        }
        ok, _ = matches_rules(_job(), rules, None)
        assert ok

    def test_location_match(self):
        rules = {"locations": ["Berlin", "London"], "remote_keywords": []}
        ok, reasons = matches_rules(_job(location="Berlin, Germany"), rules, None)
        assert ok
        assert any("locations" in r for r in reasons)

    def test_location_no_match(self):
        rules = {"locations": ["London"], "remote_keywords": []}
        ok, _ = matches_rules(_job(location="Berlin"), rules, None)
        assert not ok

    def test_remote_keyword_matches(self):
        rules = {"locations": ["London"], "remote_keywords": ["remote"]}
        ok, reasons = matches_rules(_job(location="", raw_snippet="Analyst remote"), rules, None)
        assert ok
        assert any("remote" in r for r in reasons)

    def test_remote_keyword_pure_remote_location_matches(self):
        rules = {"locations": ["Malmö"], "remote_keywords": ["remote", "anywhere"]}
        ok, reasons = matches_rules(_job(location="Remote"), rules, None)
        assert ok
        assert any("remote" in r for r in reasons)

    def test_remote_keyword_home_based_matches(self):
        rules = {"locations": ["Malmö"], "remote_keywords": ["remote"]}
        ok, _ = matches_rules(
            _job(location="Remote | Home Based - May require travel"), rules, None
        )
        assert ok

    def test_remote_keyword_does_not_bypass_specific_city(self):
        # "Remote | Nairobi" is a city-specific role; the leading "Remote" tag
        # must not admit it when Nairobi isn't a listed location.
        rules = {"locations": ["Malmö"], "remote_keywords": ["remote"]}
        ok, _ = matches_rules(_job(location="Remote | Nairobi"), rules, None)
        assert not ok

    def test_remote_tagged_listed_city_still_matches(self):
        rules = {"locations": ["Copenhagen"], "remote_keywords": ["remote"]}
        ok, reasons = matches_rules(_job(location="Remote | Copenhagen"), rules, None)
        assert ok
        assert any("locations: matched" == r for r in reasons)

    def test_conditional_location_hybrid_in_title_confirmed(self):
        ok, reasons = matches_rules(
            _job(title="Analyst (Hybrid)", location="Stockholm"), _COND, _COND_HYBRID_PATTERN
        )
        assert ok
        assert _HYBRID_CONFIRMED_REASON in reasons

    def test_conditional_location_without_hybrid_is_pending(self):
        # Not rejected — Layer 2 still has to look at the description.
        ok, reasons = matches_rules(_job(location="Stockholm"), _COND, _COND_HYBRID_PATTERN)
        assert ok
        assert _HYBRID_PENDING_REASON in reasons

    def test_conditional_location_confirmed_marker_short_circuits(self):
        job = _job(location="Stockholm", matched_reasons=[_HYBRID_CONFIRMED_REASON])
        ok, reasons = matches_rules(job, _COND, _COND_HYBRID_PATTERN)
        assert ok
        assert _HYBRID_CONFIRMED_REASON in reasons

    def test_conditional_location_all_gothenburg_spellings(self):
        for spelling in ("Gothenburg", "Göteborg", "Goteborg"):
            ok, reasons = matches_rules(_job(location=spelling), _COND, _COND_HYBRID_PATTERN)
            assert ok, spelling
            assert _HYBRID_PENDING_REASON in reasons, spelling

    def test_conditional_location_swedish_compound_confirms(self):
        job = _job(location="Karlskrona", raw_snippet="Analyst hybridarbete två dagar")
        ok, reasons = matches_rules(job, _COND, _COND_HYBRID_PATTERN)
        assert ok
        assert _HYBRID_CONFIRMED_REASON in reasons

    def test_hybrid_does_not_admit_unlisted_city(self):
        ok, _ = matches_rules(
            _job(title="Analyst (Hybrid)", location="Uppsala"), _COND, _COND_HYBRID_PATTERN
        )
        assert not ok

    def test_unconditional_location_unaffected_by_hybrid_gate(self):
        ok, reasons = matches_rules(_job(location="Malmö"), _COND, _COND_HYBRID_PATTERN)
        assert ok
        assert reasons == ["locations: matched"]

    def test_conditional_locations_inert_without_keywords(self):
        rules = {"locations": ["Malmö"], "conditional_locations": ["Stockholm"],
                 "conditional_location_keywords": [], "remote_keywords": []}
        ok, _ = matches_rules(_job(title="Analyst (Hybrid)", location="Stockholm"), rules, None)
        assert not ok

    # -----------------------------------------------------------------
    # WP8d — the third location state: present, but naming no place
    # -----------------------------------------------------------------

    def test_placeholder_location_count_is_pending_not_dropped(self):
        # "2 Locations" is a listing page refusing to name its duty stations.
        # Judging it against the city list is judging a placeholder.
        for placeholder in ("2 Locations", "21 Locations", "Multiple locations"):
            ok, reasons = _unresolvable(_job(location=placeholder))
            assert ok, placeholder
            assert _UNRESOLVED_PENDING_REASON in reasons, placeholder

    def test_region_only_location_is_pending(self):
        for region in ("Home base - EMEA", "Home based - Worldwide", "Sweden"):
            ok, reasons = _unresolvable(_job(location=region))
            assert ok, region
            assert _UNRESOLVED_PENDING_REASON in reasons, region

    def test_home_base_singular_is_recognised(self):
        # The bug this package was written for: _GENERIC_LOCATION_TOKENS had
        # "home based" but not "home base", so this read as a city.
        ok, reasons = _unresolvable(_job(location="Home base - EMEA"))
        assert ok
        assert _UNRESOLVED_PENDING_REASON in reasons

    def test_both_home_base_spellings_are_recognised_in_code(self):
        # The wording is English, not a place list, so it must not depend on
        # rules.json. (The region *after* it still does — that is what
        # non_place_locations is for, and "Home base - EMEA" needs both halves.)
        rules = {"locations": ["Malmö"], "remote_keywords": []}
        for spelling in ("Home base", "Home based", "home-based", "Homebased"):
            ok, reasons = matches_rules(_job(location=spelling), rules, None)
            assert ok, spelling
            assert _UNRESOLVED_PENDING_REASON in reasons, spelling

    def test_a_field_of_only_remote_keywords_is_remote_not_unresolvable(self):
        # Under title_only the location field is outside the haystack, so
        # remote_ok cannot fire. Deferring "Remote" to Layer 2 would buy a
        # detail fetch for a shape the location rules already have an answer
        # for — and on an aggregator that tags every posting "Remote", a great
        # many of them.
        rules = {"locations": ["Malmö"], "remote_keywords": ["remote", "anywhere"],
                 "match_in": "title_only"}
        ok, reasons = matches_rules(_job(location="Remote"), rules, None)
        assert not ok
        assert reasons == ["locations: city not on the list"]

    def test_nothing_is_deferred_when_there_are_no_locations_to_defer_to(self):
        # Layer 2 settles a deferred job by searching the description for a
        # listed location. With none configured it can never settle one, so the
        # job would be dropped unverifiable, never stored, and re-fetched on
        # every subsequent run.
        rules = {
            "locations": [],
            "conditional_locations": ["Stockholm"],
            "conditional_location_keywords": ["hybrid"],
            "remote_keywords": ["remote"],
        }
        ok, reasons = matches_rules(
            _job(location="2 Locations"), rules, build_hybrid_pattern(rules)
        )
        assert not ok
        assert _UNRESOLVED_PENDING_REASON not in reasons

    def test_a_named_city_beside_a_region_still_names_a_place(self):
        # The reason the classifier strikes terms out and looks at what is left
        # rather than splitting on dashes: real city names contain them, and a
        # country suffix does not turn a city into a placeholder.
        for named in ("Barcelona, Sweden", "Sweden - Uppsala", "Aix-en-Provence"):
            ok, _ = _unresolvable(_job(location=named))
            assert not ok, named

    def test_empty_location_is_admitted_not_deferred(self):
        # An extractor gap (WP8e) must not be laundered into "unresolvable"
        # (WP8d) — there is nothing on the page to resolve it against, so
        # WP8f settles it here, permanently, rather than deferring to Layer 2.
        ok, reasons = matches_rules(
            _job(location="", raw_snippet="Analyst"),
            _UNRESOLVABLE,
            None,
            non_place_pattern=_NON_PLACE_PATTERN,
        )
        assert ok
        assert reasons == [_LOCATION_EMPTY_ADMITTED_REASON]
        assert _UNRESOLVED_PENDING_REASON not in reasons

    def test_listed_city_never_becomes_pending(self):
        ok, reasons = _unresolvable(_job(location="Malmö, Sweden"))
        assert ok
        assert reasons == ["locations: matched"]

    def test_remote_role_is_still_admitted_outright(self):
        # Cheaper than deferring: a genuine anywhere role needs no detail page.
        ok, reasons = _unresolvable(_job(location="Remote", raw_snippet="Analyst remote"))
        assert ok
        assert reasons == ["locations: matched via remote_keywords"]

    def test_the_code_shapes_work_without_any_configured_terms(self):
        rules = {"locations": ["Malmö"], "remote_keywords": []}
        for placeholder in ("3 Locations", "Home based"):
            ok, reasons = matches_rules(_job(location=placeholder), rules, None)
            assert ok, placeholder
            assert _UNRESOLVED_PENDING_REASON in reasons, placeholder

    def test_conditional_city_keeps_its_own_pending_state(self):
        # A hybrid-gated city is resolvable — it names a place — so it must not
        # be swallowed by the new state.
        rules = dict(_COND, non_place_locations=["Sweden"])
        ok, reasons = matches_rules(
            _job(location="Stockholm, Sweden"),
            rules,
            _COND_HYBRID_PATTERN,
            non_place_pattern=build_non_place_pattern(rules),
        )
        assert ok
        assert _HYBRID_PENDING_REASON in reasons


class TestMatchesRulesKeywords:
    def test_exclude_keyword_rejects(self):
        rules = {"exclude_keywords": ["intern"], "locations": []}
        ok, _ = matches_rules(_job(title="Marketing Intern"), rules, None)
        assert not ok

    def test_include_keyword_required(self):
        rules = {"include_keywords": ["data"], "locations": []}
        ok, _ = matches_rules(_job(title="Marketing Analyst"), rules, None)
        assert not ok

    def test_include_keyword_matches(self):
        rules = {"include_keywords": ["analyst"], "locations": []}
        ok, _ = matches_rules(_job(title="Marketing Analyst"), rules, None)
        assert ok


# ---------------------------------------------------------------------------
# apply_title_keyword_filter
# ---------------------------------------------------------------------------

class TestBuildLocationPattern:
    """Layer 2's copy of `locations`, for searching a description."""

    def test_matches_a_listed_city_whole_word(self):
        pattern = build_location_pattern(_UNRESOLVABLE)
        assert pattern is not None
        assert pattern.search("The team sits in Lund, two days a week.")

    def test_does_not_match_a_city_name_inside_a_longer_word(self):
        # Substring matching is fine against a short location field and wrong
        # against a page of prose: "Lund" is inside plenty of Swedish surnames.
        pattern = build_location_pattern(_UNRESOLVABLE)
        assert pattern is not None
        assert not pattern.search("Report to Anna Lundberg, Head of Delivery.")

    def test_returns_none_when_locations_are_unconfigured(self):
        assert build_location_pattern({"locations": []}) is None


class TestTitleKeywordFilter:
    def test_word_match_excludes(self):
        entries = [("sales", "word")]
        kept, excluded = apply_title_keyword_filter([_job(title="Sales Manager")], entries)
        assert len(excluded) == 1
        assert len(kept) == 0

    def test_word_match_no_partial(self):
        entries = [("sales", "word")]
        kept, excluded = apply_title_keyword_filter([_job(title="Salesforce Admin")], entries)
        assert len(kept) == 1
        assert len(excluded) == 0

    def test_prefix_match_excludes(self):
        entries = [("design", "prefix")]
        kept, excluded = apply_title_keyword_filter([_job(title="Graphic Designer")], entries)
        assert len(excluded) == 1

    def test_prefix_does_not_match_mid_word(self):
        entries = [("design", "prefix")]
        kept, excluded = apply_title_keyword_filter([_job(title="Redesign Lead")], entries)
        # "design" as prefix should match at a word boundary — "Redesign" starts
        # with "Re", not "design"
        assert len(kept) == 1

    def test_empty_entries_keeps_all(self):
        kept, excluded = apply_title_keyword_filter([_job(), _job()], [])
        assert len(kept) == 2
        assert len(excluded) == 0
