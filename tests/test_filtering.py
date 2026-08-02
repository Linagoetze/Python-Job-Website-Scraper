"""Tests for job_scraper.filtering."""

import sys
from unittest.mock import MagicMock, patch

from job_scraper.filtering import (
    apply_language_filter,
    apply_non_english_text_filter,
    apply_title_keyword_filter,
    load_title_exclude_keywords,
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


# ---------------------------------------------------------------------------
# matches_rules
# ---------------------------------------------------------------------------

class TestMatchesRules:
    def test_empty_rules_passes_everything(self):
        rules = {"include_keywords": [], "exclude_keywords": [], "locations": [], "remote_keywords": []}
        ok, _ = matches_rules(_job(), rules)
        assert ok

    def test_location_match(self):
        rules = {"locations": ["Berlin", "London"], "remote_keywords": []}
        ok, reasons = matches_rules(_job(location="Berlin, Germany"), rules)
        assert ok
        assert any("locations" in r for r in reasons)

    def test_location_no_match(self):
        rules = {"locations": ["London"], "remote_keywords": []}
        ok, _ = matches_rules(_job(location="Berlin"), rules)
        assert not ok

    def test_remote_keyword_matches(self):
        rules = {"locations": ["London"], "remote_keywords": ["remote"]}
        ok, reasons = matches_rules(_job(location="", raw_snippet="Analyst remote"), rules)
        assert ok
        assert any("remote" in r for r in reasons)

    def test_remote_keyword_pure_remote_location_matches(self):
        rules = {"locations": ["Malmö"], "remote_keywords": ["remote", "anywhere"]}
        ok, reasons = matches_rules(_job(location="Remote"), rules)
        assert ok
        assert any("remote" in r for r in reasons)

    def test_remote_keyword_home_based_matches(self):
        rules = {"locations": ["Malmö"], "remote_keywords": ["remote"]}
        ok, _ = matches_rules(
            _job(location="Remote | Home Based - May require travel"), rules
        )
        assert ok

    def test_remote_keyword_does_not_bypass_specific_city(self):
        # "Remote | Nairobi" is a city-specific role; the leading "Remote" tag
        # must not admit it when Nairobi isn't a listed location.
        rules = {"locations": ["Malmö"], "remote_keywords": ["remote"]}
        ok, _ = matches_rules(_job(location="Remote | Nairobi"), rules)
        assert not ok

    def test_remote_tagged_listed_city_still_matches(self):
        rules = {"locations": ["Copenhagen"], "remote_keywords": ["remote"]}
        ok, reasons = matches_rules(_job(location="Remote | Copenhagen"), rules)
        assert ok
        assert any("locations: matched" == r for r in reasons)

    def test_exclude_keyword_rejects(self):
        rules = {"exclude_keywords": ["intern"], "locations": []}
        ok, _ = matches_rules(_job(title="Marketing Intern"), rules)
        assert not ok

    def test_include_keyword_required(self):
        rules = {"include_keywords": ["data"], "locations": []}
        ok, _ = matches_rules(_job(title="Marketing Analyst"), rules)
        assert not ok

    def test_include_keyword_matches(self):
        rules = {"include_keywords": ["analyst"], "locations": []}
        ok, _ = matches_rules(_job(title="Marketing Analyst"), rules)
        assert ok


# ---------------------------------------------------------------------------
# apply_title_keyword_filter
# ---------------------------------------------------------------------------

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
        # "design" as prefix should match at word boundary — "Redesign" starts with "Re" not "design"
        assert len(kept) == 1

    def test_empty_entries_keeps_all(self):
        kept, excluded = apply_title_keyword_filter([_job(), _job()], [])
        assert len(kept) == 2
        assert len(excluded) == 0


# ---------------------------------------------------------------------------
# apply_language_filter
# ---------------------------------------------------------------------------

class TestLanguageFilter:
    # --- languages that must still be blocked ---

    def test_blocks_spanish_speaker(self):
        _, excluded = apply_language_filter([_job(title="Spanish Speaker needed")])
        assert len(excluded) == 1

    def test_blocks_french_speaking(self):
        _, excluded = apply_language_filter([_job(title="French speaking Account Manager")])
        assert len(excluded) == 1

    def test_blocks_dutch_hyphen_speaking(self):
        _, excluded = apply_language_filter([_job(title="Dutch-speaking Account Manager")])
        assert len(excluded) == 1

    def test_blocks_unlisted_language_speaking(self):
        # Languages not in the old explicit blocklist must also be blocked
        _, excluded = apply_language_filter([_job(title="Tagalog speaking Support Agent")])
        assert len(excluded) == 1

    def test_blocks_arbitrary_word_speaking(self):
        # Any made-up or rare language word should be caught
        _, excluded = apply_language_filter([_job(title="Klingon speaking Developer")])
        assert len(excluded) == 1

    # --- allowed languages ---

    def test_keeps_german_speaker(self):
        kept, excluded = apply_language_filter([_job(title="German Speaker")])
        assert len(kept) == 1
        assert len(excluded) == 0

    def test_keeps_german_speaking(self):
        kept, excluded = apply_language_filter([_job(title="German-speaking Account Manager")])
        assert len(kept) == 1
        assert len(excluded) == 0

    def test_keeps_english_speaking(self):
        kept, excluded = apply_language_filter([_job(title="English Speaking Developer")])
        assert len(kept) == 1
        assert len(excluded) == 0

    def test_keeps_english_speaker(self):
        kept, excluded = apply_language_filter([_job(title="English Speaker Required")])
        assert len(kept) == 1
        assert len(excluded) == 0

    def test_keeps_english_hyphen_speaking(self):
        kept, excluded = apply_language_filter([_job(title="English-speaking role in Berlin")])
        assert len(kept) == 1
        assert len(excluded) == 0

    def test_keeps_germ_speaking(self):
        # 'Germ' is an explicitly allowed abbreviation for German
        kept, excluded = apply_language_filter([_job(title="Germ speaking Team Lead")])
        assert len(kept) == 1
        assert len(excluded) == 0

    def test_keeps_english_title(self):
        kept, _ = apply_language_filter([_job(title="Marketing Analyst")])
        assert len(kept) == 1


# ---------------------------------------------------------------------------
# apply_non_english_text_filter
# ---------------------------------------------------------------------------

def _langdetect_mock(lang: str) -> MagicMock:
    """Return a sys.modules-injectable langdetect mock that returns *lang* from detect()."""
    FakeLangDetectException = type("LangDetectException", (Exception,), {})
    m = MagicMock()
    m.detect.return_value = lang
    m.LangDetectException = FakeLangDetectException
    return m


def _langdetect_mock_raising() -> MagicMock:
    """Return a langdetect mock whose detect() raises LangDetectException."""
    FakeLangDetectException = type("LangDetectException", (Exception,), {})
    m = MagicMock()
    m.detect.side_effect = FakeLangDetectException(0, "")
    m.LangDetectException = FakeLangDetectException
    return m


class TestNonEnglishTextFilter:
    def test_keeps_english_job(self):
        with patch.dict(sys.modules, {"langdetect": _langdetect_mock("en")}):
            kept, excluded = apply_non_english_text_filter([_job(title="Product Manager in Berlin")])
        assert len(kept) == 1
        assert len(excluded) == 0

    def test_excludes_swedish_job(self):
        with patch.dict(sys.modules, {"langdetect": _langdetect_mock("sv")}):
            kept, excluded = apply_non_english_text_filter([_job(title="Produktchef i Malmö", location="Malmö, Sweden")])
        assert len(excluded) == 1
        assert len(kept) == 0

    def test_excludes_german_text_job(self):
        with patch.dict(sys.modules, {"langdetect": _langdetect_mock("de")}):
            kept, excluded = apply_non_english_text_filter([_job(title="Projektmanager gesucht")])
        assert len(excluded) == 1
        assert len(kept) == 0

    def test_excludes_danish_job(self):
        with patch.dict(sys.modules, {"langdetect": _langdetect_mock("da")}):
            kept, excluded = apply_non_english_text_filter([_job(title="Projektleder søges", location="Frederiksberg, København")])
        assert len(excluded) == 1

    def test_keeps_on_short_text(self):
        # Text <= 50 chars → fail open, keep the job without calling detect
        mock_ld = _langdetect_mock("sv")
        with patch.dict(sys.modules, {"langdetect": mock_ld}):
            kept, excluded = apply_non_english_text_filter([_job(title="Nej", raw_snippet="")])
        mock_ld.detect.assert_not_called()
        assert len(kept) == 1

    def test_keeps_on_detection_failure(self):
        with patch.dict(sys.modules, {"langdetect": _langdetect_mock_raising()}):
            kept, excluded = apply_non_english_text_filter([_job(title="Some long enough title here")])
        assert len(kept) == 1
        assert len(excluded) == 0

    def test_langdetect_not_installed(self):
        # Simulate langdetect not being importable — all jobs are kept (fail open)
        with patch.dict(sys.modules, {"langdetect": None}):
            kept, excluded = apply_non_english_text_filter([_job(), _job()])
        assert len(kept) == 2
        assert len(excluded) == 0
