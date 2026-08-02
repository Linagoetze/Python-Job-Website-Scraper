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
