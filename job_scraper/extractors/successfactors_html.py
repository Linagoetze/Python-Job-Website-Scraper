"""Generic extractor for SAP SuccessFactors hosted career sites (HTML variant).

These portals serve job listings as server-rendered HTML at a /search/ or
/search URL. Job links follow the pattern:
    <a href="/job/{Location}-{Title}/{ID}/">…</a>

Pagination uses a ?startrow=N query parameter. Loop until a page returns no job
links or the page_step would exceed a reasonable upper bound. Both layouts state
how many postings there are — "Page 1 of 201, Results 1 to 10 of 2010" in the
classic pagination label, "Showing 1 to 20 of 62 Jobs" on the tile skin — so a
walk is checked against that count before it returns; see `pagination.py`.

Every page states that count, and the walk also checks that it does not change.
A posting added or removed ahead of the walk's position moves every later
posting by one place, so one is read twice or one is never read, and a board
that loses a posting already read ends with a walk that matches its new total
while missing a live posting. DSV's walk of about 230 pages takes four minutes,
and on 2026-10-09 (run 38) its pages stated 2,296 up to startrow 270 and 2,297
from 280. So a change in the stated total stops the walk with `BoardMovedError`.

A source's `url` may carry the search form's country facet
(`?optionsFacetsDD_country=<CC>`), which narrows a worldwide board to one country
(SP17). A static fetch honours it, but a site that stopped doing so would answer
with the whole board and look like a busy one, so every row's country (the third
field of its location) is checked against the facet and a foreign row raises
`FacetNotAppliedError`. The stated total cannot prove it: it describes whatever page
was returned, narrowed or not.

Whether a page needs JavaScript is the caller's business, not this module's:
`sources.yaml`'s `strategy` picks the fetcher, and this extractor uses whatever
callable it is handed. When that callable renders, it is wrapped with a selector
wait so Playwright blocks until job links exist rather than relying on a fixed
settle delay.

Known instances:
  DSV        https://jobs.dsv.com/search/           page_step=10, static
  Novo Nordisk https://careers.novonordisk.com/search  page_step=100, static
  Coloplast  https://careers.coloplast.com/search/  page_step=25, static
  ISS        https://jobs.issworld.com/search/      page_step=20, dynamic
             (AJAX infinite scroll — the ?startrow=N parameter alone is not
             enough, so its sources.yaml entry sets `strategy: dynamic`)
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import partial
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from job_scraper.extractors import pagination


class BoardMovedError(pagination.ShortWalkError):
    """The board's stated total changed between two pages of one walk.

    A `ShortWalkError`, so a run treats it as a failed source: stored jobs are
    kept and nothing is delisted. It is raised at the first page that disagrees,
    because nothing fetched after that point can be trusted either. The two
    totals and the page are fields, not only words in the message: the probe
    reports a moved board from them, and a reworded message must not be able to
    break that (the `RobotsDisallowed.url` rule).
    """

    def __init__(self, message: str, *, before: int, after: int, url: str) -> None:
        super().__init__(message)
        self.before = before
        self.after = after
        self.url = url


class FacetNotAppliedError(pagination.ShortWalkError):
    """The board answered a country facet with postings from outside it (SP17).

    A `ShortWalkError`, so a run treats it as a failed source and keeps the
    stored jobs. An ignored facet would otherwise read as a board of the wrong
    size, and a source narrowed on purpose would go on reporting it as quiet.
    `found` is the country a row named, or "" when no row named one; the fields
    are for the probe, as `BoardMovedError`'s are.
    """

    def __init__(self, message: str, *, facet: str, wanted: str, found: str, url: str) -> None:
        super().__init__(message)
        self.facet = facet
        self.wanted = wanted
        self.found = found
        self.url = url


_WAIT_SELECTOR = 'a[href*="/job/"]'

# The search form's own GET parameters (`optionsFacetsDD_country`, `_city`, ...).
# They are the only part of a configured URL's query this reader takes over: the
# rest of a source's URL (a sort order, a view) never mattered to the walk, and
# tetrapak's already differs from its registry line.
_FACET_PREFIX = "optionsFacetsDD_"
_COUNTRY_FACET = _FACET_PREFIX + "country"
# Row locations read "City, region, CC, postcode" ("Malmo, Skane, SE, 21120").
# The facet's value is the same ISO code, which is what lets a row prove it.
_ROW_COUNTRY = re.compile(r"[A-Z]{2}")

# "Results 1 to 10 of 2010" (classic, in an aria-label) and "Showing 1 to 20 of
# 62 Jobs" (tile, in visible text). One pattern reads both, and it is anchored
# on the run of numbers rather than on the wording around them so that a
# rephrasing does not silently turn the total into None.
_TOTAL_PATTERN = re.compile(
    r"(?:results|showing)\s+[\d,]+\s*(?:to|-|–)\s*[\d,]+\s+of\s+([\d,]+)", re.I
)

# Job links are usually rooted at /job/, but an instance hosting sub-brands
# prefixes them with the brand: Coloplast serves Kerecis and Atos postings as
# /Kerecis/job/… and /Atos/job/…. Matching only "starts with /job/" dropped
# those silently — 6 of 25 rows on the captured page — so allow one optional
# leading segment. Deliberately only one: it admits the brand prefix without
# matching arbitrary deep paths that merely contain the word.
_JOB_HREF = re.compile(r"^(?:/[^/]+)?/job/")

# SuccessFactors ships two row layouts, and they need different reading.
# Both label their cells, so both are read structurally; the positional
# heuristic is only a last resort for a skin neither branch recognises.
#
# Classic table (DSV, and every static instance here): <tr> of <td>s, with
# `span.jobLocation` in `td.colLocation` and `span.jobFacility` in
# `td.colFacility`. The heuristic *happened* to land on the location here, but
# it read the posting date as the job family.
#
# Modern tile (ISS): <li class="job-tile"> holding `div.section-field.<kind>`
# blocks, each a `span.sr-only` naming the field followed by a div of value.
# The heuristic cannot read this layout at all: the fields are ordered job
# category first, so even ignoring the labels the "first text" is a department.
#
# Reading the labels rather than guessing over them is what workday.py already
# does when it prefers its dedicated locations element over its subtitle list.
_TILE_FIELD_SELECTOR = "div.section-field"
_CLASSIC_LOCATION_SELECTOR = "span.jobLocation"
# The same column has two class names across instances — DSV and Novo Nordisk
# label it `jobFacility` under a "Category" heading, Coloplast `jobDepartment`
# under "Job Family". Both are the job family, so both are accepted; matching
# only one silently blanks the other, which is how Coloplast was found.
_CLASSIC_DEPARTMENT_SELECTOR = "span.jobFacility, span.jobDepartment"
# `location` is the single-site field, `multilocation` the multi-site one; ISS
# uses the latter throughout. Order is preference, not precedence in markup.
_TILE_LOCATION_KINDS = ("location", "multilocation")
_TILE_DEPARTMENT_KINDS = ("department",)

# Labels for screen readers are not data. This is why ISS put the word "Title"
# in 33 locations: `span.sr-only` sits inside the title's own container, so it
# was the first text that was not the title. Stripping it is a general guard,
# not an ISS fix — a label is not a location for any of these sites.
_SR_ONLY_CLASS = "sr-only"


def _is_screen_reader_only(text: Any, container: Any) -> bool:
    """True if *text* sits inside an sr-only element at or below *container*."""
    node = text.parent
    while node is not None:
        if _SR_ONLY_CLASS in (node.get("class") or ()):
            return True
        if node is container:
            return False
        node = node.parent
    return False


def _visible_strings(container: Any) -> list[str]:
    """Text in *container*, skipping anything addressed only to screen readers.

    Built from `find_all(string=True)` rather than `stripped_strings`, because
    the latter yields bare `str` with no way back up to the element that holds
    it, and the sr-only test needs the ancestors.
    """
    out: list[str] = []
    for text in container.find_all(string=True):
        stripped = text.strip()
        if not stripped or _is_screen_reader_only(text, container):
            continue
        out.append(stripped)
    return out


def _classic_field(row: Any, selector: str) -> str | None:
    """Read a classic-table cell, or None if this row has no such cell.

    DSV renders each row three times (desktop/tablet/phone) and every copy
    carries the same text, so the first match is as good as any.
    """
    element = row.select_one(selector)
    if element is None:
        return None
    return " ".join(_visible_strings(element)).strip()


def _tile_field(row: Any, kinds: tuple[str, ...]) -> str | None:
    """Read a labelled `section-field` block, or None if this row has no such field.

    None and "" mean different things to the caller: None is "this layout does
    not label its fields, fall back to the heuristic", "" is "the field is here
    and genuinely empty", which WP8f admits at Layer 1 rather than guessing.
    """
    for field in row.select(_TILE_FIELD_SELECTOR):
        classes = field.get("class") or ()
        if not any(kind in classes for kind in kinds):
            continue
        # The value sits in a sibling div of the sr-only label; taking the
        # field's visible text covers both that and any layout that inlines it.
        return " ".join(_visible_strings(field)).strip()
    return None


def _declared_total(soup: Any) -> int | None:
    """How many postings this board says it has, if it says.

    Looks in the page's text and in its aria-labels: the classic skin puts the
    sentence in an attribute, the tile skin in a visible span, and an instance
    may carry either.
    """
    candidates = [soup.get_text(" ", strip=True)]
    candidates.extend(str(tag.get("aria-label")) for tag in soup.select("[aria-label]"))
    for candidate in candidates:
        match = _TOTAL_PATTERN.search(candidate)
        if match:
            return int(match.group(1).replace(",", ""))
    return None


def _set_startrow(base_url: str, startrow: int) -> str:
    parsed = urlparse(base_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params["startrow"] = [str(startrow)]
    # preserve other params (q, sortColumn, sortDirection …)
    new_query = urlencode({k: v[0] for k, v in params.items()})
    return urlunparse(parsed._replace(query=new_query))


def _facets_of(listing_url: str, base_search_url: str) -> dict[str, str]:
    """The search-form facets on the configured URL, to be applied to the walk.

    Only same-host facets are taken, and only ones the guard can check: a country
    is proven by every row's location, a city or job area by nothing on the page.
    Refusing the rest is SP3c's rule (refuse what cannot be translated
    faithfully), not a limit of the site.
    """
    if urlparse(listing_url).netloc != urlparse(base_search_url).netloc:
        return {}
    params = parse_qs(urlparse(listing_url).query, keep_blank_values=True)
    facets = {k: v[0] for k, v in params.items() if k.startswith(_FACET_PREFIX) and v[0]}
    unchecked = sorted(set(facets) - {_COUNTRY_FACET})
    if unchecked:
        raise ValueError(
            f"{listing_url}: the facet(s) {', '.join(unchecked)} cannot be checked against "
            "the rows, so a board that ignored them would read as a quiet one. Only "
            f"{_COUNTRY_FACET} is supported."
        )
    return facets


def _search_url(base_search_url: str, facets: dict[str, str]) -> str:
    if not facets:
        return base_search_url
    parsed = urlparse(base_search_url)
    params = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
    params.update(facets)
    return urlunparse(parsed._replace(query=urlencode(params)))


def _row_country(location: str) -> str | None:
    """The country code a row's location names, or None if it names none."""
    tokens = [t.strip() for t in location.split(",")]
    if len(tokens) >= 3 and _ROW_COUNTRY.fullmatch(tokens[2]):
        return tokens[2]
    return None


