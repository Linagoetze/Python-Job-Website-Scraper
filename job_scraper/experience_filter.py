"""Experience-level filtering.

Layer 3 — title heuristic: excludes jobs whose title contains seniority
           signal words (Senior, Lead, Director, etc.). Zero extra HTTP
           requests. Configured via rules.json.

Layer 5 — detail-page parsing: fetches each job's detail_url, strips HTML,
           and reads the experience requirement (_read_years_requirement:
           the largest requirement, the lowest alternative route). Jobs
           requiring >= 3 years are excluded; jobs with no requirement or
           <= 2 years are kept. Always runs, but only for jobs not already in the store
           — it is the one layer that costs an HTTP request per job.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from typing import Any

from bs4 import BeautifulSoup

from job_scraper import JobRecord
from job_scraper.drops import LAYER_DETAIL, layer_short
from job_scraper.filtering import (
    _EMPTY_CONFIRMED_REASON,
    _EMPTY_PENDING_REASON,
    _HYBRID_CONFIRMED_REASON,
    _HYBRID_PENDING_REASON,
    _UNRESOLVED_CONFIRMED_REASON,
    _UNRESOLVED_PENDING_REASON,
    DROP_RULE_KEY,
    _build_title_keyword_pattern,
    build_title_keyword_matchers,
    title_keyword_rule,
)
from job_scraper.robots import RobotsDisallowed, host_of
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
# SP4f's pair for an empty field (the owner's Q4). Same state, same failure
# direction, separate strings: an empty field and a placeholder have different
# causes and different fixes, and the drop log must not merge them.
RULE_LOCATION_EMPTY_NOT_LISTED = "location: no location given, description names no listed place"
RULE_LOCATION_EMPTY_UNVERIFIED = "location: no location given, could not read the description"

# Each deferred location state Layer 0 hands over, with what settles it: the
# reason it becomes when the description names a listed place, and the rules
# for a drop that was checked and one that could not be.
_LOCATION_DEFERRALS = {
    _UNRESOLVED_PENDING_REASON: (
        _UNRESOLVED_CONFIRMED_REASON,
        RULE_LOCATION_NOT_LISTED,
        RULE_LOCATION_UNVERIFIED,
    ),
    _EMPTY_PENDING_REASON: (
        _EMPTY_CONFIRMED_REASON,
        RULE_LOCATION_EMPTY_NOT_LISTED,
        RULE_LOCATION_EMPTY_UNVERIFIED,
    ),
}

# Marks an exclusion this run could not actually verify — no URL, a fetch or
# parse error, or no pattern configured — as opposed to one that was checked and
# failed. Both deferred states (hybrid and unresolvable location) fail closed, so
# both can be dropped by a network hiccup, and neither may be persisted as a
# permanent 'rejected'. One key, because it is one fact about the run rather than
# two facts about two filters (WP8d; was `hybrid_unverified` in WP5/WP6).
UNVERIFIED_KEY = "unverified_this_run"

# What a kept job's experience_level says when its detail page was fetched but
# held no posting (SP4c). Its own value, never "unspecified": that one means a
# posting was read in full and states no requirement, and an unreadable page
# says nothing of the kind.
EXPERIENCE_UNREADABLE = "unchecked (page unreadable)"
LEVEL_UNSPECIFIED = "unspecified"

# What this run learnt about a job's detail page, carried on the job dict like
# UNVERIFIED_KEY and DROP_RULE_KEY so the pipeline can count per source without
# a second return value. Absent when no fetch was attempted (no URL).
PAGE_STATE_KEY = "detail_page_state"
PAGE_READ = "read"
PAGE_UNREADABLE = "unreadable"
PAGE_FAILED = "failed"
# A fetch robots.txt refused, apart from one that merely failed (SP4f review):
# a failure is retried and may succeed next run, a refusal recurs every run
# until the owner exempts the host, so the summary has to name it as such.
PAGE_REFUSED = "refused"

# A detail page is unreadable when its stripped text is shorter than this (SP4c,
# measured against the store on 2026-10-01). Every stored description of a shell
# was 159 characters or fewer; every one of a posting read in full was 2,060 or
# more; nothing lay between. 500 sits well clear of both ends of that gap, so a
# short page that really is a posting still has room to be longer than a shell,
# and a shell has room to grow a cookie banner without being read as a posting.
MIN_READABLE_CHARS = 500

# A JS-shell notice only proves a shell while the page is short. A fully rendered
# posting can carry the same noscript line in its footer, and must not be called
# unreadable for it. 2,000 is just under the shortest readable posting stored.
_SHELL_MARKER_CEILING = 2_000
_JS_SHELL_MARKER = re.compile(
    r"enable\s+javascript|javascript\s+(?:is\s+)?(?:required|disabled|must\s+be\s+enabled)"
    r"|requires?\s+javascript|turn\s+on\s+javascript",
    re.IGNORECASE,
)


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
            kw_excluded.append(dict(job, **{DROP_RULE_KEY: title_keyword_rule(title, kw_matchers)}))
        elif sen_pattern and sen_pattern.search(title):
            sen_excluded.append(dict(job, **{DROP_RULE_KEY: _seniority_rule(title, sen_matchers)}))
        else:
            kept.append(job)

    return kept, kw_excluded, sen_excluded


# ---------------------------------------------------------------------------
# Layer 5 — detail-page experience extraction
# ---------------------------------------------------------------------------

# How Layer 5 reads a years requirement (SP4e). Not a list of phrasings each
# yielding a number, and no longer the smallest number in the text: SP4b
# measured that reading as wrong in both directions, an age or the employer's
# own history excluding a job, and a narrow skill or an "additional N years"
# clause letting a senior one through. The reading has three steps.
#
# 1. Find every *figure*: a number with a unit of time, in English, Swedish,
#    Danish or German ("3 years", "five (5) years", "3–5 yrs", "3 års").
# 2. Decide whether each figure is a *requirement*. It is when it is tied to
#    experience ("3 years of relevant experience", "3 års erfarenhet"), stated
#    as a minimum ("at least 3 years", "3+ years"), or tied to a qualification
#    ("a Master's degree and 2 years"). It is not when its context says it is
#    something else: an age, a time ago, a window ("in the last 3 years"), a
#    contract's length, a cap ("up to 7 years"), an additive clause ("an
#    additional 2 years ... in lieu of"), a preference ("ideally 5 years"), or
#    the employer speaking about itself.
# 3. Combine the requirements. A candidate must meet every requirement, so
#    separate requirements combine by the *largest*. But a posting may offer
#    alternative routes to the same role, one per qualification or per level
#    hired at, and the owner qualifies through whichever asks least (SP4b Q1).
#    So routes combine by the *smallest*, and that route then joins the other
#    requirements. A route offered in lieu of the main requirement can only
#    lower the answer, and a qualification standing in for some years takes
#    them off it.
#
# The errors are arranged to fall on the kept side. A figure wrongly taken as a
# route can only lower the answer, and a vetoed figure is simply not read; only
# a figure wrongly taken as a requirement raises it, which is why step 2 asks
# for a tie, a minimum or a qualification, and never reads a bare "N years".

_EN_NUMBERS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "fifteen": 15,
    "twenty": 20,
}
# Swedish, Danish and Norwegian. Kept apart from the English words because "to"
# and "ni" are also English and Danish for something else: a Nordic word counts
# only before a Nordic unit, and an English one only before an English unit.
_NORDIC_NUMBERS: dict[str, int] = {
    "ett": 1,
    "en": 1,
    "et": 1,
    "två": 2,
    "to": 2,
    "tre": 3,
    "fyra": 4,
    "fire": 4,
    "fem": 5,
    "sex": 6,
    "seks": 6,
    "sju": 7,
    "syv": 7,
    "åtta": 8,
    "otte": 8,
    "nio": 9,
    "ni": 9,
    "tio": 10,
    "ti": 10,
}
_NORDIC_UNITS = frozenset({"år", "års"})
_MONTH_UNITS = frozenset({"month", "months", "månad", "månader", "månaders", "måned", "måneder"})

_NUMBER = (
    r"(?:\d{1,2}(?:[.,]5)?|"
    + "|".join(sorted({*_EN_NUMBERS, *_NORDIC_NUMBERS}, key=len, reverse=True))
    + r")"
)
# One figure. A range is one figure read at its lower bound, so "3–5 years"
# never yields a separate 5. A number restated in brackets ("five (5) years",
# "5 (five) years") is one figure, read by its first form. The lookbehind
# keeps a year such as 2026, or a grade such as P-2, from lending its digits.
_FIGURE = re.compile(
    rf"(?<![\w,/-])(?<!\d\.)(?P<n>{_NUMBER})(?:\s*\(\s*{_NUMBER}\s*\))?"
    rf"(?P<range>\s*(?:-|–|—|to|till|until)\s*{_NUMBER}(?:\s*\(\s*{_NUMBER}\s*\))?)?"
    r"\s*(?P<plus>\+)?\s*"
    r"(?P<unit>years?|yrs?|års?|jahren?|months?|månad(?:er|ers)?|måned(?:er)?)\b"
    r"(?P<plus_after>\s*\+)?",
    re.IGNORECASE,
)

# Step 2: what follows a figure that ties it to experience. A few words may sit
# between ("of relevant progressively responsible experience"), never a full
# stop. "working" and "as a" tie it as firmly as the noun does.
_TIE_AFTER = re.compile(
    r"^\s*(?:['’]s?\s*)?(?:of|av|af|in|i)?\s*(?:[\w’'/&-]+,?\s+){0,5}?"
    r"(?:experience|expertise|erfarenhet|erfaring|erfahrung|berufserfahrung"
    r"|arbetslivserfarenhet|working\b|worked\b|as\s+an?\s)"
    # "3–5 years selling medical devices", "5 years leading teams", but not
    # "2 years following graduation".
    r"|^\s*(?!(?:during|following|starting|beginning|including|according|remaining)\b)[a-z]+ing\b",
    re.IGNORECASE,
)
_EXPERIENCE_NOUN = re.compile(
    r"\b(?:experience|expertise|erfarenhet|erfaring|erfahrung)", re.IGNORECASE
)
_MINIMUM_BEFORE = re.compile(
    r"(?:\bminimum(?:\s+of)?|\bmin\.?|\bat\s+least|\bno\s+less\s+than|\bnot\s+less\s+than"
    r"|\bminst|\bmindst|\bmindestens)\s*[(:]?\s*$",
    re.IGNORECASE,
)
_MINIMUM_WORD_BEFORE = re.compile(
    r"(?:\bminimum(?:\s+of)?|\bmin\.|\bat\s+least|\bno\s+less\s+than|\bnot\s+less\s+than|\bminst"
    r"|\bmindst|\bmindestens)\s*[(:]?\s*$",
    re.IGNORECASE,
)
_MINIMUM_AFTER = re.compile(r"^\s*(?:minimum|min\.?)\b", re.IGNORECASE)
# A range or a "+" reads as a requirement when a field follows it ("3–5 years
# in customer support"), but not when a full stop or a comma does ("Duration:
# 3–4 years, subject to funding").
_FIELD_AFTER = re.compile(r"^\s*(?:['’]s?\s*)?(?:of|in|as|at|within|working|av|inom|i)\b", re.I)

# What precedes a figure that makes it something other than a requirement: an
# age, a window or a time ago, a cap, an additive clause, a contract's length.
_VETO_BEFORE = re.compile(
    r"(?:(?:\baged?|\blast|\bpast|\bnext|\bfirst|\bwithin(?:\s+the)?|\bevery|\bafter"
    r"|\bnearly|\balmost|\bspent|\bspanning"
    r"|(?<!looking\s)(?<!seeking\s)\bfor(?:\s+(?:over|more\s+than|almost|nearly|about|around|some))?"
    r"|\bsince|\badditional|\bfurther|\bextra|\bup\s+to|\bmaximum(?:\s+of)?|\bmax\.?"
    r"|\bno\s+more\s+than|\bnot\s+more\s+than|\bless\s+than|\bfewer\s+than|\bunder"
    r"|\bcommit(?:ment)?\s+(?:to|for)|\bsenaste|\binom|\bsedan|\bunder\s+de)\s*\(?"
    # A header may end in a colon ("Duration: 3–4 years"). "Looking for:" may
    # not, which is why the colon is allowed only here.
    r"|(?:\bage(?:\s+of)?|\bduration(?:\s+of)?|\bperiod(?:\s+of)?|\bterm(?:\s+of)?"
    r"|\bcommitment(?:\s+of)?|\bcontract(?:\s+of)?|\blength(?:\s+of\s+\w+)?)\s*[(:]?)\s*$",
    re.IGNORECASE,
)
# "You have worked in finance for 5+ years": a "for" after work is a career,
# not a contract ("working days for 12 months" is not work in this sense). It
# lifts the "for" veto and ties the figure to experience.
_WORKED_FOR = re.compile(
    r"\b(?:worked|been\s+working|been\s+employed|served)\b[^.;:]{0,60}?"
    r"\bfor(?:\s+(?:over|more\s+than|about|around|some))?\s*$",
    re.IGNORECASE,
)
# "Age: interns must be at least 18 years" names the age before the figure.
_AGE_BEFORE = re.compile(r"\bage\b[^.;]{0,60}$", re.IGNORECASE)
_VETO_AFTER = re.compile(
    r"^\s*(?:old\b|of\s+age|or\s+(?:older|over|above)|ago\b|from\s+now|later\b|gammal|gamle?\b"
    r"|before\b|prior\s+to\b"
    r"|of\s+(?:\w+\s+){0,2}?(?:education|schooling|study|studies|validity)\b"
    r"|of\s+(?:history|existence|operations?)\b"
    r"|(?!of\b)(?:\w+\s+)?(?:contract|duration|commitment|appointment|fixed[-\s]term|renewable"
    r"|horizon|warranty|history|track\s+record|anniversary|validity)\b"
    r"|valid\b|in\s+(?:business|operation|existence)"
    # Years of study, not of work: "minst två års högskolestudier".
    r"|\w*studier\b|\w*utbildning\b|\w*uddannelse\b)",
    re.IGNORECASE,
)

# A preference is not a requirement. Read before the figure only when it says so
# unmistakably ("ideally", "Preferred:"). A bare "preferred" just before a figure
# usually ends the item before it in a stripped list ("... a related field
# preferred 6 years of experience"), so it is not read there.
_PREFERENCE_BEFORE = re.compile(
    r"(?:\bideally|\bpreferably|\bdesirably|\boptimally|\bpreferred:|\bdesirable:|\bdesired:"
    r"|\bnice\s+to\s+have:|\bmeriterande:?|\bhelst)\s*(?:\w+\s+){0,2}?$",
    re.IGNORECASE,
)
# A heading opens a section, and every figure under it until the next heading
# of the other kind belongs to it ("Minimum qualifications: 1+ years ...
# Preferred qualifications: 4+ years ..."). Only a capitalised heading word
# counts, because "a related field preferred Experience: ..." ends an item; the
# exceptions are phrases that are only ever a heading or a preference.
_PREFERENCE_HEADING = re.compile(
    r"(?<![\w-])(?:Preferred|Desired|Desirable|Bonus|Optional|PREFERRED|DESIRED|DESIRABLE|BONUS)"
    r"(?i:\s+(?:qualifications?|skills?|experience|requirements?|points?|criteria|competenc\w*"
    r"|profile|background))\b"
    r"|(?<![\w-])(?:Preferred|Desired|Desirable|PREFERRED|DESIRED|DESIRABLE)\s*:"
    r"|(?i:\b(?:nice|good)[-\s]to[-\s]haves?\b|\bbonus\s+points?\b|\bit\s+would\s+be\s+(?:great|nice)"
    r"\s+if\b|\b(?:det\s+är\s+)?meriterande\b|\bextra\s+plus\b)"
)
_REQUIREMENT_HEADING = re.compile(
    r"(?<![\w-])(?:Minimum|Basic|Required|Essential|Mandatory|Key|MINIMUM|BASIC|REQUIRED|ESSENTIAL)"
    r"(?i:\s+(?:qualifications?|requirements?|skills?|experience|criteria|competenc\w*))\b"
    r"|(?<!Preferred\s)(?<!Desired\s)(?<!Desirable\s)(?<!Bonus\s)(?<!Optional\s)(?<!PREFERRED\s)"
    r"(?<!DESIRED\s)(?<!DESIRABLE\s)(?<!BONUS\s)(?<![\w-])"
    r"(?:Requirements|REQUIREMENTS|Qualifications|QUALIFICATIONS|Must[-\s]haves?|MUST[-\s]HAVES?"
    r"|Krav|KRAV)\b"
    r"|(?i:\bwhat\s+you(?:['’]ll|\s+will)?\s+(?:need|bring)\b|\bwe(?:['’]re|\s+are)\s+looking\s+for\b"
    r"|\b(?:other|additional|general)\s+requirements\b|\bwe\s+believe\s+you\s+(?:have|are|bring)\b"
    r"|\b(?:essential|required)\s*:)"
)
_PREFERENCE_AFTER = re.compile(
    r"\b(?:preferred|preferable|desirable|desired|advantageous|an?\s+(?:additional\s+|added\s+)?"
    r"(?:asset|plus|advantage|bonus)|nice\s+to\s+have|meriterande|önskvärt|ett\s+plus)\b",
    re.IGNORECASE,
)
# "5+ years of sales experience, knowledge of Salesforce is a plus": after a
# comma, a preference whose subject is another qualification is about that.
_OTHER_SUBJECT = re.compile(
    r"[,;]\s*(?:and\s+|but\s+)?(?:[\w’'-]+\s+){0,2}?(?:knowledge|familiarity|proficiency|fluency"
    r"|understanding|exposure|certifications?|certificates?|degrees?|background|skills?|ability"
    r"|interest|languages?|command|competence|qualifications?)\b[^,;]*$",
    re.IGNORECASE,
)
# A preference after the figure is read to the end of its sentence or list item
# ("Applicants with 4 years or more of relevant experience in ... are
# preferred"). A stripped page shows a new list item only as a capital letter
# after a lower-case word ("... payroll administration Fluent in Spanish"),
# except after a word that cannot end an item ("the delivery of Cloud
# services").
_PREFERENCE_REACH = 250
_NAMED_PARENTHETICAL = re.compile(
    r"\((?!\s*(?:is\s+|are\s+|would\s+be\s+)?(?:an?\s+)?(?:added\s+|additional\s+)?"
    r"(?:preferred|preferable|desirable|desired|advantageous|asset|plus|advantage|bonus"
    r"|nice\s+to\s+have)\s*\))[^)]*\)"
)
_ITEM_START = re.compile(
    r"(?<=[a-z])(?<!\bof)(?<!\bin)(?<!\bon)(?<!\bat)(?<!\bby)(?<!\bto)(?<!\ba)(?<!\ban)(?<!\bthe)"
    r"(?<!\band)(?<!\bor)(?<!\bwith)(?<!\bfor)(?<!\bfrom)\s+(?=[A-Z][a-z])"
)

# The employer speaking about itself ("with more than 40 years of experience,
# our organisation ..."): first person plural, and nothing addressed to a
# candidate, in the figure's sentence.
_SENTENCE_END = re.compile(r"[.!?;\n•]")
_FIRST_PERSON_PLURAL = re.compile(
    r"\b(?:we|we['’](?:ve|re)|our|ours|us|vi|vår|vårt|våra|vores|wir|unser\w*)\b", re.IGNORECASE
)
_ADDRESSED_TO_CANDIDATE = re.compile(
    r"\b(?:you|your|you['’](?:ll|ve|re|d)|candidates?|applicants?|du|dig|din|ditt|dina|sie"
    r"|required|requires?|requirements?|must|should|essential|qualifications?|qualified"
    r"|someone|somebody|person|individual|profile|looking\s+for|seeking|ideal|need|needs"
    r"|expect|expects|must-haves?|bring)\b",
    re.IGNORECASE,
)

# Step 3: a figure tied to a qualification is one route among alternatives.
# "Master" only as a degree, never "master data" or "Scrum Master"; "degree"
# never as "a high degree of autonomy".
_QUALIFICATION = (
    r"(?:(?<!high\s)(?<!great\s)(?<!certain\s)(?<!some\s)degree|bachelor\S*|master['’]?s\b"
    r"|master\s+(?:degree|of)\b|ph\.?\s?d|doctora\w*|diploma|secondary\s+(?:education|school)"
    r"|high\s+school|first[-\s]level|b\.?sc|m\.?sc|mba|examen|gymnasie\w*"
    r"|(?:university|college)\s+(?:graduates?|education))"
)
# "<qualification> ... with|and|plus <figure>": the qualification and its years
# are one route. Nothing between them may be another figure, or the link word
# belongs to that figure instead ("a degree. 6 years of experience, including
# at least 1 year leading a team" is not a route of 1).
_ROUTE_BEFORE = re.compile(
    rf"\b{_QUALIFICATION}[^.;:•\n]{{0,300}}?"
    r"(?:\bwith|\band|\bplus|\+|\bfollowed\s+by|\bcombined\s+with|\bin\s+combination\s+with"
    r"|\boch|\bmed|\bog)\s+(?:an?\s+)?(?:minimum\s+(?:of\s+)?|at\s+least\s+|minst\s+)?$",
    re.IGNORECASE,
)
# "<figure> (with a bachelor's degree)", "<figure> of experience with a Master's".
# A posting that hires at several levels at once states one figure per level
# ("two years for level C, five for level B", "Entry level: 1-4 years").
# Each level is a route in, exactly as each degree is.
_LEVEL = (
    r"(?:(?:category|band|level|grade|tier)\s+[A-Z0-9]\b|[PGD]-?\d\b|(?:entry|junior|mid|senior)"
    r"[-\s]level)"
)
_ROUTE_AFTER = re.compile(
    r"^[^.;•\n]{0,100}?(?:\(\s*(?:with\s+|for\s+|if\s+only\s+)?(?:an?\s+|the\s+)?[^)]{0,30}?"
    rf"(?:{_QUALIFICATION}|{_LEVEL})|\b(?:with|for|if\s+only)\s+(?:an?\s+|the\s+)?"
    rf"(?:[\w’']+\s+){{0,2}}?(?:{_QUALIFICATION}|{_LEVEL}))",
    re.IGNORECASE,
)
_LEVEL_BEFORE = re.compile(
    rf"{_LEVEL}[^.;•\n]{{0,40}}?:?\s*(?:essential\s*:?\s*)?"
    r"(?:at\s+least|minimum(?:\s+of)?)?\s*$",
    re.IGNORECASE,
)
# The same, labelled loosely: "Junior: 0–2 years. Mid: 3–5 years.", "0–2 years
# (junior)", "2 years for junior candidates". A bare level word counts only
# in these places, so "experience with senior stakeholders" is no route.
_LEVEL_WORD = r"(?:junior|mid|mid[-\s]?level|intermediate|senior|entry(?:[-\s]?level)?|graduate)"
_LEVEL_HEADING_BEFORE = re.compile(
    rf"(?:^|[\s.;•(])(?:for\s+)?{_LEVEL_WORD}(?:\s+(?:level|roles?|positions?|candidates?|profiles?))?"
    r"\s*:\s*(?:at\s+least|minimum(?:\s+of)?|min\.?)?\s*$",
    re.IGNORECASE,
)
_LEVEL_WORD_AFTER = re.compile(
    rf"^[^.;•\n]{{0,60}}?(?:\(\s*{_LEVEL_WORD}\b[^)]{{0,20}}\)"
    rf"|\bfor\s+(?:the\s+)?{_LEVEL_WORD}\s+(?:level|roles?|positions?|candidates?|profiles?)\b)",
    re.IGNORECASE,
)
# "... 3 years of X, or 5 years of Y": alternatives with no qualification named.
_OR_BEFORE = re.compile(
    r"\b(?:or|eller|alternatively)\s+(?:an?\s+)?(?:minimum\s+(?:of\s+)?|at\s+least\s+)?$",
    re.IGNORECASE,
)
# "... OR a Master's degree": the figure before it was one route of two.
_OR_QUALIFICATION = re.compile(rf"\b(?:or|eller)\b[^.;]{{0,40}}?\b{_QUALIFICATION}", re.IGNORECASE)
# A route offered instead of the main requirement ("a first degree with six
# years may be accepted in lieu of the above").
_SUBSTITUTE = re.compile(
    r"\bin\s+lieu\b|\binstead\s+of\b|\bin\s+place\s+of\b|\bmay\s+be\s+(?:accepted|considered)"
    r"|\bwill\s+be\s+(?:accepted|considered)|\bsubstitut\w*",
    re.IGNORECASE,
)
# The years a qualification stands in for ("a relevant degree can count in place
# of two years of experience"): not a requirement, a reduction of one.
_DEDUCTION_BEFORE = re.compile(
    r"(?:\bin\s+lieu\s+of|\binstead\s+of|\bin\s+place\s+of|\bsubstitut\w*\s+for)\s*$",
    re.IGNORECASE,
)

# How far around a figure its context is read: far enough for "a minimum of",
# not so far that it borrows the context of the item before.
_CONTEXT_CHARS = 100
# How far back a qualification may name the route a figure belongs to. UN
# postings list a whole field of study between the degree and its years ("an
# advanced degree in economics, statistics, public policy, or a closely related
# field of the social sciences, with a minimum of two years").
_ROUTE_CHARS = 320
# No posting asks for more years than this. A larger figure is the employer's
# history ("the charity has spent nearly 60 years helping ...") or an age.
_MAX_PLAUSIBLE_YEARS = 25


class _Kind(Enum):
    """What part of the requirement a figure is."""

    # Must be met whatever the route.
    REQUIREMENT = "requirement"
    # One of several ways in, by qualification or by level.
    ROUTE = "route"
    # A way in offered in lieu of the posting's main requirement.
    SUBSTITUTE = "substitute"
    # Years a qualification stands in for.
    DEDUCTION = "deduction"


@dataclass(frozen=True)
class _Figure:
    """One figure the reading took as part of the requirement, and what part."""

    years: int
    kind: _Kind


# The PhD rule (SP4e, F14). A doctorate decides only when the text requires it
# in so many words: a requiring verb or a requirement heading just before the
# mention ("must hold a PhD", "Requirements: PhD in ..."), or a requiring
# predicate just after it ("A Ph.D. in economics is required"). A "must" or a
# "need" elsewhere in the clause is not enough ("a team of PhD economists ...
# must be fluent in English"). It is not required when it is offered beside
# another degree ("a Master's or PhD") or as a preference.
# "Ph.D." keeps its own full stop, which would otherwise end the clause.
_DOCTORATE = (
    r"(?:ph\.\s?d\.|ph\.?\s?d\b|d\.?phil\b|doctorate|doctoral\s+degree|doktorsexamen|doktorgrad)"
)
# A mention that names the job or the people rather than a qualification is not
# one: "PhD students", "mandatory PhD courses", "PhD economists",
# "PhD-holding researchers".
_DOCTORATE_MENTION = re.compile(
    rf"{_DOCTORATE}(?![\s‑-]*(?:students?|stud(?:y|ies)|courses?|candidates?|positions?|projects?"
    r"|programmes?|programs?|thesis|level|supervis\w*|education|fellows?|researchers?|economists?"
    r"|scientists?|holders?|holding|staff|teams?|colleagues?|graduates?|peers?|experts?))"
    r"(?![‑-]\w)",
    re.IGNORECASE,
)
_DOCTORATE_ALTERNATIVE = re.compile(
    rf"(?:degree|master\S*|m\.?sc|mba|bachelor\S*|equivalent)\s*,?\s*(?:or|and/or|/)\s*"
    rf"(?:an?\s+)?(?:{_DOCTORATE})"
    rf"|(?:{_DOCTORATE})(?:\s+in\s+[\w\s,&-]{{0,50}}?)?\s*[,(]?\s*(?:or|and/or|/)\s*"
    rf"(?:an?\s+|the\s+)?(?:[\w-]+\s+){{0,3}}?"
    rf"(?:degree|master\S*|m\.?sc|mba|bachelor\S*|equivalent|corresponding|comparable|similar)",
    re.IGNORECASE,
)
_DOCTORATE_REQUIRED_BEFORE = re.compile(
    r"(?:\b(?:require[sd]?|requiring|must\s+(?:hold|have|possess|be\s+awarded)"
    r"|should\s+(?:hold|have|possess)|needs?\s+(?:to\s+(?:hold|have)\s+)?|to\s+(?:hold|have)"
    r"|holds?|holding|possess(?:es|ing)?|(?:has|have)\s+(?:obtained|completed|earned|been\s+awarded)"
    r"|requires?\s+that\s+(?:the\s+)?\w+\s+(?:has|have|holds?))\s+(?:[\w’'-]+\s+){0,3}?"
    r"|\b(?:qualifications?|requirements?|criteria|experience|education)\s*(?:required\s*)?"
    r"(?:for\s+the\s+(?:position|role)\s+)?(?:includes?\s*)?:?\s*(?:an?\s+)?(?:completed\s+)?)$",
    re.IGNORECASE,
)
_DOCTORATE_REQUIRED_AFTER = re.compile(
    r"^[^.;:!?]{0,80}?\b(?:is|are|will\s+be)\s+(?:an?\s+)?(?:absolute\s+)?"
    r"(?:required|mandatory|essential|necessary|requirements?|prerequisites?|must)\b",
    re.IGNORECASE,
)
_DOCTORATE_PREFERRED = re.compile(
    r"\b(?:preferred|preferably|desired|desirable|ideally|a\s+plus|an?\s+(?:asset|advantage)"
    r"|beneficial|nice\s+to\s+have|bonus|advantageous|meriterande|preference)\b",
    re.IGNORECASE,
)
# "a PhD, or a foreign degree judged equivalent to a PhD" offers no way in
# without a doctorate.
_DOCTORATE_EQUIVALENT = re.compile(rf"equivalent\s+to\s+(?:an?\s+)?{_DOCTORATE}", re.IGNORECASE)
# A doctorate's clause: its sentence, cut to this many characters either side so
# that one long stripped list does not lend it a "required" from elsewhere.
_DOCTORATE_CONTEXT_CHARS = 70


# Page furniture that is never the posting: a consent dialog, the menu, the
# footer. Left in, a Teamtailor description opened with the cookie banner and a
# menu of sign-in links (SP8). `<header>` stays, because many pages put the
# title, place and work type there, which the location test reads; so do
# scripts, whose JSON-LD can carry a posting's workplace type.
_CHROME_TAGS = ("dialog", "nav", "footer")


def _strip_html(html: str) -> str:
    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup.find_all(_CHROME_TAGS):
            tag.decompose()
        return soup.get_text(" ", strip=True)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)


def is_unreadable(text: str, supplied: bool = False) -> bool:
    """True when a fetched detail page, stripped, holds no posting to judge.

    A 200 response is not a reading: a client-rendered page arrives as a title
    and a "please enable JavaScript" notice. Decided on the stripped text alone,
    so it costs nothing and needs no knowledge of the source.

    `supplied` marks a description the reader took from the platform's own data
    rather than a page Layer 5 fetched (SP4d). The length test is a test for a
    page that came back as chrome around nothing; a description field has no
    chrome, so a short one is a short posting. jobsinlund's run from about 420
    characters. Only the marker test applies, and empty is still unreadable.

    The tetrapak signature (one text on several different postings of a source)
    is deliberately not here: see docs/DECISIONS.md, SP4c.
    """
    stripped = text.strip()
    if not stripped:
        return True
    if not supplied and len(stripped) < MIN_READABLE_CHARS:
        return True
    return len(stripped) < _SHELL_MARKER_CEILING and bool(_JS_SHELL_MARKER.search(stripped))


def supplied_description(job: JobRecord) -> str:
    """The description a reader supplied with the job, as Layer 5 would store it.

    '' when the reader supplied none, in which case Layer 5 fetches the detail
    page. The reader hands over plain text: knowing its platform's format is the
    reader's job, so nothing here parses HTML out of it. The pipeline compares
    this with the stored description to decide whether a stored job has been
    judged on it yet, so it must be exactly what `apply_detail_filter` stores.
    """
    return str(job.get("description_text") or "").strip()[:_MAX_DESCRIPTION_CHARS]


def _figure_years(match: re.Match[str]) -> int | None:
    """The whole years a figure states, or None when its number and unit disagree.

    A Nordic number word counts only before a Nordic unit and an English one only
    before an English unit, so "to years" is never two years.
    """
    number, unit = match.group("n").lower(), match.group("unit").lower()
    nordic_unit = unit in _NORDIC_UNITS
    if number[0].isdigit():
        value = float(number.replace(",", "."))
    elif nordic_unit or unit.startswith("må"):
        if number not in _NORDIC_NUMBERS:
            return None
        value = _NORDIC_NUMBERS[number]
    else:
        if number not in _EN_NUMBERS:
            return None
        value = _EN_NUMBERS[number]
    if unit in _MONTH_UNITS:
        return int(value // 12)
    return int(value)


def _sentence(text: str, start: int, end: int) -> str:
    """The sentence holding text[start:end], cut to _CONTEXT_CHARS * 2 either side."""
    lo = max(0, start - 2 * _CONTEXT_CHARS)
    head = text[lo:start]
    cut = [m.end() for m in _SENTENCE_END.finditer(head)]
    if cut:
        head = head[cut[-1] :]
    tail = text[end : end + 2 * _CONTEXT_CHARS]
    stop = _SENTENCE_END.search(tail)
    if stop:
        tail = tail[: stop.start()]
    return head + text[start:end] + tail


def _preferred_after(text: str, end: int) -> bool:
    """True when a preference later in the figure's sentence is about this figure.

    It is not when another figure, or a second mention of experience, comes
    between them: "5+ years of account management experience, ... and agency
    experience preferred" prefers the agency experience, not the five years.
    """
    tail = text[end : end + _PREFERENCE_REACH]
    for boundary in (_SENTENCE_END, _ITEM_START):
        stop = boundary.search(tail)
        if stop:
            tail = tail[: stop.start()]
    # "(desirable)" marks the whole item; "(fintech preferred)" marks only
    # what it names.
    tail = _NAMED_PARENTHETICAL.sub(" ", tail)
    preference = _PREFERENCE_AFTER.search(tail)
    if preference is None:
        return False
    between = tail[: preference.start()]
    own_tie = _TIE_AFTER.match(between)
    rest = between[own_tie.end() :] if own_tie else between
    return not (
        _FIGURE.search(rest) or _EXPERIENCE_NOUN.search(rest) or _OTHER_SUBJECT.search(rest)
    )


def _preference_sections(text: str) -> list[tuple[int, int]]:
    """Where the text is under a preference heading, as (start, end) spans.

    A section runs from a preference heading to the next requirement heading,
    or to the end of the text. Reaching too far only drops figures, which keeps
    a job, so the end of the text is the safe default.
    """
    headings = sorted(
        [(m.start(), True) for m in _PREFERENCE_HEADING.finditer(text)]
        + [(m.start(), False) for m in _REQUIREMENT_HEADING.finditer(text)]
    )
    sections: list[tuple[int, int]] = []
    open_at: int | None = None
    for position, preference in headings:
        if preference and open_at is None:
            open_at = position
        elif not preference and open_at is not None:
            sections.append((open_at, position))
            open_at = None
    if open_at is not None:
        sections.append((open_at, len(text)))
    return sections


def _read_figure(
    text: str,
    match: re.Match[str],
    previous_end: int,
    preferences: Sequence[tuple[int, int]] = (),
) -> _Figure | None:
    """Step 2 for one figure: the requirement it states, or None when it states none.

    `preferences` are the text's preference sections (`_preference_sections`),
    passed in so they are found once per text rather than once per figure.
    """
    years = _figure_years(match)
    if years is None or years > _MAX_PLAUSIBLE_YEARS:
        return None
    start, end = match.span()
    before = text[max(0, start - _CONTEXT_CHARS) : start]
    after = text[end : end + _CONTEXT_CHARS]
    sentence = _sentence(text, start, end)

    # Under a preference heading only a figure stated as a minimum in words
    # still binds: some boards put their whole list of requirements under
    # "Desired qualifications", "Minimum of 5 years" among them.
    if any(lo <= start < hi for lo, hi in preferences) and not _MINIMUM_WORD_BEFORE.search(before):
        return None
    worked_for = bool(_WORKED_FOR.search(before))
    vetoed_before = _VETO_BEFORE.search(before) and not worked_for
    if vetoed_before or _VETO_AFTER.match(after) or _AGE_BEFORE.search(before):
        return None
    if _PREFERENCE_BEFORE.search(before):
        return None
    if _preferred_after(text, end):
        return None
    if _DEDUCTION_BEFORE.search(before):
        return _Figure(years=years, kind=_Kind.DEDUCTION)

    # Only the text since the previous figure can link this one to a route:
    # anything earlier belongs to that figure.
    own_before = text[max(previous_end, start - _ROUTE_CHARS) : start]
    route = bool(
        _ROUTE_BEFORE.search(own_before)
        or _LEVEL_BEFORE.search(own_before)
        or _LEVEL_HEADING_BEFORE.search(own_before)
        or _ROUTE_AFTER.match(text[end : end + _ROUTE_CHARS])
        or _LEVEL_WORD_AFTER.match(after)
    )
    tied = bool(_TIE_AFTER.match(after)) or worked_for
    minimum = bool(
        _MINIMUM_BEFORE.search(before)
        or _MINIMUM_AFTER.match(after)
        or match.group("plus")
        or match.group("plus_after")
    )
    ranged = bool(match.group("range"))
    if not (tied or minimum or route or (ranged and _FIELD_AFTER.match(after))):
        return None

    if _FIRST_PERSON_PLURAL.search(sentence) and not _ADDRESSED_TO_CANDIDATE.search(sentence):
        return None
    if route and _SUBSTITUTE.search(sentence):
        return _Figure(years=years, kind=_Kind.SUBSTITUTE)
    return _Figure(years=years, kind=_Kind.ROUTE if route else _Kind.REQUIREMENT)


def _requirement_figures(text: str) -> list[_Figure]:
    """Steps 1 and 2: every figure in the text that states a years requirement.

    A requirement joined to the one before it by "or" makes both of them
    routes: "3 years of X, or 5 years of Y" offers two ways in, as a pair of
    degrees does. So does "... with 8 years. OR a Master's degree with 6
    years", where the first degree sits too far back to be read as its own.
    """
    figures: list[_Figure] = []
    previous_end = 0
    preferences = _preference_sections(text)
    for match in _FIGURE.finditer(text):
        figure = _read_figure(text, match, previous_end, preferences)
        if figure is not None and figures and figures[-1].kind is _Kind.REQUIREMENT:
            between = text[previous_end : match.start()]
            near = len(between) < _CONTEXT_CHARS
            if (figure.kind is _Kind.REQUIREMENT and near and _OR_BEFORE.search(between)) or (
                figure.kind is _Kind.ROUTE and _OR_QUALIFICATION.search(between)
            ):
                figures[-1] = _Figure(years=figures[-1].years, kind=_Kind.ROUTE)
                if figure.kind is _Kind.REQUIREMENT:
                    figure = _Figure(years=figure.years, kind=_Kind.ROUTE)
        if figure is not None:
            figures.append(figure)
        previous_end = match.end()
    return figures


def _read_years_requirement(text: str) -> int | None:
    """The years of experience a posting requires, or None when it states none.

    Step 3 of the reading described above `_EN_NUMBERS`. Every requirement must
    be met, so they combine by the largest. Routes are alternatives, and the
    owner qualifies through whichever asks least (SP4b Q1), so they combine by
    the smallest, and that route joins the requirements. A route offered *in
    lieu of* the posting's main requirement is an alternative to all of that,
    so it can only lower the answer. A qualification standing in for some years
    ("a relevant degree can count in place of two years") takes them off.

    min() over every number in the text went in SP4e: it let a narrow skill ("at
    least 2 years with survey software") or an additive clause decide a role
    that asks for 5 (docs/DECISIONS.md).
    """
    by_kind: dict[_Kind, list[int]] = {}
    for figure in _requirement_figures(text):
        by_kind.setdefault(figure.kind, []).append(figure.years)
    needed = list(by_kind.get(_Kind.REQUIREMENT, []))
    if _Kind.ROUTE in by_kind:
        needed.append(min(by_kind[_Kind.ROUTE]))
    answer = max(needed) if needed else None
    if _Kind.SUBSTITUTE in by_kind:
        substitute = min(by_kind[_Kind.SUBSTITUTE])
        answer = substitute if answer is None else min(answer, substitute)
    if answer is not None and _Kind.DEDUCTION in by_kind:
        answer = max(0, answer - max(by_kind[_Kind.DEDUCTION]))
    return answer


def _has_phd_required(text: str) -> bool:
    """True only when the text requires a doctorate in so many words.

    Each mention is judged in its own clause. One offered beside another degree
    ("a Master's or PhD") or as a preference does not count. One that counts
    must be what the clause requires: see `_DOCTORATE_REQUIRED_BEFORE` and
    `_DOCTORATE_REQUIRED_AFTER`.
    """
    for mention in _DOCTORATE_MENTION.finditer(text):
        start, end = mention.span()
        lo = max(0, start - _DOCTORATE_CONTEXT_CHARS)
        clause = text[lo : end + _DOCTORATE_CONTEXT_CHARS]
        offset = start - lo
        head, tail = clause[:offset], clause[offset:]
        cut = [m.end() for m in _SENTENCE_END.finditer(head)]
        if cut:
            head = head[cut[-1] :]
        stop = _SENTENCE_END.search(tail, end - start)
        if stop:
            tail = tail[: stop.start()]
        clause = head + tail
        alternative = _DOCTORATE_ALTERNATIVE.search(clause) and not _DOCTORATE_EQUIVALENT.search(
            clause
        )
        if alternative or _DOCTORATE_PREFERRED.search(clause):
            continue
        if _DOCTORATE_REQUIRED_BEFORE.search(head) or _DOCTORATE_REQUIRED_AFTER.match(
            tail[end - start :]
        ):
            return True
    return False


@dataclass(frozen=True)
class _DetailSignals:
    """What one detail page said about one job.

    A record rather than a tuple: WP8d needs a second deferred-state answer out
    of the same fetch, and a seventh positional element is where a tuple stops
    being readable at the call site.
    """

    job: JobRecord
    years_required: int | None = None
    phd_required: bool = False
    fetch_failed: bool = False
    # The page was fetched and holds no posting (SP4c). Distinct from
    # fetch_failed, which is a request that did not complete, though both mean
    # "this job was not checked" and both fail the same way.
    unreadable: bool = False
    # A fetch refused by robots.txt, as opposed to one that merely failed. It is
    # not transient and not per-job: if a site disallows one detail page it
    # disallows the pattern, so the whole source loses its Layer 5 checks and
    # its stored descriptions. Counted separately so that fact gets said out
    # loud rather than left in a debug line (WP10 review).
    robots_refused: bool = False
    # None means "the description could not be read at all" (no URL, fetch
    # failed, or no pattern configured) rather than read-and-not-found. Both
    # deferred states fail closed, so the caller must tell the two apart.
    hybrid_found: bool | None = None
    listed_location_found: bool | None = None
    # The page says the job is remote where the owner can work: Workday's exact
    # "Fully Remote" label, or a "Location:" line naming worldwide or one of the
    # owner's regions (`filtering.build_page_remote_reader`). Settles only an
    # empty location field, never a placeholder or a conditional city.
    page_says_remote: bool | None = None
    description_text: str = ""


def _fetch_and_analyze(
    job: JobRecord,
    fn: Callable[[str], str],
    hybrid_pattern: re.Pattern[str] | None = None,
    location_pattern: re.Pattern[str] | None = None,
    page_remote_reader: Callable[[str], bool] | None = None,
) -> _DetailSignals:
    """Fetch a job's detail page and extract experience/PhD/deferred-state signals.

    A job whose reader supplied its description (`supplied_description`) is read
    from that instead, and nothing is fetched.
    years_required is None when no requirement was found or there was no URL.
    `unreadable` is set, and nothing else read, when the page held no posting.
    description_text is the stripped page text, capped at _MAX_DESCRIPTION_CHARS,
    or '' when nothing was fetched — WP7's scorer reads this back from the store
    rather than re-fetching.
    One fetch answers every question: the hybrid gate and WP8d's unresolvable
    location are both searched in the text already in hand, so neither costs an
    HTTP request of its own.
    """
    supplied = supplied_description(job)
    if supplied:
        # The reader already holds the posting (SP4d): read it, and spend no
        # request on a detail page that would only say the same thing, or less.
        return _analyze(
            job, supplied, hybrid_pattern, location_pattern, page_remote_reader, supplied=True
        )
    url = str(job.get("detail_url") or job.get("apply_url") or "").strip()
    if not url:
        return _DetailSignals(job=job)
    try:
        html = fn(url)
        return _analyze(
            job, _strip_html(html), hybrid_pattern, location_pattern, page_remote_reader
        )
    except RobotsDisallowed as exc:
        logger.debug("%s: robots.txt refused %r — keeping job", layer_short(LAYER_DETAIL), exc)
        return _DetailSignals(job=job, fetch_failed=True, robots_refused=True)
    except Exception as exc:
        logger.debug(
            "%s: fetch failed for %r — keeping job. Error: %s",
            layer_short(LAYER_DETAIL),
            url,
            exc,
        )
        return _DetailSignals(job=job, fetch_failed=True)


def _analyze(
    job: JobRecord,
    text: str,
    hybrid_pattern: re.Pattern[str] | None,
    location_pattern: re.Pattern[str] | None,
    page_remote_reader: Callable[[str], bool] | None = None,
    supplied: bool = False,
) -> _DetailSignals:
    """Every Layer 5 signal from one posting's text, fetched or supplied."""
    if is_unreadable(text, supplied=supplied):
        # Nothing below this line may be read off a shell: no years, no PhD,
        # and above all no "the description names no listed place".
        return _DetailSignals(job=job, unreadable=True)
    return _DetailSignals(
        job=job,
        years_required=_read_years_requirement(text),
        phd_required=_has_phd_required(text),
        hybrid_found=(bool(hybrid_pattern.search(text)) if hybrid_pattern is not None else None),
        listed_location_found=(
            bool(location_pattern.search(text)) if location_pattern is not None else None
        ),
        page_says_remote=(page_remote_reader(text) if page_remote_reader is not None else None),
        description_text=text[:_MAX_DESCRIPTION_CHARS],
    )


def judge_experience(years: int | None, phd_required: bool) -> tuple[str, str | None]:
    """The level a posting reads as, and the rule that excludes it, if one does.

    The verdict half of Layer 5, kept apart from the reading so that a run and
    the re-filter pass (SP4g) cannot come to different verdicts on the same
    figures. A doctorate is checked first, as it always was. The rule is None
    when the job stays.
    """
    if phd_required:
        return "phd_required", RULE_PHD_REQUIRED
    if years is None:
        return LEVEL_UNSPECIFIED, None
    if years <= _MAX_JUNIOR_YEARS:
        return f"junior (<={_MAX_JUNIOR_YEARS}yr)", None
    return f"senior ({years}+yr)", f"experience: {years}+ years required"


def rejudge_stored_description(job: JobRecord) -> tuple[str, str | None] | None:
    """Layer 5's years and PhD verdict on a stored job, from its stored description.

    None when there is nothing to judge: no stored description, or a level of
    EXPERIENCE_UNREADABLE (SP4c). That is decided from the level and not from the
    description's length (SP4d), because the store cannot tell a description a
    reader supplied from one that was fetched, and a supplied one can be short
    and still a real posting. A description at the storage cap may be cut short
    and is not judged either. A description is only ever stored once it passed
    `is_unreadable`, so there is no shell here to mistake for a posting.

    Years and PhD only. The location and hybrid states are settled by what a run
    read at the time (a field the store does not hold: raw_snippet), so a stored
    row cannot reproduce them and the pass never judges them (docs/DECISIONS.md).
    """
    text = str(job.get("description_text") or "")
    if not text or job.get("experience_level") == EXPERIENCE_UNREADABLE:
        return None
    if len(text) >= _MAX_DESCRIPTION_CHARS:
        # Stored text is cut at the cap and a run read the whole page, so this
        # may be a prefix. A route figure past the cut could read higher here
        # than it did there, and a rejection is never undone: leave it be.
        return None
    return judge_experience(_read_years_requirement(text), _has_phd_required(text))


def _page_state(signals: _DetailSignals) -> dict[str, str]:
    if signals.unreadable:
        return {PAGE_STATE_KEY: PAGE_UNREADABLE}
    if signals.robots_refused:
        return {PAGE_STATE_KEY: PAGE_REFUSED}
    if signals.fetch_failed:
        return {PAGE_STATE_KEY: PAGE_FAILED}
    if signals.description_text:
        return {PAGE_STATE_KEY: PAGE_READ}
    return {}


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


def _location_deferral(job: JobRecord) -> str | None:
    """The deferred location state *job* carries, or None (WP8d, SP4f)."""
    reasons = job.get("matched_reasons") or []
    return next((pending for pending in _LOCATION_DEFERRALS if pending in reasons), None)


def _resolve_unresolved_location(
    job: JobRecord, listed_location_found: bool | None
) -> JobRecord | None:
    """Settle a job whose location field named no place, against the description.

    Covers both deferred location states: a field that names no place (WP8d) and
    an empty one (SP4f, Q4). Returns the job with its pending marker rewritten to
    confirmed, or None if it must be excluded. Jobs not awaiting a location
    decision are returned unchanged.

    Fails closed, exactly as `_resolve_hybrid` does and for the same reason: the
    listing never established that this job is in range, so a description that
    names nothing on the list has not established it either. WP8d's point is that
    these jobs get *read* before they are dropped, not that they are kept.
    """
    pending = _location_deferral(job)
    if pending is None:
        return job
    if not listed_location_found:
        return None
    confirmed = _LOCATION_DEFERRALS[pending][0]
    reasons = job.get("matched_reasons") or []
    return dict(job, matched_reasons=[confirmed if r == pending else r for r in reasons])


def apply_detail_filter(
    jobs: list[JobRecord],
    fetch_text: Callable[[str], str],
    source_fetch_map: dict[str, Callable[[str], str]] | None = None,
    hybrid_pattern: re.Pattern[str] | None = None,
    location_pattern: re.Pattern[str] | None = None,
    page_remote_reader: Callable[[str], bool] | None = None,
) -> tuple[list[JobRecord], list[JobRecord]]:
    """Fetch each job's detail page and filter by experience requirement and PhD.

    A job that arrives with a `description_text` its reader supplied is read
    from that, and its page is not fetched (SP4d; see `supplied_description`).
    Keeps jobs where: no requirement found, or the years required <= _MAX_JUNIOR_YEARS,
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
    page_remote_reader settles an *empty* location field too: a page that says
    the job is remote where the owner can work confirms it, though it names no
    listed place (SP4f follow-up; see `filtering.build_page_remote_reader`).
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
    A page that was fetched but holds no posting (SP4c; see `is_unreadable`) is
    the same case: nothing is read off it, so a deferred job is unverified, and
    any other job is kept with experience_level EXPERIENCE_UNREADABLE and no
    stored description, so the next run fetches it again. Each job also carries
    PAGE_STATE_KEY saying whether its page was read, unreadable, refused by
    robots.txt or failed.
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
    unreadable = 0
    fetched_at = utc_now_iso()

    def _task(job: JobRecord) -> _DetailSignals:
        source = str(job.get("source_name") or "")
        fn = (source_fetch_map or {}).get(source, fetch_text)
        return _fetch_and_analyze(job, fn, hybrid_pattern, location_pattern, page_remote_reader)

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
        pipeline.py's use of UNVERIFIED_KEY. An unreadable page is exactly this
        case (SP4c): `_fetch_and_analyze` reads nothing off a shell, so both
        deferred answers arrive as None and land here as unverified.
        """
        state = _page_state(signals)
        if not verified:
            return dict(
                job,
                experience_level=level,
                description_text="",
                description_fetched_at="",
                **state,
                **{UNVERIFIED_KEY: True, DROP_RULE_KEY: unverified_rule},
            )
        return dict(
            job,
            experience_level=level,
            description_text=signals.description_text,
            description_fetched_at=fetched_at if signals.description_text else "",
            **state,
            **{DROP_RULE_KEY: checked_rule},
        )

    for signals in results:
        job = signals.job
        years, phd_req, failed = signals.years_required, signals.phd_required, signals.fetch_failed
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
        pending = _location_deferral(job)
        found = signals.listed_location_found
        if pending == _EMPTY_PENDING_REASON and signals.page_says_remote:
            # An empty field is settled by the page saying the job is remote
            # where the owner can work, as well as by its naming a listed place.
            found = True
        resolved = _resolve_unresolved_location(job, found)
        if resolved is None and pending is not None:
            location_excluded += 1
            if found is None:
                unverified += 1
            _, checked_rule, unverified_rule = _LOCATION_DEFERRALS[pending]
            excluded.append(
                _deferred_exclusion(
                    job,
                    signals,
                    "unresolvable_location",
                    found is not None,
                    checked_rule,
                    unverified_rule,
                )
            )
            continue
        job = resolved

        extra = {
            "description_text": description_text,
            "description_fetched_at": fetched_at if description_text else "",
            **_page_state(signals),
        }
        if signals.unreadable or failed or not description_text:
            # Kept, and marked: the owner decided (SP4b Q6) that a job whose page
            # could not be read is shown rather than lost, but never as though it
            # was checked. A failed fetch and a missing URL are the same fact as
            # a shell, so they carry the same level; "unspecified" is reserved
            # for a posting read in full that states no requirement. Fails open
            # for years and PhD because there is nothing to fail on.
            if failed:
                fetch_failed += 1
            else:
                unreadable += 1
            kept.append(dict(job, experience_level=EXPERIENCE_UNREADABLE, **extra))
        else:
            level, drop_rule = judge_experience(years, phd_req)
            if drop_rule is None:
                if level == LEVEL_UNSPECIFIED:
                    no_requirement += 1
                kept.append(dict(job, experience_level=level, **extra))
            else:
                excluded.append(
                    dict(job, experience_level=level, **extra, **{DROP_RULE_KEY: drop_rule})
                )

    refused = [sig.job for sig in results if sig.robots_refused]
    if refused:
        hosts = sorted(
            {
                host
                for job in refused
                if (host := host_of(str(job.get("detail_url") or job.get("apply_url") or "")))
            }
        )
        # WARNING, not debug: these jobs are kept, but kept *unfiltered* — no
        # experience check, no PhD check, no description stored for scoring —
        # and the funnel counts them among the ones that passed this layer. A
        # run that quietly stops filtering a whole source must not look healthy.
        # A job only its page could place (an empty or placeholder location, a
        # conditional city) is not kept at all but held back, and since a
        # refusal recurs, held back on every run (SP4f review): say which.
        held_back = sum(
            1
            for job in excluded
            if job.get(UNVERIFIED_KEY) and job.get(PAGE_STATE_KEY) == PAGE_REFUSED
        )
        logger.warning(
            "%s: robots.txt refused the detail pages of %d job(s) on %s. Kept, but "
            "unchecked and with no description stored: %d. Held back from the sheet "
            "until the page can be read, because only the page could settle their "
            "location: %d. If those rules are not meant for us, exempt the host in "
            "that source's `ignore_robots` list.",
            layer_short(LAYER_DETAIL),
            len(refused),
            ", ".join(hosts) or "an unparseable host",
            len(refused) - held_back,
            held_back,
        )

    if jobs:
        logger.debug(
            "%s: %d/%d jobs failed to fetch (kept fail-open); "
            "%d had no numeric requirement; "
            "%d dropped as non-hybrid in a conditional location; "
            "%d dropped because an unresolvable location field named no listed place "
            "in the description; %d kept unchecked because the page held no posting "
            "(%d of those two totals because nothing could be verified this run, not "
            "because it was checked and found lacking)",
            layer_short(LAYER_DETAIL),
            fetch_failed,
            len(jobs),
            no_requirement,
            hybrid_excluded,
            location_excluded,
            unreadable,
            unverified,
        )
    return kept, excluded
