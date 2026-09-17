"""`sources probe`: can this career page be scraped within this design?

The first two rungs of the feasibility ladder CU2 worked by hand for
`probably_good` (docs/REFACTOR-PLAN.md), as a command: static HTML, then
rendered HTML, with every supported ATS reader tried against what they show.
Rungs 3 and 4 — a private API, a third-party index behind it — are judgements
about fragility and about what a fixture would end up holding, so they stay
judgements. Where the first two fail the report says so and points at that
section, rather than dressing a hard call up as a computed one.

The probe **reports; it never writes.** Not `sources.yaml`, not `registry.py`,
not `tests/fixtures/` — on a `reuse` verdict it prints the paste-ready entry
and the registry line, because code that generates one line of code has to be
maintained for ever to save a paste. Capturing a fixture stays
`scripts/capture_fixtures.py`'s job.

Every fetch goes through `ProbeFetcher`. The live one (`live_fetcher`) is
`http.py`'s normal fetcher inside `polite_fetching` and `http_cache`: honest
User-Agent, robots.txt honoured, per-host spacing, response cache. The tests
hand in a stub that serves saved fixtures, so no test reaches the network.

The steps, in the order the report prints them:

1. the lists — tombstone, candidates, `sources.yaml` — by board identity; a
   tombstoned board stops the probe before anything is fetched;
2. robots.txt, quoting the rule and the User-Agent;
3. the page, static first and rendered if the static page holds no postings;
4. ATS fingerprints, and the board URL where one can be found;
5. each matching generic reader, run through the fetcher, with samples;
6. pagination: a declared total or a pager against the rows read (WP11);
7. the verdict.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml
from bs4 import BeautifulSoup, Tag

from job_scraper import curated
from job_scraper.config_loader import load_sources
from job_scraper.extractors import (
    ashby,
    breezy,
    greenhouse,
    lever,
    personio,
    smartrecruiters,
    successfactors_html,
    teamtailor,
    workable,
    workday,
)
from job_scraper.extractors.registry import REGISTRY
from job_scraper.http import FetchedPage
from job_scraper.robots import RobotsVerdict
from job_scraper.urlutil import board_identity

Emit = Callable[[str], None]

# Where a human picks up when the automated rungs run out.
LADDER_REFERENCE = (
    'docs/REFACTOR-PLAN.md, section "`probably_good` — genuinely unfixable within this design"'
)

# No more than this many boards per platform are read. A page that links to a
# dozen Greenhouse boards is an aggregator, and reading all of them would be a
# dozen API calls to answer a question the first three already answer.
MAX_BOARDS_PER_PLATFORM = 3

SAMPLE_ROWS = 3

# Page sizes listings tend to come in. A reader that returns exactly one of
# these from a listing with a pager has probably read one page.
TYPICAL_PAGE_SIZES = frozenset({10, 12, 15, 16, 18, 20, 24, 25, 30, 36, 40, 48, 50, 60, 100})

# Below this much visible text, a page with no postings reads as a shell.
SHELL_TEXT_CHARS = 300


class ProbeFetcher(Protocol):
    """Everything the probe fetches, in one place a test can replace."""

    user_agent: str

    def robots(self, url: str) -> RobotsVerdict: ...

    def page(self, url: str) -> FetchedPage: ...

    def text(self, url: str) -> str: ...

    def rendered(self, url: str, **kwargs: Any) -> str: ...


class LiveFetcher:
    """`http.py`'s fetchers, as the pipeline uses them. Built by `live_fetcher`."""

    def __init__(self, policy: Any, user_agent: str) -> None:
        self._policy = policy
        self.user_agent = user_agent

    def robots(self, url: str) -> RobotsVerdict:
        return self._policy.explain(url)

    def page(self, url: str) -> FetchedPage:
        from job_scraper import http

        return http.fetch_page(url)

    def text(self, url: str) -> str:
        from job_scraper import http

        return http.fetch_text(url)

    def rendered(self, url: str, **kwargs: Any) -> str:
        from job_scraper import http

        return http.fetch_rendered(url, **kwargs)


@contextmanager
def live_fetcher(user_agent: str, cache_path: Path) -> Iterator[LiveFetcher]:
    """The normal fetcher, with the run-scoped politeness and cache a scrape has.

    Robots checking is on and has no overrides: a probe is the question "may
    we?", and `ignore_robots` is the owner's answer to it for a source that
    already exists, not something a probe should assume.
    """
    from job_scraper import http

    with http.polite_fetching(user_agent=user_agent) as policy, http.http_cache(cache_path):
        yield LiveFetcher(policy, user_agent)


# --- platforms -------------------------------------------------------------


@dataclass(frozen=True)
class Platform:
    """One supported ATS: how to recognise it, where its board is, how to read it."""

    key: str  # the extractor module, as registry.py imports it
    label: str
    markers: tuple[re.Pattern[str], ...]
    boards: tuple[re.Pattern[str], ...]  # each has a `slug` group
    board_url: Callable[[re.Match[str]], str]
    # "static" and "dynamic" are fixed; "page" follows whichever fetch of the
    # listing page showed postings.
    strategy: str
    # What the reader does about pagination, in a sentence.
    walk: str
    # True when the reader walks the listing and checks a total itself.
    guarded: bool = False
    # Slugs a board pattern can capture that are paths, not boards.
    not_slugs: frozenset[str] = frozenset()
    # Platforms served from the employer's own host: the page is the board.
    page_is_board: bool = False


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.I)


_SLUG = r"(?P<slug>[A-Za-z0-9][A-Za-z0-9_.%-]*)"

