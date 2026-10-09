"""Tests for the Teamtailor extractor — focus on remote work-type handling."""

import pytest

from job_scraper.extractors.teamtailor import _check_row, extract

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


def _image_card(title: str, shown: str, meta_spans: str) -> str:
    """SP8's layout: an image grid, where the title is a <span title> and the
    metadata <div> is its sibling inside one wrapper, both inside the <a>."""
    return f"""
    <html><body><ul><li>
      <a href="/jobs/8395577-intern">
        <div><figure><img alt="x" src="x.jpg"/></figure></div>
        <div>
          <span title="{title}">{shown}</span>
          <div>{meta_spans}</div>
        </div>
      </a>
    </li></ul></body></html>
    """


_META = (
    "<span>Internship</span><span>·</span><span>Stockholm</span><span>·</span>"
    '<span>Hybrid <i class="fas fa-wifi"></i></span>'
)
_LONG_TITLE = "Intern with the WWF Baltic Sea programme: project management and communication"


class TestImageCardLayout:
    def test_reads_metadata_from_the_title_spans_sibling(self):
        html = _image_card(_LONG_TITLE, "Intern with the WWF Baltic Sea programme: proje...", _META)
        (job,) = extract(_LISTING_URL, _fetch(html), "test_source")
        assert job["title"] == _LONG_TITLE
        assert job["location"] == "Stockholm"
        assert job["department"] == "Internship"
        assert "Hybrid" in job["raw_snippet"]

    def test_card_with_no_department_chip_keeps_its_location(self):
        meta = '<span>Stockholm</span><span>·</span><span>Remote <i class="fas"></i></span>'
        html = _image_card("A title", "A title", meta)
        (job,) = extract(_LISTING_URL, _fetch(html), "test_source")
        assert (job["department"], job["location"]) == ("", "Stockholm")


class TestTitleReadAsLocation:
    """A layout the reader does not know must fail, not return the title as the place."""

    def test_image_card_misread_raises(self):
        # SP5's layout read by a reader that takes the card's wrapper <div> as
        # the metadata block: its first direct <span> is the title.
        html = _image_card(_LONG_TITLE, "Intern with the WWF Baltic Sea programme: proje...", "")
        html = html.replace("<div></div>", "")  # no metadata <div> to find
        with pytest.raises(ValueError, match="test_source"):
            extract(_LISTING_URL, _fetch(html), "test_source")

    @pytest.mark.parametrize("location", ["A title", "A tit...", "A tit\u2026"])
    def test_check_raises_on_title_as_location(self, location):
        with pytest.raises(ValueError, match="src"):
            _check_row("src", {"title": "A title", "location": location, "detail_url": "u"})

    def test_a_city_that_starts_a_title_is_not_a_misread(self):
        _check_row(
            "src", {"title": "Stockholm Office Manager", "location": "Stockholm", "detail_url": "u"}
        )

    def test_empty_location_is_a_real_state(self):
        _check_row("src", {"title": "A title", "location": "", "detail_url": "u"})
