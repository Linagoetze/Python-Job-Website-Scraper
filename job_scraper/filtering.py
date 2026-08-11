"""Keyword / location / exclusion rules."""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from typing import Any

from job_scraper import JobRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Title keyword filter (CSV-driven)
# ---------------------------------------------------------------------------

def load_title_exclude_keywords(path: Path) -> list[tuple[str, str]]:
    """Read title_exclude_keywords.csv and return a list of (keyword, match_type) pairs.

    match_type is either 'word' (whole-word, default) or 'prefix' (start-of-word).
    Returns an empty list if the file is missing or empty.
    """
    if not path.is_file():
        return []
    entries: list[tuple[str, str]] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            kw = str(row.get("keyword") or "").strip()
            match_type = str(row.get("match") or "word").strip().lower()
            if kw:
                entries.append((kw, match_type if match_type in ("word", "prefix") else "word"))
    return entries


def _build_title_keyword_pattern(
    entries: list[tuple[str, str]],
) -> re.Pattern[str] | None:
    """Build a combined regex from (keyword, match_type) pairs.

    'word'   → \\bkeyword\\b  (exact whole word; 'sales' won't match 'Salesforce')
    'prefix' → \\bkeyword     (word-start prefix; 'design' matches 'Designer')
    """
    if not entries:
        return None
    word_parts = [re.escape(kw) for kw, m in entries if m == "word"]
    prefix_parts = [re.escape(kw) for kw, m in entries if m == "prefix"]
    fragments: list[str] = []
    if word_parts:
        fragments.append(r"\b(?:" + "|".join(word_parts) + r")\b")
    if prefix_parts:
        fragments.append(r"\b(?:" + "|".join(prefix_parts) + r")")
    combined = "|".join(f"(?:{f})" for f in fragments)
    return re.compile(combined, re.IGNORECASE)


def apply_title_keyword_filter(
    jobs: list[JobRecord],
    entries: list[tuple[str, str]],
) -> tuple[list[JobRecord], list[JobRecord]]:
    """Split jobs into (kept, excluded) based on title keyword matches.

    Uses word-boundary matching: 'sales' (word) excludes 'Sales Manager' but not
    'Salesforce'; 'design' (prefix) excludes 'Graphic Designer' and 'Design Lead'.
    Returns (kept_jobs, excluded_jobs).
    """
    pattern = _build_title_keyword_pattern(entries)
    if pattern is None:
        return jobs, []
    kept: list[JobRecord] = []
    excluded: list[JobRecord] = []
    for job in jobs:
        title = str(job.get("title") or "")
        if pattern.search(title):
            excluded.append(job)
        else:
            kept.append(job)
    return kept, excluded


# ---------------------------------------------------------------------------
# Language filter
# ---------------------------------------------------------------------------

# Allowlist: only English, German, and the "germ" abbreviation are permitted.
# Every other WORD[-/ ]speaker or WORD[-/ ]speaking pattern is blocked.
_LANGUAGE_PATTERN: re.Pattern[str] = re.compile(
    r"\b(?!(?:english|german|germ)\b)\w+[\s-]+(?:speaker|speaking)\b",
    re.IGNORECASE,
)


def apply_language_filter(
    jobs: list[JobRecord],
) -> tuple[list[JobRecord], list[JobRecord]]:
    """Split jobs into (kept, excluded) based on language-speaker patterns in the title.

    Excludes any title containing 'WORD speaker/speaking' unless WORD is
    'English', 'German', or 'Germ'.  Uses an allowlist so unlisted languages
    (e.g. 'Tagalog speaking') are also blocked automatically.
    Returns (kept_jobs, excluded_jobs).
    """
    kept: list[JobRecord] = []
    excluded: list[JobRecord] = []
    for job in jobs:
        title = str(job.get("title") or "")
        if _LANGUAGE_PATTERN.search(title):
            excluded.append(job)
        else:
            kept.append(job)
    return kept, excluded


