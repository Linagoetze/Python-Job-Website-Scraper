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
from urllib.parse import urlsplit

from job_scraper.extractors import pagination

# Workday's own page size, and the most its endpoint will serve per request.
_PAGE_SIZE = 20

# A runaway guard, not an expected bound: 500 pages is 10,000 postings, well
# past any board here. A walk that reaches it with more to come fails loudly.
_MAX_PAGES = 500

_HOST = re.compile(r"(?P<tenant>[a-z0-9][a-z0-9-]*)\.wd\d+\.myworkdayjobs\.com", re.I)
_LOCALE = re.compile(r"[a-z]{2}-[A-Z]{2}")
_DEFAULT_LOCALE = "en-US"


def _endpoints(listing_url: str) -> tuple[str, str]:
    """The board's JSON endpoint, and the prefix its detail URLs hang off.

    Refuses a listing it cannot map exactly. A query string is refused too: on
    the rendered page it would be a search facet, and silently dropping it
    would read a different set of postings than the one configured.
    """
    parts = urlsplit(listing_url)
    host = parts.hostname or ""
    match = _HOST.fullmatch(host)
    if match is None:
        raise ValueError(f"{listing_url} is not a <tenant>.wdN.myworkdayjobs.com board")
    if parts.query:
        raise ValueError(
            f"{listing_url} carries a query ({parts.query}); the JSON walk does not "
            "translate search facets, so it would read a different set of postings"
        )
    segments = [s for s in parts.path.split("/") if s]
    locale = segments[0] if segments and _LOCALE.fullmatch(segments[0]) else None
    rest = segments[1:] if locale else segments
    if len(rest) != 1:
        raise ValueError(f"{listing_url} is not /<board> or /<locale>/<board>")
    board = rest[0]
    origin = f"{parts.scheme or 'https'}://{host}"
    return (
        f"{origin}/wday/cxs/{match.group('tenant')}/{board}/jobs",
        f"{origin}/{locale or _DEFAULT_LOCALE}/{board}",
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
    api_url, detail_prefix = _endpoints(listing_url)

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    total: int | None = None
    offset = 0

    for _ in range(_MAX_PAGES):
        data = post_json(
            api_url,
            {"limit": _PAGE_SIZE, "offset": offset, "searchText": "", "appliedFacets": {}},
            headers={"Accept": "application/json"},
        )
        postings = (data.get("jobPostings") if isinstance(data, dict) else None) or []
        # Taken from the first response only. Workday is reported to send the
        # total with the first page and 0 with the rest, and a later 0 must
        # not overwrite the number the walk is checked against.
        if offset == 0:
            total = _declared_total(data)
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
