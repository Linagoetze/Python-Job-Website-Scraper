"""WP9: the render pool.

No network: a `http.server` bound to localhost serves the pages, and a real
headless Chromium renders them. The thing under test is Playwright's threading
behaviour, and a mock would assert nothing about it.
"""

from __future__ import annotations

import http.server
import socketserver
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest

from job_scraper import http as http_mod


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
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield state
    finally:
        srv.shutdown()
        srv.server_close()




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
