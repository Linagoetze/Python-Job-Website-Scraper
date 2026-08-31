"""HTTP fetch helpers.

Two shared, run-scoped resources live here. Both are opened by the pipeline for
the length of a run and both are optional: call the fetchers without them and
they behave exactly as they did before WP9.

* `render_pool()` keeps one headless Chromium alive per render thread for the
  whole run, rather than launching and tearing one down around every page.
* `http_cache()` puts a short-TTL, ETag-aware cache in front of `fetch_text`,
  so a re-run inside the TTL costs no network round trip at all.
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
from pathlib import Path
from typing import TYPE_CHECKING

import certifi
import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import SSLError

try:
    from urllib3.util.ssl_ import create_urllib3_context
except ImportError:  # pragma: no cover
    create_urllib3_context = None  # type: ignore[misc,assignment]

if TYPE_CHECKING:  # playwright is imported lazily, so its types are too
    from playwright.sync_api import Browser, Playwright

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


# ---------------------------------------------------------------------------
# Response cache (WP9)
# ---------------------------------------------------------------------------

# Short by design. The cache exists so that the runs the owner fires in quick
# succession — add a source, tweak rules.json, run again — do not re-download
# fifty career pages each time. A scheduled daily run is outside this window and
# revalidates against the site as usual.
DEFAULT_CACHE_TTL = 30 * 60  # seconds


@dataclass
class CacheStats:
    """What the cache did this run, so its effect is never invisible in a log."""

    hits: int = 0  # served from disk, no network at all
    revalidated: int = 0  # conditional request, server answered 304
    misses: int = 0  # fetched in full

    def summary(self) -> str:
        return (
            f"HTTP cache: {self.hits} hit, {self.revalidated} revalidated (304), "
            f"{self.misses} fetched"
        )


_CACHE_STATS = CacheStats()
_STATS_LOCK = threading.Lock()


def cache_stats() -> CacheStats:
    """A snapshot of this run's cache outcomes."""
    with _STATS_LOCK:
        return CacheStats(_CACHE_STATS.hits, _CACHE_STATS.revalidated, _CACHE_STATS.misses)


def default_http_cache_path() -> Path:
    """Where the response cache lives. Named `.sqlite3` so `.gitignore` already covers it."""
    from job_scraper.config_loader import default_data_dir  # local: keep import-time deps thin

    return default_data_dir() / "http_cache.sqlite3"


def _build_cached_session(path: Path, ttl: int) -> requests.Session:
    from requests_cache import CachedSession

    # cache_control is deliberately *off*. Six of the fourteen listing pages
    # sampled in WP9 answer `no-cache, no-store`, which is a blanket CDN default
    # aimed at browsers holding sensitive pages, not a statement about a public
    # jobs list. Honouring it would mean re-downloading those six on every run —
    # i.e. more load on somebody else's server, not less, which is the wrong way
    # round for priority 3. Our own short TTL governs freshness instead.
    #
    # Turning it off does not cost conditional requests: requests-cache still
    # sends If-None-Match / If-Modified-Since from a stored ETag or
    # Last-Modified once the TTL lapses, and counts the 304 as a hit.
    session = CachedSession(
        # The path verbatim, suffix and all: requests-cache appends `.sqlite`
        # to an extension-less name, which would land the file outside
        # `.gitignore`'s `data/*.sqlite3` and commit somebody's career pages.
        cache_name=str(path),
        backend="sqlite",
        expire_after=ttl,
        cache_control=False,
        # stale_if_error stays OFF, deliberately. It would hand back the previous
        # copy when a site errors, which keeps a run going but reports a
        # successful scrape of a page that is minutes or hours old — priority 2
        # says a broken site must fail, not be papered over. impactpool.org's
        # intermittent 500s are already handled where they should be, by
        # fetch_text's 5xx retry; what that retry cannot rescue is a genuine
        # outage, and a genuine outage is exactly what the owner wants to see.
        stale_if_error=False,
    )
    session.mount("https://", _TLSAdapter())
    return session


_SESSION_LOCK = threading.Lock()
_SESSION: requests.Session = _session()