PLATFORMS: tuple[Platform, ...] = (
    Platform(
        key="greenhouse",
        label="Greenhouse",
        markers=(_p(r"greenhouse\.io"), _p(r"\bgrnhse")),
        boards=(
            _p(r"boards-api(?P<eu>\.eu)?\.greenhouse\.io/v1/boards/" + _SLUG),
            _p(
                r"boards(?P<eu>\.eu)?\.greenhouse\.io/embed/job_board(?:/js)?\?(?:[^\"'\s]*&)?for="
                + _SLUG
            ),
            _p(r"(?:job-)?boards(?P<eu>\.eu)?\.greenhouse\.io/" + _SLUG),
        ),
        board_url=lambda m: (
            f"https://job-boards{'.eu' if m.group('eu') else ''}.greenhouse.io/{m.group('slug')}"
        ),
        strategy="static",
        walk="reads the whole board from one Greenhouse API response",
        not_slugs=frozenset({"embed", "v1"}),
    ),
    Platform(
        key="lever",
        label="Lever",
        markers=(_p(r"lever\.co\b"),),
        boards=(
            _p(r"api(?:\.eu)?\.lever\.co/v0/postings/" + _SLUG),
            _p(r"jobs(?P<eu>\.eu)?\.lever\.co/" + _SLUG),
        ),
        board_url=lambda m: f"https://jobs.lever.co/{m.group('slug')}",
        strategy="static",
        walk="reads the whole board from one Lever API response",
        not_slugs=frozenset({"v0"}),
    ),
    Platform(
        key="ashby",
        label="Ashby",
        markers=(_p(r"ashbyhq\.com"), _p(r"window\.__appData")),
        boards=(_p(r"jobs\.ashbyhq\.com/" + _SLUG),),
        board_url=lambda m: f"https://jobs.ashbyhq.com/{m.group('slug')}",
        strategy="static",
        walk="reads the whole board from the data embedded in the board page",
        not_slugs=frozenset({"api"}),
    ),
    Platform(
        key="workable",
        label="Workable",
        markers=(_p(r"workable\.com"), _p(r"whr_embed")),
        boards=(
            _p(r"apply\.workable\.com/api/v\d+/(?:widget/)?accounts/" + _SLUG),
            _p(r"apply\.workable\.com/" + _SLUG),
        ),
        board_url=lambda m: f"https://apply.workable.com/{m.group('slug')}/",
        strategy="static",
        walk=(
            "reads one Workable API response and follows no next-page token, so a board "
            "longer than one response is not known to read whole (SP4 captures it)"
        ),
        not_slugs=frozenset({"api", "j"}),
    ),
    Platform(
        key="teamtailor",
        label="Teamtailor",
        markers=(_p(r"teamtailor(?:-cdn)?\.com"),),
        boards=(),
        board_url=lambda m: "",
        strategy="page",
        walk="reads the listing page only; a board behind a 'Show more' pager reads short",
        page_is_board=True,
    ),
    Platform(
        key="personio",
        label="Personio",
        markers=(_p(r"personio\.(?:de|com)"),),
        boards=(_p(r"(?P<slug>[a-z0-9][a-z0-9-]*)\.jobs\.personio\.(?P<tld>de|com)"),),
        board_url=lambda m: f"https://{m.group('slug')}.jobs.personio.{m.group('tld')}/",
        strategy="static",
        walk="reads the whole board from Personio's XML feed",
    ),
    Platform(
        key="smartrecruiters",
        label="SmartRecruiters",
        markers=(_p(r"smartrecruiters\.com"),),
        boards=(
            _p(r"api\.smartrecruiters\.com/v1/companies/" + _SLUG),
            _p(r"(?:careers|jobs)\.smartrecruiters\.com/" + _SLUG),
        ),
        board_url=lambda m: f"https://careers.smartrecruiters.com/{m.group('slug')}",
        strategy="static",
        walk="walks the API against its totalFound and fails a short walk (pagination.py)",
        guarded=True,
        not_slugs=frozenset({"v1"}),
    ),
    Platform(
        key="workday",
        label="Workday",
        markers=(_p(r"myworkdayjobs\.com"), _p(r"myworkday\.com")),
        boards=(
            _p(
                r"(?P<tenant>[a-z0-9][a-z0-9-]*)\.(?P<dc>wd\d+)\.myworkdayjobs\.com/"
                r"(?:[a-z]{2}-[A-Z]{2}/)?" + _SLUG
            ),
        ),
        board_url=lambda m: (
            f"https://{m.group('tenant')}.{m.group('dc')}.myworkdayjobs.com/{m.group('slug')}"
        ),
        strategy="dynamic",
        walk="reads the first rendered page only, so a board longer than one page reads short",
        not_slugs=frozenset({"wday", "assets"}),
    ),
    Platform(
        key="breezy",
        label="Breezy",
        markers=(_p(r"breezy\.hr"),),
        boards=(_p(r"(?P<slug>[a-z0-9][a-z0-9-]*)\.breezy\.hr"),),
        board_url=lambda m: f"https://{m.group('slug')}.breezy.hr",
        strategy="static",
        walk="reads the whole board from Breezy's JSON feed",
        not_slugs=frozenset({"www", "app", "assets", "attachments", "api", "marketing"}),
    ),
    Platform(
        key="successfactors_html",
        label="SuccessFactors",
        markers=(_p(r"rmkcdn"), _p(r"jobs2web"), _p(r"successfactors\.(?:com|eu)")),
        boards=(),
        board_url=lambda m: "",
        strategy="page",
        walk="walks ?startrow= and fails a walk shorter than the page's own total (pagination.py)",
        guarded=True,
        page_is_board=True,
    ),
)


# --- what a page shows -----------------------------------------------------