def _check_country(jobs: list[dict[str, Any]], country: str, source_name: str, url: str) -> int:
    """Raise on the first row outside *country*; return how many rows proved it.

    A row with no location says nothing either way and is let through, as WP8f
    admits it at Layer 1. A row whose location names no country is the same: the
    caller fails the walk if no row at all could prove the facet.
    """
    proved = 0
    for job in jobs:
        found = _row_country(job["location"])
        if found is None:
            continue
        if found.upper() != country.upper():
            raise FacetNotAppliedError(
                f"{source_name}: asked for {_COUNTRY_FACET}={country} but {url} lists "
                f"{job['title']!r} in {found} ({job['location']!r}). The board did not "
                "apply the facet, so this is not the board that was configured.",
                facet=_COUNTRY_FACET,
                wanted=country,
                found=found,
                url=url,
            )
        proved += 1
    return proved


def _read_fields(container: Any, title: str) -> tuple[str, str]:
    """Location and department for one posting, by whichever layout this row is.

    Both labelled layouts are tried before the positional heuristic, because a
    site that names its fields is telling us which is which and guessing over
    the top of that is how the date ended up in `department`. The heuristic
    survives only for a skin neither branch recognises.
    """
    for reader in (
        lambda: (
            _tile_field(container, _TILE_LOCATION_KINDS),
            _tile_field(container, _TILE_DEPARTMENT_KINDS),
        ),
        lambda: (
            _classic_field(container, _CLASSIC_LOCATION_SELECTOR),
            _classic_field(container, _CLASSIC_DEPARTMENT_SELECTOR),
        ),
    ):
        field_location, field_department = reader()
        # One labelled field is enough to identify the layout. The other may be
        # legitimately absent, and "" is an honest answer WP8f admits at Layer 1.
        if field_location is not None or field_department is not None:
            return field_location or "", field_department or ""

    texts = [t for t in _visible_strings(container) if t != title]
    # Heuristic: first non-title text is usually location or job family
    location = texts[0] if texts else ""
    department = texts[1] if len(texts) >= 2 else ""
    return location, department


