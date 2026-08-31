"""HTTP fetch helpers.

`render_pool()` keeps one headless Chromium alive per render thread for a whole
run, rather than launching and tearing one down around every page. It is opened
by the pipeline for the length of a run and is optional: call `fetch_rendered`
outside one and it behaves exactly as it did before WP9.
"""

from __future__ import annotations

import logging
import queue
import ssl
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass

import certifi
import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import SSLError

try:
    from urllib3.util.ssl_ import create_urllib3_context
except ImportError:  # pragma: no cover
    create_urllib3_context = None  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "job-scraper/0.1 (+https://example.com; contact=you@example.com)"
)
DEFAULT_TIMEOUT = 30


class _TLSAdapter(HTTPAdapter):
    """Prefer TLS 1.2+ so older system SSL stacks still negotiate with modern hosts."""

    def init_poolmanager(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if create_urllib3_context is not None:
            ctx = create_urllib3_context()
            if hasattr(ssl, "TLSVersion"):
                ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def _session() -> requests.Session:
    s = requests.Session()
    s.mount("https://", _TLSAdapter())
    return s


_SESSION = _session()


def _fetch_text_curl(url: str, *, timeout: int, user_agent: str) -> str:
    """Fallback when the Python SSL stack cannot negotiate with the host (e.g. old LibreSSL)."""
    r = subprocess.run(
        [
            "curl",
            "-fsSL",
            "-A",
            user_agent,
            "--max-time",
            str(timeout),
            url,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip() or f"exit {r.returncode}"
        raise RuntimeError(f"curl failed for {url!r}: {err}")
    return r.stdout


_RETRY_ATTEMPTS = 5


def fetch_text(
    url: str, *, timeout: int = DEFAULT_TIMEOUT, user_agent: str = DEFAULT_USER_AGENT
) -> str:
    """GET *url* and return response text. Raises on HTTP errors.

    Transient 5xx responses are retried (some hosts, e.g. impactpool.org,
    intermittently 500 on pages that succeed moments later).
    """
    headers = {"User-Agent": user_agent}
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            r = _SESSION.get(url, headers=headers, timeout=timeout, verify=certifi.where())
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        except (SSLError, requests.exceptions.Timeout):
            return _fetch_text_curl(url, timeout=timeout, user_agent=user_agent)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status < 500 or attempt == _RETRY_ATTEMPTS:
                raise
            time.sleep(2 * attempt)
    raise AssertionError("unreachable")


# ---------------------------------------------------------------------------
# Rendered fetches (WP9)
# ---------------------------------------------------------------------------

# How many Chromiums a run may hold at once. Rendered fetches no longer run on
# the Layer 5 detail pool (_DETAIL_WORKERS = 10): a browser cannot be driven
# from a thread other than the one that created it, so ten detail workers
# rendering pages meant ten browsers. Four dedicated render threads bound that,
# and each keeps its browser for the whole run.
_RENDER_WORKERS = 4

# How long close() waits for a render thread to finish its page and shut its
# browser down. Long enough for one in-flight page, short enough that a wedged
# Chromium cannot hold the run open indefinitely — the threads are daemons, so
# a straggler cannot outlive the process either.
_RENDER_SHUTDOWN_TIMEOUT = 60.0


@dataclass(frozen=True)
class _RenderRequest:
    url: str
    timeout: int
    settle_ms: int
    wait_for_selector: str | None
    future: Future


class RenderPool:
    """A fixed set of render threads, each owning one Chromium for the whole run.

    Playwright's sync API binds every object to the greenlet of the thread that
    created it: passing a `Browser` to another thread raises "Cannot switch to a
    different thread" the moment it is touched. So a single browser shared by
    the callers is not available to us at all, and "one context per worker
    thread" implies one *browser* per worker thread.

    Hence this shape. Callers — the source loop on the main thread, the Layer 5
    detail workers on theirs — hand a URL to the queue and block on a Future.
    The rendering happens on a render thread that owns its browser from first
    use to `close()`, so the run launches at most `_RENDER_WORKERS` browsers
    however many pages it renders, and each page costs a fresh context rather
    than a fresh process.

    Threads start on the first fetch, so a run with no dynamic source launches
    nothing.
    """

    def __init__(
        self, workers: int = _RENDER_WORKERS, user_agent: str = DEFAULT_USER_AGENT
    ) -> None:
        self._queue: queue.Queue[_RenderRequest | None] = queue.Queue()
        self._workers = max(1, workers)
        self._user_agent = user_agent
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._closed = False
        self.launches = 0
        self.pages_rendered = 0

    def fetch(
        self,
        url: str,
        *,
        timeout: int,
        settle_ms: int,
        wait_for_selector: str | None = None,
    ) -> str:
        """Render *url* on a render thread and return its HTML. Raises what the render raised."""
        self._start()
        future: Future = Future()
        self._queue.put(_RenderRequest(url, timeout, settle_ms, wait_for_selector, future))
        return future.result()

    def close(self) -> None:
        """Stop the render threads and close their browsers. Safe to call twice."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            threads = list(self._threads)
        # A single sentinel, relayed thread to thread (see _serve). Queueing one
        # per thread instead lets the idle threads take them all while another
        # is mid-render, and that one then blocks on an empty queue for ever.
        self._queue.put(None)
        for t in threads:
            t.join(timeout=_RENDER_SHUTDOWN_TIMEOUT)
            if t.is_alive():
                logger.warning("Render thread %s did not shut down in time", t.name)
        # Callers are expected to have finished before close() — the pipeline's
        # ExitStack unwinds after the detail pool has joined. Should one race in
        # anyway, its request would sit behind the sentinel with no thread left
        # to serve it, and the caller would block on that Future for ever.
        self._fail_queued(RuntimeError("render pool closed while this fetch was queued"))

    # -- internals ---------------------------------------------------------

    def _start(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("render pool is closed")
            if self._threads:
                return
            for i in range(self._workers):
                t = threading.Thread(target=self._run, name=f"render-{i}", daemon=True)
                t.start()
                self._threads.append(t)

    def _run(self) -> None:
        try:
            from playwright.sync_api import (
                sync_playwright,  # lazy — the rest of the module works without playwright
            )

            with sync_playwright() as pw:
                self._serve(pw)
        except BaseException as exc:  # noqa: BLE001 - the driver itself failed to start
            # Nothing can be rendered on this thread. Fail every request it
            # would have taken, loudly, rather than leaving callers blocked on a
            # Future that will never be set.
            self._fail_pending(exc)

    def _serve(self, pw) -> None:  # type: ignore[no-untyped-def]
        browser = None
        try:
            while True:
                req = self._queue.get()
                if req is None:
                    self._queue.put(None)  # pass the sentinel on to the next thread
                    return
                if not req.future.set_running_or_notify_cancel():
                    continue
                try:
                    # Launched on first use and kept. Relaunched only if Chromium
                    # actually died, which would otherwise fail every remaining
                    # page on this thread rather than just the one that crashed.
                    if browser is None or not browser.is_connected():
                        browser = pw.chromium.launch(headless=True)
                        with self._lock:
                            self.launches += 1
                    req.future.set_result(self._render(browser, req))
                except BaseException as exc:  # noqa: BLE001 - belongs to the caller
                    req.future.set_exception(exc)
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception as exc:  # pragma: no cover - teardown, never fatal
                    logger.debug("Closing a render browser failed: %s", exc)

    def _render(self, browser, req: _RenderRequest) -> str:  # type: ignore[no-untyped-def]
        # A context per fetch, not a browser per fetch: a fresh context is as
        # isolated (its own cookies and storage) at roughly a tenth of the cost.
        context = browser.new_context(user_agent=self._user_agent)
        try:
            page = context.new_page()
            page.goto(req.url, wait_until="domcontentloaded", timeout=req.timeout)
            if req.wait_for_selector:
                try:
                    page.wait_for_selector(req.wait_for_selector, timeout=req.timeout)
                except Exception:
                    pass  # selector never appeared — fall through to settle_ms
            page.wait_for_timeout(req.settle_ms)
            html = page.content()
            with self._lock:
                self.pages_rendered += 1
            return html
        finally:
            context.close()

    def _fail_queued(self, exc: BaseException) -> None:
        """Empty the queue without blocking, failing anything still waiting in it."""
        while True:
            try:
                req = self._queue.get_nowait()
            except queue.Empty:
                return
            if req is not None and req.future.set_running_or_notify_cancel():
                req.future.set_exception(exc)

    def _fail_pending(self, exc: BaseException) -> None:
        while True:
            req = self._queue.get()
            if req is None:
                self._queue.put(None)  # pass the sentinel on to the next thread
                return
            if req.future.set_running_or_notify_cancel():
                req.future.set_exception(exc)


_POOL_LOCK = threading.Lock()
_POOL: RenderPool | None = None
_POOL_DEPTH = 0


@contextmanager
def render_pool(
    workers: int = _RENDER_WORKERS, user_agent: str = DEFAULT_USER_AGENT
) -> Iterator[RenderPool]:
    """Reuse browsers across every `fetch_rendered` call made inside the block.

    Nests: an inner block joins the open pool rather than starting a second one,
    and only the outermost exit closes it.
    """
    global _POOL, _POOL_DEPTH

    with _POOL_LOCK:
        if _POOL is None:
            _POOL = RenderPool(workers=workers, user_agent=user_agent)
        _POOL_DEPTH += 1
        pool = _POOL
    try:
        yield pool
    finally:
        with _POOL_LOCK:
            _POOL_DEPTH -= 1
            outermost = _POOL_DEPTH == 0
            if outermost:
                _POOL = None
        if outermost:
            pool.close()
            if pool.launches:
                # Reported at INFO, and only when something was actually
                # rendered: this count is the whole point of WP9, and before it
                # the number was one per page with nothing saying so.
                logger.info(
                    "Render pool: %d browser launch(es) for %d rendered page(s)",
                    pool.launches,
                    pool.pages_rendered,
                )


def _render_once(
    url: str, *, timeout: int, settle_ms: int, wait_for_selector: str | None
) -> str:
    """Render one page in a browser of its own. The no-pool path: a script, a test, a one-off."""
    from playwright.sync_api import (
        sync_playwright,  # lazy import — rest of module works without playwright
    )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=DEFAULT_USER_AGENT)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            if wait_for_selector:
                try:
                    page.wait_for_selector(wait_for_selector, timeout=timeout)
                except Exception:
                    pass  # selector never appeared — fall through to settle_ms
            page.wait_for_timeout(settle_ms)
            return page.content()
        finally:
            browser.close()


def fetch_rendered(
    url: str,
    *,
    timeout: int = 30_000,
    settle_ms: int = 4_000,
    wait_for_selector: str | None = None,
) -> str:
    """Render *url* with headless Chromium and return the fully-rendered HTML.

    Uses domcontentloaded + an optional CSS selector wait, then a fixed settle
    delay rather than networkidle (which hangs on long-polling connections).
    If *wait_for_selector* is given, Playwright waits until that element appears
    before the settle delay — useful for JS-heavy pages like Workday.

    Inside a `render_pool()` block this borrows a running browser and pays only
    for a new context. Outside one it launches and tears down its own, exactly
    as it did before WP9.
    """
    with _POOL_LOCK:
        pool = _POOL
    if pool is not None:
        return pool.fetch(
            url, timeout=timeout, settle_ms=settle_ms, wait_for_selector=wait_for_selector
        )
    return _render_once(
        url, timeout=timeout, settle_ms=settle_ms, wait_for_selector=wait_for_selector
    )


# Extractors that want a selector-based wait need to know whether the fetcher
# they were handed renders JavaScript. Comparing identity against
# `fetch_rendered` breaks the moment anything passes a wrapper — the fixture
# capture script wraps it to record URLs. Mark the capability instead, and let
# wrappers copy the mark.
fetch_rendered.renders = True  # type: ignore[attr-defined]


def is_rendering_fetcher(fetch: Callable[..., str]) -> bool:
    """True if *fetch* renders JavaScript, through any number of wrappers."""
    return bool(getattr(fetch, "renders", False))
