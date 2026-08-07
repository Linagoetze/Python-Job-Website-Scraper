"""Experience-level filtering.

Layer 1 — title heuristic: excludes jobs whose title contains seniority
           signal words (Senior, Lead, Director, etc.). Zero extra HTTP
           requests. Configured via rules.json.

Layer 2 — detail-page parsing: fetches each job's detail_url, strips HTML,
           and looks for numeric experience requirements. Jobs requiring
           >= 3 years are excluded; jobs with no requirement or <= 2 years
           are kept. Always runs, but only for jobs not already stored in
           jobs.csv — it is the one layer that costs an HTTP request per job.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from bs4 import BeautifulSoup

from job_scraper import JobRecord
from job_scraper.filtering import (
    _HYBRID_CONFIRMED_REASON,
    _HYBRID_PENDING_REASON,
    _build_title_keyword_pattern,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layer 1 — title heuristic
# ---------------------------------------------------------------------------

_MAX_JUNIOR_YEARS = 2

# Parallel detail-page fetches. Every one is a request to somebody else's career
# site, so lower this if you are scraping a lot of sources or a host starts
# rate-limiting you.
_DETAIL_WORKERS = 10


def _build_seniority_pattern(terms: list[str]) -> re.Pattern[str]:
    escaped = [re.escape(t.strip()) for t in terms if t.strip()]
    alternation = "|".join(escaped)
    return re.compile(rf"\b(?:{alternation})\b", re.IGNORECASE)


def apply_title_filter(
    jobs: list[JobRecord],
    rules: dict[str, Any],
) -> tuple[list[JobRecord], list[JobRecord]]:
    """Split jobs into (kept, excluded) using seniority title signals.

    Does nothing when seniority_filter_enabled is False or
    seniority_exclude_titles is absent/empty.
    Returns (kept_jobs, excluded_jobs).
    """
    if not rules.get("seniority_filter_enabled", True):
        return jobs, []

    terms: list[str] = [
        str(t) for t in (rules.get("seniority_exclude_titles") or []) if str(t).strip()
    ]
    if not terms:
        return jobs, []

    pattern = _build_seniority_pattern(terms)
    kept: list[JobRecord] = []
    excluded: list[JobRecord] = []
    for job in jobs:
        title = str(job.get("title") or "")
        if pattern.search(title):
            excluded.append(job)
        else:
            kept.append(job)
    return kept, excluded


def apply_combined_title_filter(
    jobs: list[JobRecord],
    entries: list[tuple[str, str]],
    rules: dict[str, Any],
) -> tuple[list[JobRecord], list[JobRecord], list[JobRecord]]:
    """Run Layer 1a (keyword) and Layer 1 (seniority) in a single title scan.

    Keyword exclusion is checked first; seniority second.
    Returns (kept, keyword_excluded, seniority_excluded).
    """
    kw_pattern = _build_title_keyword_pattern(entries)

    seniority_enabled = rules.get("seniority_filter_enabled", True)
    terms: list[str] = [
        str(t) for t in (rules.get("seniority_exclude_titles") or []) if str(t).strip()
    ]
    sen_pattern = _build_seniority_pattern(terms) if seniority_enabled and terms else None

    kept: list[JobRecord] = []
    kw_excluded: list[JobRecord] = []
    sen_excluded: list[JobRecord] = []

    for job in jobs:
        title = str(job.get("title") or "")
        if kw_pattern and kw_pattern.search(title):
            kw_excluded.append(job)
        elif sen_pattern and sen_pattern.search(title):
            sen_excluded.append(job)
        else:
            kept.append(job)

    return kept, kw_excluded, sen_excluded


# ---------------------------------------------------------------------------
# Layer 2 — detail-page experience extraction
# ---------------------------------------------------------------------------

_EXPERIENCE_PATTERNS: list[re.Pattern[str]] = [
    # "2+ years of experience", "2 years of experience"
    re.compile(r"\b(\d+)\s*\+?\s*years?\s+of\s+experience\b", re.IGNORECASE),
    # "minimum of 3 years", "minimum 3 years"
    re.compile(r"\bminimum\s+(?:of\s+)?(\d+)\s+years?\b", re.IGNORECASE),
    # "at least 3 years"
    re.compile(r"\bat\s+least\s+(\d+)\s+years?\b", re.IGNORECASE),
    # "1-3 years" — extract lower bound
    re.compile(r"\b(\d+)\s*[-–]\s*\d+\s+years?\b", re.IGNORECASE),
    # "3+ years of/in [field]", "3+ years' experience"
    re.compile(r"\b(\d+)\s*\+\s*years?\b", re.IGNORECASE),
]

_WORD_YEARS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_WORD_YEARS_PATTERN = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten)"
    r"\s+years?\s+(?:of\s+)?experience\b",
    re.IGNORECASE,
)
_MONTHS_PATTERN = re.compile(
    r"\b(\d+)\s*\+?\s*months?\s+(?:of\s+)?experience\b",
    re.IGNORECASE,
)

# PhD is preferred/optional → keep the job regardless of other PhD signals.
_PHD_PREFERRED = re.compile(
    r"\bPhD\b.{0,60}?\b(?:preferred|desired|a\s+plus|an?\s+advantage|beneficial|nice\s+to\s+have|is\s+a\s+bonus|ideally)\b"
    r"|\b(?:preferred|desired|ideally)\b.{0,60}?\bPhD\b",
    re.IGNORECASE | re.DOTALL,
)
# PhD is a hard requirement → exclude the job.
_PHD_REQUIRED = re.compile(
    r"\bPhD\b.{0,60}?\b(?:required|mandatory|essential|necessary)\b"
    r"|\b(?:requires?|must\s+have|must\s+hold|need\s+a|holding\s+a)\b.{0,60}?\bPhD\b"
    r"|\bdoctorate\b.{0,60}?\b(?:required|mandatory|essential)\b",
    re.IGNORECASE | re.DOTALL,
)


def _strip_html(html: str) -> str:
    try:
        return BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)


def _extract_min_years(text: str) -> int | None:
    """Return the minimum years requirement found in text, or None if not found."""
    all_years: list[int] = []
    for pat in _EXPERIENCE_PATTERNS:
        for m in pat.finditer(text):
            try:
                all_years.append(int(m.group(1)))
            except (IndexError, ValueError):
                pass
    for m in _WORD_YEARS_PATTERN.finditer(text):
        n = _WORD_YEARS.get(m.group(1).lower())
        if n is not None:
            all_years.append(n)
    for m in _MONTHS_PATTERN.finditer(text):
        try:
            all_years.append(int(m.group(1)) // 12)
        except (IndexError, ValueError):
            pass
    return min(all_years) if all_years else None


def _has_phd_required(text: str) -> bool:
    """Return True only if the text requires a PhD (not merely preferred or desired)."""
    if _PHD_PREFERRED.search(text):
        return False
    return bool(_PHD_REQUIRED.search(text))


def _fetch_and_analyze(
    job: JobRecord,
    fn: Callable[[str], str],
    hybrid_pattern: re.Pattern[str] | None = None,
) -> tuple[JobRecord, int | None, bool, bool, bool | None]:
    """Fetch a job's detail page and extract experience/PhD signals.

    Returns (job, min_years, phd_required, fetch_failed, hybrid_found).
    min_years is None when no numeric requirement was found or there was no URL.
    hybrid_found is None when the description could not be read at all (no URL,
    fetch failed, or no pattern configured) — the caller decides what that means.
    """
    url = str(job.get("detail_url") or job.get("apply_url") or "").strip()
    if not url:
        return job, None, False, False, None
    try:
        html = fn(url)
        text = _strip_html(html)
        hybrid = bool(hybrid_pattern.search(text)) if hybrid_pattern is not None else None
        return job, _extract_min_years(text), _has_phd_required(text), False, hybrid
    except Exception as exc:
        logger.debug("Layer 2: fetch failed for %r — keeping job. Error: %s", url, exc)
        return job, None, False, True, None


def _resolve_hybrid(job: JobRecord, hybrid_found: bool | None) -> JobRecord | None:
    """Settle a conditional-location job against what the description said.

    Returns the job with its pending marker rewritten to confirmed, or None if it
    must be excluded. Jobs not awaiting a hybrid decision are returned unchanged.

    Unlike the rest of Layer 2 this fails *closed*: a conditional location is out
    of range by default, so a job whose description could not be read has not
    earned its exception.
    """
    reasons = job.get("matched_reasons") or []
    if _HYBRID_PENDING_REASON not in reasons:
        return job
    if not hybrid_found:
        return None
    return dict(
        job,
        matched_reasons=[
            _HYBRID_CONFIRMED_REASON if r == _HYBRID_PENDING_REASON else r for r in reasons
        ],
    )


def apply_detail_filter(
    jobs: list[JobRecord],
    fetch_text: Callable[[str], str],
    source_fetch_map: dict[str, Callable[[str], str]] | None = None,
    hybrid_pattern: re.Pattern[str] | None = None,
) -> tuple[list[JobRecord], list[JobRecord]]:
    """Fetch each job's detail page and filter by experience requirement and PhD.

    Keeps jobs where: no numeric requirement found, or min years <= _MAX_JUNIOR_YEARS,
    and the role does not require a PhD.
    Fails open on fetch/parse errors (job is kept).
    Annotates each job dict with an `experience_level` string.
    source_fetch_map maps source_name → fetch_fn so dynamic sources use the
    correct renderer (e.g. fetch_rendered for Playwright-backed sources).
    hybrid_pattern resolves jobs that Layer 1 admitted provisionally from a
    conditional location: the same fetched description is searched for it, so
    those jobs cost no extra HTTP request. They fail closed — see _resolve_hybrid.
    Fetches run in parallel with up to _DETAIL_WORKERS threads.
    Returns (kept_jobs, excluded_jobs).
    """
    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    fetch_failed = 0
    no_requirement = 0
    hybrid_excluded = 0

    def _task(job: JobRecord) -> tuple[JobRecord, int | None, bool, bool, bool | None]:
        source = str(job.get("source_name") or "")
        fn = (source_fetch_map or {}).get(source, fetch_text)
        return _fetch_and_analyze(job, fn, hybrid_pattern)

    with ThreadPoolExecutor(max_workers=_DETAIL_WORKERS) as pool:
        results = list(pool.map(_task, jobs))

    for job, min_years, phd_req, failed, hybrid_found in results:
        resolved = _resolve_hybrid(job, hybrid_found)
        if resolved is None:
            hybrid_excluded += 1
            excluded.append(dict(job, experience_level="non_hybrid_conditional_location"))
            continue
        job = resolved

        if failed:
            fetch_failed += 1
            kept.append(dict(job, experience_level="unspecified"))
        elif phd_req:
            excluded.append(dict(job, experience_level="phd_required"))
        elif min_years is None:
            no_requirement += 1
            kept.append(dict(job, experience_level="unspecified"))
        elif min_years <= _MAX_JUNIOR_YEARS:
            kept.append(dict(job, experience_level=f"junior (<={_MAX_JUNIOR_YEARS}yr)"))
        else:
            excluded.append(dict(job, experience_level=f"senior ({min_years}+yr)"))

    if jobs:
        logger.debug(
            "Layer 2: %d/%d jobs failed to fetch (kept fail-open); "
            "%d had no numeric requirement; "
            "%d dropped as non-hybrid in a conditional location",
            fetch_failed,
            len(jobs),
            no_requirement,
            hybrid_excluded,
        )
    return kept, excluded
