"""Tests for the Teamtailor extractor — focus on remote work-type handling."""

from job_scraper.extractors.teamtailor import extract

_LISTING_URL = "https://careers.example.com/jobs"

_MIDDOT = "·"


def _fetch(html: str):
    return lambda url: html


def _html(meta: str) -> str:
    """Minimal Teamtailor HTML with one job listing."""
    return f"""
    <html><body>
      <a href="/jobs/123-software-engineer">
        <h3>Software Engineer</h3>
        <p>{meta}</p>
      </a>
    </body></html>
    """


class TestRemoteWorkType:
    def test_remote_only_job_preserves_remote_in_snippet(self):
        """A job with work type 'Remote' but no geographic location must keep
        'remote' in raw_snippet so matches_rules can find it via remote_keywords."""
        html = _html(f"Engineering {_MIDDOT} Remote")
        jobs = extract(_LISTING_URL, _fetch(html), "test_source")
        assert len(jobs) == 1
        job = jobs[0]
        assert "remote" in job["raw_snippet"].lower(), (
            f"'remote' not found in raw_snippet={job['raw_snippet']!r}. "
            "Remote jobs would be incorrectly filtered out."
        )

    def test_geographic_location_is_preserved(self):
        """Location field should contain the city, not the work type."""
        html = _html(f"Engineering {_MIDDOT} Malmö {_MIDDOT} Remote")
        jobs = extract(_LISTING_URL, _fetch(html), "test_source")
        assert len(jobs) == 1
        job = jobs[0]
        assert "malmö" in job["location"].lower()
        assert "remote" not in job["location"].lower()

    def test_hybrid_job_preserves_work_type_in_snippet(self):
        """Same preservation logic applies to 'Hybrid' work type."""
        html = _html(f"Engineering {_MIDDOT} Hybrid")
        jobs = extract(_LISTING_URL, _fetch(html), "test_source")
        assert len(jobs) == 1
        job = jobs[0]
        assert "hybrid" in job["raw_snippet"].lower(), (
            f"'hybrid' not found in raw_snippet={job['raw_snippet']!r}."
        )

    def test_normal_job_without_work_type_unaffected(self):
        """Jobs without a remote/hybrid token should be unaffected."""
        html = _html(f"Engineering {_MIDDOT} Malmö")
        jobs = extract(_LISTING_URL, _fetch(html), "test_source")
        assert len(jobs) == 1
        job = jobs[0]
        assert job["location"] == "Malmö"
        assert job["department"] == "Engineering"
