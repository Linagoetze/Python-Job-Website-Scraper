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
import urllib.robotparser
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests

logger = logging.getLogger(__name__)

# Short: robots.txt is one small file and a slow one must not hold up a run.
ROBOTS_TIMEOUT = 10


def _plain_get(url: str, user_agent: str, timeout: int) -> tuple[int, str]:
    """The default robots.txt fetch: no shared session, no TLS fallback."""
    response = requests.get(url, headers={"User-Agent": user_agent}, timeout=timeout)
    return response.status_code, response.text


class RobotsDisallowed(RuntimeError):
    """A site's robots.txt forbids the URL we were about to fetch.

    `url` is that URL, as a field rather than only as words in the message, so
    a caller reporting the refusal (`sources probe`) never has to parse the
    wording. Optional only so a bare `RobotsDisallowed("...")` still works.
    """

    def __init__(self, message: str, *, url: str | None = None) -> None:
        super().__init__(message)
        self.url = url


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
    # The site answered 4xx: there is no robots.txt, which means no restrictions.
    absent: bool = False


@dataclass(frozen=True)
class RobotsVerdict:
    """`RobotsPolicy.allows`, with its working shown.

    `allowed` is always the policy's own answer. `rule` is the line of
    robots.txt that decided it, as the parser holds it (`Disallow: /careers`),
    and `group` the `User-agent:` lines it sits under; both are None when no
    line decided — an unreadable file, an exemption, or a file with nothing to
    say about this URL — and `reason` says which.
    """

    url: str
    robots_url: str
    user_agent: str
    allowed: bool
    reason: str
    rule: str | None = None
    group: str | None = None
    crawl_delay: float | None = None


def _robots_path(url: str) -> str:
    """The path-and-query `RobotFileParser.can_fetch` compares rules against."""
    parts = urlsplit(url)
    path = urlunsplit(("", "", parts.path, parts.query, parts.fragment))
    # The stdlib has renamed this between patch releases: 3.13.15 calls it
    # `normalize_uri`, 3.13.12 `normalize_path`, and older ones had neither.
    normalise = getattr(urllib.robotparser, "normalize_uri", None) or getattr(
        urllib.robotparser, "normalize_path", None
    )
    path = normalise(path) if normalise is not None else quote(unquote(path))
    return path or "/"


# `explain` has to find the group and the line the parser itself used, and the
# stdlib changed how it does both in a patch release. Up to 3.13.12 the first
# group naming the agent wins, else the `*` group kept aside as
# `default_entry`, and the first matching line decides. From 3.13.15 (RFC 9309)
# groups are looked up through `_find_entry`, `*` is one of them, and the
# longest matching line decides, an Allow winning a tie. These two helpers
# follow whichever the running parser has. Both are private stdlib details,
# which is why `explain` checks its finding against `allows()` and never
# quotes a line that disagrees with it.


def _deciding_group(parser: RobotFileParser, user_agent: str) -> Any:
    find = getattr(parser, "_find_entry", None)
    if find is not None:
        return find(user_agent)
    for entry in parser.entries:
        if entry.applies_to(user_agent):
            return entry
    return getattr(parser, "default_entry", None)


def _deciding_line(entry: Any, path: str) -> tuple[Any, str]:
    best, best_length = None, 0
    for line in entry.rulelines:
        match = line.applies_to(path)
        if isinstance(match, bool):
            if match:
                return line, "the first matching line decides"
            continue
        if match > best_length or (
            match == best_length and best is not None and not best.allowance
        ):
            best, best_length = line, match
    return best, "the most specific matching line decides"


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
        fetch: Callable[[str, str, int], tuple[int, str]] | None = None,
    ) -> None:
        self._user_agent = user_agent
        # How to GET a robots.txt, as (url, user_agent, timeout) -> (status,
        # text). `http.polite_fetching` passes its own, which carries the
        # certificate bundle and the curl fallback the fetchers use; without it
        # the check would fail on precisely the hosts with awkward TLS and read
        # as "no restrictions". The plain default keeps this module usable on
        # its own, and importable without importing `http`.
        self._fetch_robots = fetch or _plain_get
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

    def explain(self, url: str) -> RobotsVerdict:
        """Why *url* is or is not allowed, quoting the rule that decided.

        For `sources probe`, which has to show its reasoning to someone deciding
        whether to add a source. The answer itself is `allows`, unchanged; this
        only finds the line behind it, the same way `can_fetch` walks the file —
        first group naming our product token, else the `*` group, first
        matching line wins. If that walk ever disagrees with `allows`, the rule
        is reported as unidentified rather than quoted wrongly.
        """
        host = host_of(url)
        robots_url = f"{host}/robots.txt" if host else ""
        verdict = {"url": url, "robots_url": robots_url, "user_agent": self._user_agent}
        if not host:
            return RobotsVerdict(**verdict, allowed=True, reason="not an http(s) URL")
        if host in self._overrides:
            return RobotsVerdict(
                **verdict, allowed=True, reason="exempted by ignore_robots in sources.yaml"
            )
        parser = self._rules_for(host).parser
        if parser is None:
            return RobotsVerdict(
                **verdict,
                allowed=True,
                reason="robots.txt could not be read; an unreadable file allows the crawl "
                "(see job_scraper/robots.py)",
            )
        if self._rules_for(host).absent:
            return RobotsVerdict(
                **verdict,
                allowed=True,
                reason="the host has no robots.txt (4xx), so nothing is restricted",
            )
        allowed = self.allows(url)
        delay = self.crawl_delay(url)
        path = _robots_path(url)
        if path == "/robots.txt":
            return RobotsVerdict(
                **verdict, allowed=allowed, reason="robots.txt itself is always allowed"
            )
        entry = _deciding_group(parser, self._user_agent)
        if entry is None:
            return RobotsVerdict(
                **verdict,
                allowed=allowed,
                reason="robots.txt has no group for this user agent and no `*` group",
                crawl_delay=delay,
            )
        group = "\n".join(f"User-agent: {agent}" for agent in entry.useragents)
        line, how = _deciding_line(entry, path)
        if line is None:
            decided, rule, reason = True, None, "no line in the matching group covers this path"
        else:
            decided, rule, reason = bool(line.allowance), str(line), how
        if decided != allowed:
            rule, reason = None, "the deciding line could not be identified"
        return RobotsVerdict(
            **verdict, allowed=allowed, reason=reason, rule=rule, group=group, crawl_delay=delay
        )

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
            rules = self._fetch(host)
            with self._guard:
                self._rules[host] = rules
            return rules

    def _fetch(self, host: str) -> _Rules:
        url = f"{host}/robots.txt"
        try:
            status, text = self._fetch_robots(url, self._user_agent, ROBOTS_TIMEOUT)
        except Exception as exc:
            logger.warning("Could not read %s (%s); proceeding as if it allowed us", url, exc)
            return _Rules(None)

        if status >= 500:
            logger.warning("%s answered %d; proceeding as if it allowed us", url, status)
            return _Rules(None)
        parser = RobotFileParser()
        parser.set_url(url)
        if status >= 400:
            # No robots.txt is the ordinary case, and it means "no restrictions".
            parser.parse([])
            parser.modified()
            return _Rules(parser, absent=True)
        parser.parse(text.splitlines())
        # `parse` does not stamp the read time and `RobotFileParser.crawl_delay`
        # returns None without one — so a site's stated delay would be silently
        # dropped. Note also that the stdlib parser only accepts a whole number
        # of seconds there.
        parser.modified()
        logger.debug("Read %s", url)
        return _Rules(parser)
