"""WP9: the response cache and the render pool.

No network: every test serves from a `http.server` bound to localhost, so the
counts below are exact rather than best-effort. The render-pool tests drive a
real headless Chromium against that same server — the thing under test is
Playwright's threading behaviour, and a mock would assert nothing about it.
"""

from __future__ import annotations

import http.server
import logging
import socketserver
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest
import requests

from job_scraper import http as http_mod
from job_scraper import run as run_module


@dataclass
class _Server:
    """A localhost origin with scriptable caching behaviour."""

    port: int = 0
    requests: list[tuple[str, str | None]] = field(default_factory=list)
    body: str = "<html><body>one</body></html>"
    etag: str = '"v1"'
    status: int = 200  # flipped to 500 to exercise stale_if_error

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/listing"


@pytest.fixture
def server() -> Iterator[_Server]:
    state = _Server()

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 - stdlib's spelling
            inm = self.headers.get("If-None-Match")
            state.requests.append((self.path, inm))

            if state.status >= 500:
                self.send_response(state.status)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if inm == state.etag:
                self.send_response(304)
                self.send_header("ETag", state.etag)
                self.end_headers()
                return
            payload = state.body.encode()
            self.send_response(200)
            self.send_header("ETag", state.etag)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    state.port = srv.server_address[1]
    # poll_interval, not a detail: shutdown() blocks until the serve_forever loop
    # notices, and the 0.5s default was costing half a second per test — about
    # ten seconds across this file, dwarfing the work being tested.
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    try:
        yield state
    finally:
        srv.shutdown()
        srv.server_close()


# ---------------------------------------------------------------------------
# Response cache
# ---------------------------------------------------------------------------


def test_no_cache_block_means_every_call_hits_the_site(server, tmp_path):
    """The default path is unchanged: without http_cache(), nothing is stored."""
    for _ in range(3):
        assert "one" in http_mod.fetch_text(server.url)
    assert len(server.requests) == 3


def test_second_fetch_inside_the_ttl_costs_no_request(server, tmp_path):
    with http_mod.http_cache(path=tmp_path / "c.sqlite3", ttl=300) as stats:
        first = http_mod.fetch_text(server.url)
        second = http_mod.fetch_text(server.url)

    assert first == second == server.body
    assert len(server.requests) == 1, "the second fetch should never have left the process"
    assert (stats.misses, stats.hits) == (1, 1)


# requests-cache reads expire_after=0 as "do not cache", so a test that wants a
# stored-but-stale entry has to store one and then outlive its TTL.
_BRIEF_TTL = 1


def test_expired_entry_is_revalidated_with_the_stored_etag(server, tmp_path):
    """A lapsed TTL sends If-None-Match; a 304 means no body crosses the network."""
    # One block, deliberately: http_cache() prunes expired rows as it exits, so
    # seeding in a first block and waiting out the TTL races that housekeeping —
    # a slow enough exit deletes the very entry the test needs.
    with http_mod.http_cache(path=tmp_path / "c.sqlite3", ttl=_BRIEF_TTL) as stats:
        http_mod.fetch_text(server.url)
        time.sleep(_BRIEF_TTL + 0.2)
        again = http_mod.fetch_text(server.url)

    assert again == server.body
    assert len(server.requests) == 2
    assert server.requests[0][1] is None, "first request cannot carry a validator"
    assert server.requests[1][1] == server.etag, "second must carry the stored ETag"
    assert stats.revalidated == 1, f"304 not counted as a revalidation: {stats.summary()}"


def test_cache_survives_the_block_and_is_reused_by_the_next_run(server, tmp_path):
    path = tmp_path / "c.sqlite3"
    with http_mod.http_cache(path=path, ttl=300):
        http_mod.fetch_text(server.url)
    with http_mod.http_cache(path=path, ttl=300) as stats:
        assert http_mod.fetch_text(server.url) == server.body

    assert len(server.requests) == 1
    assert stats.hits == 1