_POSTING_PATH = re.compile(
    r"/(?:jobs?|job-?details?|positions?|vacanc(?:y|ies)|openings?|postings?|requisitions?"
    r"|stellen(?:angebote)?|lediga-jobb)/(?P<rest>[^/?#]{2,})",
    re.I,
)
_POSTING_QUERY = re.compile(r"[?&](?:gh_jid|jobid|job_id|reqid|requisitionid|jid)=", re.I)
_NOT_A_POSTING = frozenset(
    {
        "search",
        "page",
        "category",
        "categories",
        "department",
        "departments",
        "locations",
        "location",
        "alerts",
        "alert",
        "saved",
        "all",
        "list",
        "index",
        "feed",
        "rss",
    }
)
_CARD_TOKEN = re.compile(
    r"\bjob[-_]?(?:card|tile|item|listing|row|post(?:ing)?|result|teaser)s?\b"
    r"|\bvacanc(?:y|ies)[-_]?(?:card|item|row)s?\b",
    re.I,
)
_JSON_LD_POSTING = re.compile(r"\"@type\"\s*:\s*\"JobPosting\"", re.I)
_DATA_PAYLOADS = ("window.__appData", "__NEXT_DATA__", "__NUXT__", "__INITIAL_STATE__")
_JOB_ARRAY = re.compile(r"\"(?:jobPostings|jobs|postings|vacancies|positions)\"\s*:\s*\[\s*\{")
_NOSCRIPT_JS = re.compile(r"enable\s+javascript|requires?\s+javascript", re.I)
_MOUNT_POINTS = ("root", "app", "__next", "__nuxt", "main-app")

_RANGE_TOTAL = re.compile(
    r"(?:(?:results|showing|jobs?|positions?)\s+(?P<first>[\d,]+)\s*(?:to|-|–)\s*"
    r"(?P<last>[\d,]+)\s+of\s+(?P<total>[\d,]+))"
    r"|(?:(?P<first2>[\d,]+)\s*(?:to|-|–)\s*(?P<last2>[\d,]+)\s+of\s+(?P<total2>[\d,]+)\s+"
    r"(?:results?|jobs?|positions?|vacanc(?:y|ies)|openings?))",
    re.I,
)
_FOUND_TOTAL = re.compile(
    r"(?P<total>[\d,]+)\s+(?:open\s+)?(?:jobs?|positions?|vacanc(?:y|ies)|openings?|roles?"
    r"|results?)\s+found",
    re.I,
)
_LABELLED_TOTAL = re.compile(
    r"(?:vacant\s+positions|open\s+positions|jobs|vacancies)\s*:\s*(?P<total>[\d,]+)\b", re.I
)
_JSON_TOTAL = re.compile(r"\"(?P<key>totalFound|totalJobs|totalCount|total_count)\"\s*:\s*(\d+)")
_PAGER_QUERY = re.compile(r"[?&](?:page|startrow|offset|start|pg)=(?P<n>\d+)", re.I)
_PAGER_CLASS = re.compile(r"paginat|pager\b|paging", re.I)
_MORE_CONTROL = re.compile(
    r"^\s*(?:load|show|see|view)\s+more(?:\s+(?:jobs|results|positions|vacancies|roles))?\s*$",
    re.I,
)


@dataclass(frozen=True)
class DeclaredTotal:
    total: int
    phrase: str
    page_size: int | None = None  # "1 to 25 of 60" says a page holds 25


def _number(text: str) -> int:
    return int(text.replace(",", ""))


def declared_total(soup: BeautifulSoup, html: str) -> DeclaredTotal | None:
    """How many postings the page says the listing holds, with the words it used."""
    texts = [soup.get_text(" ", strip=True)]
    texts += [str(tag.get("aria-label")) for tag in soup.select("[aria-label]")]
    for text in texts:
        match = _RANGE_TOTAL.search(text)
        if match:
            total = match.group("total") or match.group("total2")
            last = match.group("last") or match.group("last2")
            return DeclaredTotal(_number(total), match.group(0).strip(), _number(last))
    for pattern in (_FOUND_TOTAL, _LABELLED_TOTAL):
        for text in texts:
            match = pattern.search(text)
            if match:
                return DeclaredTotal(_number(match.group("total")), match.group(0).strip())
    match = _JSON_TOTAL.search(html)
    if match:
        return DeclaredTotal(int(match.group(2)), match.group(0))
    return None


def pager_signs(soup: BeautifulSoup) -> list[str]:
    """Whatever on the page suggests there is more than one page of it."""
    signs: list[str] = []
    if soup.select_one('link[rel~="next"], a[rel~="next"]'):
        signs.append('a rel="next" link')
    for tag in soup.find_all(True):
        attrs = " ".join(
            [
                " ".join(tag.get("class") or ()),
                str(tag.get("id") or ""),
                str(tag.get("aria-label") or ""),
            ]
        )
        if _PAGER_CLASS.search(attrs):
            signs.append(f"a pagination element (<{tag.name}>)")
            break
    for a in soup.find_all("a", href=True):
        match = _PAGER_QUERY.search(str(a["href"]))
        if match and int(match.group("n")) > 1:
            signs.append(f"a link to a later page ({match.group(0).lstrip('?&')})")
            break
    for tag in soup.find_all(["button", "a"]):
        if _MORE_CONTROL.match(tag.get_text(" ", strip=True)):
            signs.append(f"a '{tag.get_text(' ', strip=True)[:30]}' control")
            break
    return signs


@dataclass
class PageScan:
    """What one fetch of the listing page showed."""

    route: str  # "static" or "rendered"
    url: str
    size: int
    posting_links: int
    cards: int
    json_ld_postings: int
    payloads: list[str]
    job_array: bool
    visible_chars: int
    shell_hints: list[str]
    total: DeclaredTotal | None
    pager: list[str]
    html: str = field(repr=False)

    @property
    def has_job_data(self) -> bool:
        return bool(
            self.posting_links
            or self.cards
            or self.json_ld_postings
            or (self.payloads and self.job_array)
        )


def _looks_like_posting(href: str) -> bool:
    match = _POSTING_PATH.search(href)
    if match:
        return match.group("rest").casefold() not in _NOT_A_POSTING
    return bool(_POSTING_QUERY.search(href))