def apply_non_english_text_filter(
    jobs: list[JobRecord],
) -> tuple[list[JobRecord], list[JobRecord]]:
    """Exclude jobs whose title or description is detected as any non-English language.

    Uses langdetect; keeps the job if detection fails or text is too short (<= 10 chars).
    """
    try:
        from langdetect import LangDetectException, detect  # type: ignore[import]
    except ImportError:
        logger.warning(
            "langdetect is not installed; non-English text filter is disabled and "
            "all jobs will pass through. Install it (pip install langdetect) to "
            "filter out foreign-language listings."
        )
        return jobs, []

    kept: list[JobRecord] = []
    excluded: list[JobRecord] = []
    for job in jobs:
        title = str(job.get("title") or "")
        snippet = str(job.get("raw_snippet") or "")
        text = (title + " " + snippet).strip()
        try:
            lang = detect(text) if len(text) > 50 else "en"
        except LangDetectException:
            lang = "en"
        if lang != "en":
            excluded.append(job)
        else:
            kept.append(job)
    return kept, excluded


def _lower(s: str) -> str:
    return s.casefold()


# Generic (non-city) location segments that indicate a role is not tied to a
# specific duty station, beyond the configured remote_keywords (e.g. "Remote").
_GENERIC_LOCATION_TOKENS = ("home based", "home-based", "homebased")

# Separators used inside a single location field, e.g. "Remote | Nairobi".
_LOCATION_SPLIT = re.compile(r"[|/\n]+")


# ---------------------------------------------------------------------------
# Conditional locations (hybrid-gated cities)
# ---------------------------------------------------------------------------
#
# Cities in `conditional_locations` are too far to commute to daily, so they only
# qualify when the role is hybrid. "hybrid" may appear in the title (visible at
# Layer 1) or only in the job description (visible at Layer 2, which is the sole
# place the detail-page body text is ever fetched). So the check is two-stage and
# these two reason strings are the contract between the stages:
#
#   PENDING   — Layer 1 admitted the job provisionally; Layer 2 must confirm it
#               against the description, and drops it if it cannot.
#   CONFIRMED — hybrid was seen, either in the Layer 1 text or by Layer 2.
#
# A confirmation earned from a detail page is persisted as the store's
# hybrid_confirmed column (WP5), so a stored conditional-city job skips the
# re-fetch on later runs like any other stored job. The reason strings remain
# the in-run contract between the two stages; refilter_stored_jobs sees a
# stored conditional-city row as PENDING and keeps it — correct, since Layer 2
# is what put it there in the first place.
_HYBRID_PENDING_REASON = "locations: conditional (hybrid unconfirmed)"
_HYBRID_CONFIRMED_REASON = "locations: conditional (hybrid confirmed)"


def build_hybrid_pattern(rules: dict[str, Any]) -> re.Pattern[str] | None:
    """Compile the keyword matcher that gates `conditional_locations`.

    Keywords match as word-start prefixes, so "hybrid" also covers the Swedish
    compounds hybridarbete / hybridjobb / hybridlösning, and "hybrid-remote".
    Returns None when the feature is unconfigured.
    """
    keywords = [
        str(x).strip()
        for x in (rules.get("conditional_location_keywords") or [])
        if str(x).strip()
    ]
    return _build_title_keyword_pattern([(kw, "prefix") for kw in keywords])


def _has_confirmed_hybrid(job: JobRecord) -> bool:
    """True if this job already carries the confirmed marker.

    Matches on the stringified form so it also holds for a job dict that has been
    round-tripped through CSV, where the list becomes its repr.
    """
    return _HYBRID_CONFIRMED_REASON in str(job.get("matched_reasons") or "")


def _location_names_specific_city(loc_field_cf: str, remote_keywords: list[str]) -> bool:
    """Return True if the (casefolded) location field names a concrete place.

    A location like "Remote" or "Remote | Home Based - May require travel" names
    no specific city, so it qualifies as a genuine anywhere/remote role. But
    "Remote | Nairobi" names Nairobi as the duty station, so the leading "Remote"
    tag must not be treated as "located anywhere".

    A segment is non-specific if it contains a configured remote_keyword or a
    generic token like "home based"; any other non-empty segment is a city.
    """
    remote_cf = [_lower(rk) for rk in remote_keywords]
    for raw_seg in _LOCATION_SPLIT.split(loc_field_cf):
        seg = raw_seg.strip()
        if not seg:
            continue
        if any(rk in seg for rk in remote_cf):
            continue
        if any(tok in seg for tok in _GENERIC_LOCATION_TOKENS):
            continue
        return True
    return False


