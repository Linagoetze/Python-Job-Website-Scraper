"""Experience-level filtering.

Layer 3 — title heuristic: excludes jobs whose title contains seniority
           signal words (Senior, Lead, Director, etc.). Zero extra HTTP
           requests. Configured via rules.json.

Layer 5 — detail-page parsing: fetches each job's detail_url, strips HTML,
           and looks for numeric experience requirements. Jobs requiring
           >= 3 years are excluded; jobs with no requirement or <= 2 years
           are kept. Always runs, but only for jobs not already in the store
           — it is the one layer that costs an HTTP request per job.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup

from job_scraper import JobRecord
from job_scraper.drops import LAYER_DETAIL, layer_short
from job_scraper.filtering import (
    _HYBRID_CONFIRMED_REASON,
    _HYBRID_PENDING_REASON,
    _UNRESOLVED_CONFIRMED_REASON,
    _UNRESOLVED_PENDING_REASON,
    DROP_RULE_KEY,
    _build_title_keyword_pattern,
    build_title_keyword_matchers,
    title_keyword_rule,
)
from job_scraper.storage.db import utc_now_iso

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layer 3 — title heuristic
# ---------------------------------------------------------------------------

_MAX_JUNIOR_YEARS = 2

# Stored for WP7's LLM scoring stage. A job posting's full text rarely runs
# past a few thousand characters; this is generous headroom without inviting
# an oversized row for the rare page that embeds unrelated boilerplate.
_MAX_DESCRIPTION_CHARS = 20_000

# Parallel detail-page fetches. Every one is a request to somebody else's career
# site, so lower this if you are scraping a lot of sources or a host starts
# rate-limiting you.
#
# This caps *static* fetches only. A detail page belonging to a dynamic source
# is rendered, and since WP9 a worker that needs one hands it to http.py's
# render pool and blocks: rendered fetches are capped by _RENDER_WORKERS there,
# not by this number, because a Chromium cannot be driven from a thread other
# than the one that launched it.
# Threads reading detail pages. This is a cap on *us*, not on any one site:
# since WP10 a host allows only http.DEFAULT_PER_HOST_REQUESTS of these through
# at a time, spaced by http.DEFAULT_HOST_DELAY, so ten workers means ten
# different employers making progress at once rather than ten requests landing
# on one. Threads that queue behind a busy host cost nothing but a wait.
_DETAIL_WORKERS = 10


# Layer 5 rule strings for the exclusion log. The years case is formatted with
# the number that fired, so "3+ years" and "8+ years" are separable when the
# owner asks whether the threshold is set too low.
RULE_PHD_REQUIRED = "phd: required (not merely preferred)"
RULE_HYBRID_NOT_HYBRID = "hybrid: conditional city, description is not hybrid"
RULE_HYBRID_UNVERIFIED = "hybrid: conditional city, could not read the description"
# WP8d's two, deliberately separate from the location rules Layer 1 owns: a job
# that got this far was never rejected for being in the wrong city, it was read
# and found to name no listed one.
RULE_LOCATION_NOT_LISTED = "location: unresolvable field, description names no listed place"
RULE_LOCATION_UNVERIFIED = "location: unresolvable field, could not read the description"

# Marks an exclusion this run could not actually verify — no URL, a fetch or
# parse error, or no pattern configured — as opposed to one that was checked and
# failed. Both deferred states (hybrid and unresolvable location) fail closed, so
# both can be dropped by a network hiccup, and neither may be persisted as a
# permanent 'rejected'. One key, because it is one fact about the run rather than
# two facts about two filters (WP8d; was `hybrid_unverified` in WP5/WP6).
UNVERIFIED_KEY = "unverified_this_run"


def _build_seniority_pattern(terms: list[str]) -> re.Pattern[str]:
    escaped = [re.escape(t.strip()) for t in terms if t.strip()]
    alternation = "|".join(escaped)
    return re.compile(rf"\b(?:{alternation})\b", re.IGNORECASE)


def _build_seniority_matchers(terms: list[str]) -> list[tuple[str, str, re.Pattern[str]]]:
    """Per-term patterns for naming which seniority word excluded a job.

    Whole-word matching, exactly as `_build_seniority_pattern` does it — the
    same shape as the title-keyword matchers, so both report a (term, match
    type) pair and neither is rebuilt inside a loop.
    """
    return build_title_keyword_matchers([(t.strip(), "word") for t in terms if t.strip()])


def _seniority_rule(title: str, matchers: list[tuple[str, str, re.Pattern[str]]]) -> str:
    return title_keyword_rule(title, matchers, prefix="seniority")


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
    matchers = _build_seniority_matchers(terms)
    kept: list[JobRecord] = []
    excluded: list[JobRecord] = []
    for job in jobs:
        title = str(job.get("title") or "")
        if pattern.search(title):
            excluded.append(dict(job, **{DROP_RULE_KEY: _seniority_rule(title, matchers)}))
        else:
            kept.append(job)
    return kept, excluded


def apply_combined_title_filter(
    jobs: list[JobRecord],
    entries: list[tuple[str, str]],
    rules: dict[str, Any],
) -> tuple[list[JobRecord], list[JobRecord], list[JobRecord]]:
    """Run Layer 2 (keyword) and Layer 3 (seniority) in a single title scan.

    Keyword exclusion is checked first; seniority second. Excluded jobs carry
    the exact term that fired under DROP_RULE_KEY; the per-term matchers are
    built once here, alongside the combined patterns, and consulted only for
    the jobs those patterns reject.
    Returns (kept, keyword_excluded, seniority_excluded).
    """
    kw_pattern = _build_title_keyword_pattern(entries)
    kw_matchers = build_title_keyword_matchers(entries)

    seniority_enabled = rules.get("seniority_filter_enabled", True)
    terms: list[str] = [
        str(t) for t in (rules.get("seniority_exclude_titles") or []) if str(t).strip()
    ]
    sen_pattern = _build_seniority_pattern(terms) if seniority_enabled and terms else None
    sen_matchers = _build_seniority_matchers(terms) if sen_pattern is not None else []

    kept: list[JobRecord] = []
    kw_excluded: list[JobRecord] = []
    sen_excluded: list[JobRecord] = []

    for job in jobs:
        title = str(job.get("title") or "")
        if kw_pattern and kw_pattern.search(title):
            kw_excluded.append(
                dict(job, **{DROP_RULE_KEY: title_keyword_rule(title, kw_matchers)})
            )
        elif sen_pattern and sen_pattern.search(title):
            sen_excluded.append(
                dict(job, **{DROP_RULE_KEY: _seniority_rule(title, sen_matchers)})
            )
        else:
            kept.append(job)

    return kept, kw_excluded, sen_excluded


# ---------------------------------------------------------------------------
# Layer 5 — detail-page experience extraction
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


@dataclass(frozen=True)
class _DetailSignals:
    """What one detail page said about one job.

    A record rather than a tuple: WP8d needs a second deferred-state answer out
    of the same fetch, and a seventh positional element is where a tuple stops
    being readable at the call site.
    """

    job: JobRecord
    min_years: int | None = None
    phd_required: bool = False
    fetch_failed: bool = False
    # None means "the description could not be read at all" (no URL, fetch
    # failed, or no pattern configured) rather than read-and-not-found. Both
    # deferred states fail closed, so the caller must tell the two apart.
    hybrid_found: bool | None = None
    listed_location_found: bool | None = None
    description_text: str = ""


def _fetch_and_analyze(
    job: JobRecord,
    fn: Callable[[str], str],
    hybrid_pattern: re.Pattern[str] | None = None,
    location_pattern: re.Pattern[str] | None = None,
) -> _DetailSignals:
    """Fetch a job's detail page and extract experience/PhD/deferred-state signals.

    min_years is None when no numeric requirement was found or there was no URL.
    description_text is the stripped page text, capped at _MAX_DESCRIPTION_CHARS,
    or '' when nothing was fetched — WP7's scorer reads this back from the store
    rather than re-fetching.
    One fetch answers every question: the hybrid gate and WP8d's unresolvable
    location are both searched in the text already in hand, so neither costs an
    HTTP request of its own.
    """
    url = str(job.get("detail_url") or job.get("apply_url") or "").strip()
    if not url:
        return _DetailSignals(job=job)
    try:
        html = fn(url)
        text = _strip_html(html)
        return _DetailSignals(
            job=job,
            min_years=_extract_min_years(text),
            phd_required=_has_phd_required(text),
            hybrid_found=(
                bool(hybrid_pattern.search(text)) if hybrid_pattern is not None else None
            ),
            listed_location_found=(
                bool(location_pattern.search(text)) if location_pattern is not None else None
            ),
            description_text=text[:_MAX_DESCRIPTION_CHARS],
        )
    except Exception as exc:
        logger.debug(
            "%s: fetch failed for %r — keeping job. Error: %s",
            layer_short(LAYER_DETAIL),
            url,
            exc,
        )
        return _DetailSignals(job=job, fetch_failed=True)


def _resolve_hybrid(job: JobRecord, hybrid_found: bool | None) -> JobRecord | None:
    """Settle a conditional-location job against what the description said.

    Returns the job with its pending marker rewritten to confirmed, or None if it
    must be excluded. Jobs not awaiting a hybrid decision are returned unchanged.

    Unlike the rest of Layer 5 this fails *closed*: a conditional location is out
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