def _parse_page(
    soup: BeautifulSoup,
    base_search_url: str,
    source_name: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    # DSV renders every row three times, once per breakpoint (desktop, tablet,
    # phone). That is one posting shown thrice, not three postings, so it is
    # settled here; the walk's own `seen` is for repeats *across* pages.
    on_this_page: set[str] = set()

    # Derive the host root from the search URL so relative hrefs resolve correctly
    parsed = urlparse(base_search_url)
    host_root = f"{parsed.scheme}://{parsed.netloc}"

    for a in soup.find_all("a", href=lambda h: h and _JOB_HREF.match(h.strip())):
        href = str(a["href"]).strip()
        detail_url = urljoin(host_root, href)
        if detail_url in on_this_page:
            continue

        title = a.get_text(" ", strip=True)
        if not title:
            continue
        on_this_page.add(detail_url)

        # Walk up to the row that holds the whole posting, not the innermost
        # box that happens to wrap the link. On the tile layout the nearest
        # <div> is `div.tiletitle`, which contains the title and its sr-only
        # label and nothing else — the location is two siblings away and was
        # never in scope.
        location = ""
        department = ""
        container = a.find_parent(["li", "tr"]) or a.find_parent("div")
        if container:
            location, department = _read_fields(container, title)

        raw_snippet = " ".join(x for x in [title, department, location] if x)
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": location,
                "department": department,
                "listing_url": base_search_url,
                "detail_url": detail_url,
                "apply_url": detail_url,
                "raw_snippet": raw_snippet,
            }
        )
    return out


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str,
    page_step: int,
    base_search_url: str,
) -> list[dict[str, Any]]:
    from job_scraper.http import is_rendering_fetcher  # avoid circular import

    # ISS's listing is built by AJAX, so it needs a selector wait before the
    # job links exist. Test the fetcher's *capability* and wrap the callable we
    # were given rather than substituting fetch_rendered: the caller's wrapper
    # may be doing something (the fixture capture script records the URL
    # through it, and an extractor that fetches for itself records nothing).
    fetch_fn: Callable[..., str] = fetch_text
    if is_rendering_fetcher(fetch_text):
        fetch_fn = partial(fetch_text, wait_for_selector=_WAIT_SELECTOR)

    facets = _facets_of(listing_url, base_search_url)
    base_search_url = _search_url(base_search_url, facets)
    country = facets.get(_COUNTRY_FACET)
    proved = 0

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    startrow = 0

    total: int | None = None
    stated_at = ""

    while True:
        url = _set_startrow(base_search_url, startrow)
        soup = BeautifulSoup(fetch_fn(url), "lxml")
        jobs = _parse_page(soup, base_search_url, source_name)
        if not jobs:
            break
        if country:
            proved += _check_country(jobs, country, source_name, url)

        # Only a page that parsed can be asked how long the board is. A page
        # whose label cannot be read says nothing about the board, so it is
        # not a change; the walk is still reconciled against the last total.
        declared = _declared_total(soup)
        if declared is not None and total is not None and declared != total:
            raise BoardMovedError(
                f"{source_name}: the board changed during the walk: it stated {total} "
                f"posting(s) up to {stated_at} and {declared} at {url}. A posting added "
                "or removed ahead of the walk moves the rest by a place, so one is read "
                "twice or never read; refusing a list that may be short.",
                before=total,
                after=declared,
                url=url,
            )
        if declared is not None:
            total, stated_at = declared, url

        new_jobs = 0
        for job in jobs:
            key = job["detail_url"]
            if key not in seen:
                seen.add(key)
                out.append(job)
                new_jobs += 1
        if new_jobs == 0:
            # All duplicates: the board is serving the same page over again,
            # which is the end of the list only if we have the whole list —
            # settled below, like every other way out of this loop.
            break
        if len(jobs) < page_step:
            break
        startrow += page_step

    if country and out and not proved:
        raise FacetNotAppliedError(
            f"{source_name}: asked for {_COUNTRY_FACET}={country} but no row of {len(out)} "
            "names its country, so nothing shows the facet was applied. Check the location "
            "column's markup before trusting this board.",
            facet=_COUNTRY_FACET,
            wanted=country,
            found="",
            url=url,
        )

    # Whichever of the three exits was taken, the board published a count and
    # this walk is measured against it. The short-page exit is the one that
    # needs it most: a page that half-renders is short, never empty, so nothing
    # inside the loop would have noticed.
    pagination.reconcile(source_name, url, collected=len(out), total=total)
    return out
