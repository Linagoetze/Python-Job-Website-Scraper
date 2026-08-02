"""Tests for job_scraper.urlutil."""

from job_scraper.urlutil import (
    canonical_detail_url,
    dedupe_key_from_url,
    normalize_http_url,
    oatly_canonical_job_url,
)


class TestNormalizeHttpUrl:
    def test_strips_whitespace(self):
        assert normalize_http_url("  https://example.com  ") == "https://example.com"

    def test_upgrades_http_to_https(self):
        assert normalize_http_url("http://example.com") == "https://example.com"

    def test_leaves_https_unchanged(self):
        assert normalize_http_url("https://example.com") == "https://example.com"

    def test_empty_string(self):
        assert normalize_http_url("") == ""
        assert normalize_http_url("   ") == ""


class TestOatlyCanonicalJobUrl:
    def test_adds_locale_prefix(self):
        listing = "https://careers.oatly.com/en-GB/jobs"
        href = "https://careers.oatly.com/jobs/12345-some-slug"
        result = oatly_canonical_job_url(listing, href)
        assert result == "https://careers.oatly.com/en-GB/jobs/12345-some-slug"

    def test_keeps_existing_locale(self):
        listing = "https://careers.oatly.com/en-GB/jobs"
        href = "https://careers.oatly.com/en-GB/jobs/12345-some-slug"
        result = oatly_canonical_job_url(listing, href)
        assert result == href

    def test_no_locale_in_listing(self):
        listing = "https://careers.oatly.com/jobs"
        href = "https://careers.oatly.com/jobs/12345-slug"
        result = oatly_canonical_job_url(listing, href)
        assert result == href


class TestCanonicalDetailUrl:
    def test_oatly_source_applies_canonical(self):
        result = canonical_detail_url(
            "oatly",
            "https://careers.oatly.com/en-GB/jobs",
            "https://careers.oatly.com/jobs/123-slug",
        )
        assert "/en-GB/jobs/" in result

    def test_non_oatly_source_normalizes_only(self):
        result = canonical_detail_url(
            "airbus",
            "https://airbus.com/careers",
            "http://airbus.com/job/123",
        )
        assert result == "https://airbus.com/job/123"


class TestDedupeKeyFromUrl:
    def test_oatly_uses_job_id(self):
        url = "https://careers.oatly.com/en-GB/jobs/12345-some-slug"
        assert dedupe_key_from_url(url) == "oatly:job:12345"

    def test_generic_url_returns_url(self):
        url = "https://example.com/jobs/123"
        assert dedupe_key_from_url(url) == url

    def test_oatly_variants_same_key(self):
        a = "https://careers.oatly.com/en-GB/jobs/100-slug-a"
        b = "https://careers.oatly.com/jobs/100-slug-b"
        assert dedupe_key_from_url(a) == dedupe_key_from_url(b)
