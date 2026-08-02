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
        from langdetect import detect, LangDetectException  # type: ignore[import]
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


def matches_rules(job: JobRecord, rules: dict[str, Any]) -> tuple[bool, list[str]]:
    """
    Return (passes, reasons).

    Semantics:
    - Empty `include_keywords`: no keyword requirement.
    - Empty `locations`: no location requirement.
    - `exclude_keywords`: if any matches haystack, reject.
    - When `locations` is non-empty: pass if any location string matches the `location`
      field, OR any `remote_keyword` appears in title/snippet/dept/location (OR).
    """
    reasons: list[str] = []
    match_in = str(rules.get("match_in") or "title_and_description")

    include_keywords = [str(x) for x in (rules.get("include_keywords") or []) if str(x).strip()]
    exclude_keywords = [str(x) for x in (rules.get("exclude_keywords") or []) if str(x).strip()]
    locations = [str(x) for x in (rules.get("locations") or []) if str(x).strip()]
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

    if locations:
        loc_ok = any(_lower(loc) in loc_field for loc in locations)
        # A remote_keyword only admits a job when its location field does not
        # name a specific (non-listed) city. This stops sources like Impactpool —
        # which tag every posting "Remote | <duty station>" — from bypassing the
        # location filter on city-specific roles (e.g. "Remote | Nairobi").
        remote_kw_present = any(_lower(rk) in hay_cf for rk in remote_keywords)
        remote_ok = remote_kw_present and not _location_names_specific_city(
            loc_field, remote_keywords
        )
        if not loc_ok and not remote_ok:
            return False, ["locations: no match (and no remote_keywords match)"]
        if loc_ok:
            reasons.append("locations: matched")
        elif remote_ok:
            reasons.append("locations: matched via remote_keywords")

    if not reasons and (include_keywords or locations):
        reasons.append("matched rules")
    if not reasons and not include_keywords and not locations:
        reasons.append("no filters (pass)")

    return True, reasons
