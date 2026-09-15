"""Normalize job URLs so they are valid https links."""

from __future__ import annotations

import re
from urllib.parse import urlparse

_OATLY_LOCALE = re.compile(r"careers\.oatly\.com/(?P<locale>[a-z]{2}-[A-Z]{2})/", re.I)
_OATLY_JOB_ID = re.compile(r"careers\.oatly\.com/(?:[a-z]{2}-[A-Z]{2}/)?jobs/(\d+)", re.I)


def normalize_http_url(url: str) -> str:
    """Strip whitespace; upgrade http→https for http(s) URLs."""
    u = url.strip()
    if not u:
        return u
    if u.startswith("http://"):
        u = "https://" + u[len("http://") :]
    return u


def oatly_canonical_job_url(listing_url: str, resolved_href: str) -> str:
    """
    Prefer locale-prefixed job URLs when the listing page is under e.g. /en-GB/jobs,
    so links match the site's canonical pattern (both forms usually work; this is clearer).
    """
    u = normalize_http_url(resolved_href)
    m = _OATLY_LOCALE.search(listing_url)
    if not m:
        return u
    locale = m.group("locale")
    path = urlparse(u).path
    if not path.startswith("/jobs/"):
        return u
    if f"/{locale}/jobs/" in u:
        return u
    return f"https://careers.oatly.com/{locale}{path}"


def canonical_detail_url(source_name: str, listing_url: str, detail_url: str) -> str:
    """Apply source-specific canonicalization (e.g. Oatly locale in job path)."""
    u = normalize_http_url(detail_url)
    if source_name.strip().lower() == "oatly":
        return oatly_canonical_job_url(listing_url, u)
    return u


def dedupe_key_from_url(url: str) -> str:
    """
    Stable key for CSV deduplication: Oatly jobs are keyed by numeric ID so
    slug variants of the same posting are treated as one row.
    """
    u = normalize_http_url(url)
    m = _OATLY_JOB_ID.search(u)
    if m:
        return f"oatly:job:{m.group(1)}"
    return u


# Hosts that put every customer on one hostname, where the first path segment
# names the employer's board. Without this, a brand-new Greenhouse employer
# would match the six Greenhouse boards already in sources.yaml — and in SP3
# and SP7 would be reported as already tombstoned, which is worse than a
# missed duplicate. Platforms that give each employer its own subdomain
# (Teamtailor, Breezy, Personio, Recruitee) need no entry: the host is already
# the identity.
_SHARED_BOARD_HOSTS = frozenset(
    {
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "boards.eu.greenhouse.io",
        "job-boards.eu.greenhouse.io",
        "jobs.ashbyhq.com",
        "apply.workable.com",
        "jobs.workable.com",
        "careers.smartrecruiters.com",
        "jobs.smartrecruiters.com",
        "jobs.lever.co",
        "jobs.eu.lever.co",
        "jobs.jobvite.com",
        "jobs.recruitee.com",
    }
)

# Workday gives each *tenant* a subdomain and each board a path segment, and a
# tenant can host another brand's board (sources.yaml has Busuu under Chegg's).
# Treating the path segment as part of the identity therefore errs towards
# "different board", which is the safe direction here.
_SHARED_BOARD_HOST_SUFFIXES = (".myworkdayjobs.com",)

_LOCALE_SEGMENT = re.compile(r"^[a-z]{2}([-_][a-z]{2,3})?$", re.I)


def _is_shared_board_host(host: str) -> bool:
    return host in _SHARED_BOARD_HOSTS or host.endswith(_SHARED_BOARD_HOST_SUFFIXES)


def board_identity(url: str) -> str:
    """Key identifying the employer *board* a source URL points at.

    `https://job-boards.greenhouse.io/canonical/jobs/123?utm=x` and
    `http://www.job-boards.greenhouse.io/canonical/` are the same board;
    `.../dimagi` is a different one. Single-tenant hosts are their own
    identity, so any two URLs on `careers.oatly.com` match whatever their path.

    Raises ValueError when *url* has no host, so callers can tell "not a URL"
    from "a URL that matches nothing".
    """
    u = normalize_http_url(url)
    if "://" not in u:
        # Accept a bare host the way a person types it into a search box.
        u = "https://" + u.lstrip("/")
    host = (urlparse(u).hostname or "").strip(".").removeprefix("www.")
    if not host or "." not in host:
        raise ValueError(f"no host in URL: {url!r}")
    if not _is_shared_board_host(host):
        return host
    segments = [s for s in urlparse(u).path.split("/") if s]
    # Only ever strip a locale when a segment survives it: a two-letter board
    # slug looks exactly like a language code, and dropping it would collapse
    # two different boards onto the bare host.
    while len(segments) > 1 and _LOCALE_SEGMENT.match(segments[0]):
        segments.pop(0)
    if not segments:
        return host
    return f"{host}/{segments[0].casefold()}"


def same_board(left: str, right: str) -> bool:
    """True when both URLs name the same employer board. False if either is not a URL."""
    try:
        return board_identity(left) == board_identity(right)
    except ValueError:
        return False
