"""Tests for job_scraper.experience_filter."""

import pytest

from job_scraper.experience_filter import (
    _extract_min_years,
    _strip_html,
    apply_title_filter,
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
