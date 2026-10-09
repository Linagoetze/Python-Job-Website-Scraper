"""Keyword / location / exclusion rules."""

from __future__ import annotations

import csv
import re
from collections.abc import Callable
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


MATCH_TYPES = ("word", "prefix", "contains")


def load_title_exclude_keywords(path: Path) -> list[tuple[str, str]]:
    """Read title_exclude_keywords.csv and return a list of (keyword, match_type) pairs.

    match_type is 'word' (whole-word, default), 'prefix' (start-of-word) or
    'contains' (anywhere inside a word, for family words German and Swedish
    put at the end of a compound). Returns an empty list if the file is missing or empty.
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
                entries.append((kw, match_type if match_type in MATCH_TYPES else "word"))
    return entries


def _build_title_keyword_pattern(
    entries: list[tuple[str, str]],
    *,
    exact_acronyms: bool = False,
) -> re.Pattern[str] | None:
    """Build a combined regex from (keyword, match_type) pairs.

    'word'   → \\bkeyword\\b  (exact whole word; 'sales' won't match 'Salesforce')
    'prefix' → \\bkeyword     (word-start prefix; 'design' matches 'Designer')
    'contains' → keyword     (anywhere in a word; 'techniker' matches 'Prüftechniker')

    'contains' rather than a word-end type because the compounds it exists for
    inflect after the family word: 'Mjukvaruingenjörer', 'Technikerin'. A match
    that had to end at the word's edge would lose those. It is only safe for a
    long, distinctive keyword, so each use is measured with eval --compare.

    With `exact_acronyms`, a keyword written in capitals matches case-sensitively;
    only the title keyword list asks for it, since location and remote terms share
    this builder and are written in capitals for other reasons.
    """
    if not entries:
        return None
    fragments: list[str] = []
    # An all-caps keyword is an acronym ("SEA", "AI", "IT") and matches only as
    # written: case-insensitively, "SEA" excluded a "Baltic Sea programme"
    # internship (SP8) and "IT" would match the pronoun. The rest ignore case.
    for scoped, group in (
        ("", [e for e in entries if not (exact_acronyms and e[0].isupper())]),
        ("-i", [e for e in entries if exact_acronyms and e[0].isupper()]),
    ):
        contains_parts = [re.escape(kw) for kw, m in group if m == "contains"]
        word_parts = [re.escape(kw) for kw, m in group if m == "word"]
        prefix_parts = [re.escape(kw) for kw, m in group if m == "prefix"]
        local: list[str] = []
        if contains_parts:
            local.append("(?:" + "|".join(contains_parts) + ")")
        if word_parts:
            local.append(r"\b(?:" + "|".join(word_parts) + r")\b")
        if prefix_parts:
            local.append(r"\b(?:" + "|".join(prefix_parts) + r")")
        if local:
            body = "|".join(f"(?:{f})" for f in local)
            fragments.append(f"(?{scoped}:{body})" if scoped else f"(?:{body})")
    return re.compile("|".join(fragments), re.IGNORECASE)


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
        pattern = _build_title_keyword_pattern([(kw, match_type)], exact_acronyms=True)
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
    pattern = _build_title_keyword_pattern(entries, exact_acronyms=True)
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

# Words that say a role is open everywhere (SP4f, the owner's Q3a). English, like
# the home-base tokens, so they live in code rather than in any rules.json:
# "Home based - Worldwide" and a bare "Worldwide" need no configuration to be
# read as remote. "International" is deliberately not one: "US + International"
# names a country first and the rest of the world second.
_EVERYWHERE = re.compile(r"\b(?:worldwide|global)\b")
# "International" means everywhere only beside remote wording (the owner's answer,
# 2026-10-06): "United States + International (Remote)" is open beyond the US,
# while a plain "US + International" names a country first and is read as one.
_INTERNATIONAL = re.compile(r"\binternational\b")

# Words that sit in a remote option without naming a place: "Fully Remote", and
# Impactpool's "Remote | Home Based - May require travel". Struck out only in an
# option that already carries remote wording, so they never make a place vanish.
_REMOTE_NOISE = re.compile(r"\bfully\b|\bmay\s+require\s+travel\b")

# Separators used inside a single location field, e.g. "Remote | Nairobi". `;`
# joins the options of a field that offers several ("Home based - <region>;
# Office Based - <city>"), and since SP4f an option is judged on its own
# (Q3c), so it separates them too.
_LOCATION_SPLIT = re.compile(r"[|/\n;]+")

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
# Empty locations (WP8f, revised by SP4f)
# ---------------------------------------------------------------------------
#
# The fourth Layer 1 outcome. WP8f admitted an empty field outright, on the
# premise that no page could supply a location the listing never gave. SP4b
# found that premise false wherever the detail page is read anyway, which is
# every source with empty fields today: 12 of impactpool's 13 empty-location
# rows in the sheet named no listed place in their description. The owner's
# answer (Q4, 2026-10-01): defer it to the description and fail closed, as a
# placeholder is. So it is a pending state of its own, settled by Layer 5
# exactly as the unresolvable one is, with its own pair of drop rules so the
# drop log can tell an empty field from a placeholder.
#
# It costs no extra detail fetch: every new job is fetched for its years
# anyway, and a stored one is not re-judged.
_EMPTY_PENDING_REASON = "locations: no location given (place unconfirmed)"
_EMPTY_CONFIRMED_REASON = "locations: no location given (place confirmed)"

# The one case still admitted unread: `locations` is empty, so there is no list
# for Layer 5 to search a description for, and a deferred job could only ever
# come back unverified. Same reasoning as the unresolvable branch's guard.
_LOCATION_EMPTY_ADMITTED_REASON = "locations: no location given (admitted)"


# ---------------------------------------------------------------------------
# Remote regions (SP4f)
# ---------------------------------------------------------------------------
#
# The owner's answers to SP4b Q3 (2026-10-01): a role that is not in one place
# counts as remote. Home-based or worldwide (a), home-based across a region that
# includes where the owner lives (b), and such an option beside an office city
# (c). Which regions include the owner is private, so it is `remote_regions` in
# rules.json, never a list in code. Without that key, (a) still holds and every
# regional field is deferred to Layer 5 as before.
_REMOTE_ANYWHERE_REASON = "locations: remote, anywhere or worldwide"
_REMOTE_REGION_REASON = "locations: remote in a listed region"


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


def build_remote_region_pattern(rules: dict[str, Any]) -> re.Pattern[str] | None:
    """Compile `remote_regions`: regions a remote role may span and still include you.

    The owner's answer to SP4b Q3b. Matched whole-word and casefolded, longest
    first, like `non_place_locations`, and usually a subset of it: a region
    is a place nobody can be sent to, so it defers on its own, and it admits
    only beside remote or home-based wording (`_remote_admission`).
    Returns None when the key is absent or empty, and every regional field is
    then deferred as it was before SP4f.
    """
    terms = sorted(
        {str(x).strip() for x in (rules.get("remote_regions") or []) if str(x).strip()},
        key=len,
        reverse=True,
    )
    return _build_title_keyword_pattern([(t, "word") for t in terms])


# Workday's own workplace label, as its rendered detail page shows it (SP4f
# follow-up, the owner's choice of 2026-10-06). Exact, not a loose "remote":
# Impactpool's descriptions speak of travelling to "remote locations" constantly.
_WORKDAY_FULLY_REMOTE = re.compile(r"\bremote\s+type\s+fully\s+remote\b", re.IGNORECASE)


def build_page_remote_reader(rules: dict[str, Any]) -> Callable[[str], bool]:
    """Compile what Layer 5 may read off a page to settle an empty location field.

    The owner's choice (2026-10-06): an empty field is settled by its page naming
    a listed place (`build_location_pattern`), or by the page saying the job is
    remote where the owner can work. Two things count, and nothing looser:

    - Workday's exact label "remote type Fully Remote", which names no region,
      so it counts as a bare remote field does at Layer 0.
    - An employer's own "Location: <value>" line, judged by Layer 0's reading
      (`_remote_admission`), but only when the value names worldwide, global or
      international, or a `remote_regions` term. A bare "Location: Remote" does
      not count here. A stripped page has no line breaks, so the value's end is
      found by the vocabulary running out, and "Location: Remote, <country>
      Languages: ..." would otherwise read as remote anywhere.

    The value is the longest run of remote, everywhere and region words (with
    "and", "or" and punctuation between them) that is followed by the end of the
    text, a full stop, or the next "Label:". Built once per run, like the other
    patterns. It can only settle a job, never reject one.
    """
    remote_keywords = [str(x) for x in (rules.get("remote_keywords") or []) if str(x).strip()]
    region_pattern = build_remote_region_pattern(rules)
    terms = {
        *(_lower(t) for t in remote_keywords),
        *_GENERIC_LOCATION_TOKENS,
        *(_lower(str(t).strip()) for t in (rules.get("remote_regions") or []) if str(t).strip()),
        "worldwide",
        "global",
        "international",
        "fully",
        "may require travel",
        "and",
        "or",
    }
    words = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    line = re.compile(
        r"\b(?i:locations?)\s*:\s*"
        rf"(?P<value>(?:(?i:{words})\b|[\s,/&+()\-–])+)"
        r"(?=$|[.;|•]|[A-Z][^\s:]*(?:\s+[^\s:]+){0,4}\s*:)"
    )

    def says_remote_for_the_owner(text: str) -> bool:
        if _WORKDAY_FULLY_REMOTE.search(text):
            return True
        for match in line.finditer(text):
            value = _lower(match.group("value"))
            reason = _remote_admission(
                value, value, remote_keywords, region_pattern, field_in_haystack=True
            )
            if reason == _REMOTE_REGION_REASON:
                return True
            if reason == _REMOTE_ANYWHERE_REASON and (
                _EVERYWHERE.search(value) or _INTERNATIONAL.search(value)
            ):
                return True
        return False

    return says_remote_for_the_owner


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


def _location_names_no_place(
    loc_field_cf: str,
    remote_keywords: list[str],
    non_place_pattern: re.Pattern[str] | None,
) -> bool:
    """Return True if the (casefolded) location field names no place at all.

    The third state (WP8d): present, but unresolvable from the listing page —
    "2 Locations", "Home base - EMEA", a bare country. Distinct from an empty
    field, which is WP8f's fourth state: deferred too since SP4f, but under its
    own reason, so the drop log can tell the two apart.

    A segment is judged by striking out every term that names no place — the
    remote keywords, the generic tokens, the configured `non_place_locations` —
    and asking whether any letter survives. That is what separates
    "home base - emea" (nothing left to look up) from "barcelona, spain"
    (Barcelona is a place whatever country follows it), *without* splitting on
    dashes: real city names contain them.

    Deliberately not `_remote_admission`'s inverse. That function answers a
    different question — may a remote option stand? — and a field this one
    calls placeless ("Remote | <country>") is one that function may still
    decline to admit. Two questions, two classifiers.
    """
    if not loc_field_cf.strip():
        return False
    remote_cf = [_lower(rk) for rk in remote_keywords]
    # A field made only of remote keywords is "remote", a state the location
    # rules already have an answer for — it must not be re-labelled
    # unresolvable and sent to Layer 5 for a fetch. That distinction is
    # invisible under `match_in: title_and_description`, where the location
    # field is part of the haystack and `_remote_admission` settles such a job
    # before this function is reached, and it is the whole story under
    # `title_only`, where it is not.
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


def _remote_admission(
    loc_field_cf: str,
    hay_cf: str,
    remote_keywords: list[str],
    remote_region_pattern: re.Pattern[str] | None,
    *,
    field_in_haystack: bool,
) -> str | None:
    """The reason a remote, home-based or worldwide field admits a job, or None.

    The owner's answers to SP4b Q3 (2026-10-01), tightened on 2026-10-06: remote
    work counts only where it is open to the owner. It is the one remote reading
    at Layer 0. Each option of the field is read on its own once its remote
    wording is struck out: the remote keywords, the home-base tokens, and the
    noise that travels with them ("Fully", "May require travel"). What is left
    decides.

    - Nothing: a bare "Remote", "Fully Remote" or "Home Based" names no region,
      and admits when every option is like that (Q3a). Beside a named place it
      is Impactpool's "Remote | <duty station>" tag, and the place decides.
    - An everywhere word, alone or beside remote wording: "Worldwide", "Home
      based - Worldwide", "US - Remote, Global - Remote", "United States +
      International (Remote)". It admits, even beside an office city or another
      region, because the world includes the owner (Q3a, Q3c). "International"
      counts only with remote wording in the same option.
    - Only `remote_regions` terms, with remote wording somewhere in the job:
      "Home based - <region>", "<region> - Remote", "Remote | <region>". It
      admits (Q3b), even beside an office city (Q3c). A region with no remote
      wording at all is a bare region, which still defers to Layer 5.
    - Anything else admits nothing here: a city, a country or region outside
      `remote_regions` ("Germany - Remote", "USA - MA - Remote"), a placeholder.
      The rest of `matches_rules` judges the field: a bare country or region
      defers to Layer 5, and a place is dropped.

    An empty field admits when a remote keyword is in the job, as one always
    has: a remote role whose listing gives no place at all names no region.

    A remote keyword is read wherever `match_in` reads it, so under
    `title_only` one in the location field says nothing (`field_in_haystack`).
    The home-base and everywhere words are location vocabulary, not configured
    keywords, and are always read in the field. An everywhere word is read only
    there: "Global" opens many a title ("Global Health ...") without saying
    where anyone works.
    """
    remote_cf = [_lower(rk) for rk in remote_keywords]
    keyword_in_job = any(rk in hay_cf for rk in remote_cf)
    if not loc_field_cf.strip():
        return _REMOTE_ANYWHERE_REASON if keyword_in_job else None
    wording = (
        keyword_in_job
        or any(tok in hay_cf or tok in loc_field_cf for tok in _GENERIC_LOCATION_TOKENS)
        or bool(_EVERYWHERE.search(loc_field_cf))
    )
    if not field_in_haystack:
        remote_cf = []
    bare = 0
    other = 0
    for raw_seg in _LOCATION_SPLIT.split(loc_field_cf):
        seg = raw_seg.strip()
        if not seg:
            continue
        remainder = seg
        for term in (*remote_cf, *_GENERIC_LOCATION_TOKENS):
            remainder = remainder.replace(term, " ")
        option_says_remote = remainder != seg
        if option_says_remote:
            remainder = _REMOTE_NOISE.sub(" ", remainder)
        everywhere = bool(_EVERYWHERE.search(remainder)) or (
            option_says_remote and bool(_INTERNATIONAL.search(remainder))
        )
        if everywhere:
            remainder = _INTERNATIONAL.sub(" ", _EVERYWHERE.sub(" ", remainder))
            if option_says_remote or not _LETTER.search(remainder):
                return _REMOTE_ANYWHERE_REASON
        if not _LETTER.search(remainder):
            bare += 1
            continue
        if (
            wording
            and remote_region_pattern is not None
            and remote_region_pattern.search(remainder)
            and not _LETTER.search(remote_region_pattern.sub(" ", remainder))
        ):
            return _REMOTE_REGION_REASON
        other += 1
    if bare and not other:
        return _REMOTE_ANYWHERE_REASON
    return None


def _location_drop_rule(
    loc_field_cf: str,
    remote_kw_present: bool,
    conditional_locations: list[str],
) -> str:
    """Name why the location rules rejected a job.

    Reached only from the failing branch of `matches_rules`, so the listed
    locations are already known not to match, and — since WP8f — the field is
    already known not to be empty: `matches_rules` defers that case (SP4f)
    before this function is ever called. The order below is the order the remaining
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
    remote_region_pattern: re.Pattern[str] | None = None,
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
    - A home-based, worldwide or remote field that is not in one place (SP4f,
      the owner's Q3) is admitted: home-based or worldwide anywhere, or across a
      region in `remote_regions`, even beside an office city. See
      `_remote_admission`, which runs before the conditional cities so that a
      home-based option is not gated on an office's hybrid arrangement.
    - A field that is empty (WP8f, revised by SP4f's Q4) is deferred to Layer 5
      with `_EMPTY_PENDING_REASON`, and fails closed there like a placeholder.
      Only when `locations` is empty, so there is nothing to search for, is it
      admitted outright with `_LOCATION_EMPTY_ADMITTED_REASON`.

    `hybrid_pattern` gates `conditional_locations`, `non_place_pattern` carries
    the configured `non_place_locations` and `remote_region_pattern` the
    configured `remote_regions`. All three must be built once by the caller —
    `build_hybrid_pattern(rules)`, `build_non_place_pattern(rules)` and
    `build_remote_region_pattern(rules)` — and passed down, never rebuilt here,
    since this runs once per job. The last two are keyword-only and default to
    None so they can never be mistaken for the hybrid one. None narrows each
    state to the shapes recognised in code; it does not switch either off.

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
        # Only names the drop rule now. Until 2026-10-06 a remote keyword also
        # admitted any field none of whose options named a city outright, so
        # "Germany - Remote" passed wherever Germany was; `_remote_admission`
        # is the one remote reading since (the owner's tightening).
        remote_kw_present = any(_lower(rk) in hay_cf for rk in remote_keywords)
        if loc_ok:
            reasons.append("locations: matched")
        elif remote_reason := _remote_admission(
            loc_field,
            hay_cf,
            remote_keywords,
            remote_region_pattern,
            field_in_haystack=match_in != "title_only",
        ):
            reasons.append(remote_reason)
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
            # A genuinely empty field, as opposed to WP8d's "present but names
            # no place" — the branch above already refuses that case for an
            # empty field, so this is reached only when the field truly has
            # nothing in it. Deferred to Layer 5 and failing closed there (SP4f,
            # Q4), unless there is no list to settle it against.
            reasons.append(_EMPTY_PENDING_REASON if locations else _LOCATION_EMPTY_ADMITTED_REASON)
        else:
            return False, [_location_drop_rule(loc_field, remote_kw_present, conditional_locations)]

    if not reasons and (include_keywords or locations or conditional_locations):
        reasons.append("matched rules")
    if not reasons and not include_keywords and not locations and not conditional_locations:
        reasons.append("no filters (pass)")

    return True, reasons
