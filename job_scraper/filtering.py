"""Keyword / location / exclusion rules."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from job_scraper import JobRecord

# ---------------------------------------------------------------------------
# Drop attribution (WP8a)
# ---------------------------------------------------------------------------
#
# Every filter that excludes a job names the specific thing that fired — the
# keyword, the seniority term, the location case — not just its layer.
# A layer name alone answers "how many did I lose?"; only the rule answers "why,
# and would loosening this one bring back something I wanted?".
#
# The rule travels on the excluded job dict under this key, so the filters stay
# data-in/data-out and nothing needs a callback. It is stripped by `job_to_row`
# (which copies named columns only), so it never reaches the store's jobs table.
DROP_RULE_KEY = "drop_rule"

# Layer 1 rule strings. The location cases are split finely on purpose: the
# location rules reject the overwhelming majority of everything scraped, and
# "off-criteria" is not a diagnosis.
RULE_INCLUDE_NO_MATCH = "include_keywords: no match"
RULE_LOC_UNLISTED_CITY = "locations: city not on the list"
RULE_LOC_REMOTE_OVERRIDDEN = "locations: remote keyword overridden by a named city"
RULE_LOC_CONDITIONAL_UNGATED = "locations: conditional city, hybrid gate not configured"


def _with_rule(job: JobRecord, rule: str) -> JobRecord:
    """A copy of *job* carrying the rule that excluded it."""
    return dict(job, **{DROP_RULE_KEY: rule})


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


def build_title_keyword_matchers(
    entries: list[tuple[str, str]],
) -> list[tuple[str, str, re.Pattern[str]]]:
    """One compiled pattern per entry, for naming which keyword excluded a job.

    Built once at setup alongside the combined pattern and passed down, never
    rebuilt per job. The combined pattern stays the decision — this list is only
    consulted for the minority of jobs it rejects, so the common path is
    unchanged.
    """
    matchers: list[tuple[str, str, re.Pattern[str]]] = []
    for kw, match_type in entries:
        pattern = _build_title_keyword_pattern([(kw, match_type)])
        if pattern is not None:
            matchers.append((kw, match_type, pattern))
    return matchers


def title_keyword_rule(
    title: str,
    matchers: list[tuple[str, str, re.Pattern[str]]],
    *,
    prefix: str = "title_keyword",
) -> str:
    """Name the first configured keyword that matches *title*, with its match type.

    "First configured" rather than "leftmost in the title": when two keywords
    both match, the file order is the order the owner reads their own list in.
    The seniority filter shares this, under its own *prefix*, because both are
    a list of terms against a title and both must name the term that fired.
    """
    for kw, match_type, pattern in matchers:
        if pattern.search(title):
            return f"{prefix}: {kw!r} ({match_type})"
    return f"{prefix}: unattributed"


def apply_title_keyword_filter(
    jobs: list[JobRecord],
    entries: list[tuple[str, str]],
) -> tuple[list[JobRecord], list[JobRecord]]:
    """Split jobs into (kept, excluded) based on title keyword matches.

    Uses word-boundary matching: 'sales' (word) excludes 'Sales Manager' but not
    'Salesforce'; 'design' (prefix) excludes 'Graphic Designer' and 'Design Lead'.
    Excluded jobs carry the keyword that fired under DROP_RULE_KEY.
    Returns (kept_jobs, excluded_jobs).
    """
    pattern = _build_title_keyword_pattern(entries)
    if pattern is None:
        return jobs, []
    matchers = build_title_keyword_matchers(entries)
    kept: list[JobRecord] = []
    excluded: list[JobRecord] = []
    for job in jobs:
        title = str(job.get("title") or "")
        if pattern.search(title):
            excluded.append(_with_rule(job, title_keyword_rule(title, matchers)))
        else:
            kept.append(job)
    return kept, excluded


def _lower(s: str) -> str:
    return s.casefold()


# Generic (non-city) location segments that indicate a role is not tied to a
# specific duty station, beyond the configured remote_keywords (e.g. "Remote").
#
# Both spellings of the home-base wording live here rather than in config: this
# is English, not a place list, so no rules.json should have to know it. Sorted
# longest first because `_location_names_no_place` strikes them out by plain
# substring — with "home base" tried first, "home based" would leave a stray
# "d" behind and read as a place.
_GENERIC_LOCATION_TOKENS = tuple(
    sorted({"home based", "home-based", "homebased", "home base"}, key=len, reverse=True)
)

# Separators used inside a single location field, e.g. "Remote | Nairobi".
_LOCATION_SPLIT = re.compile(r"[|/\n]+")

# Listing pages that will not name their duty stations: "2 Locations",
# "Multiple locations". A shape rather than a list — no config key can
# enumerate every N — so this one stays in code (WP8d).
_PLACEHOLDER_LOCATION = re.compile(r"(?:\d+|multiple|several|various)\s+locations?")

# A segment still names a place if any *letter* survives having every non-place
# term struck out of it. Digits and punctuation do not count: "home base - emea,
# 2" has nothing left to look up, while "barcelona, spain" keeps Barcelona.
_LETTER = re.compile(r"[^\W\d_]")


# ---------------------------------------------------------------------------
# Conditional locations (hybrid-gated cities)
# ---------------------------------------------------------------------------
#
# Cities in `conditional_locations` are too far to commute to daily, so they only
# qualify when the role is hybrid. "hybrid" may appear in the title (visible at
# Layer 1) or only in the job description (visible at Layer 5, which is the sole
# place the detail-page body text is ever fetched). So the check is two-stage and
# these two reason strings are the contract between the stages:
#
#   PENDING   — Layer 1 admitted the job provisionally; Layer 5 must confirm it
#               against the description, and drops it if it cannot.
#   CONFIRMED — hybrid was seen, either in the Layer 1 text or by Layer 5.
#
# A confirmation earned from a detail page is persisted as the store's
# hybrid_confirmed column (WP5), so a stored conditional-city job skips the
# re-fetch on later runs like any other stored job. The reason strings remain
# the in-run contract between the two stages; refilter_stored_jobs sees a
# stored conditional-city row as PENDING and keeps it — correct, since Layer 5
# is what put it there in the first place.
_HYBRID_PENDING_REASON = "locations: conditional (hybrid unconfirmed)"
_HYBRID_CONFIRMED_REASON = "locations: conditional (hybrid confirmed)"


# ---------------------------------------------------------------------------
# Unresolvable locations (WP8d)
# ---------------------------------------------------------------------------
#
# The third state a location field can be in. Layer 1 used to know two: empty,
# or naming a specific city. A field that is present but names no place at all
# — "2 Locations", "Home base - EMEA", a bare country — fell into the second
# and died against a list it was never going to match, having never been read.
#
# It is now treated exactly as a hybrid-gated city is: admitted provisionally,
# then settled at Layer 5 against the fetched description, and dropped if the
# description names nothing on the list. Same two-stage contract, same failure
# direction — see `_resolve_unresolved_location` in experience_filter.py.
#
# No store column backs this one. A hybrid confirmation needed `hybrid_confirmed`
# only to re-check rows written before that column existed; a state introduced
# today has no such legacy population, so "already in the store" is enough to
# skip the re-fetch, and WP6's stored `description_text` already keeps the page
# itself. See the WP8d section of docs/REFACTOR-PLAN.md.
_UNRESOLVED_PENDING_REASON = "locations: unresolvable field (place unconfirmed)"
_UNRESOLVED_CONFIRMED_REASON = "locations: unresolvable field (place confirmed)"


# ---------------------------------------------------------------------------
# Empty locations (WP8f)
# ---------------------------------------------------------------------------
#
# The fourth Layer 1 outcome. An empty location field is not a placeholder
# that might name a place once read (WP8d's third state) and it is not a
# conditional city awaiting a hybrid check — it is an extractor or listing
# page that never had a location to give, and WP8e confirmed real postings
# die here for want of one nobody ever had. Keyword and seniority filters
# never get a turn to judge these jobs today; they die before Layer 2.
#
# Unlike the two pending states above, this is settled here and permanently:
# no description is going to retroactively supply a location that was never
# on the listing, so there is nothing for Layer 5 to confirm. This reason is
# therefore *not* wired into `_resolve_hybrid`/`_resolve_unresolved_location`
# or `UNVERIFIED_KEY` — a job carrying it must not cost a detail fetch it
# would not otherwise need, and must not be mistaken by Layer 5 for a marker
# it is meant to settle.
_LOCATION_EMPTY_ADMITTED_REASON = "locations: no location given (admitted)"


def build_non_place_pattern(rules: dict[str, Any]) -> re.Pattern[str] | None:
    """Compile the configured terms that name no specific place.

    `non_place_locations` in rules.json: regions ("EMEA", "Worldwide") and bare
    country names, matched as whole words. It *extends* `_GENERIC_LOCATION_TOKENS`
    rather than replacing it, so a rules.json without the key behaves as it did
    before WP8d. Longest first, so "United States of America" is struck out
    whole rather than leaving "of America" behind.
    Returns None when the key is absent or empty.
    """
    terms = sorted(
        {str(x).strip() for x in (rules.get("non_place_locations") or []) if str(x).strip()},
        key=len,
        reverse=True,
    )
    return _build_title_keyword_pattern([(t, "word") for t in terms])


def build_location_pattern(rules: dict[str, Any]) -> re.Pattern[str] | None:
    """Compile `locations` for searching a *description* (Layer 5's copy).

    Whole-word, unlike `matches_rules`'s substring test against the location
    field: a city name loose in a page of prose needs the tighter match, or
    "Lund" finds "Lundberg" in a hiring manager's name. Conditional locations
    are deliberately absent — a conditional city still owes a hybrid check, and
    resolving one unconfirmed state into another is not a decision.
    Returns None when `locations` is unconfigured.
    """
    locations = [str(x).strip() for x in (rules.get("locations") or []) if str(x).strip()]
    return _build_title_keyword_pattern([(loc, "word") for loc in locations])


def build_hybrid_pattern(rules: dict[str, Any]) -> re.Pattern[str] | None:
    """Compile the keyword matcher that gates `conditional_locations`.

    Keywords match as word-start prefixes, so "hybrid" also covers the Swedish
    compounds hybridarbete / hybridjobb / hybridlösning, and "hybrid-remote".
    Returns None when the feature is unconfigured.
    """
    keywords = [
        str(x).strip() for x in (rules.get("conditional_location_keywords") or []) if str(x).strip()
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


def _location_names_no_place(
    loc_field_cf: str,
    remote_keywords: list[str],
    non_place_pattern: re.Pattern[str] | None,
) -> bool:
    """Return True if the (casefolded) location field names no place at all.

    The third state (WP8d): present, but unresolvable from the listing page —
    "2 Locations", "Home base - EMEA", a bare country. Distinct from an empty
    field, which is WP8f's fourth state and is admitted outright rather than
    deferred to Layer 5.

    A segment is judged by striking out every term that names no place — the
    remote keywords, the generic tokens, the configured `non_place_locations` —
    and asking whether any letter survives. That is what separates
    "home base - emea" (nothing left to look up) from "barcelona, spain"
    (Barcelona is a place whatever country follows it), *without* splitting on
    dashes: real city names contain them.

    Deliberately not `_location_names_specific_city`'s inverse. That function
    answers a different question — may a remote tag stand? — and widening its
    idea of "not a city" would quietly admit "Remote | Berlin, EMEA" as a
    genuine anywhere role. Two questions, two classifiers.
    """
    if not loc_field_cf.strip():
        return False
    remote_cf = [_lower(rk) for rk in remote_keywords]
    # A field made only of remote keywords is "remote", a state the location
    # rules already have an answer for — it must not be re-labelled
    # unresolvable and sent to Layer 5 for a fetch. That distinction is
    # invisible under `match_in: title_and_description`, where the location
    # field is part of the haystack and `remote_ok` settles such a job before
    # this function is reached, and it is the whole story under `title_only`,
    # where it is not.
    placeless_for_a_reason = False
    for raw_seg in _LOCATION_SPLIT.split(loc_field_cf):
        seg = raw_seg.strip()
        if not seg:
            continue
        remainder = seg
        for term in remote_cf:
            remainder = remainder.replace(term, " ")
        if not _LETTER.search(remainder):
            continue
        remainder = _PLACEHOLDER_LOCATION.sub(" ", remainder)
        for term in _GENERIC_LOCATION_TOKENS:
            remainder = remainder.replace(term, " ")
        if non_place_pattern is not None:
            remainder = non_place_pattern.sub(" ", remainder)
        if _LETTER.search(remainder):
            return False
        placeless_for_a_reason = True
    return placeless_for_a_reason


def _location_drop_rule(
    loc_field_cf: str,
    remote_kw_present: bool,
    conditional_locations: list[str],
) -> str:
    """Name why the location rules rejected a job.

    Reached only from the failing branch of `matches_rules`, so the listed
    locations are already known not to match, and — since WP8f — the field is
    already known not to be empty: `matches_rules` intercepts that case before
    this function is ever called. The order below is the order the remaining
    cases are worth telling apart:

    - a remote keyword was present but the field also named a city, so the
      remote tag was overridden (Impactpool's "Remote | Nairobi" shape);
    - a conditional city matched but the hybrid gate is unconfigured, leaving
      the whole conditional list inert;
    - otherwise the field simply names somewhere not on the list.
    """
    if remote_kw_present:
        return RULE_LOC_REMOTE_OVERRIDDEN
    if any(_lower(loc) in loc_field_cf for loc in conditional_locations):
        return RULE_LOC_CONDITIONAL_UNGATED
    return RULE_LOC_UNLISTED_CITY


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
    *,
    non_place_pattern: re.Pattern[str] | None = None,
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
      `_HYBRID_PENDING_REASON` for Layer 5 to confirm against the description.
    - A field that is present but names no place (WP8d: "2 Locations",
      "Home base - EMEA", a bare country) is not a city that failed to match.
      It passes with `_UNRESOLVED_PENDING_REASON`, again for Layer 5 to settle
      against the description.
    - A field that is empty (WP8f: no location was ever given, an extractor or
      listing-page gap rather than a placeholder) is not the same failure as
      an unresolvable one — there is no page for Layer 5 to read it off, so it
      is admitted outright with `_LOCATION_EMPTY_ADMITTED_REASON` rather than
      deferred. This is settled here, permanently.

    `hybrid_pattern` gates `conditional_locations` and `non_place_pattern` carries
    the configured `non_place_locations`. Both must be built once by the caller —
    `build_hybrid_pattern(rules)` and `build_non_place_pattern(rules)` — and passed
    down, never rebuilt here, since this runs once per job. `non_place_pattern` is
    keyword-only and defaults to None so it can never be mistaken for the hybrid
    one; None only narrows the third state to the shapes recognised in code, it
    does not switch it off.

    On rejection the single returned reason is the drop rule: it names the
    keyword or the specific location case that fired, and the caller records it
    verbatim in the run's exclusion log.
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
            return False, [f"exclude_keywords: matched {ex!r}"]

    if include_keywords:
        matched_kw = [kw for kw in include_keywords if _lower(kw) in hay_cf]
        if not matched_kw:
            return False, [RULE_INCLUDE_NO_MATCH]
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
            # from the Layer 1 text, else defer to Layer 5's detail fetch.
            # (With no conditional_location_keywords configured the gate can never
            # be satisfied, so the whole conditional list stays inert.)
            if _has_confirmed_hybrid(job) or hybrid_pattern.search(hay):
                reasons.append(_HYBRID_CONFIRMED_REASON)
            else:
                reasons.append(_HYBRID_PENDING_REASON)
        elif locations and _location_names_no_place(loc_field, remote_keywords, non_place_pattern):
            # Present, but naming no place this layer can resolve. Judging it
            # against the list would be judging a placeholder, so defer to
            # Layer 5 and let the description decide (it fails closed there).
            #
            # Only worth deferring when there *is* a list: with `locations`
            # empty, Layer 5 has nothing to search the description for, so
            # every deferred job would come back unverifiable — dropped for the
            # run, never stored, and re-fetched on every run after it. Defer
            # only what can actually be settled.
            reasons.append(_UNRESOLVED_PENDING_REASON)
        elif not loc_field.strip():
            # A genuinely empty field (WP8f), as opposed to WP8d's "present but
            # names no place" — the branch above already refuses that case for
            # an empty field, so this is reached only when the field truly has
            # nothing in it. Settled here, permanently: see the reason's own
            # comment for why this must not become a Layer 5 pending marker.
            reasons.append(_LOCATION_EMPTY_ADMITTED_REASON)
        else:
            return False, [_location_drop_rule(loc_field, remote_kw_present, conditional_locations)]

    if not reasons and (include_keywords or locations or conditional_locations):
        reasons.append("matched rules")
    if not reasons and not include_keywords and not locations and not conditional_locations:
        reasons.append("no filters (pass)")

    return True, reasons
