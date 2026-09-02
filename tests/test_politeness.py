"""WP10: the User-Agent, the per-host throttle, and the robots.txt check.

No network: a localhost origin serves both the robots.txt and the pages, so
what is asserted here is exactly what a site would have seen. The timings use a
small delay rather than the shipped one second — what is under test is that the
spacing and the cap exist and are respected, not the value of the default.
"""

from __future__ import annotations

import http.server
import socketserver
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import pytest
import requests

from job_scraper import http as http_mod
from job_scraper.robots import RobotsDisallowed, RobotsPolicy, host_of


@dataclass
class _Server:
    """A localhost origin that records what was asked of it, and by whom."""

    port: int = 0
    requests: list[tuple[str, str | None]] = field(default_factory=list)
    robots: str | None = "User-agent: *\nDisallow: /private\n"
    robots_status: int = 200
    # How many times a POST should 500 before it starts succeeding, for the
    # retry test — impactpool's flakiness in miniature.
    post_failures: int = 0

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture
def server() -> Iterator[_Server]:
    state = _Server()

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 - stdlib's spelling
            state.requests.append((self.path, self.headers.get("User-Agent")))
            if self.path == "/robots.txt":
                if state.robots is None or state.robots_status >= 400:
                    self.send_response(state.robots_status if state.robots is None else 404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = state.robots.encode()
            else:
                body = b"<html><body>page</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802 - stdlib's spelling
            """Workable and Tetra Pak are POST-only JSON boards."""
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            state.requests.append((self.path, self.headers.get("User-Agent")))
            if state.post_failures > 0:
                state.post_failures -= 1
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            # The server does not enforce robots.txt — the client does — so it
            # answers everything. A test asserting on a refusal therefore fails
            # loudly if the request was ever actually made.
            body = b'{"results": []}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    state.port = srv.server_address[1]
    # poll_interval as in tests/test_http_fetch.py: shutdown() blocks until the
    # serve_forever loop notices, and the 0.5s default costs that per test.
    threading.Thread(
        target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    ).start()
    try:
        yield state
    finally:
        srv.shutdown()
        srv.server_close()


# -- the user agent ---------------------------------------------------------


def test_the_default_user_agent_promises_no_contact_it_cannot_keep() -> None:
    """The old default pointed at example.com, which is worse than saying nothing."""
    assert "example.com" not in http_mod.DEFAULT_USER_AGENT
    assert "no contact configured" in http_mod.DEFAULT_USER_AGENT


def test_contact_details_from_rules_reach_the_site(server: _Server) -> None:
    agent = http_mod.user_agent_from_rules(
        {"contact_url": "https://example.invalid/scraper", "contact_email": "me@example.invalid"}
    )
    assert agent == (
        "job-scraper/0.1 (+https://example.invalid/scraper; contact=me@example.invalid)"
    )
    with http_mod.polite_fetching(user_agent=agent, delay=0, check_robots=False):
        http_mod.fetch_text(f"{server.origin}/listing")
    assert server.requests == [("/listing", agent)]


def test_half_a_contact_is_still_honest() -> None:
    """A missing email leaves the URL in place rather than an empty field."""
    assert http_mod.build_user_agent(contact_url="https://example.invalid") == (
        "job-scraper/0.1 (+https://example.invalid)"
    )
    assert http_mod.build_user_agent() == http_mod.DEFAULT_USER_AGENT


def test_a_run_without_contact_details_says_so(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"), http_mod.polite_fetching(delay=0, check_robots=False):
        pass
    assert "No contact details configured" in caplog.text


# -- the per-host throttle --------------------------------------------------


def test_requests_to_one_host_are_spaced_out() -> None:
    throttle = http_mod.HostThrottle(delay=0.2, per_host=2)
    started = []
    for _ in range(3):
        with throttle.slot("https://one.invalid/a"):
            started.append(time.monotonic())
    assert started[1] - started[0] >= 0.2
    assert started[2] - started[1] >= 0.2


def test_a_slow_host_does_not_hold_up_a_different_one() -> None:
    throttle = http_mod.HostThrottle(delay=0.3, per_host=2)
    with throttle.slot("https://one.invalid/a"):
        pass
    start = time.monotonic()
    with throttle.slot("https://two.invalid/a"):
        pass
    assert time.monotonic() - start < 0.3


def test_only_two_requests_hit_one_host_at_a_time() -> None:
    """The cap the ten detail workers used not to have."""
    throttle = http_mod.HostThrottle(delay=0, per_host=2)
    lock = threading.Lock()
    live = 0
    peak = 0

    def one(_: int) -> None:
        nonlocal live, peak
        with throttle.slot("https://one.invalid/a"):
            with lock:
                live += 1
                peak = max(peak, live)
            time.sleep(0.05)
            with lock:
                live -= 1

    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(one, range(10)))
    assert peak == 2


def test_a_cached_response_does_not_cost_the_next_one_its_turn() -> None:
    """A fetch answered from disk loaded nobody's server, so it refunds its slot."""
    throttle = http_mod.HostThrottle(delay=0.3, per_host=2)
    with throttle.slot("https://one.invalid/a") as slot:
        slot.refund()
    start = time.monotonic()
    with throttle.slot("https://one.invalid/b"):
        pass
    assert time.monotonic() - start < 0.3


def test_a_site_stated_crawl_delay_wins_when_it_is_longer(server: _Server) -> None:
    """A site that names a rate is the better authority on what it can take.

    Asserted against the delay the throttle would apply rather than by waiting
    it out: the stdlib parser accepts only whole seconds there, and a test that
    sleeps for one to prove a comparison is a second every run pays for ever.
    """
    server.robots = "User-agent: *\nCrawl-delay: 1\n"
    policy = RobotsPolicy("job-scraper/0.1 (test)")
    url = f"{server.origin}/a"
    assert policy.crawl_delay(url) == 1.0
    assert http_mod.HostThrottle(delay=0.05, policy=policy)._delay_for(url) == 1.0
    # ...and ours stands when it is the longer of the two.
    assert http_mod.HostThrottle(delay=2.0, policy=policy)._delay_for(url) == 2.0


# -- robots.txt -------------------------------------------------------------


def test_a_disallowed_page_raises_rather_than_returning_nothing(server: _Server) -> None:
    """Priority 2: a page we may not read must fail, not look like an empty listing."""
    with http_mod.polite_fetching(user_agent="job-scraper/0.1 (test)", delay=0):
        http_mod.fetch_text(f"{server.origin}/public")  # allowed
        with pytest.raises(RobotsDisallowed):
            http_mod.fetch_text(f"{server.origin}/private/job/1")


def test_robots_is_read_once_per_host_however_many_pages_follow(server: _Server) -> None:
    with http_mod.polite_fetching(user_agent="job-scraper/0.1 (test)", delay=0):
        for i in range(5):
            http_mod.fetch_text(f"{server.origin}/page{i}")
    assert [path for path, _ in server.requests].count("/robots.txt") == 1


def test_concurrent_first_hits_still_read_robots_once(server: _Server) -> None:
    policy = RobotsPolicy("job-scraper/0.1 (test)")
    with ThreadPoolExecutor(max_workers=8) as pool:
        answers = list(pool.map(policy.allows, [f"{server.origin}/p{i}" for i in range(8)]))
    assert all(answers)
    assert [path for path, _ in server.requests].count("/robots.txt") == 1


def test_an_override_exempts_the_host_and_asks_it_nothing(server: _Server) -> None:
    """`ignore_robots: true` in sources.yaml — for sites where the answer is wrong for us."""
    with http_mod.polite_fetching(
        user_agent="job-scraper/0.1 (test)",
        delay=0,
        robots_overrides={host_of(server.origin)},
    ):
        http_mod.fetch_text(f"{server.origin}/private/job/1")
    assert "/robots.txt" not in [path for path, _ in server.requests]


def test_a_missing_robots_txt_allows_everything(server: _Server) -> None:
    server.robots = None
    server.robots_status = 404
    policy = RobotsPolicy("job-scraper/0.1 (test)")
    assert policy.allows(f"{server.origin}/private/job/1")


def test_an_unreadable_robots_txt_allows_the_crawl_and_says_so(
    server: _Server, caplog: pytest.LogCaptureFixture
) -> None:
    """A flaky 500 must not read as a site-wide ban — that is impactpool's failure mode."""
    server.robots = None
    server.robots_status = 503
    policy = RobotsPolicy("job-scraper/0.1 (test)")
    with caplog.at_level("WARNING"):
        assert policy.allows(f"{server.origin}/private/job/1")
    assert "proceeding as if it allowed us" in caplog.text


def test_outside_a_polite_block_nothing_is_checked_or_throttled(server: _Server) -> None:
    """Tests, scripts and the fixture capture tool pay for none of this."""
    http_mod.fetch_text(f"{server.origin}/private/job/1")
    assert "/robots.txt" not in [path for path, _ in server.requests]


def test_the_block_restores_what_it_replaced(server: _Server) -> None:
    before = http_mod.current_user_agent()
    with http_mod.polite_fetching(user_agent="job-scraper/0.1 (test)", delay=0):
        assert http_mod.current_robots_policy() is not None
    assert http_mod.current_robots_policy() is None
    assert http_mod.current_user_agent() == before


# -- a forbidden source, through the pipeline --------------------------------


def _pipeline_env(tmp_path, listing: str, *, ignore_robots: bool = False):
    import json

    import yaml

    source = {"name": "acme", "url": listing, "strategy": "static"}
    if ignore_robots:
        source["ignore_robots"] = True
    (tmp_path / "sources.yaml").write_text(
        yaml.dump({"sources": [source]}), encoding="utf-8"
    )
    (tmp_path / "rules.json").write_text(
        json.dumps({"locations": ["Berlin"]}), encoding="utf-8"
    )
    return tmp_path


def _run_against(tmp_path, monkeypatch, listing: str, *, ignore_robots: bool = False):
    from job_scraper import pipeline as pipeline_mod
    from job_scraper.pipeline import run_pipeline

    _pipeline_env(tmp_path, listing, ignore_robots=ignore_robots)
    extracted: list[str] = []

    def extractor(url: str, fetch_fn: object) -> list[dict[str, str]]:
        extracted.append(url)
        return []

    monkeypatch.setattr(pipeline_mod, "get_extractor", lambda name: extractor)
    summary = run_pipeline(
        sources_path=tmp_path / "sources.yaml",
        rules_path=tmp_path / "rules.json",
        out_db_path=tmp_path / "jobs.sqlite3",
        cache_path=tmp_path / "http_cache.sqlite3",
        host_delay=0,
    )
    return summary, extracted


def test_a_forbidden_source_is_skipped_once_not_fetched_page_by_page(
    server: _Server, tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING"):
        summary, extracted = _run_against(
            tmp_path, monkeypatch, f"{server.origin}/private/jobs"
        )
    assert (summary.sources_processed, summary.sources_skipped) == (0, 1)
    assert extracted == []  # the extractor never ran, so the site was never read
    assert "robots.txt disallows" in caplog.text


def test_a_skipped_source_records_no_health_row(
    server: _Server, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It was never scraped, so it has no row count and cannot look like a collapse."""
    from job_scraper.storage.db import JobStore

    _run_against(tmp_path, monkeypatch, f"{server.origin}/private/jobs")
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        rows = store._c().execute("SELECT count(*) FROM source_health").fetchone()[0]
    assert rows == 0


def test_ignore_robots_lets_a_source_through(
    server: _Server, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary, extracted = _run_against(
        tmp_path, monkeypatch, f"{server.origin}/private/jobs", ignore_robots=True
    )
    assert (summary.sources_processed, summary.sources_skipped) == (1, 0)
    assert extracted == [f"{server.origin}/private/jobs"]


# -- exempting a host the source does not itself name ------------------------


def test_ignore_robots_true_exempts_the_source_own_host() -> None:
    from job_scraper.pipeline import _robots_overrides

    assert _robots_overrides(
        [{"name": "a", "url": "https://careers.example.com/jobs", "ignore_robots": True}]
    ) == {"https://careers.example.com"}


def test_ignore_robots_can_name_the_api_host_an_extractor_reaches_for() -> None:
    """The OECD case: the listing is on one host, the postings on another.

    `ignore_robots: true` exempts only the host in sources.yaml, so a source
    whose extractor fetches elsewhere could not be exempted at all until the key
    accepted a list.
    """
    from job_scraper.pipeline import _robots_overrides

    assert _robots_overrides(
        [
            {
                "name": "oecd",
                "url": "https://careers.smartrecruiters.com/OECD/oecd---en",
                "ignore_robots": ["careers.smartrecruiters.com", "api.smartrecruiters.com"],
            }
        ]
    ) == {"https://careers.smartrecruiters.com", "https://api.smartrecruiters.com"}


def test_a_source_without_the_key_exempts_nothing() -> None:
    from job_scraper.pipeline import _robots_overrides

    assert _robots_overrides([{"name": "a", "url": "https://a.example/jobs"}]) == set()


def test_an_unusable_ignore_robots_value_is_refused_loudly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Silently exempting nothing would read as "the override does not work"."""
    from job_scraper.pipeline import _robots_overrides

    with caplog.at_level("WARNING"):
        overrides = _robots_overrides(
            [{"name": "a", "url": "https://a.example", "ignore_robots": 7}]
        )
    assert overrides == set()
    assert "should be true or a list of hosts" in caplog.text


def test_the_refusal_names_the_host_that_has_to_be_exempted(server: _Server) -> None:
    """The message is the fix: naming the source is not enough when the host differs."""
    with http_mod.polite_fetching(user_agent="job-scraper/0.1 (test)", delay=0):
        with pytest.raises(RobotsDisallowed) as raised:
            http_mod.fetch_text(f"{server.origin}/private/job/1")
    assert host_of(server.origin) in str(raised.value)
    assert "ignore_robots" in str(raised.value)


# -- nothing fetches behind the politeness layer's back ----------------------


def test_no_extractor_reaches_the_network_directly() -> None:
    """The gap this package shipped with, kept shut.

    `nutrition_international`, `simprints` (Workable) and `tetrapak` called
    `requests.post` themselves, so they sent `python-requests` as their User-
    Agent, read no robots.txt and paid no per-host spacing — Tetra Pak's
    paginated loop hardest of all. An extractor must go through `job_scraper.
    http`, which is the only place those three things live.
    """
    import pathlib
    import re

    from job_scraper import extractors

    # Located from the package, not from the working directory. A relative path
    # works from the project root and silently matches nothing from anywhere
    # else, and a guard that can pass without reading a single file is worse
    # than no guard: it reports safety it never checked.
    package_dir = pathlib.Path(extractors.__file__).parent
    modules = sorted(package_dir.glob("*.py"))
    assert len(modules) > 20, (
        f"only {len(modules)} extractor modules found in {package_dir} — this guard "
        "is not looking where it thinks it is"
    )

    offenders = {}
    for path in modules:
        source = path.read_text(encoding="utf-8")
        hits = re.findall(r"^\s*(?:import requests|from requests|import urllib\.request)", source,
                          flags=re.MULTILINE)
        hits += re.findall(r"\b(?:urlopen|httpx)\b", source)
        if hits:
            offenders[path.name] = sorted(set(hits))
    assert offenders == {}, f"extractors fetching outside job_scraper.http: {offenders}"


def test_a_post_api_takes_its_turn_and_carries_the_user_agent(server: _Server) -> None:
    """Workable and Tetra Pak are POST-only JSON boards; they get the same treatment."""
    agent = "job-scraper/0.1 (test)"
    with http_mod.polite_fetching(user_agent=agent, delay=0, check_robots=False):
        http_mod.post_json(f"{server.origin}/api/jobs", {"q": ""})
    assert server.requests == [("/api/jobs", agent)]


def test_a_post_api_is_refused_when_robots_says_no(server: _Server) -> None:
    with http_mod.polite_fetching(user_agent="job-scraper/0.1 (test)", delay=0):
        with pytest.raises(RobotsDisallowed):
            http_mod.post_json(f"{server.origin}/private/api/jobs", {"q": ""})


def test_a_revalidated_response_keeps_its_turn() -> None:
    """A 304 came from disk but the question went to the site, so it cost a turn."""

    class _Revalidated:
        from_cache = True
        revalidated = True

    class _Cached:
        from_cache = True
        revalidated = False

    assert http_mod._refunds_its_turn(_Cached()) is True
    assert http_mod._refunds_its_turn(_Revalidated()) is False
    assert http_mod._refunds_its_turn(object()) is False


def test_robots_is_fetched_with_the_tls_handling_the_fetchers_use(server: _Server) -> None:
    """Otherwise the check is absent from exactly the hosts with awkward SSL."""
    seen: list[tuple[str, str, int]] = []

    def recording(url: str, user_agent: str, timeout: int) -> tuple[int, str]:
        seen.append((url, user_agent, timeout))
        return 200, "User-agent: *\nDisallow: /private\n"

    policy = RobotsPolicy("job-scraper/0.1 (test)", fetch=recording)
    assert policy.allows(f"{server.origin}/public")
    assert not policy.allows(f"{server.origin}/private/x")
    assert seen and seen[0][0] == f"{server.origin}/robots.txt"
    # And the fetcher the run installs is the TLS-aware one, not requests.get.
    with http_mod.polite_fetching(user_agent="job-scraper/0.1 (test)", delay=0) as installed:
        assert installed is not None
        assert installed._fetch_robots is http_mod._fetch_robots


# -- a refused detail page is not allowed to be quiet ------------------------


def test_detail_pages_refused_by_robots_are_reported_not_whispered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Layer 5 fails open, deliberately — but silence would hide a whole source.

    A job whose detail page is refused is kept with no experience check, no PhD
    check and no stored description, and the funnel counts it among the ones
    that passed. If robots.txt blocks the detail-page pattern that happens to
    every job on the source at once, so it is said out loud, once, naming the
    host to exempt.
    """
    from job_scraper.experience_filter import apply_detail_filter

    def refused(url: str, *args: object, **kwargs: object) -> str:
        raise RobotsDisallowed(f"robots.txt forbids {url}")

    jobs = [
        {"source_name": "oecd", "title": "Analyst", "location": "Berlin",
         "detail_url": f"https://api.example.com/postings/{i}"}
        for i in range(3)
    ]
    with caplog.at_level("WARNING"):
        kept, excluded = apply_detail_filter(jobs, refused)

    assert len(kept) == 3 and excluded == []  # fail-open is unchanged
    assert "robots.txt refused the detail pages of 3 job(s)" in caplog.text
    assert "https://api.example.com" in caplog.text
    assert "ignore_robots" in caplog.text


def test_a_post_api_retries_a_transient_500(
    server: _Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One flaky page must not abandon the rest of a paginated board.

    Tetra Pak walks its whole board ten postings at a time; before this, a 500
    on page seven ended the source with a short list and no error. The backoff
    is flattened so the test proves the retry, not the wait.
    """
    monkeypatch.setattr(http_mod, "_retry_delay", lambda attempt: 0.0)
    server.post_failures = 2

    with http_mod.polite_fetching(user_agent="job-scraper/0.1 (test)", delay=0, check_robots=False):
        assert http_mod.post_json(f"{server.origin}/api/jobs", {"pageNum": 7}) == {"results": []}
    assert len(server.requests) == 3  # two refusals, then the answer


def test_a_post_api_gives_up_and_raises_rather_than_returning_half_a_board(
    server: _Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A site that is genuinely down must fail the source, not shorten it."""
    monkeypatch.setattr(http_mod, "_retry_delay", lambda attempt: 0.0)
    server.post_failures = 99

    with http_mod.polite_fetching(user_agent="job-scraper/0.1 (test)", delay=0, check_robots=False):
        with pytest.raises(requests.HTTPError):
            http_mod.post_json(f"{server.origin}/api/jobs", {"pageNum": 7})
    assert len(server.requests) == http_mod._RETRY_ATTEMPTS


def test_a_client_error_is_not_retried(server: _Server, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 4xx is an answer, not a hiccup; retrying it is just noise for the host."""
    monkeypatch.setattr(http_mod, "_retry_delay", lambda attempt: 0.0)

    class _Response:
        status_code = 404

        def raise_for_status(self) -> None:
            raise requests.HTTPError("404", response=self)  # type: ignore[arg-type]

    calls = []

    class _Session:
        def post(self, *args: object, **kwargs: object) -> _Response:
            calls.append(1)
            return _Response()

    monkeypatch.setattr(http_mod, "_SESSION", _Session())
    with pytest.raises(requests.HTTPError):
        http_mod.post_json("https://one.invalid/api", {})
    assert len(calls) == 1
