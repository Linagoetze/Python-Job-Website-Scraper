"""Generic extractor for Workday-hosted job boards, read through the board's JSON.

A Workday listing page is a JavaScript app. It shows twenty postings and pages
by button, not by URL: `?page=2` renders page one again (SP3b, checked live).
What the app itself calls for each page is

    POST https://<tenant>.<dc>.myworkdayjobs.com/wday/cxs/<tenant>/<board>/jobs
    {"limit": 20, "offset": N, "searchText": "", "appliedFacets": {}}

which answers with the board's `total` and, per posting, `title`,
`externalPath` and `locationsText` — the same title and location the rendered
card shows. This reader walks that endpoint twenty at a time and checks the
walk against `total` before it returns (see `pagination.py`), so a board longer
than one page is read whole, and a walk that stops short fails the source.

The detail URL is rebuilt exactly as the rendered page linked it, because
stored jobs are keyed on it: `https://<host>/<locale>/<board>` + `externalPath`,
the locale taken from the listing URL, or `en-US` when the listing names none —
which is what the rendered page used for every such board. See
docs/DECISIONS.md (SP3b) before changing that construction.

A listing URL may carry the board's own filter query, as Workday's listing puts
it when a facet is ticked (`?locationCountry=<country-id>`). Each key is a
facet parameter, a repeated key a list of ids, and the reader sends them as the
POST's `appliedFacets` (SP3c). The query never reaches a detail URL: the
rendered page appends it to every job link, and a stored key built that way
would never match again. A filter Workday ignored would read a different board
from the one configured, so the first response has to show it applied (see
`_check_applied`) or the source fails.

The POST goes through the fetcher this reader is handed: `http.fetch_text` and
`http.fetch_rendered` carry `http.post_json` as their `post_json`, and the
fixture capture script and the probe hand in fetchers that record or replay
it. A fetcher that cannot POST is refused rather than bypassed, so no caller
reaches the network by a route it did not choose.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from job_scraper.extractors import pagination

# Workday's own page size, and the most its endpoint will serve per request.
_PAGE_SIZE = 20

# A runaway guard, not an expected bound: 500 pages is 10,000 postings, well
# past any board here. A walk that reaches it with more to come fails loudly.
_MAX_PAGES = 500

# The most `total` ever says. Airbus's board answered exactly 2000 while its
# single-valued facets (full/part time, worker type) summed to about 2,940
# (SP3b, 2026-09-23); boards under the cap — axis_comms at 98, irc at 350 —
# summed to their total exactly. At the cap the count is a floor, not a length,
# so a walk checked against it would pass as whole while short. A board that
# reaches it fails before the walk rather than after a hundred requests.
_TOTAL_CAP = 2000


class CappedTotalError(pagination.ShortWalkError):
    """The board states Workday's capped `total`, so no walk of it can be checked.

    The stated total and the endpoint are fields, not only words in the message:
    the probe tells the owner to narrow the board from them, and a reworded
    message must not be able to break that (the `RobotsDisallowed.url` rule).
    """

    def __init__(self, message: str, *, total: int, endpoint: str) -> None:
        super().__init__(message)
        self.total = total
        self.endpoint = endpoint


class FacetNotAppliedError(ValueError):
    """The first response does not show the listing's filter query applied.

    A different board from the one configured, so the source fails. The
    endpoint and the filter are fields for the same reason as
    `CappedTotalError`'s: the probe tells the owner to check the query from
    them, and must not blame this reader, which is right to refuse.
    """

    def __init__(self, message: str, *, endpoint: str, facets: dict[str, list[str]]) -> None:
        super().__init__(message)
        self.endpoint = endpoint
        self.facets = facets


_HOST = re.compile(r"(?P<tenant>[a-z0-9][a-z0-9-]*)\.wd\d+\.myworkdayjobs\.com", re.I)
_LOCALE = re.compile(r"[a-z]{2}-[A-Z]{2}")
_DEFAULT_LOCALE = "en-US"


def _endpoints(listing_url: str) -> tuple[str, str, dict[str, list[str]]]:
    """The board's JSON endpoint, the prefix its detail URLs hang off, and its facets.

    Refuses a listing it cannot map exactly, and a query it cannot translate
    faithfully into `appliedFacets`: silently dropping or bending one would read
    a different set of postings than the one configured.
    """
    parts = urlsplit(listing_url)
    host = parts.hostname or ""
    match = _HOST.fullmatch(host)
    if match is None:
        raise ValueError(f"{listing_url} is not a <tenant>.wdN.myworkdayjobs.com board")
    segments = [s for s in parts.path.split("/") if s]
    locale = segments[0] if segments and _LOCALE.fullmatch(segments[0]) else None
    rest = segments[1:] if locale else segments
    if len(rest) != 1:
        raise ValueError(f"{listing_url} is not /<board> or /<locale>/<board>")
    board = rest[0]
    # The tenant id in the endpoint is the page's own `tenant: "osv_chegg"`,
    # and a hostname cannot carry an underscore, so the host spells it
    # `osv-chegg`. POSTing the host's spelling answered 422 (SP3b, busuu).
    tenant = match.group("tenant").replace("-", "_")
    origin = f"{parts.scheme or 'https'}://{host}"
    return (
        f"{origin}/wday/cxs/{tenant}/{board}/jobs",
        f"{origin}/{locale or _DEFAULT_LOCALE}/{board}",
        _facets(listing_url, parts.query),
    )


def _facets(listing_url: str, query: str) -> dict[str, list[str]]:
    """The listing's filter query as `appliedFacets`: each key a facet, each value an id.

    Only the shape Workday's own listing writes is accepted. Anything else —
    a key with no `=`, an empty key or id — has no faithful translation, and
    guessing one would read a board nobody configured.
    """
    if not query:
        return {}
    try:
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise ValueError(
            f"{listing_url} carries a query ({query}) that is not key=value pairs; "
            "only the listing's own facet query, such as ?locationCountry=<id>, translates"
        ) from None
    facets: dict[str, list[str]] = {}
    for key, value in pairs:
        if not key.strip() or not value.strip():
            raise ValueError(
                f"{listing_url} has an empty facet name or id in its query ({query}); "
                "an empty filter has no faithful translation"
            )
        ids = facets.setdefault(key, [])
        # A repeated id says nothing a single one does not.
        if value not in ids:
            ids.append(value)
    return facets


def _facet_lists(nodes: Any) -> dict[str, list[dict[str, Any]]]:
    """Every facet in a response, by its parameter, however deeply it is nested.

    `locationCountry` sits inside `locationMainGroup` rather than at the top,
    so a flat lookup would miss the very facet airbus is narrowed by.
    """
    found: dict[str, list[dict[str, Any]]] = {}
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict):
            continue
        values = node.get("values")
        parameter = node.get("facetParameter")
        if isinstance(parameter, str) and isinstance(values, list):
            found.setdefault(parameter, [v for v in values if isinstance(v, dict)])
        found.update({k: v for k, v in _facet_lists(values).items() if k not in found})
    return found


def _check_applied(
    source_name: str, api_url: str, facets: dict[str, list[str]], data: Any, total: int | None
) -> None:
    """Fail unless the first response shows every facet in the query applied.

    Workday's facets are disjunctive: the applied parameter's own list keeps
    the whole board's counts, while everything else narrows. So a filter that
    took effect shows as its id in that list with a count equal to `total`,
    and one Workday ignored cannot, since `total` is then the whole board's
    (SP3c, docs/DECISIONS.md). With several ids under one parameter a posting
    can be listed under two of them, so their summed counts only have to
    reach `total`.
    """
    if not facets:
        return
    if total is None:
        raise FacetNotAppliedError(
            f"{source_name}: {api_url} states no total, so nothing shows that the "
            f"filter {facets} was applied; refusing a board that may not be the one configured",
            endpoint=api_url,
            facets=facets,
        )
    listed = _facet_lists(data.get("facets") if isinstance(data, dict) else None)
    for parameter, ids in facets.items():
        if parameter not in listed:
            raise FacetNotAppliedError(
                f"{source_name}: {api_url} lists no {parameter!r} facet, so nothing shows "
                "that filter was applied; Workday may have ignored it and read the whole "
                "board. Check the name against the listing's own filter query.",
                endpoint=api_url,
                facets=facets,
            )
        counts = {str(v.get("id")): v.get("count") for v in listed[parameter] if "id" in v}
        missing = [i for i in ids if not isinstance(counts.get(i), int)]
        if missing:
            raise FacetNotAppliedError(
                f"{source_name}: {api_url} has no {parameter!r} value counted under id "
                f"{', '.join(missing)}: a mistyped id, or one with no postings today. "
                "Either way nothing shows the filter was applied.",
                endpoint=api_url,
                facets=facets,
            )
        applied = sum(int(counts[i]) for i in ids)
        shown = applied == total if len(ids) == 1 else applied >= total
        if not shown:
            raise FacetNotAppliedError(
                f"{source_name}: {api_url} states {total} postings, but its {parameter!r} "
                f"facet gives the filter's own value(s) {applied}. Workday did not apply "
                "the filter as configured, so this is a different board; refusing it.",
                endpoint=api_url,
                facets=facets,
            )


def _declared_total(data: Any) -> int | None:
    """The board's `total`, or None if the response does not carry a number."""
    total = data.get("total") if isinstance(data, dict) else None
    if isinstance(total, int) and not isinstance(total, bool):
        return total
    return None


