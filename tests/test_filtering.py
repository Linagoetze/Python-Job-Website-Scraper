"""Tests for job_scraper.filtering."""

from job_scraper.filtering import (
    _EMPTY_PENDING_REASON,
    _HYBRID_CONFIRMED_REASON,
    _HYBRID_PENDING_REASON,
    _LOCATION_EMPTY_ADMITTED_REASON,
    _REMOTE_ANYWHERE_REASON,
    _REMOTE_REGION_REASON,
    _UNRESOLVED_PENDING_REASON,
    RULE_LOC_REMOTE_OVERRIDDEN,
    RULE_LOC_UNLISTED_CITY,
    apply_title_keyword_filter,
    build_hybrid_pattern,
    build_location_pattern,
    build_non_place_pattern,
    build_remote_region_pattern,
    build_title_keyword_matchers,
    load_title_exclude_keywords,
    matches_rules,
    title_keyword_rule,
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
        rules = {
            "locations": ["Malmö"],
            "conditional_locations": ["Stockholm"],
            "conditional_location_keywords": [],
            "remote_keywords": [],
        }
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
        # "Home based - Worldwide" left this list in SP4f: worldwide is remote
        # (the owner's Q3a). A region is deferred until rules.json says it
        # includes the owner (remote_regions, tested below).
        for region in ("Home base - EMEA", "Sweden"):
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
        # Since SP4f a bare home base is remote (Q3a) rather than deferred.
        rules = {"locations": ["Malmö"], "remote_keywords": []}
        for spelling in ("Home base", "Home based", "home-based", "Homebased"):
            ok, reasons = matches_rules(_job(location=spelling), rules, None)
            assert ok, spelling
            assert reasons == [_REMOTE_ANYWHERE_REASON], spelling

    def test_a_field_of_only_remote_keywords_is_remote_not_unresolvable(self):
        # Under title_only the location field is outside the haystack, so
        # remote_ok cannot fire. Deferring "Remote" to Layer 2 would buy a
        # detail fetch for a shape the location rules already have an answer
        # for — and on an aggregator that tags every posting "Remote", a great
        # many of them.
        rules = {
            "locations": ["Malmö"],
            "remote_keywords": ["remote", "anywhere"],
            "match_in": "title_only",
        }
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

    def test_empty_location_is_deferred_under_its_own_reason(self):
        # SP4f (the owner's Q4) revised WP8f: the detail page is read anyway,
        # and must name a listed place. Deferred like a placeholder, but not
        # laundered into "unresolvable" (WP8d), so the drop log keeps the two
        # causes apart.
        ok, reasons = matches_rules(
            _job(location="", raw_snippet="Analyst"),
            _UNRESOLVABLE,
            None,
            non_place_pattern=_NON_PLACE_PATTERN,
        )
        assert ok
        assert reasons == [_EMPTY_PENDING_REASON]
        assert _UNRESOLVED_PENDING_REASON not in reasons

    def test_empty_location_is_admitted_when_there_is_no_list_to_settle_it(self):
        # Layer 5 settles the deferral by finding a listed location in the
        # description. With `locations` empty there is none to find, and the job
        # could only ever come back unverified, so it is admitted as before.
        rules = {
            "locations": [],
            "conditional_locations": ["Stockholm"],
            "conditional_location_keywords": ["hybrid"],
        }
        ok, reasons = matches_rules(_job(location=""), rules, build_hybrid_pattern(rules))
        assert ok
        assert reasons == [_LOCATION_EMPTY_ADMITTED_REASON]

    def test_listed_city_never_becomes_pending(self):
        ok, reasons = _unresolvable(_job(location="Malmö, Sweden"))
        assert ok
        assert reasons == ["locations: matched"]

    def test_remote_role_is_still_admitted_outright(self):
        # Cheaper than deferring: a genuine anywhere role needs no detail page.
        ok, reasons = _unresolvable(_job(location="Remote", raw_snippet="Analyst remote"))
        assert ok
        assert reasons == [_REMOTE_ANYWHERE_REASON]

    def test_the_code_shapes_work_without_any_configured_terms(self):
        rules = {"locations": ["Malmö"], "remote_keywords": []}
        ok, reasons = matches_rules(_job(location="3 Locations"), rules, None)
        assert ok
        assert _UNRESOLVED_PENDING_REASON in reasons
        # A bare home base, deferred here until SP4f, is now remote (Q3a).
        ok, reasons = matches_rules(_job(location="Home based"), rules, None)
        assert ok
        assert reasons == [_REMOTE_ANYWHERE_REASON]

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


# ---------------------------------------------------------------------------
# SP4f — remote, home-based and worldwide fields (the owner's SP4b Q3)
# ---------------------------------------------------------------------------

# The regions are invented: which real ones include the owner is private, and
# lives only in rules.json (SP4f, as the chosen country did in SP3c).
_REGIONS = {
    "locations": ["Malmö", "Lund"],
    "conditional_locations": ["Stockholm"],
    "conditional_location_keywords": ["hybrid"],
    "remote_keywords": ["remote", "anywhere"],
    "non_place_locations": ["Wingtip Region", "Contoso Basin", "EMEA"],
    "remote_regions": ["Wingtip Region"],
}


def _regional(location, rules=_REGIONS, **kw):
    return matches_rules(
        _job(location=location, **kw),
        rules,
        build_hybrid_pattern(rules),
        non_place_pattern=build_non_place_pattern(rules),
        remote_region_pattern=build_remote_region_pattern(rules),
    )


class TestRemoteRegions:
    def test_home_based_across_a_region_that_includes_the_owner_is_admitted(self):
        ok, reasons = _regional("Home based - Wingtip Region")
        assert ok
        assert reasons == [_REMOTE_REGION_REASON]

    def test_the_option_admits_beside_an_office_city(self):
        # Q3c. The office is a conditional city with no hybrid in sight, which
        # alone would defer the job to Layer 5; the home-based option settles it.
        ok, reasons = _regional("Home based - Wingtip Region; Office Based - Stockholm")
        assert ok
        assert reasons == [_REMOTE_REGION_REASON]

    def test_remote_in_another_segment_counts_as_remote_wording(self):
        # Impactpool's "Remote | <where>" shape, with a region for the where.
        ok, reasons = _regional("Remote | Wingtip Region")
        assert ok
        assert reasons == [_REMOTE_REGION_REASON]

    def test_remote_in_the_title_counts_as_remote_wording(self):
        # As `remote_keywords` always have: anywhere in the haystack.
        ok, reasons = _regional("Wingtip Region", title="Analyst (Remote)")
        assert ok
        assert reasons == [_REMOTE_REGION_REASON]

    def test_a_bare_region_with_no_remote_wording_still_defers(self):
        # A region is not remote by itself: it may hold an office. The owner
        # kept bare regions and countries deferred (Q3).
        ok, reasons = _regional("Wingtip Region")
        assert ok
        assert reasons == [_UNRESOLVED_PENDING_REASON]

    def test_a_region_that_excludes_the_owner_still_defers(self):
        for field in ("Home based - Contoso Basin", "Remote | Contoso Basin"):
            ok, reasons = _regional(field)
            assert ok, field
            assert reasons == [_UNRESOLVED_PENDING_REASON], field

    def test_a_region_beside_one_that_excludes_the_owner_is_not_enough_in_one_option(self):
        # One option spanning both regions is not "across a region that includes
        # the owner"; split into two options, the owner's one admits.
        ok, reasons = _regional("Home based - Wingtip Region and Contoso Basin")
        assert reasons != [_REMOTE_REGION_REASON]
        ok, reasons = _regional("Home based - Contoso Basin; Home based - Wingtip Region")
        assert ok
        assert reasons == [_REMOTE_REGION_REASON]

    def test_without_the_key_a_regional_field_behaves_as_before(self):
        rules = {k: v for k, v in _REGIONS.items() if k != "remote_regions"}
        ok, reasons = _regional("Home based - Wingtip Region", rules)
        assert ok
        assert reasons == [_UNRESOLVED_PENDING_REASON]
        ok, _ = _regional("Home based - Wingtip Region; Office Based - Nairobi", rules)
        assert not ok

    def test_worldwide_admits_without_any_configuration(self):
        rules = {"locations": ["Malmö"], "remote_keywords": []}
        for field in ("Worldwide", "Home based - Worldwide", "Global", "Home Based - Global"):
            ok, reasons = matches_rules(_job(location=field), rules, None)
            assert ok, field
            assert reasons == [_REMOTE_ANYWHERE_REASON], field

    def test_worldwide_beside_an_office_city_admits(self):
        ok, reasons = _regional("Home based - Worldwide; Office Based - Nairobi")
        assert ok
        assert reasons == [_REMOTE_ANYWHERE_REASON]

    def test_a_bare_home_base_beside_a_city_is_the_city(self):
        # The Impactpool guard, extended to the home-base wording: a bare tag
        # beside a duty station says where the job is, not that it is anywhere.
        ok, reasons = _regional("Home Based | Nairobi")
        assert not ok
        assert reasons == [RULE_LOC_UNLISTED_CITY]

    def test_a_global_title_is_not_remote_wording(self):
        # "Global" in a location field means everywhere; in a title it is a
        # team's name and says nothing about where anyone works.
        ok, reasons = _regional("Wingtip Region", title="Global Health Analyst")
        assert ok
        assert reasons == [_UNRESOLVED_PENDING_REASON]

    def test_a_listed_city_still_wins_first(self):
        ok, reasons = _regional("Home based - Contoso Basin; Office Based - Lund")
        assert ok
        assert reasons == ["locations: matched"]

    def test_semicolon_separates_a_remote_tag_from_a_city(self):
        # Before SP4f "Remote; Nairobi" was one segment holding a remote
        # keyword, and so admitted as anywhere. It is two options now.
        ok, reasons = _regional("Remote; Nairobi")
        assert not ok
        assert reasons == [RULE_LOC_REMOTE_OVERRIDDEN]

    # --- tightened on 2026-10-06: remote counts only where it is open to the owner

    def test_remote_in_a_region_outside_the_list_is_not_admitted(self):
        # Until the tightening, any option holding a remote keyword passed,
        # wherever it was. A country or region outside the list now defers like
        # a bare one; a place is dropped.
        ok, reasons = _regional("Contoso Basin - Remote")
        assert ok
        assert reasons == [_UNRESOLVED_PENDING_REASON]
        ok, reasons = _regional("Fabrikam City - Remote")
        assert not ok
        assert reasons == [RULE_LOC_REMOTE_OVERRIDDEN]

    def test_remote_in_a_listed_region_is_admitted_whichever_way_round(self):
        for field in (
            "Wingtip Region - Remote",
            "Remote - Wingtip Region",
            "Wingtip Region (Remote)",
        ):
            ok, reasons = _regional(field)
            assert ok, field
            assert reasons == [_REMOTE_REGION_REASON], field

    def test_remote_somewhere_else_and_worldwide_is_admitted(self):
        # The owner's case: remote in one country, and remote worldwide too.
        for field in (
            "Remote - Contoso Basin; Remote - Worldwide",
            "Contoso Basin - Remote, Global - Remote",
            "Contoso Basin + International (Remote)",
        ):
            ok, reasons = _regional(field)
            assert ok, field
            assert reasons == [_REMOTE_ANYWHERE_REASON], field

    def test_international_without_remote_wording_is_not_worldwide(self):
        ok, reasons = _regional("Fabrikam City + International")
        assert not ok
        assert reasons == [RULE_LOC_UNLISTED_CITY]

    def test_a_remote_option_that_names_no_region_is_still_admitted(self):
        # The owner kept these (2026-10-06): no region is named, so none is ruled
        # out. "Fully" and Impactpool's "May require travel" are noise, not places.
        for field in ("Remote", "Fully Remote", "Remote | Home Based - May require travel"):
            ok, reasons = _regional(field)
            assert ok, field
            assert reasons == [_REMOTE_ANYWHERE_REASON], field

    def test_noise_words_do_not_make_a_place_vanish(self):
        # Struck only beside remote wording: without it, they are left in place.
        ok, _ = _regional("May require travel")
        assert not ok

    def test_an_empty_field_with_remote_in_the_title_is_remote(self):
        ok, reasons = _regional("", title="Analyst (Remote)")
        assert ok
        assert reasons == [_REMOTE_ANYWHERE_REASON]
        ok, reasons = _regional("")
        assert reasons == [_EMPTY_PENDING_REASON]


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


class TestContainsMatch:
    """`contains` is for family words German and Swedish put at a compound's end (SP4h)."""

    def test_matches_family_word_at_the_end_of_a_compound(self):
        entries = [("techniker", "contains")]
        for title in ("Prüftechniker (m/w/d)", "Instandhaltungstechniker:in", "Techniker"):
            _, excluded = apply_title_keyword_filter([_job(title=title)], entries)
            assert len(excluded) == 1, title

    def test_matches_an_inflected_ending_where_a_word_end_match_would_not(self):
        # The reason it is `contains` and not a word-end type.
        entries = [("ingenjör", "contains")]
        for title in ("Mjukvaruingenjörer", "Elektronikingenjör", "Ingenjör till Lund"):
            _, excluded = apply_title_keyword_filter([_job(title=title)], entries)
            assert len(excluded) == 1, title

    def test_prefix_still_misses_the_compound(self):
        _, excluded = apply_title_keyword_filter(
            [_job(title="Prüftechniker")], [("techniker", "prefix")]
        )
        assert excluded == []

    def test_unrelated_word_is_kept(self):
        kept, _ = apply_title_keyword_filter(
            [_job(title="Product Owner")], [("techniker", "contains")]
        )
        assert len(kept) == 1

    def test_loader_accepts_contains_and_falls_back_to_word_for_an_unknown_type(self, tmp_path):
        path = tmp_path / "kw.csv"
        path.write_text("keyword,match\nchaufför,contains\nfoo,suffix\n", encoding="utf-8")
        assert load_title_exclude_keywords(path) == [("chaufför", "contains"), ("foo", "word")]

    def test_an_all_caps_keyword_is_an_acronym_and_matches_case_sensitively(self):
        # "SEA" is search-engine advertising; the Baltic Sea is a place (SP8).
        matchers = build_title_keyword_matchers([("SEA", "word"), ("design", "prefix")])
        assert title_keyword_rule("SEA Manager", matchers) == "title_keyword: 'SEA' (word)"
        assert title_keyword_rule("Intern, WWF Baltic Sea programme", matchers) == (
            "title_keyword: unattributed"
        )
        # Other keywords stay case-insensitive.
        assert title_keyword_rule("DESIGNER", matchers) == "title_keyword: 'design' (prefix)"

    def test_an_acronym_keyword_still_excludes_in_the_combined_pattern(self):
        entries = [("SEA", "word"), ("design", "prefix")]
        kept, excluded = apply_title_keyword_filter([_job(title="Baltic Sea intern")], entries)
        assert len(kept) == 1 and excluded == []
        kept, excluded = apply_title_keyword_filter([_job(title="SEA Lead")], entries)
        assert kept == [] and len(excluded) == 1

    def test_attribution_names_the_match_type(self):
        matchers = build_title_keyword_matchers([("chaufför", "contains")])
        assert (
            title_keyword_rule("Fjärrchaufför till DSV", matchers)
            == "title_keyword: 'chaufför' (contains)"
        )

    def test_shipped_list_catches_the_compounds_it_was_changed_for(self):
        from job_scraper.config_loader import default_title_keywords_path

        entries = load_title_exclude_keywords(default_title_keywords_path())
        titles = [
            "Distributionschaufför till DSV Haulage AB, Stockholm",
            "Instandhaltungstechniker:in (f/m/d)",
            "Industriemechaniker:in (f/m/d)",
            "Frontendutvecklare",
            "Elektronikingenjör",
            "Physiotherapist",
            "Lasbil- og trailermekaniker søges til værksted i Horsens",
        ]
        kept, _ = apply_title_keyword_filter([_job(title=t) for t in titles], entries)
        assert kept == []

    def test_shipped_list_catches_swedish_tax_compounds_that_tax_misses(self):
        # `tax` is a whole-word English entry; Swedish writes the family word as the
        # start of a compound, and inflects it (SP10, measured against 12,305 titles).
        from job_scraper.config_loader import default_title_keywords_path

        entries = load_title_exclude_keywords(default_title_keywords_path())
        titles = [
            "Erfaren skatterådgivare",
            "Skattekonsult | Tull & internationell handel",
            "Juniora Skattejurister till Tax & Legal (Göteborg)",
            "Praktik på skatteavdelningen i Stockholm våren 2027",
        ]
        kept, _ = apply_title_keyword_filter([_job(title=t) for t in titles], entries)
        assert kept == []

    def test_donor_is_no_longer_a_keyword(self):
        from job_scraper.config_loader import default_title_keywords_path

        entries = load_title_exclude_keywords(default_title_keywords_path())
        assert "donor" not in {kw.lower() for kw, _ in entries}
