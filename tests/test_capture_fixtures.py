"""Tests for the fixture capture script and for the fixtures themselves.

Nothing here touches the network. The fixtures on disk are the input.
"""

from __future__ import annotations

import http.server
import json
import re
import socketserver
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

# capture_fixtures lives in scripts/, outside the package. fixture_cases owns
# the sys.path amendment that makes it importable and re-exports the module, so
# this is a single ordinary import with no ordering hazard for an import-sorter.
from tests.fixture_cases import FIXTURE_CASES, FIXTURES_DIR, capture_fixtures, parse_fixture

capture_one = capture_fixtures.capture_one
recorded_pages_fetch = capture_fixtures.recorded_pages_fetch
sanitise_html = capture_fixtures.sanitise_html

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


# Walks the tree rather than the top level: fixtures now include directories
# (the eval harness's config fixture), and a nested file is exactly as capable
# of carrying a leaked value as a top-level one.
@pytest.mark.parametrize(
    "path",
    sorted(p for p in FIXTURES_DIR.rglob("*") if p.is_file()),
    ids=lambda p: str(p.relative_to(FIXTURES_DIR)),
)
def test_fixture_contains_no_secret_shaped_values(path: Path) -> None:
    """Guards the GitHub secret-scanning incident that prompted the sanitiser."""
    text = path.read_text(encoding="utf-8", errors="replace")
    for pattern in _SECRET_PATTERNS:
        match = pattern.search(text)
        assert match is None, f"{path.name} matches {pattern.pattern}: {match.group(0)[:40]!r}"


# --- STEP 0: a capture is a polite guest ------------------------------------
#
# The one part of this script that does touch the network, in the sense that
# real sockets open — to 127.0.0.1, never the internet, exactly like
# tests/test_politeness.py. What is under test is that `main()` wraps its
# batch in `http.polite_fetching` built from `sources.yaml` and `rules.json`
# the way `pipeline.run_pipeline` does, not the throttle or retry behaviour
# that module already covers.


@dataclass
class _Server:
    port: int = 0
    requests: list[tuple[str, str | None]] = field(default_factory=list)
    robots: str | None = None

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture
def polite_server() -> Iterator[_Server]:
    state = _Server()

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 - stdlib's spelling
            state.requests.append((self.path, self.headers.get("User-Agent")))
            if self.path == "/robots.txt":
                if state.robots is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = state.robots.encode()
            else:
                body = b"<html><body>hello</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    state.port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    try:
        yield state
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def main_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point `main()` at a throwaway fixtures directory, same as `capture_env`.

    Separate from `capture_env` because these tests exercise `main()`, not
    `capture_one` directly, and so also stub `load_sources` / `load_rules`.
    """
    monkeypatch.setattr(capture_fixtures, "FIXTURES_DIR", tmp_path)
    monkeypatch.setattr(capture_fixtures, "default_project_root", lambda: tmp_path)
    return tmp_path


def test_main_identifies_itself_with_the_rules_json_user_agent(
    polite_server: _Server, main_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = {
        "name": "not_in_registry",
        "url": f"{polite_server.origin}/careers",
        "strategy": "static",
    }
    monkeypatch.setattr(capture_fixtures, "load_sources", lambda: [source])
    monkeypatch.setattr(
        capture_fixtures,
        "load_rules",
        lambda: {
            "contact_url": "https://example.invalid/bot",
            "contact_email": "bot@example.invalid",
        },
    )
    monkeypatch.setattr(sys, "argv", ["capture_fixtures.py", "not_in_registry"])

    exit_code = capture_fixtures.main()

    assert exit_code == 0
    agents = [ua for path, ua in polite_server.requests if path == "/careers"]
    assert agents == ["job-scraper/0.1 (+https://example.invalid/bot; contact=bot@example.invalid)"]
    assert (main_env / "not_in_registry.html").exists()


def test_a_robots_refusal_is_a_failed_capture_not_a_crash(
    polite_server: _Server,
    main_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    polite_server.robots = "User-agent: *\nDisallow: /careers\n"
    source = {
        "name": "not_in_registry",
        "url": f"{polite_server.origin}/careers",
        "strategy": "static",
    }
    monkeypatch.setattr(capture_fixtures, "load_sources", lambda: [source])
    monkeypatch.setattr(capture_fixtures, "load_rules", lambda: {})
    monkeypatch.setattr(sys, "argv", ["capture_fixtures.py", "not_in_registry"])

    exit_code = capture_fixtures.main()

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "robots.txt forbids" in out
    assert [p for p, _ in polite_server.requests if p == "/careers"] == []
    assert not (main_env / "not_in_registry.html").exists()


def test_a_source_s_ignore_robots_exempts_the_capture(
    polite_server: _Server, main_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same `ignore_robots` a real run reads, built by the same code (`_robots_overrides`)."""
    polite_server.robots = "User-agent: *\nDisallow: /careers\n"
    source = {
        "name": "not_in_registry",
        "url": f"{polite_server.origin}/careers",
        "strategy": "static",
        "ignore_robots": True,
    }
    monkeypatch.setattr(capture_fixtures, "load_sources", lambda: [source])
    monkeypatch.setattr(capture_fixtures, "load_rules", lambda: {})
    monkeypatch.setattr(sys, "argv", ["capture_fixtures.py", "not_in_registry"])

    exit_code = capture_fixtures.main()

    assert exit_code == 0
    assert (main_env / "not_in_registry.html").exists()


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


