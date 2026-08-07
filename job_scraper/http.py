"""HTTP fetch helpers."""

from __future__ import annotations

import ssl
import subprocess
import time
from collections.abc import Callable

import certifi
import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import SSLError

try:
    from urllib3.util.ssl_ import create_urllib3_context
except ImportError:  # pragma: no cover
    create_urllib3_context = None  # type: ignore[misc,assignment]

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


def fetch_text(url: str, *, timeout: int = DEFAULT_TIMEOUT, user_agent: str = DEFAULT_USER_AGENT) -> str:
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
    """
    from playwright.sync_api import sync_playwright  # lazy import — rest of module works without playwright

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


# Extractors that want a selector-based wait need to know whether the fetcher
# they were handed renders JavaScript. Comparing identity against
# `fetch_rendered` breaks the moment anything passes a wrapper — the fixture
# capture script wraps it to record URLs, and WP9 will replace it outright.
# Mark the capability instead, and let wrappers copy the mark.
fetch_rendered.renders = True  # type: ignore[attr-defined]


def is_rendering_fetcher(fetch: Callable[..., str]) -> bool:
    """True if *fetch* renders JavaScript, through any number of wrappers."""
    return bool(getattr(fetch, "renders", False))
