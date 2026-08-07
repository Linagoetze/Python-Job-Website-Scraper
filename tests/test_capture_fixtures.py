"""Tests for the fixture capture script and for the fixtures themselves.

Nothing here touches the network. The fixtures on disk are the input.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

import capture_fixtures  # noqa: E402
from capture_fixtures import capture_one, sanitise_html, single_response_fetch  # noqa: E402

from job_scraper.extractors import (  # noqa: E402
    ashby,
    greenhouse,
    impactpool,
    successfactors_html,
    teamtailor,
    workday,
)

FIXTURES_DIR = _PROJECT_ROOT / "tests" / "fixtures"


# --- the sanitiser ----------------------------------------------------------


def test_sanitiser_drops_tracking_but_keeps_app_data() -> None:
    """Only scripts without a job-data payload are removed.

    The kept case is not hypothetical: ashby.py locates jobs by searching the
    raw HTML for window.__appData, so a sanitiser that stripped every script
    would silently empty the kognity fixture.
    """
    html = """<html><head>
      <script>ga('send','pageview');window.ENV={"KEY":"public-value"};</script>
      <script>window.__appData = {"jobs":[{"title":"Engineer"}]};</script>
    </head><body><a href="/jobs/1">Engineer</a></body></html>"""

    out = sanitise_html(html)

    assert "ga('send','pageview')" not in out
    assert "window.ENV" not in out
    assert 'window.__appData = {"jobs":[{"title":"Engineer"}]};' in out
    assert '<a href="/jobs/1">' in out


# --- no secrets in fixtures -------------------------------------------------

# A bare mention of "api_key" in page copy is not a leak; an opaque value
# assigned to one is. These patterns look for the value, not the word.
_SECRET_PATTERNS = (
    re.compile(r"AIza[A-Za-z0-9_-]{35}"),
    re.compile(r"""api[_-]?key["'\s:=]{0,10}["']?[A-Za-z0-9_-]{16,}""", re.IGNORECASE),
    re.compile(r"""_token["'\s:=]{0,10}["']?[A-Za-z0-9_-]{16,}""", re.IGNORECASE),
)


@pytest.mark.parametrize("path", sorted(FIXTURES_DIR.iterdir()), ids=lambda p: p.name)
def test_fixture_contains_no_secret_shaped_values(path: Path) -> None:
    """Guards the GitHub secret-scanning incident that prompted the sanitiser."""
    text = path.read_text(encoding="utf-8", errors="replace")
    for pattern in _SECRET_PATTERNS:
        match = pattern.search(text)
        assert match is None, f"{path.name} matches {pattern.pattern}: {match.group(0)[:40]!r}"


# --- fixtures still parse ---------------------------------------------------
#
# This is the real guard on the sanitiser. Asserting that fixtures contain no
# <script> would be wrong: kognity's job data lives inside one.

_Extractor = Callable[[str, Callable[..., str]], list[dict[str, Any]]]

# source name -> (fixture filename, listing URL, extractor bound to its args)
_FIXTURE_CASES: dict[str, tuple[str, str, _Extractor]] = {
    "givewell": (
        "givewell.json",
        "https://job-boards.greenhouse.io/givewell",
        lambda url, fetch: greenhouse.extract(url, fetch, source_name="givewell"),
    ),
    "kognity": (
        "kognity.html",
        "https://jobs.ashbyhq.com/kognity",
        lambda url, fetch: ashby.extract(url, fetch, source_name="kognity"),
    ),
    "storytel": (
        "storytel.html",
        "https://jobs.storytel.com/jobs",
        lambda url, fetch: teamtailor.extract(url, fetch, source_name="storytel"),
    ),
    "busuu": (
        "busuu.html",
        "https://osv-chegg.wd5.myworkdayjobs.com/Busuu",
        lambda url, fetch: workday.extract(url, fetch, source_name="busuu"),
    ),
    "dsv": (
        "dsv.html",
        "https://jobs.dsv.com/search/",
        lambda url, fetch: successfactors_html.extract(
            url,
            fetch,
            source_name="dsv",
            page_step=10,
            base_search_url="https://jobs.dsv.com/search/",
        ),
    ),
    "impactpool": (
        "impactpool.html",
        "https://www.impactpool.org/search",
        lambda url, fetch: impactpool.extract(url, fetch, source_name="impactpool"),
    ),
}


# --- capture_one, with the network faked out --------------------------------
#
# Everything about capture_one except the socket itself: which URL the
# extractor is allowed to reach, how many requests it makes, what lands on
# disk. The fetcher is replaced, so no test here opens a connection.


class _FakeFetcher:
    """Stands in for fetch_text / fetch_rendered. Records every URL asked for."""

    def __init__(self, body: str, renders: bool = False) -> None:
        self.body = body
        self.urls: list[str] = []
        self.kwargs: list[dict[str, Any]] = []
        self.renders = renders

    def __call__(self, url: str, *args: Any, **kwargs: Any) -> str:
        self.urls.append(url)
        self.kwargs.append(kwargs)
        return self.body