def test_a_failing_site_still_fails_even_with_a_cached_copy_to_hand(server, tmp_path, monkeypatch):
    """Priority 2. The cache must never stand in for a site that is down.

    requests-cache will happily serve the previous copy when the origin errors
    (`stale_if_error`), which keeps a run going at the cost of reporting a
    successful scrape of a page that is minutes old. That is off, and this is the
    test that says so: a cached copy exists, is findable, and is *not* used.
    """
    # Two attempts rather than the real five, and no waiting between them: the
    # ladder sleeps 2s, 4s, 6s, 8s, and this test is about what happens once the
    # attempts run out, not about how long they take. Only http.py's reference to
    # the module is swapped, so nothing else loses its sleep.
    monkeypatch.setattr(http_mod, "_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(http_mod, "time", SimpleNamespace(sleep=lambda _seconds: None))

    # One block, for the reason given in the revalidation test above. It matters
    # more here: if the seeded entry were pruned, the 500 would still raise and
    # the test would pass while proving nothing about the cache.
    with http_mod.http_cache(path=tmp_path / "c.sqlite3", ttl=_BRIEF_TTL):
        http_mod.fetch_text(server.url)
        time.sleep(_BRIEF_TTL + 0.2)

        server.status = 500
        attempts_before = len(server.requests)
        with pytest.raises(requests.HTTPError) as exc:
            http_mod.fetch_text(server.url)

    assert exc.value.response.status_code == 500
    assert len(server.requests) - attempts_before == 2, (
        "the 5xx retry must still run — it, not the cache, is what covers a flaky 500"
    )


def test_stats_are_scoped_to_one_block(server, tmp_path):
    with http_mod.http_cache(path=tmp_path / "a.sqlite3", ttl=300):
        http_mod.fetch_text(server.url)
    with http_mod.http_cache(path=tmp_path / "b.sqlite3", ttl=300) as stats:
        assert stats.misses == 0, "a new block starts from zero"


def test_the_tls_adapter_survives_the_cached_session(tmp_path):
    """The cache wraps Session.send, so the https mount must still be in place."""
    with http_mod.http_cache(path=tmp_path / "c.sqlite3", ttl=300):
        with http_mod._SESSION_LOCK:
            session = http_mod._SESSION
        assert isinstance(session.get_adapter("https://example.org"), http_mod._TLSAdapter)


def test_a_zero_ttl_revalidates_every_page_rather_than_caching_none(server, tmp_path, caplog):
    """`--cache-ttl 0` is not `--no-cache`: nothing is ever *fresh*, but pages are
    still stored and still revalidated, so a 304 saves the download.

    It is also the setting that catches the classification the hard way. A 304
    refreshes the entry and then re-applies the zero TTL, so a healthy
    revalidation comes back flagged expired. Counted on `is_expired` alone, every
    page here is reported as the site having failed.
    """
    with (
        caplog.at_level(logging.WARNING),
        http_mod.http_cache(path=tmp_path / "c.sqlite3", ttl=0) as stats,
    ):
        for _ in range(3):
            assert http_mod.fetch_text(server.url) == server.body

    assert len(server.requests) == 3, "every run goes to the site"
    assert [r[1] for r in server.requests] == [None, server.etag, server.etag]
    assert (stats.misses, stats.revalidated) == (1, 2)
    assert "stale" not in caplog.text, "a healthy 304 is not a site failure"


@pytest.mark.parametrize("ttl", [-1, -3600])
def test_a_negative_ttl_is_refused(tmp_path, ttl):
    """-1 is requests-cache's NEVER_EXPIRE, and it is the obvious guess for "off".

    Left unguarded, `--cache-ttl -1` caches every career page for ever and the
    scraper quietly stops finding new postings — the opposite of what the person
    typing it wanted. Every negative is refused, not just the one that bites.
    """
    with pytest.raises(ValueError, match="zero or more seconds"):
        with http_mod.http_cache(path=tmp_path / "c.sqlite3", ttl=ttl):
            pass


@pytest.mark.parametrize("ttl", ["-1", "-3600"])
def test_the_cli_refuses_a_negative_ttl_before_anything_runs(monkeypatch, capsys, ttl):
    monkeypatch.setattr(sys, "argv", ["run.py", "--cache-ttl", ttl])
    with pytest.raises(SystemExit) as exc:
        run_module.main()

    assert exc.value.code == 2
    assert "--no-cache" in capsys.readouterr().err, "the error must name the flag they wanted"


def test_a_zero_ttl_is_allowed(server, tmp_path):
    """The boundary the guard must not overshoot — 0 is meaningful, see below."""
    with http_mod.http_cache(path=tmp_path / "c.sqlite3", ttl=0):
        http_mod.fetch_text(server.url)


def test_the_cache_file_keeps_the_name_it_was_given(server, tmp_path):
    """requests-cache rewrites an extension-less name to `.sqlite`.

    Left to do that, the cache lands in `data/` as `http_cache.sqlite`, which
    `.gitignore`'s `data/*.sqlite3` does not match — and the file holds the body
    of every career page fetched. Pin the name, not just the behaviour.
    """
    path = tmp_path / "http_cache.sqlite3"
    with http_mod.http_cache(path=path, ttl=300):
        http_mod.fetch_text(server.url)

    assert path.exists()
    assert sorted(f.name for f in tmp_path.glob("*.sqlite")) == []


def test_the_default_cache_path_is_one_gitignore_covers():
    assert http_mod.default_http_cache_path().suffix == ".sqlite3"


def test_the_session_is_restored_after_the_block(tmp_path):
    before = http_mod._SESSION
    with http_mod.http_cache(path=tmp_path / "c.sqlite3", ttl=300):
        assert http_mod._SESSION is not before
    assert http_mod._SESSION is before


# ---------------------------------------------------------------------------
# Render pool
# ---------------------------------------------------------------------------

playwright = pytest.importorskip("playwright.sync_api")
PlaywrightError = playwright.Error

# Nothing listens on port 1, so Chromium reports a navigation failure promptly.
UNREACHABLE = "http://127.0.0.1:1/nothing-here"


def test_many_pages_share_one_browser(server):
    """The point of WP9: pages cost a context, not a process."""
    with http_mod.render_pool(workers=1) as pool:
        for _ in range(3):
            assert "one" in http_mod.fetch_rendered(server.url, settle_ms=0)

    assert pool.pages_rendered == 3
    assert pool.launches == 1, "three pages must not have cost three Chromiums"


def test_a_run_with_no_rendered_fetch_launches_nothing(server):
    with http_mod.render_pool(workers=2) as pool:
        http_mod.fetch_text(server.url)
    assert pool.launches == 0


def test_browsers_are_bounded_by_the_pool_not_by_the_callers(server):
    """Eight concurrent callers, four render threads, at most four browsers."""
    errors: list[BaseException] = []

    def call() -> None:
        try:
            assert "one" in http_mod.fetch_rendered(server.url, settle_ms=0)
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    with http_mod.render_pool(workers=4) as pool:
        threads = [threading.Thread(target=call) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

    assert errors == []
    assert pool.pages_rendered == 8
    assert 1 <= pool.launches <= 4


def test_a_render_failure_reaches_the_caller():
    """Layer 5 fails open on exceptions, so a render error must arrive as one."""
    with http_mod.render_pool(workers=1):
        with pytest.raises(PlaywrightError):
            http_mod.fetch_rendered(UNREACHABLE, timeout=5_000, settle_ms=0)


def test_one_failure_does_not_poison_the_pool(server):
    """A dead page must cost its own fetch and nothing else — the browser stays up."""
    with http_mod.render_pool(workers=1) as pool:
        with pytest.raises(PlaywrightError):
            http_mod.fetch_rendered(UNREACHABLE, timeout=5_000, settle_ms=0)
        assert "one" in http_mod.fetch_rendered(server.url, settle_ms=0)
    assert pool.launches == 1


def _break_driver_for(n_threads: int, monkeypatch) -> None:
    """Make the first *n_threads* calls to sync_playwright() blow up.

    Each render thread starts exactly one, so this fails that many threads'
    drivers and leaves the rest healthy — the memory-pressure case.
    """
    import playwright.sync_api as pw_api

    real = pw_api.sync_playwright
    remaining = [n_threads]
    lock = threading.Lock()

    def flaky():
        with lock:
            broken = remaining[0] > 0
            if broken:
                remaining[0] -= 1
        if broken:
            raise RuntimeError("no memory for a browser")
        return real()

    monkeypatch.setattr(pw_api, "sync_playwright", flaky)


def test_one_broken_driver_does_not_steal_work_from_the_healthy_threads(server, monkeypatch):
    """The failure mode this guards is subtle and would look like a broken scrape.

    A thread whose driver died can fail a page instantly, far faster than a
    healthy thread renders one. If it keeps taking work off the queue, a single
    bad driver fails most of the run's pages while good browsers sit idle. It
    must stand down instead.
    """
    _break_driver_for(3, monkeypatch)

    with http_mod.render_pool(workers=4) as pool:
        for _ in range(6):
            assert "one" in http_mod.fetch_rendered(server.url, settle_ms=0)

    assert pool.pages_rendered == 6, "the one healthy thread should have served every page"
    assert pool.launches == 1


def test_when_no_driver_starts_the_caller_gets_the_error_not_a_hang(server, monkeypatch):
    """The total-failure case still has to fail loudly rather than block."""
    _break_driver_for(4, monkeypatch)

    with http_mod.render_pool(workers=4):
        with pytest.raises(RuntimeError, match="no memory for a browser"):
            http_mod.fetch_rendered(server.url, settle_ms=0)


def test_a_pool_closed_under_a_caller_raises_rather_than_blocking(server):
    """The enqueue side of shutdown: a request must never outlive its servers."""
    pool = http_mod.RenderPool(workers=1)
    pool.fetch(server.url, timeout=10_000, settle_ms=0)
    pool.close()

    with pytest.raises(RuntimeError, match="closed"):
        pool.fetch(server.url, timeout=10_000, settle_ms=0)


def test_nested_blocks_share_the_pool_and_only_the_outer_one_closes_it(server):
    with http_mod.render_pool(workers=1) as outer:
        with http_mod.render_pool(workers=1) as inner:
            assert inner is outer
            http_mod.fetch_rendered(server.url, settle_ms=0)
        # The inner exit must not have closed anything.
        http_mod.fetch_rendered(server.url, settle_ms=0)
    assert outer.pages_rendered == 2
    assert outer.launches == 1


def test_outside_a_pool_fetch_rendered_still_works_on_its_own(server):
    assert http_mod._POOL is None
    assert "one" in http_mod.fetch_rendered(server.url, settle_ms=0)


def test_the_pool_is_cleared_after_the_block(server):
    with http_mod.render_pool(workers=1):
        pass
    assert http_mod._POOL is None


def test_a_closed_pool_refuses_new_work(server):
    pool = http_mod.RenderPool(workers=1)
    pool.close()
    with pytest.raises(RuntimeError):
        pool.fetch(server.url, timeout=2_000, settle_ms=0)


def test_the_rendering_capability_mark_is_intact():
    assert http_mod.is_rendering_fetcher(http_mod.fetch_rendered)
    assert not http_mod.is_rendering_fetcher(http_mod.fetch_text)