def _outermost(tags: list[Tag]) -> list[Tag]:
    chosen = set(map(id, tags))
    return [t for t in tags if not any(id(p) in chosen for p in t.parents)]


def scan_page(html: str, url: str, route: str) -> PageScan:
    soup = BeautifulSoup(html, "lxml")
    hrefs = {str(a["href"]).strip() for a in soup.find_all("a", href=True)}
    posting_links = sum(1 for h in hrefs if _looks_like_posting(h))
    card_tags = [
        tag
        for tag in soup.find_all(True)
        if _CARD_TOKEN.search(" ".join(tag.get("class") or ()) + " " + str(tag.get("id") or ""))
        or tag.get("data-automation-id") == "jobTitle"
    ]
    payloads = [marker for marker in _DATA_PAYLOADS if marker in html]
    shell_hints: list[str] = []
    for noscript in soup.find_all("noscript"):
        if _NOSCRIPT_JS.search(noscript.get_text(" ", strip=True)):
            shell_hints.append("a <noscript> asking for JavaScript")
            break
    for mount in _MOUNT_POINTS:
        tag = soup.find(id=mount)
        if isinstance(tag, Tag) and not tag.get_text(strip=True):
            shell_hints.append(f'an empty <{tag.name} id="{mount}"> mount point')
    text_soup = BeautifulSoup(html, "lxml")
    for tag in text_soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    visible = len(text_soup.get_text(" ", strip=True))
    return PageScan(
        route=route,
        url=url,
        size=len(html.encode("utf-8")),
        posting_links=posting_links,
        cards=len(_outermost(card_tags)),
        json_ld_postings=len(_JSON_LD_POSTING.findall(html)),
        payloads=payloads,
        job_array=bool(_JOB_ARRAY.search(html)),
        visible_chars=visible,
        shell_hints=shell_hints,
        total=declared_total(soup, html),
        pager=pager_signs(soup),
        html=html,
    )


def describe_scan(scan: PageScan) -> list[str]:
    lines = [
        f"{scan.size:,} bytes, {scan.visible_chars:,} characters of visible text",
        f"{scan.posting_links} link(s) shaped like a posting, "
        f"{scan.cards} element(s) named like a job card, "
        f"{scan.json_ld_postings} JSON-LD JobPosting(s)",
    ]
    if scan.payloads:
        lines.append(
            f"embedded data: {', '.join(scan.payloads)}"
            + (" (holding a job list)" if scan.job_array else " (no job list seen in it)")
        )
    if scan.has_job_data:
        lines.append("=> the HTML carries job data")
    elif scan.shell_hints or scan.visible_chars < SHELL_TEXT_CHARS:
        hints = ", ".join(scan.shell_hints) or "almost no visible text"
        lines.append(f"=> a client-rendered shell: {hints}")
    else:
        lines.append("=> content, but nothing in it looks like a posting")
    return lines


# --- fingerprints ----------------------------------------------------------


@dataclass(frozen=True)
class Board:
    platform: Platform
    url: str
    slug: str | None
    found_in: str


def _evidence(
    probed: str, page: FetchedPage | None, scans: list[PageScan]
) -> list[tuple[str, str]]:
    """Every string a platform could be recognised in, labelled with where it came from.

    Ordered most-telling first — where the request went, then what the page
    loads, then what it links to, then the page text — so a board found in
    the redirect chain is preferred over one mentioned in passing.
    """
    items: list[tuple[str, str]] = [("the URL probed", probed)]
    if page is not None:
        items += [("the redirect chain", u) for u in page.redirects]
        if page.final_url != probed:
            items.append(("the final URL", page.final_url))
    loaded: list[tuple[str, str]] = []
    linked: list[tuple[str, str]] = []
    bodies: list[tuple[str, str]] = []
    for scan in scans:
        soup = BeautifulSoup(scan.html, "lxml")
        for tag in soup.find_all(["script", "iframe"], src=True):
            loaded.append((f"a {tag.name} src ({scan.route})", str(tag["src"])))
        for tag in soup.find_all(["a", "link"], href=True):
            linked.append((f"a link ({scan.route})", str(tag["href"])))
        bodies.append((f"the {scan.route} HTML", scan.html))
    return items + loaded + linked + bodies


def fingerprint(
    probed: str, page: FetchedPage | None, scans: list[PageScan]
) -> tuple[dict[str, list[str]], list[Board]]:
    """Which platforms the page shows signs of, and the boards that can be named.

    Returns the evidence per platform key (where a marker was seen) and the
    boards, deduplicated by board identity and capped per platform.
    """
    evidence = _evidence(probed, page, scans)
    seen: dict[str, list[str]] = {}
    boards: list[Board] = []
    identities: set[str] = set()
    for platform in PLATFORMS:
        where = [
            label
            for label, text in evidence
            if any(marker.search(text) for marker in platform.markers)
        ]
        found: list[Board] = []
        for label, text in evidence:
            for pattern in platform.boards:
                for match in pattern.finditer(text):
                    slug = match.group("slug").rstrip(".")
                    if slug.casefold() in platform.not_slugs:
                        continue
                    url = platform.board_url(match)
                    try:
                        identity = board_identity(url)
                    except ValueError:
                        continue
                    if identity in identities:
                        continue
                    identities.add(identity)
                    found.append(Board(platform, url, slug, label))
                    if label not in where:
                        where.append(label)
        if platform.page_is_board and where and page is not None:
            url = _page_board_url(platform, page.final_url)
            identity = board_identity(url)
            if identity not in identities:
                identities.add(identity)
                found.append(Board(platform, url, None, "the page itself"))
        if where:
            seen[platform.key] = list(dict.fromkeys(where))
        boards += found[:MAX_BOARDS_PER_PLATFORM]
        extra = len(found) - MAX_BOARDS_PER_PLATFORM
        if extra > 0:
            seen[platform.key].append(f"{extra} more board(s) not read")
    return seen, boards