def _jpal_page(jobs: int, last_page: int | None) -> str:
    """Minimal J-PAL listing markup: the jobs view, *jobs* postings, a pager.

    The `view-id-jobs` wrapper is not decoration — the extractor reads it to
    tell a rendered listing from a page whose view is missing.
    """
    nodes = "".join(
        '<div class="node node--type-job">'
        f'<h3 class="job-teaser-title"><a href="/careers/job-{i}">Job {i}</a></h3>'
        "</div>"
        for i in range(jobs)
    )
    pager = ""
    if last_page is not None:
        links = "".join(f'<a href="?page={n}">{n}</a>' for n in range(last_page + 1))
        pager = f'<nav class="pager">{links}</nav>'
    return (
        '<html><body><div class="view view-id-jobs">'
        f'<div class="view-content">{nodes}</div>{pager}'
        "</div></body></html>"
    )


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
        {
            "name": "givewell",
            "url": "https://job-boards.greenhouse.io/givewell",
            "strategy": "static",
        }
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
        {
            "name": "givewell",
            "url": "https://job-boards.greenhouse.io/givewell",
            "strategy": "static",
        }
    )

    assert ok, message
    assert not (capture_env / "givewell.html").exists()
    assert "removed stale givewell.html" in message


def test_capture_records_the_whole_walk_when_asked(
    capture_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--pages all` saves every page, so the fixture replays the real walk.

    Without this a paginated fixture is one page and a faked end, which is how
    the J-PAL shortfall (WP11) stayed invisible to the golden test.
    """
    pages = {
        "https://www.povertyactionlab.org/careers": _jpal_page(1, last_page=1),
        "https://www.povertyactionlab.org/careers?page=1": _jpal_page(1, last_page=1),
    }

    def fake(url: str, *args: Any, **kwargs: Any) -> str:
        return pages[url]

    fake.renders = False  # type: ignore[attr-defined]
    monkeypatch.setattr(capture_fixtures, "fetch_text", fake)

    ok, message = capture_one(
        {"name": "jpal", "url": "https://www.povertyactionlab.org/careers", "strategy": "static"},
        pages=0,
    )

    assert ok, message
    assert (capture_env / "jpal.html").exists()
    assert (capture_env / "jpal.p1.html").exists()
    assert "2 jobs" in message


def test_capture_removes_page_files_a_shorter_walk_left_behind(
    capture_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replay is positional, so yesterday's page 2 must not outlive the walk."""
    (capture_env / "jpal.p1.html").write_text("<html>old page 2</html>", encoding="utf-8")
    (capture_env / "jpal.p2.html").write_text("<html>old page 3</html>", encoding="utf-8")
    fake = _FakeFetcher(_jpal_page(1, last_page=None))
    monkeypatch.setattr(capture_fixtures, "fetch_text", fake)

    ok, message = capture_one(
        {"name": "jpal", "url": "https://www.povertyactionlab.org/careers", "strategy": "static"},
        pages=0,
    )

    assert ok, message
    assert (capture_env / "jpal.html").exists()
    assert not (capture_env / "jpal.p1.html").exists()
    assert not (capture_env / "jpal.p2.html").exists()
    assert "removed stale jpal.p1.html, jpal.p2.html" in message


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
    """SuccessFactors adds a selector wait only if it can tell the fetcher renders JS.

    The capture script hands it a wrapper, so the mark has to survive wrapping
    or dynamic pages get captured before their job links exist. (Workday was
    the example here until SP3b moved it off the rendered page.)
    """
    fake = _FakeFetcher((FIXTURES_DIR / "iss.html").read_text(encoding="utf-8"), renders=True)
    monkeypatch.setattr(capture_fixtures, "fetch_rendered", fake)

    ok, message = capture_one(
        {"name": "iss", "url": "https://jobs.issworld.com/search/", "strategy": "dynamic"}
    )

    assert ok, message
    assert fake.kwargs[0].get("wait_for_selector") == 'a[href*="/job/"]'


def _workday_json(offset: int, count: int, total: int) -> dict[str, Any]:
    return {
        "total": total if offset == 0 else 0,
        "jobPostings": [
            {
                "title": f"Role {n}",
                "externalPath": f"/job/Lund/Role-{n}_R{n}",
                "locationsText": "Lund",
            }
            for n in range(offset, offset + count)
        ],
    }


def test_capture_records_a_walk_made_by_post(
    capture_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SP3b: Workday walks its board by POST, and every POST must land on disk.

    The fetcher's `post_json` is recorded like a GET, so the fixture holds the
    whole walk and replays it — rather than a hand-written JSON file, or
    nothing at all, which is what a reader calling `http.post_json` directly
    would get. `workable.py` used to be that reader; SP4 moved it onto the
    fetcher's `post_json` too, the same way workday.py already was.
    """
    fake = _FakeFetcher("<html>the listing, never asked for</html>", renders=True)
    posted: list[tuple[str, int]] = []

    def fake_post(url: str, payload: dict[str, Any], **kwargs: Any) -> Any:
        posted.append((url, payload["offset"]))
        return _workday_json(payload["offset"], min(20, 45 - payload["offset"]), 45)

    fake.post_json = fake_post  # type: ignore[attr-defined]
    monkeypatch.setattr(capture_fixtures, "fetch_rendered", fake)

    ok, message = capture_one(
        {
            "name": "busuu",
            "url": "https://osv-chegg.wd5.myworkdayjobs.com/Busuu",
            "strategy": "dynamic",
        },
        pages=0,
    )

    assert ok, message
    api = "https://osv-chegg.wd5.myworkdayjobs.com/wday/cxs/osv_chegg/Busuu/jobs"
    assert posted == [(api, 0), (api, 20), (api, 40)]
    assert fake.urls == []
    saved = [capture_env / name for name in ("busuu.json", "busuu.p1.json", "busuu.p2.json")]
    assert all(path.exists() for path in saved)
    assert json.loads(saved[1].read_text(encoding="utf-8"))["jobPostings"][0]["title"] == "Role 20"
    assert "45 jobs" in message
    assert f"from {api}" in message


def test_capture_stops_a_post_walk_after_one_page_by_default(
    capture_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Politeness applies to POSTs too: without --pages, one request."""
    fake = _FakeFetcher("", renders=True)
    posted: list[int] = []

    def fake_post(url: str, payload: dict[str, Any], **kwargs: Any) -> Any:
        posted.append(payload["offset"])
        return _workday_json(payload["offset"], 20, 45)

    fake.post_json = fake_post  # type: ignore[attr-defined]
    monkeypatch.setattr(capture_fixtures, "fetch_rendered", fake)

    ok, message = capture_one(
        {
            "name": "busuu",
            "url": "https://osv-chegg.wd5.myworkdayjobs.com/Busuu",
            "strategy": "dynamic",
        }
    )

    assert ok, message
    assert posted == [0]
    # One page of a 45-posting board is a short walk, and the check says so.
    assert "UNPARSEABLE" in message and "says it has 45" in message


def test_replay_serves_posts_and_gets_from_one_queue() -> None:
    fetch = recorded_pages_fetch(["<html>a</html>", '{"total": 1}'])
    assert fetch("https://x.example/") == "<html>a</html>"
    assert fetch.post_json("https://x.example/api", {}) == {"total": 1}  # type: ignore[attr-defined]
    assert fetch.post_json("https://x.example/api", {}) == {}  # type: ignore[attr-defined]


# --- fixtures still parse ---------------------------------------------------
#
# This is the real guard on the sanitiser. Asserting that fixtures contain no
# <script> would be wrong: kognity's job data lives inside one.
#
# test_extractors_golden.py now pins the exact output of these same fixtures.
# This check is deliberately kept alongside it: it is the weaker assertion but
# it fails for a different reason, and its failure message points at the
# sanitiser rather than at selector drift.


@pytest.mark.parametrize("name", sorted(FIXTURE_CASES))
def test_fixture_still_parses(name: str) -> None:
    filename = FIXTURE_CASES[name][0]
    if not (FIXTURES_DIR / filename).exists():
        pytest.fail(f"{filename} not captured yet — re-run scripts/capture_fixtures.py {name}")

    jobs = parse_fixture(name)

    assert len(jobs) > 0, f"{filename} parsed to zero jobs"
    assert all(job["title"] for job in jobs)