@contextmanager
def http_cache(
    path: Path | None = None, ttl: int = DEFAULT_CACHE_TTL
) -> Iterator[CacheStats]:
    """Route `fetch_text` through an on-disk response cache for the duration of the block.

    Yields the live `CacheStats` so the caller can report what the cache did.
    Expired rows are pruned on the way out, which is what keeps the file from
    growing without bound across runs.
    """
    global _SESSION

    if ttl < 0:
        # requests-cache reads -1 as NEVER_EXPIRE: every page would be served
        # from disk for ever and the run would stop seeing new postings. Other
        # negatives mean "do not cache", so the whole range is refused rather
        # than the one value that bites.
        raise ValueError(
            f"http_cache ttl must be zero or more seconds, not {ttl}. "
            "A negative TTL means 'never expire' to requests-cache; to switch "
            "the cache off, do not open this block at all."
        )

    path = path or default_http_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cached = _build_cached_session(path, ttl)

    with _STATS_LOCK:
        _CACHE_STATS.hits = _CACHE_STATS.revalidated = _CACHE_STATS.misses = 0
    with _SESSION_LOCK:
        previous, _SESSION = _SESSION, cached
    try:
        yield _CACHE_STATS
    finally:
        with _SESSION_LOCK:
            _SESSION = previous
        try:
            cached.cache.delete(expired=True)  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover - housekeeping must never fail a run
            logger.debug("Could not prune the response cache: %s", exc)
        cached.close()


def _record_cache_outcome(url: str, response: requests.Response) -> None:
    """Count what the cache did with one response.

    Note what is *not* here: a stale category. With stale_if_error off, a page
    the cache hands back is one the site either served this run or confirmed
    with a 304 this run. A site that is down produces an exception, not a row.
    """
    del url  # kept in the signature so a future outcome can be logged per URL
    from_cache = bool(getattr(response, "from_cache", False))
    revalidated = bool(getattr(response, "revalidated", False))

    with _STATS_LOCK:
        if not from_cache:
            _CACHE_STATS.misses += 1
        elif revalidated:
            # A 304. Deliberately not read off `is_expired`: revalidating
            # refreshes the entry and then re-applies the TTL, so at a very short
            # one a perfectly healthy revalidation still looks expired.
            _CACHE_STATS.revalidated += 1
        else:
            _CACHE_STATS.hits += 1


def _fetch_text_curl(url: str, *, timeout: int, user_agent: str) -> str:
    """Fallback when the Python SSL stack cannot negotiate with the host (e.g. old LibreSSL).

    Deliberately uncached: it is the rare escape hatch, not a path worth teaching
    about ETags.
    """
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

    Inside an `http_cache()` block this may be answered from disk, or with a
    conditional request the site closes with a 304, in which case no body
    crosses the network. Outside one it is a plain request, as before. Either
    way a site that fails still raises: the cache never stands in for an error.
    """
    headers = {"User-Agent": user_agent}
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        with _SESSION_LOCK:
            session = _SESSION
        try:
            r = session.get(url, headers=headers, timeout=timeout, verify=certifi.where())
            r.raise_for_status()
            _record_cache_outcome(url, r)
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
    future: Future[str]


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
        self._drivers_failed = 0

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
        future: Future[str] = Future()
        # The closed-check and the put happen under one lock, and close() sets
        # the flag under that same lock before queueing its sentinel. Without
        # that, a caller could enqueue behind the sentinel after the threads had
        # gone and block on a Future nobody was left to set — a hang, which is
        # the failure shape this project likes least.
        with self._lock:
            if self._closed:
                raise RuntimeError("render pool is closed")
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
            self._retire(exc)

    def _retire(self, exc: BaseException) -> None:
        """This thread's driver never started. Stand down — unless nobody is left.

        The tempting thing is to keep taking requests and fail them, so no caller
        can block. That is actively harmful while other threads are healthy: a
        thread that fails a page instantly out-races three that take seconds to
        render one, so a single bad driver would fail most of the run's pages
        with three good browsers sitting idle. Retire instead, and let the
        healthy threads do the work more slowly.

        Only when *every* thread has failed is there nobody to serve the queue,
        and then the last one out fails what is waiting so callers get an
        exception rather than a hang.
        """
        with self._lock:
            self._drivers_failed += 1
            none_left = self._drivers_failed >= self._workers
        logger.warning(
            "Render thread %s could not start Playwright and has stood down%s: %s",
            threading.current_thread().name,
            "" if none_left else "; the remaining threads carry on",
            exc,
        )
        if none_left:
            logger.error("No render thread could start Playwright; rendered pages will fail")
            self._fail_pending(exc)

    def _serve(self, pw: Playwright) -> None:
        browser: Browser | None = None
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

    def _render(self, browser: Browser, req: _RenderRequest) -> str:
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