def _page_board_url(platform: Platform, final_url: str) -> str:
    """The board for a platform served from the employer's own host."""
    parts = urlsplit(final_url)
    if platform.key == "successfactors_html":
        path = parts.path if "/search" in parts.path else "/search/"
        query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k != "startrow"])
        return urlunsplit((parts.scheme, parts.netloc, path, query, ""))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


# --- readers ---------------------------------------------------------------

Fetch = Callable[..., str]


@dataclass
class ReaderRun:
    board: Board
    strategy: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    call: str = ""  # the partial(...) expression registry.py would hold
    total: DeclaredTotal | None = None  # the listing's own total, if its page states one
    pager: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.rows)

    @property
    def short(self) -> str | None:
        """Why this read looks like part of a listing, or None."""
        if not self.rows:
            return None
        n = len(self.rows)
        if self.total is not None and n < self.total.total:
            return (
                f"read {n} posting(s) but the listing says {self.total.total} "
                f'("{self.total.phrase}")'
            )
        if (
            self.total is None
            and not self.board.platform.guarded
            and self.pager
            and n in TYPICAL_PAGE_SIZES
        ):
            return (
                f"read {n} posting(s), a typical page size, from a listing with "
                f"{self.pager[0]} and no stated total"
            )
        return None


def _rendering(fetcher: ProbeFetcher) -> Fetch:
    def fetch(url: str, *args: Any, **kwargs: Any) -> str:
        return fetcher.rendered(url, **kwargs)

    # The mark workday.py and successfactors_html.py look for before adding a
    # selector wait; see http.is_rendering_fetcher.
    fetch.renders = True  # type: ignore[attr-defined]
    return fetch


def _static(fetcher: ProbeFetcher) -> Fetch:
    def fetch(url: str, *args: Any, **kwargs: Any) -> str:
        return fetcher.text(url)

    return fetch


def _source_call(board: Board, name: str, page_step: int | None) -> tuple[Callable[..., Any], str]:
    """The reader for *board* as registry.py would bind it, and that line's expression."""
    key = board.platform.key
    if key in ("lever", "smartrecruiters"):
        module = lever if key == "lever" else smartrecruiters
        return (
            partial(module.extract, source_name=name, org_slug=board.slug),
            f'partial({key}.extract, source_name="{name}", org_slug="{board.slug}")',
        )
    if key == "successfactors_html":
        return (
            partial(
                successfactors_html.extract,
                source_name=name,
                page_step=page_step,
                base_search_url=board.url,
            ),
            f'partial(successfactors_html.extract, source_name="{name}", '
            f'page_step={page_step}, base_search_url="{board.url}")',
        )
    modules = {
        "greenhouse": greenhouse,
        "ashby": ashby,
        "workable": workable,
        "teamtailor": teamtailor,
        "personio": personio,
        "workday": workday,
        "breezy": breezy,
    }
    return (
        partial(modules[key].extract, source_name=name),
        f'partial({key}.extract, source_name="{name}")',
    )