def _haystack(job: JobRecord, match_in: str) -> str:
    title = str(job.get("title") or "")
    snippet = str(job.get("raw_snippet") or "")
    dept = str(job.get("department") or "")
    loc = str(job.get("location") or "")
    if match_in == "title_only":
        return title
    return " ".join((title, snippet, dept, loc))


def matches_rules(
    job: JobRecord,
    rules: dict[str, Any],
    hybrid_pattern: re.Pattern[str] | None,
) -> tuple[bool, list[str]]:
    """
    Return (passes, reasons).

    Semantics:
    - Empty `include_keywords`: no keyword requirement.
    - Empty `locations`: no location requirement.
    - `exclude_keywords`: if any matches haystack, reject.
    - When `locations` is non-empty: pass if any location string matches the `location`
      field, OR any `remote_keyword` appears in title/snippet/dept/location (OR).
    - `conditional_locations` match the `location` field like `locations`, but only
      admit the job when a `conditional_location_keywords` term (e.g. "hybrid") is
      present. If the keyword is not visible at this layer the job passes with
      `_HYBRID_PENDING_REASON` for Layer 2 to confirm against the description.

    `hybrid_pattern` gates `conditional_locations` and must be built once via
    `build_hybrid_pattern(rules)` by the caller and passed down — never rebuilt
    here, since this runs once per job.
    """
    reasons: list[str] = []
    match_in = str(rules.get("match_in") or "title_and_description")

    include_keywords = [str(x) for x in (rules.get("include_keywords") or []) if str(x).strip()]
    exclude_keywords = [str(x) for x in (rules.get("exclude_keywords") or []) if str(x).strip()]
    locations = [str(x) for x in (rules.get("locations") or []) if str(x).strip()]
    conditional_locations = [
        str(x) for x in (rules.get("conditional_locations") or []) if str(x).strip()
    ]
    remote_keywords = [str(x) for x in (rules.get("remote_keywords") or []) if str(x).strip()]

    hay = _haystack(job, match_in)
    hay_cf = _lower(hay)
    loc_field = _lower(str(job.get("location") or ""))

    for ex in exclude_keywords:
        if _lower(ex) in hay_cf:
            return False, [f"excluded: matched {ex!r}"]

    if include_keywords:
        matched_kw = [kw for kw in include_keywords if _lower(kw) in hay_cf]
        if not matched_kw:
            return False, ["include_keywords: no match"]
        reasons.append(f"include_keywords: {', '.join(matched_kw)}")

    if locations or conditional_locations:
        loc_ok = any(_lower(loc) in loc_field for loc in locations)
        # A remote_keyword only admits a job when its location field does not
        # name a specific (non-listed) city. This stops sources like Impactpool —
        # which tag every posting "Remote | <duty station>" — from bypassing the
        # location filter on city-specific roles (e.g. "Remote | Nairobi").
        remote_kw_present = any(_lower(rk) in hay_cf for rk in remote_keywords)
        remote_ok = remote_kw_present and not _location_names_specific_city(
            loc_field, remote_keywords
        )
        if loc_ok:
            reasons.append("locations: matched")
        elif remote_ok:
            reasons.append("locations: matched via remote_keywords")
        elif hybrid_pattern is not None and any(
            _lower(loc) in loc_field for loc in conditional_locations
        ):
            # A hybrid-gated city. Confirm from a marker already on the job, else
            # from the Layer 1 text, else defer to Layer 2's detail fetch.
            # (With no conditional_location_keywords configured the gate can never
            # be satisfied, so the whole conditional list stays inert.)
            if _has_confirmed_hybrid(job) or hybrid_pattern.search(hay):
                reasons.append(_HYBRID_CONFIRMED_REASON)
            else:
                reasons.append(_HYBRID_PENDING_REASON)
        else:
            return False, ["locations: no match (and no remote_keywords match)"]

    if not reasons and (include_keywords or locations or conditional_locations):
        reasons.append("matched rules")
    if not reasons and not include_keywords and not locations and not conditional_locations:
        reasons.append("no filters (pass)")

    return True, reasons