def extract(
    listing_url: str,
    fetch_text: Callable[..., str],
    source_name: str,
) -> list[dict[str, Any]]:
    post_json = getattr(fetch_text, "post_json", None)
    if post_json is None:
        raise TypeError(
            f"{source_name}: workday.py reads the board's JSON endpoint and was handed "
            "a fetcher with no post_json; pass http.fetch_text or http.fetch_rendered, "
            "or a wrapper that carries theirs"
        )
    api_url, detail_prefix, facets = _endpoints(listing_url)

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    total: int | None = None
    offset = 0

    for _ in range(_MAX_PAGES):
        data = post_json(
            api_url,
            {
                "limit": _PAGE_SIZE,
                "offset": offset,
                "searchText": "",
                "appliedFacets": facets,
            },
            headers={"Accept": "application/json"},
        )
        postings = (data.get("jobPostings") if isinstance(data, dict) else None) or []
        # Taken from the first response only: it is the number the walk is
        # held to. path's four pages all repeated it (SP3b); a later 0, which
        # some Workday tenants are said to send but none here has, must not be
        # able to replace it, or `reconcile` would pass any short walk. Later
        # totals would add nothing: a board that shrinks mid-walk already
        # fails the check, and one that grows only delays a new posting to the
        # next run.
        if offset == 0:
            total = _declared_total(data)
            if total is not None and total >= _TOTAL_CAP:
                raise CappedTotalError(
                    f"{source_name}: {api_url} states {total} postings, which is the "
                    "most Workday's endpoint ever reports; the board may hold more, and "
                    "a walk checked against a capped count cannot tell whole from short. "
                    "Refusing to read it until its listing is narrowed below the cap.",
                    total=total,
                    endpoint=api_url,
                )
            # After the cap check, not before: at the cap `total` is a floor,
            # and comparing a facet count with it would blame the filter.
            _check_applied(source_name, api_url, facets, data, total)
        if not postings:
            break

        new_jobs = 0
        for posting in postings:
            path = str(posting.get("externalPath") or "")
            title = str(posting.get("title") or "").strip()
            if not path or not title:
                continue
            full = detail_prefix + path
            if full in seen:
                continue
            seen.add(full)
            new_jobs += 1
            location = str(posting.get("locationsText") or "").strip()
            out.append(
                {
                    "source_name": source_name,
                    "title": title,
                    "location": location,
                    "department": "",
                    "listing_url": listing_url,
                    "detail_url": full,
                    "apply_url": full,
                    "raw_snippet": " ".join(x for x in [title, location] if x),
                }
            )

        if new_jobs == 0:
            # The same page again: the end of the list only if the list is
            # whole, which the reconciliation below settles.
            break
        offset += len(postings)
        # A total of 0 beside a page of postings is not a total, so only a
        # positive one may end the walk early; otherwise the page's shape does.
        if total and len(out) >= total:
            break
        if len(postings) < _PAGE_SIZE:
            break
    else:
        raise pagination.ShortWalkError(
            f"{source_name}: stopped at the {_MAX_PAGES}-page limit holding {len(out)} "
            "posting(s) with more still coming. Refusing a list that may be short."
        )

    # However the loop ended, the board said how long it is: either every
    # posting is here or the source fails. A short page is the exit that needs
    # this most, since a page that half-answers is short, never empty.
    pagination.reconcile(source_name, listing_url, collected=len(out), total=total)
    return out