def _set_startrow(url: str, startrow: int) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "startrow"]
    query.append(("startrow", str(startrow)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def run_reader(
    board: Board,
    fetcher: ProbeFetcher,
    name: str,
    strategy: str,
    listing: PageScan | None,
) -> ReaderRun:
    """Run one generic reader against its board, through the fetcher, and never raise.

    *listing* is the scan of the probed page when it is this board's page, so
    its declared total and pager apply to what the reader reads.
    """
    fetch = _rendering(fetcher) if strategy == "dynamic" else _static(fetcher)
    run = ReaderRun(board=board, strategy=strategy)
    if listing is not None:
        run.total, run.pager = listing.total, listing.pager
    page_step: int | None = None
    try:
        if board.platform.key == "successfactors_html":
            # The walk needs its page size before it starts. The first page
            # states it ("1 to 25 of 60"); failing that, count what it holds.
            # Asking for startrow=0 is the walk's own first request, so the
            # response cache answers the reader's copy.
            first = fetch(_set_startrow(board.url, 0))
            scan = scan_page(first, board.url, "static" if strategy != "dynamic" else "rendered")
            run.total, run.pager = scan.total, scan.pager
            if scan.total is not None and scan.total.page_size:
                page_step = scan.total.page_size
                run.notes.append(f'page size {page_step}, from "{scan.total.phrase}"')
            else:
                soup = BeautifulSoup(first, "lxml")
                page_step = len(successfactors_html._parse_page(soup, board.url, name))
                run.notes.append(
                    f"page size {page_step}, counted: the page states no range to read it from"
                )
            if not page_step:
                run.error = "the search page holds no /job/ links to size a walk from"
                return run
        reader, run.call = _source_call(board, name, page_step)
        run.rows = reader(board.url, fetch)
    except Exception as exc:  # noqa: BLE001 — every failure is part of the report
        run.error = f"{type(exc).__name__}: {exc}"
    return run


def describe_run(run: ReaderRun) -> list[str]:
    head = f"{run.board.platform.key} on {run.board.url} ({run.strategy})"
    notes = [f"    {note}" for note in run.notes]
    if run.error is not None:
        return [head, *notes, f"    FAILED — {run.error}"]
    lines = [head, f"    {len(run.rows)} row(s)", *notes]
    for row in run.rows[:SAMPLE_ROWS]:
        lines.append(f"    - title:      {row.get('title')}")
        lines.append(f"      location:   {row.get('location') or '(empty)'}")
        lines.append(f"      detail_url: {row.get('detail_url')}")
    if run.rows:
        empty = sum(1 for r in run.rows if not str(r.get("location") or "").strip())
        fallback = sum(1 for r in run.rows if r.get("detail_url") == r.get("listing_url"))
        details = {r.get("detail_url") for r in run.rows}
        if empty:
            lines.append(f"    ! {empty} of {len(run.rows)} row(s) have no location")
        if fallback:
            lines.append(
                f"    ! {fallback} row(s) link back to the listing rather than to a posting"
            )
        if len(details) < len(run.rows):
            lines.append(f"    ! only {len(details)} distinct detail_url(s)")
    return lines


# --- the lists -------------------------------------------------------------


@dataclass
class ListStatus:
    excluded: dict[str, Any] | None
    candidate: dict[str, Any] | None
    active: list[dict[str, Any]]
    sources_missing: bool


def _active_matches(url: str, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted = board_identity(url)
    out = []
    for source in sources:
        try:
            if board_identity(str(source.get("url") or "")) == wanted:
                out.append(source)
        except ValueError:
            continue
    return out


def list_status(
    url: str,
    excluded: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    sources: list[dict[str, Any]] | None,
) -> ListStatus:
    return ListStatus(
        excluded=curated.find_board(excluded, url),
        candidate=curated.find_board(candidates, url),
        active=_active_matches(url, sources or []),
        sources_missing=sources is None,
    )


def describe_status(url: str, status: ListStatus) -> list[str]:
    lines: list[str] = []
    if status.excluded is not None:
        entry = status.excluded
        lines += [
            f"TOMBSTONED — {entry['organisation']}  {entry['url']}",
            f"    reason: {entry.get('reason')}",
            f"    excluded_on: {entry.get('excluded_on') or 'null (date unknown)'}",
        ]
    if status.candidate is not None:
        entry = status.candidate
        checked = entry.get("last_checked")
        lines += [
            f"CANDIDATE — {entry['organisation']}  {entry['url']}",
            f"    blocker: {entry.get('blocker') or 'null — no finding recorded'}",
            "    last_checked: "
            + (
                str(checked)
                if checked
                else "null — the date is UNKNOWN, which is not the same as never checked "
                "(docs/SOURCES-PLAN.md, SP2's result)"
            ),
            f"    source_of_record: {entry.get('source_of_record') or 'null'}",
        ]
        if entry.get("ats"):
            lines.append(f"    ats: {entry['ats']}")
    for source in status.active:
        lines.append(
            f"ACTIVE SOURCE — {source.get('name')}  {source.get('url')} "
            f"(strategy: {source.get('strategy', 'static')})"
        )
    if not lines:
        where = "the tombstone or the candidates list"
        if not status.sources_missing:
            where = "the tombstone, the candidates list or sources.yaml"
        lines.append(f"not on {where} (board {board_identity(url)})")
    if status.sources_missing:
        lines.append("sources.yaml not found: the active-source check was SKIPPED, not passed")
    return lines


# --- the whole probe -------------------------------------------------------

REUSE = "reuse"
NEW_EXTRACTOR = "needs a new extractor"
NOT_FEASIBLE = "not feasible"


@dataclass
class ProbeResult:
    kind: str
    line: str
    run: ReaderRun | None = None
    # True when rungs 1-2 of the CU2 ladder both came up empty, which is where
    # the automated part stops and a person's investigation starts.
    ladder_exhausted: bool = False


def source_name_for(explicit: str | None, candidate: dict[str, Any] | None, board: Board) -> str:
    raw = explicit or (candidate or {}).get("organisation") or board.slug or ""
    if not raw:
        host = urlsplit(board.url).hostname or ""
        labels = [x for x in host.removeprefix("www.").split(".") if x]
        raw = labels[-2] if len(labels) >= 2 else host
    return re.sub(r"[^a-z0-9]+", "_", str(raw).casefold()).strip("_") or "new_source"


def paste_blocks(run: ReaderRun, name: str, company: str | None) -> list[str]:
    entry: dict[str, Any] = {"name": name, "url": run.board.url, "strategy": run.strategy}
    if company:
        entry["company"] = company
    block = yaml.safe_dump([entry], sort_keys=False, allow_unicode=True).rstrip("\n")
    lines = ["sources.yaml (under `sources:`):", ""]
    lines += ["  " + line for line in block.splitlines()]
    if not company:
        lines.append("    # company: <the employer's name>  (unset, and nothing to take it from)")
    lines += [
        "",
        "registry.py (in REGISTRY):",
        "",
        f'    "{name}": {run.call},',
    ]
    return lines


@dataclass
class _Probe:
    """What one probe has learnt so far, handed from rung to rung."""

    url: str
    fetcher: ProbeFetcher
    emit: Emit
    name: str | None
    company: str | None
    excluded: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    sources: list[dict[str, Any]] | None
    status: ListStatus | None = None
    page: FetchedPage | None = None
    scans: list[PageScan] = field(default_factory=list)
    boards: list[Board] = field(default_factory=list)
    runs: list[ReaderRun] = field(default_factory=list)

    @property
    def with_data(self) -> PageScan | None:
        return next((s for s in self.scans if s.has_job_data), None)

    @property
    def candidate(self) -> dict[str, Any] | None:
        return self.status.candidate if self.status else None

    def heading(self, text: str) -> None:
        if text[0] != "1":
            self.emit("")
        self.emit(text)


def probe(
    url: str,
    fetcher: ProbeFetcher,
    *,
    curated_dir: Path,
    sources_path: Path,
    emit: Emit,
    name: str | None = None,
    company: str | None = None,
) -> ProbeResult:
    """Work the ladder for *url*, printing as it goes, and return the verdict.

    Raises ValueError for something that is not a URL, and `curated`'s errors
    for a list it cannot read — both before anything is fetched.
    """
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    board_identity(url)
    state = _Probe(
        url=url,
        fetcher=fetcher,
        emit=emit,
        name=name,
        company=company,
        excluded=curated.load_excluded(curated_dir),
        candidates=curated.load_candidates(curated_dir),
        sources=load_sources(sources_path) if sources_path.is_file() else None,
    )
    for step in (_step_lists, _step_robots, _step_page, _step_fingerprint):
        stopped = step(state)
        if stopped is not None:
            return _report_verdict(state, stopped)
    _step_readers(state)
    _step_pagination(state)
    return _report_verdict(state, decide(state.runs, state.scans, state.with_data))


def _step_lists(state: _Probe) -> ProbeResult | None:
    """Rung 1, before anything is fetched: a tombstoned board stops here."""
    state.heading("1. Known already? (by board identity, not host)")
    state.status = list_status(state.url, state.excluded, state.candidates, state.sources)
    _indent(state.emit, describe_status(state.url, state.status))
    tombstone = state.status.excluded
    if tombstone is None:
        return None
    return ProbeResult(
        NOT_FEASIBLE,
        f"{NOT_FEASIBLE} — rung 1: tombstoned ({tombstone['organisation']}: "
        f"{tombstone.get('reason')}). Not fetched: re-investigating a permanent exclusion is "
        "what the tombstone exists to prevent.",
    )


def _step_robots(state: _Probe) -> ProbeResult | None:
    state.heading("2. robots.txt")
    verdict = state.fetcher.robots(state.url)
    _indent(state.emit, describe_robots(verdict))
    if verdict.allowed:
        return None
    return ProbeResult(
        NOT_FEASIBLE,
        f"{NOT_FEASIBLE} — rung 2: robots.txt forbids {state.url} ({verdict.rule}). "
        "`ignore_robots` exists for a rule not meant for us; using it is the owner's "
        "judgement about the site, not the probe's.",
    )


def _step_page(state: _Probe) -> ProbeResult | None:
    """Rung 3: static, then rendered if the static page shows no postings."""
    state.heading("3. The page")
    emit = state.emit
    static_error = rendered_error = None
    try:
        state.page = state.fetcher.page(state.url)
    except Exception as exc:  # noqa: BLE001 — a failed fetch is a finding
        static_error = f"{type(exc).__name__}: {exc}"
        emit(f"   static fetch FAILED — {static_error}")
    page = state.page
    if page is not None:
        emit(f"   static fetch: {page.final_url}")
        if not page.redirects_known:
            emit("      redirect chain not recorded (served by the curl fallback)")
        for hop in page.redirects:
            emit(f"      redirected from {hop}")
        state.scans.append(scan_page(page.text, page.final_url, "static"))
        _indent(emit, describe_scan(state.scans[0]), "      ")
    if state.with_data is not None:
        return None

    target = page.final_url if page is not None else state.url
    emit(f"   rendered fetch (the static page shows no postings): {target}")
    try:
        rendered = scan_page(state.fetcher.rendered(target), target, "rendered")
    except Exception as exc:  # noqa: BLE001
        rendered_error = f"{type(exc).__name__}: {exc}"
        emit(f"      FAILED — {rendered_error}")
    else:
        state.scans.append(rendered)
        _indent(emit, describe_scan(rendered), "      ")
    if state.scans:
        return None
    return ProbeResult(
        NOT_FEASIBLE,
        f"{NOT_FEASIBLE} — rung 3: neither route fetched the page (static: {static_error}; "
        f"rendered: {rendered_error}).",
    )


def _step_fingerprint(state: _Probe) -> ProbeResult | None:
    """Rung 4. A board found here is checked against the lists like the URL was."""
    state.heading("4. ATS fingerprint")
    emit = state.emit
    seen, boards = fingerprint(state.url, state.page, state.scans)
    if not seen:
        emit("   none of the ten supported platforms")
    for platform in PLATFORMS:
        if platform.key in seen:
            emit(f"   {platform.label}: seen in {', '.join(seen[platform.key])}")
            if not any(b.platform is platform for b in boards):
                emit("      no board URL discoverable, so its reader cannot be run")
    probed = board_identity(state.url)
    for board in boards:
        emit(f"   board: {board.url}  ({board.platform.label}, from {board.found_in})")
        if board_identity(board.url) != probed:
            found = list_status(board.url, state.excluded, state.candidates, state.sources)
            if found.excluded is not None or found.candidate is not None or found.active:
                _indent(emit, describe_status(board.url, found), "      ")
            if found.excluded is not None:
                emit("      not read: that board is tombstoned")
                continue
        state.boards.append(board)
    if boards and not state.boards:
        return ProbeResult(
            NOT_FEASIBLE,
            f"{NOT_FEASIBLE} — rung 4: every board this page points at is tombstoned.",
        )
    return None


def _step_readers(state: _Probe) -> None:
    """Rung 5: each generic reader, through the fetcher, with samples."""
    state.heading("5. Generic readers, through the normal fetcher")
    emit = state.emit
    if not state.boards:
        emit("   nothing to run: no board was found")
    probed_host = urlsplit(state.url).netloc.lower()
    for board in state.boards:
        if urlsplit(board.url).netloc.lower() != probed_host:
            robots = state.fetcher.robots(board.url)
            if not robots.allowed:
                emit(f"   {board.platform.key} on {board.url}: not read")
                _indent(emit, describe_robots(robots), "      ")
                continue
        strategy = board.platform.strategy
        if strategy == "page":
            # Served from the employer's host: read it the way it showed postings.
            with_data = state.with_data
            strategy = "dynamic" if with_data and with_data.route == "rendered" else "static"
        identity = board_identity(board.url)
        same_page = [s for s in state.scans if board_identity(s.url) == identity]
        listing = next((s for s in same_page if s.has_job_data), None) or (
            same_page[-1] if same_page else None
        )
        source_name = source_name_for(state.name, state.candidate, board)
        run = run_reader(board, state.fetcher, source_name, strategy, listing)
        state.runs.append(run)
        _indent(emit, describe_run(run))


def _step_pagination(state: _Probe) -> None:
    """Rung 6: does the listing say it is longer than what was read? (WP11)"""
    state.heading("6. Pagination")
    emit = state.emit
    for scan in state.scans:
        total = f'"{scan.total.phrase}"' if scan.total else "no total stated"
        pager = ", ".join(scan.pager) if scan.pager else "no pager"
        emit(f"   {scan.route} page: {total}; {pager}")
    for run in state.runs:
        if run.error is not None:
            continue
        emit(f"   {run.board.platform.key}: {run.board.platform.walk}")
        if run.short:
            emit(f"      SHORT — {run.short}. A silently short walk looks like success (WP11).")
        elif run.total is not None:
            emit(f"      {len(run.rows)} row(s) against a stated {run.total.total}: whole")
        else:
            emit(f"      {len(run.rows)} row(s); nothing on the page to check that against")


def _report_verdict(state: _Probe, result: ProbeResult) -> ProbeResult:
    """Rung 7, and on `reuse` the blocks to paste — printed, never written."""
    state.heading("7. Verdict")
    emit = state.emit
    emit(f"   {result.line}")
    if result.ladder_exhausted:
        emit(
            "   Rungs 3 and 4 — a private API, a third-party index behind it — are not "
            f"automated. What a proper look at them involves: {LADDER_REFERENCE}."
        )
    if result.kind != REUSE or result.run is None:
        return result
    run = result.run
    source_name = source_name_for(state.name, state.candidate, run.board)
    company = state.company or (state.candidate or {}).get("organisation")
    emit("")
    active = state.status.active if state.status else []
    if active:
        emit(
            f"   Already active as {active[0].get('name')!r}: the blocks below are for "
            "comparison with sources.yaml, not for pasting."
        )
    if source_name in REGISTRY:
        emit(f"   {source_name!r} is already a key in registry.py: pass --name for another.")
    emit("   Printed, not written — paste them yourself:")
    emit("")
    _indent(emit, paste_blocks(run, source_name, company))
    emit("")
    emit(
        "   Then capture its fixture (capture_fixtures.py reads sources.yaml, so paste first): "
        f"python scripts/capture_fixtures.py {source_name}"
    )
    if run.board.platform.guarded:
        emit("   It paginates: add --pages all to capture the whole walk (WP11).")
    return result


def decide(runs: list[ReaderRun], scans: list[PageScan], with_data: PageScan | None) -> ProbeResult:
    """The verdict, from what the readers and the page showed.

    A recognised platform whose reader fails or reads nothing is `not
    feasible` at rung 5, never `needs a new extractor`: the fix for a broken
    generic reader is that reader (SP4), not a second module beside it.
    """
    whole = [r for r in runs if r.ok and not r.short]
    if whole:
        best = max(whole, key=lambda r: len(r.rows))
        return ProbeResult(
            REUSE,
            f"{REUSE} {best.board.platform.key}  ({len(best.rows)} row(s), "
            f"strategy: {best.strategy})",
            best,
        )
    partial_reads = [r for r in runs if r.ok and r.short]
    if partial_reads:
        run = partial_reads[0]
        return ProbeResult(
            NEW_EXTRACTOR,
            f"{NEW_EXTRACTOR} — {run.board.platform.key} {run.short}: the existing reader "
            "does not walk this listing, so it needs a walking reader (or a fix to "
            f"{run.board.platform.key}.py) before it is a source.",
            run,
        )
    failed = [r for r in runs if r.error is not None]
    if failed:
        run = failed[0]
        key = run.board.platform.key
        return ProbeResult(
            NOT_FEASIBLE,
            f"{NOT_FEASIBLE} — rung 5: {key} is recognised but its reader failed "
            f"({run.error}). Re-probe a transient failure; a persistent one is a bug in "
            f"{key}.py, not a case for a new module.",
            run,
        )
    if runs:
        run = runs[0]
        key = run.board.platform.key
        if with_data is not None:
            return ProbeResult(
                NOT_FEASIBLE,
                f"{NOT_FEASIBLE} — rung 5: {key} read 0 postings, but the {with_data.route} "
                f"page shows some. That is a bug in {key}.py for this layout, not a case for "
                "a new module.",
                run,
            )
        return ProbeResult(
            NOT_FEASIBLE,
            f"{NOT_FEASIBLE} — rung 5: {key} matched but read 0 postings. An empty board and "
            "a broken reader look the same; re-probe when it lists something.",
            run,
        )
    if with_data is not None:
        return ProbeResult(
            NEW_EXTRACTOR,
            f"{NEW_EXTRACTOR} — the {with_data.route} HTML carries job data, on no supported "
            "ATS; a bespoke module would use strategy: "
            f"{'dynamic' if with_data.route == 'rendered' else 'static'}.",
        )
    routes = " and ".join(s.route for s in scans)
    return ProbeResult(
        NOT_FEASIBLE,
        f"{NOT_FEASIBLE} — rungs 1-2 of the CU2 ladder: the {routes} HTML shows no postings, "
        "and no supported ATS was recognised.",
        ladder_exhausted=True,
    )


def describe_robots(verdict: RobotsVerdict) -> list[str]:
    lines = [
        f"{verdict.robots_url or '(no robots.txt URL)'}",
        f"evaluated for User-Agent: {verdict.user_agent}",
    ]
    token = verdict.user_agent.split("/")[0]
    lines.append(f"(robots.txt groups are matched on its product token, {token!r})")
    if verdict.group is not None:
        lines.append("group: " + verdict.group.replace("\n", " / "))
    if verdict.rule is not None:
        lines.append(f"rule:  {verdict.rule}")
    lines.append(f"{'ALLOWED' if verdict.allowed else 'DISALLOWED'} — {verdict.reason}")
    if verdict.crawl_delay is not None:
        lines.append(f"Crawl-delay: {verdict.crawl_delay:g}s (honoured by the throttle)")
    return lines


def _indent(emit: Emit, lines: list[str], prefix: str = "   ") -> None:
    for line in lines:
        emit(prefix + line if line else "")