@pytest.fixture
def capture_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the capture script at a throwaway fixtures directory."""
    monkeypatch.setattr(capture_fixtures, "FIXTURES_DIR", tmp_path)
    monkeypatch.setattr(capture_fixtures, "default_project_root", lambda: tmp_path)
    return tmp_path


def test_capture_uses_the_url_the_extractor_reads(
    capture_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression test for the bug this branch fixed.

    greenhouse.py never parses the listing page; it calls the boards API. The
    capture must follow the extractor there rather than saving the listing URL.
    """
    fake = _FakeFetcher('{"jobs": [{"title": "Program Officer", "location": {"name": "Remote"}}]}')
    monkeypatch.setattr(capture_fixtures, "fetch_text", fake)

    ok, message = capture_one(
        {"name": "givewell", "url": "https://job-boards.greenhouse.io/givewell", "strategy": "static"}
    )

    assert ok, message
    assert fake.urls == ["https://boards-api.greenhouse.io/v1/boards/givewell/jobs?per_page=500"]
    assert (capture_env / "givewell.json").exists()
    assert not (capture_env / "givewell.html").exists()
    assert "1 jobs" in message


def test_capture_makes_exactly_one_request(
    capture_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Politeness: the sentinel stops a paginating extractor after page one."""
    fake = _FakeFetcher((FIXTURES_DIR / "impactpool.html").read_text(encoding="utf-8"))
    monkeypatch.setattr(capture_fixtures, "fetch_text", fake)

    ok, message = capture_one(
        {"name": "impactpool", "url": "https://www.impactpool.org/search", "strategy": "static"}
    )

    assert ok, message
    assert len(fake.urls) == 1, f"capture hit the site {len(fake.urls)} times"
    assert fake.urls[0] == "https://www.impactpool.org/search?page=1"


def test_capture_sanitises_html_and_leaves_no_temp_file(
    capture_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        '<html><body><script>window.ENV={"K":"v"};</script>'
        '<a href="/jobs/1"><h3>Engineer</h3><p>Tech · Berlin</p></a></body></html>'
    )
    fake = _FakeFetcher(body)
    monkeypatch.setattr(capture_fixtures, "fetch_text", fake)

    ok, message = capture_one(
        {"name": "storytel", "url": "https://jobs.storytel.com/jobs", "strategy": "static"}
    )

    assert ok, message
    saved = (capture_env / "storytel.html").read_text(encoding="utf-8")
    assert "window.ENV" not in saved
    assert "Engineer" in saved
    assert list(capture_env.glob("*.tmp")) == []


def test_capture_removes_the_stale_sibling(
    capture_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source that changes artefact type must not leave the old one behind."""
    (capture_env / "givewell.html").write_text("<html>stale listing page</html>", encoding="utf-8")
    fake = _FakeFetcher('{"jobs": []}')
    monkeypatch.setattr(capture_fixtures, "fetch_text", fake)

    ok, message = capture_one(
        {"name": "givewell", "url": "https://job-boards.greenhouse.io/givewell", "strategy": "static"}
    )

    assert ok, message
    assert not (capture_env / "givewell.html").exists()
    assert "removed stale givewell.html" in message


def test_capture_falls_back_to_the_listing_url_without_a_registry_entry(
    capture_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeFetcher("<html><body>hello</body></html>")
    monkeypatch.setattr(capture_fixtures, "fetch_text", fake)

    ok, message = capture_one(
        {"name": "not_in_registry", "url": "https://example.invalid/jobs", "strategy": "static"}
    )

    assert ok, message
    assert fake.urls == ["https://example.invalid/jobs"]
    assert (capture_env / "not_in_registry.html").exists()


def test_capture_keeps_the_rendering_mark_through_the_wrapper(
    capture_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Workday adds a selector wait only if it can tell the fetcher renders JS.

    The capture script hands it a wrapper, so the mark has to survive wrapping
    or dynamic pages get captured before their job cards exist.
    """
    fake = _FakeFetcher(
        (FIXTURES_DIR / "busuu.html").read_text(encoding="utf-8"), renders=True
    )
    monkeypatch.setattr(capture_fixtures, "fetch_rendered", fake)

    ok, message = capture_one(
        {"name": "busuu", "url": "https://osv-chegg.wd5.myworkdayjobs.com/Busuu", "strategy": "dynamic"}
    )

    assert ok, message
    assert fake.kwargs[0].get("wait_for_selector") == '[data-automation-id="jobTitle"]'


@pytest.mark.parametrize("name", sorted(_FIXTURE_CASES))
def test_fixture_still_parses(name: str) -> None:
    filename, listing_url, extractor = _FIXTURE_CASES[name]
    path = FIXTURES_DIR / filename
    if not path.exists():
        # givewell's captured HTML is the wrong artefact: greenhouse.py reads
        # the boards API, not the listing page. The capture script now saves
        # what the extractor asks for, so this skip clears on the next run.
        pytest.skip(f"{filename} not captured yet — re-run scripts/capture_fixtures.py {name}")

    jobs = extractor(listing_url, single_response_fetch(path.read_text(encoding="utf-8")))

    assert len(jobs) > 0, f"{filename} parsed to zero jobs"
    assert all(job["title"] for job in jobs)