def _resolve_unresolved_location(
    job: JobRecord, listed_location_found: bool | None
) -> JobRecord | None:
    """Settle a job whose location field named no place, against the description.

    Returns the job with its pending marker rewritten to confirmed, or None if it
    must be excluded. Jobs not awaiting a location decision are returned unchanged.

    Fails closed, exactly as `_resolve_hybrid` does and for the same reason: the
    listing never established that this job is in range, so a description that
    names nothing on the list has not established it either. WP8d's point is that
    these jobs get *read* before they are dropped, not that they are kept.
    """
    reasons = job.get("matched_reasons") or []
    if _UNRESOLVED_PENDING_REASON not in reasons:
        return job
    if not listed_location_found:
        return None
    return dict(
        job,
        matched_reasons=[
            _UNRESOLVED_CONFIRMED_REASON if r == _UNRESOLVED_PENDING_REASON else r
            for r in reasons
        ],
    )


def apply_detail_filter(
    jobs: list[JobRecord],
    fetch_text: Callable[[str], str],
    source_fetch_map: dict[str, Callable[[str], str]] | None = None,
    hybrid_pattern: re.Pattern[str] | None = None,
    location_pattern: re.Pattern[str] | None = None,
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
    location_pattern does the same for WP8d's other deferred state, a job whose
    location field named no place at all: the description must name a listed
    location or the job is dropped — see _resolve_unresolved_location. Unlike
    the hybrid case these jobs *are* an extra HTTP request, because they died at
    Layer 1 before this package and never reached here at all.
    Fetches run in parallel with up to _DETAIL_WORKERS threads.
    Each returned job dict is annotated with description_text and
    description_fetched_at from this fetch (both '' when nothing was fetched),
    so the caller can persist them — that is what lets a later run skip the
    fetch entirely, on both the kept and the excluded side. The one exception
    is a job dropped by one of the two deferred states — a conditional location
    whose hybrid arrangement, or an unresolvable location field whose place,
    could not actually be verified this run (no URL, a fetch/parse error, or no
    pattern configured). It is still excluded for this run — failing closed is
    unchanged and deliberate — but is marked with UNVERIFIED_KEY so the caller
    knows *not* to treat that exclusion as a durable, storable judgement. A
    transient network error must not read the same as "checked and found
    lacking".
    Every excluded job also carries the rule that dropped it under
    DROP_RULE_KEY — the years threshold that fired, the PhD requirement, or
    which of the two hybrid cases it was — for the run's exclusion log.
    Returns (kept_jobs, excluded_jobs).
    """
    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    fetch_failed = 0
    no_requirement = 0
    hybrid_excluded = 0
    location_excluded = 0
    unverified = 0
    fetched_at = utc_now_iso()

    def _task(job: JobRecord) -> _DetailSignals:
        source = str(job.get("source_name") or "")
        fn = (source_fetch_map or {}).get(source, fetch_text)
        return _fetch_and_analyze(job, fn, hybrid_pattern, location_pattern)

    with ThreadPoolExecutor(max_workers=_DETAIL_WORKERS) as pool:
        results = list(pool.map(_task, jobs))

    def _deferred_exclusion(
        job: JobRecord,
        signals: _DetailSignals,
        level: str,
        verified: bool,
        checked_rule: str,
        unverified_rule: str,
    ) -> dict[str, Any]:
        """A drop from one of the two fail-closed deferred states.

        `verified` is False when the page could not be read at all (no URL, a
        fetch/parse exception, or no pattern configured) rather than read and
        found lacking. That is not a judgement about the job, only a fact about
        this run's network conditions, so it carries no description and must not
        be persisted as a permanent rejection — see the docstring above and
        pipeline.py's use of UNVERIFIED_KEY.
        """
        if not verified:
            return dict(
                job,
                experience_level=level,
                description_text="",
                description_fetched_at="",
                **{UNVERIFIED_KEY: True, DROP_RULE_KEY: unverified_rule},
            )
        return dict(
            job,
            experience_level=level,
            description_text=signals.description_text,
            description_fetched_at=fetched_at if signals.description_text else "",
            **{DROP_RULE_KEY: checked_rule},
        )

    for signals in results:
        job = signals.job
        min_years, phd_req, failed = signals.min_years, signals.phd_required, signals.fetch_failed
        description_text = signals.description_text

        # The two deferred states, settled in Layer 1's order. Either can drop
        # the job outright; a job carrying neither marker passes both untouched.
        resolved = _resolve_hybrid(job, signals.hybrid_found)
        if resolved is None:
            hybrid_excluded += 1
            if signals.hybrid_found is None:
                unverified += 1
            excluded.append(
                _deferred_exclusion(
                    job,
                    signals,
                    "non_hybrid_conditional_location",
                    signals.hybrid_found is not None,
                    RULE_HYBRID_NOT_HYBRID,
                    RULE_HYBRID_UNVERIFIED,
                )
            )
            continue

        job = resolved
        resolved = _resolve_unresolved_location(job, signals.listed_location_found)
        if resolved is None:
            location_excluded += 1
            if signals.listed_location_found is None:
                unverified += 1
            excluded.append(
                _deferred_exclusion(
                    job,
                    signals,
                    "unresolvable_location",
                    signals.listed_location_found is not None,
                    RULE_LOCATION_NOT_LISTED,
                    RULE_LOCATION_UNVERIFIED,
                )
            )
            continue
        job = resolved

        extra = {
            "description_text": description_text,
            "description_fetched_at": fetched_at if description_text else "",
        }
        if failed:
            fetch_failed += 1
            kept.append(dict(job, experience_level="unspecified", **extra))
        elif phd_req:
            excluded.append(
                dict(
                    job,
                    experience_level="phd_required",
                    **extra,
                    **{DROP_RULE_KEY: RULE_PHD_REQUIRED},
                )
            )
        elif min_years is None:
            no_requirement += 1
            kept.append(dict(job, experience_level="unspecified", **extra))
        elif min_years <= _MAX_JUNIOR_YEARS:
            kept.append(
                dict(job, experience_level=f"junior (<={_MAX_JUNIOR_YEARS}yr)", **extra)
            )
        else:
            excluded.append(
                dict(
                    job,
                    experience_level=f"senior ({min_years}+yr)",
                    **extra,
                    **{DROP_RULE_KEY: f"experience: {min_years}+ years required"},
                )
            )

    if jobs:
        logger.debug(
            "%s: %d/%d jobs failed to fetch (kept fail-open); "
            "%d had no numeric requirement; "
            "%d dropped as non-hybrid in a conditional location; "
            "%d dropped because an unresolvable location field named no listed place "
            "in the description "
            "(%d of those two totals because nothing could be verified this run, not "
            "because it was checked and found lacking)",
            layer_short(LAYER_DETAIL),
            fetch_failed,
            len(jobs),
            no_requirement,
            hybrid_excluded,
            location_excluded,
            unverified,
        )
    return kept, excluded
