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


# --- the show-more walk (2026-11-25) ----------------------------------------
# Invented markup in the shape of a live board: a first page stating its count
# above 20 cards and a show-more link, and Turbo Stream answers in which each
# card is an `append` inside a <template> and the link comes back as an `update`.

from job_scraper.extractors.pagination import ShortWalkError  # noqa: E402


def _card(n: int) -> str:
    return (
        f'<li class="w-full"><div class="relative flex flex-col">'
        f'<a data-turbo="false" href="https://careers.example.com/jobs/{1000 + n}-role-{n}">'
        f'<span class="absolute inset-0"></span>\n      Role {n}\n</a>'
        f'<div class="text-md mt-4"><span>Team {n}</span>'
        f'<span class="mx-[2px]">&middot;</span><span>City {n}</span></div>'
        f"</div></li>"
    )


def _first_page(cards: range, stated: int | None, next_page: int | None) -> str:
    count = f"<h2> {stated} jobs <span></span></h2>" if stated is not None else ""
    more = (
        f'<div id="show_more_button"><a href="/jobs/show_more?page={next_page}">'
        f"<span>Show more</span></a></div>"
        if next_page
        else ""
    )
    items = "".join(_card(n) for n in cards)
    return (
        '<html><body><div data-blocks--jobs-target="jobsListContainer">'
        f'<div>{count}</div><ul id="jobs_list_container">{items}</ul>{more}'
        "</div></body></html>"
    )


def _stream(cards: range, next_page: int | None) -> str:
    parts = "".join(
        '<turbo-stream action="append" target="jobs_list_container">'
        f"<template>{_card(n)}</template></turbo-stream>"
        for n in cards
    )
    link = f'<a href="/jobs/show_more?page={next_page}">Show more</a>' if next_page else ""
    return (
        parts + '<turbo-stream action="update" target="show_more_button">'
        f"<template>{link}</template></turbo-stream>"
    )


def _walk(pages: dict[str, str], asked: list[str] | None = None):
    def fetch(url: str) -> str:
        if asked is not None:
            asked.append(url)
        return pages[url]

    return extract(_LISTING_URL, fetch, source_name="example")


_PAGE = "https://careers.example.com/jobs/show_more?page="


class TestShowMoreWalk:
    def test_follows_show_more_to_the_last_page(self):
        asked: list[str] = []
        rows = _walk(
            {
                _LISTING_URL: _first_page(range(20), stated=43, next_page=2),
                _PAGE + "2": _stream(range(20, 40), next_page=3),
                _PAGE + "3": _stream(range(40, 43), next_page=None),
            },
            asked,
        )
        assert len(rows) == 43
        assert asked == [_LISTING_URL, _PAGE + "2", _PAGE + "3"]

    def test_cards_inside_templates_keep_their_text(self):
        """A template's text is not in get_text(); read in place, every title was empty."""
        rows = _walk(
            {
                _LISTING_URL: _first_page(range(20), stated=21, next_page=2),
                _PAGE + "2": _stream(range(20, 21), next_page=None),
            }
        )
        last = rows[-1]
        assert last["title"] == "Role 20"
        assert last["department"] == "Team 20"
        assert last["location"] == "City 20"
        assert last["detail_url"] == "https://careers.example.com/jobs/1020-role-20"
        assert last["listing_url"] == _LISTING_URL

    def test_a_first_page_short_of_its_count_raises(self):
        """oatly before the fix: 20 read, 23 stated, and no error."""
        with pytest.raises(ShortWalkError, match="says it has 23"):
            _walk({_LISTING_URL: _first_page(range(20), stated=23, next_page=None)})

    def test_an_empty_show_more_answer_before_the_count_raises(self):
        with pytest.raises(ShortWalkError, match="says it has 43"):
            _walk(
                {
                    _LISTING_URL: _first_page(range(20), stated=43, next_page=2),
                    _PAGE + "2": "<html><body></body></html>",
                }
            )

    def test_a_single_page_that_states_no_count_is_read_as_before(self):
        rows = _walk({_LISTING_URL: _first_page(range(3), stated=None, next_page=None)})
        assert len(rows) == 3

    def test_a_walk_with_no_count_says_it_could_not_check(self, caplog):
        rows = _walk(
            {
                _LISTING_URL: _first_page(range(20), stated=None, next_page=2),
                _PAGE + "2": _stream(range(20, 22), next_page=None),
            }
        )
        assert len(rows) == 22
        assert "publishes no total" in caplog.text

    def test_a_link_that_never_ends_stops_and_says_so(self):
        def fetch(url: str) -> str:
            if url == _LISTING_URL:
                return _first_page(range(20), stated=None, next_page=2)
            n = int(url.rsplit("=", 1)[1])
            return _stream(range(n * 100, n * 100 + 20), next_page=n + 1)

        with pytest.raises(ShortWalkError, match="after 50 pages"):
            extract(_LISTING_URL, fetch, source_name="example")

    @pytest.mark.parametrize(("stated", "expected"), [("1 job", 1), ("23 jobs", 23)])
    def test_reads_the_stated_count_from_a_span(self, stated, expected):
        """Storytel's layout states it in a <span>, not an <h2>."""
        from bs4 import BeautifulSoup

        from job_scraper.extractors.teamtailor import _stated_count

        html = _first_page(range(2), stated=None, next_page=None).replace(
            "<div></div>", f'<div><p><span class="text-lg"> {stated} </span></p></div>'
        )
        assert _stated_count(BeautifulSoup(html, "lxml")) == expected
