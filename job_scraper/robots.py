"""robots.txt, fetched once per host and cached for the run (WP10).

These are other people's career sites. Asking them what they permit costs one
small request per host per run, and the answer is remembered for the whole run
however many pages we then read from that host.

Two deliberate choices, both about failing in the right direction:

* **An unreadable robots.txt allows the crawl.** RFC 9309 suggests treating a
  5xx as a site-wide "do not crawl", and for a search engine that is right. Here
  it is not: impactpool.org intermittently 500s (its listing pages do, and its
  robots.txt can too), and a transient error that silently skips a source looks
  exactly like "no vacancies" — the failure mode CLAUDE.md's priority 2 exists to
  forbid. So an unreachable robots.txt is logged as a warning and the source is
  scraped. A *readable* one that says no is obeyed, which is the case that
  actually carries the site owner's intent.
* **A `Disallow` is a hard stop, not a filter.** `RobotsDisallowed` is raised at
  the fetcher, so a disallowed page produces an error rather than an empty
  result that reads like a page with no jobs on it.

A site where the check is wrong — a robots.txt aimed at search engines that
happens to cover the careers path, say — is handled per source in
`sources.yaml` with `ignore_robots: true`, not by switching the check off
globally.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests

logger = logging.getLogger(__name__)

# Short: robots.txt is one small file and a slow one must not hold up a run.
ROBOTS_TIMEOUT = 10


class RobotsDisallowed(RuntimeError):
    """A site's robots.txt forbids the URL we were about to fetch."""


def as_origin(value: str) -> str:
    """A config-supplied host as an origin: `api.example.com` → `https://api.example.com`.

    The override in `sources.yaml` is written by hand, so it accepts what a
    person would type. A value that already carries a scheme is kept as it is,
    because http and https are different origins to `host_of` and a site that
    is exempted on one is not automatically exempted on the other.
    """
    value = value.strip()
    if not value:
        return ""
    if "://" not in value:
        value = f"https://{value}"
    return host_of(value)


def host_of(url: str) -> str:
    """The scheme+netloc a robots.txt applies to ('' for anything unparseable)."""
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return ""
    return urlunsplit((parts.scheme, parts.netloc.lower(), "", "", ""))


@dataclass
class _Rules:
    """One host's answer. `parser` is None when robots.txt could not be read."""

    parser: RobotFileParser | None


class RobotsPolicy:
    """Per-host robots.txt rules, fetched on first use and cached for the run.

    Thread-safe: the detail-page workers and the render threads all consult one
    instance. The per-host lock means concurrent first hits on the same host
    fetch robots.txt once, not once per thread.
    """

    def __init__(
        self,
        user_agent: str,
        overrides: frozenset[str] | set[str] | None = None,
    ) -> None:
        self._user_agent = user_agent
        # Hosts the owner has exempted in sources.yaml. Stored as scheme+netloc,
        # the same shape `host_of` returns, so the lookup is an exact match
        # rather than a substring test that would exempt more than was asked.
        self._overrides = frozenset(overrides or ())
        self._rules: dict[str, _Rules] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    # -- public ---------------------------------------------------------------

    def allows(self, url: str) -> bool:
        """May we fetch *url*? True when exempted, unparseable, or robots.txt is silent."""
        host = host_of(url)
        if not host or host in self._overrides:
            return True
        parser = self._rules_for(host).parser
        if parser is None:
            return True  # unreadable — see the module docstring
        return parser.can_fetch(self._user_agent, url)

    def crawl_delay(self, url: str) -> float | None:
        """The host's own Crawl-delay in seconds, if it states one.

        Honoured by the throttle when it is longer than ours: a site that has
        gone to the trouble of naming a rate is the better authority on what it
        can take.
        """
        host = host_of(url)
        if not host or host in self._overrides:
            return None
        parser = self._rules_for(host).parser
        if parser is None:
            return None
        try:
            delay = parser.crawl_delay(self._user_agent)
        except Exception:  # pragma: no cover - malformed robots.txt
            return None
        return float(delay) if delay is not None else None

    # -- internals ------------------------------------------------------------

    def _rules_for(self, host: str) -> _Rules:
        with self._guard:
            cached = self._rules.get(host)
            if cached is not None:
                return cached
            lock = self._locks.setdefault(host, threading.Lock())
        with lock:
            # Re-checked under the per-host lock: the thread that lost the race
            # takes the winner's answer rather than fetching robots.txt again.
            with self._guard:
                cached = self._rules.get(host)
            if cached is not None:
                return cached
            rules = _Rules(self._fetch(host))
            with self._guard:
                self._rules[host] = rules
            return rules

    def _fetch(self, host: str) -> RobotFileParser | None:
        url = f"{host}/robots.txt"
        try:
            response = requests.get(
                url,
                headers={"User-Agent": self._user_agent},
                timeout=ROBOTS_TIMEOUT,
            )
        except Exception as exc:
            logger.warning("Could not read %s (%s); proceeding as if it allowed us", url, exc)
            return None

        if response.status_code >= 500:
            logger.warning(
                "%s answered %d; proceeding as if it allowed us", url, response.status_code
            )
            return None
        parser = RobotFileParser()
        parser.set_url(url)
        if response.status_code >= 400:
            # No robots.txt is the ordinary case, and it means "no restrictions".
            parser.parse([])
            parser.modified()
            return parser
        parser.parse(response.text.splitlines())
        # `parse` does not stamp the read time and `RobotFileParser.crawl_delay`
        # returns None without one — so a site's stated delay would be silently
        # dropped. Note also that the stdlib parser only accepts a whole number
        # of seconds there.
        parser.modified()
        logger.debug("Read %s", url)
        return parser
