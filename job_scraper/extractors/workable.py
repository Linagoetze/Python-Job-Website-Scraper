"""Generic extractor for Workable-hosted job boards (apply.workable.com).

Uses the Workable public jobs API (POST):
    POST https://apply.workable.com/api/v3/accounts/{slug}/jobs
    Body: {"query":"","location":[],"department":[],"worktype":[],"remote":[]}

Returns JSON with a "results" array of job objects.
Detail URL pattern: https://apply.workable.com/{slug}/j/{shortcode}/

The POST goes through the fetcher this reader is handed, as workday.py's does
(SP3b): `http.fetch_text` and `http.fetch_rendered` carry `http.post_json` as
their `post_json`, and the fixture capture script and the probe hand in
fetchers that record or replay it. Until SP4 this module called
`http.post_json` directly, which meant the capture script recorded nothing for
it ("extractor made no request") and the probe stubbed it at the module
instead of through its own fetcher. A fetcher that cannot POST is refused
rather than bypassed, so no caller reaches the network by a route it did not
choose.

The board is walked (SP13). A response holds at most ten postings and, when
there may be more, a `nextPage` token; the next POST is the same body with that
token as `"token"`. `total` is the whole board's count, and the walk is
reconciled against it (see `pagination.py`), so a board of eleven or more reads
whole or fails the source. Two facts decide where the walk ends, both seen in
the saved responses: a *full* page carries a token even when it is the last
(simprints: ten postings, `total: 10`, a token, then an empty page), so a token
alone does not mean "more"; and the end is therefore an empty page, or a
response with no token, with the count reached. Before SP13 the reader sent one
POST and nutrition_international's board of eleven was read as ten.

The listing also says how and where each job is worked, which its description
often does not (SP4f). `workplace` (remote, hybrid or on_site) goes into
`raw_snippet`, the text Layer 0's remote keywords and hybrid gate already read.
`remote` is the same fact as a boolean and is not read twice. The location
field lists every location the posting shows (`locations`, without the ones
marked `hidden`), one segment each. A posting that shows none keeps the single
`location` Workable reports, as before.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from job_scraper.extractors import pagination

_API_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}
_EMPTY_BODY = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}

# A runaway guard, not an expected bound. A response holds ten postings, so a
# hundred requests read a board of up to 990: ninety times the larger of the two
# boards read here (eleven postings) and past any Workable employer a personal
# job search reads. A walk that reaches it with a token still coming raises.
_MAX_PAGES = 100

# The workplace values that say something Layer 0 can read, in the words its
# remote keywords and hybrid gate look for. on_site adds nothing to a location.
_WORKPLACE_WORDS = {"remote": "Remote", "hybrid": "Hybrid"}


def extract(
    listing_url: str,
    fetch_text: Callable[..., str],
    source_name: str,
) -> list[dict[str, Any]]:
    post_json = getattr(fetch_text, "post_json", None)
    if post_json is None:
        raise TypeError(
            f"{source_name}: workable.py reads the board's JSON API and was handed a "
            "fetcher with no post_json; pass http.fetch_text or http.fetch_rendered, "
            "or a wrapper that carries theirs"
        )
    slug = listing_url.rstrip("/").split("/")[-1]
    api_url = f"https://apply.workable.com/api/v3/accounts/{slug}/jobs"

    results: list[dict[str, Any]] = []
    seen_posts: set[str] = set()
    seen_tokens: set[str] = set()
    total: int | None = None
    body = dict(_EMPTY_BODY)

    for page in range(_MAX_PAGES):
        data = post_json(api_url, body, headers=_API_HEADERS)
        data = data if isinstance(data, dict) else {}
        # The first response's count only, as workday.py does: it is the number
        # the walk is held to, and a later page must not be able to replace it.
        if page == 0:
            total = _declared_total(data)
        for job in data.get("results") or []:
            # A page served twice would otherwise count toward `total` and let a
            # short walk pass; a posting with no shortcode or id cannot be told
            # apart, so it is kept.
            key = str(job.get("shortcode") or job.get("id") or "")
            if key and key in seen_posts:
                continue
            seen_posts.add(key)
            results.append(job)
        token = data.get("nextPage")
        if not data.get("results") or not token:
            break
        token = str(token)
        if token in seen_tokens:
            raise pagination.ShortWalkError(
                f"{source_name}: {api_url} sent the same nextPage token twice, so the "
                f"walk is going round in a circle at {len(results)} posting(s). "
                "Refusing a list that may be short."
            )
        seen_tokens.add(token)
        body = {**_EMPTY_BODY, "token": token}
    else:
        raise pagination.ShortWalkError(
            f"{source_name}: stopped at the {_MAX_PAGES}-page limit holding {len(results)} "
            "posting(s) with more still coming. Refusing a list that may be short."
        )

    # However the loop ended, `total` is the board's count: either every
    # posting is here or the source fails. More is fine (a posting added mid-walk).
    pagination.reconcile(source_name, listing_url, collected=len(results), total=total)

    out: list[dict[str, Any]] = []
    for job in results:
        title = (job.get("title") or "").strip()
        if not title:
            # Skipping would turn an API whose shape changed into a shorter
            # list that reads as fewer vacancies.
            raise ValueError(
                f"{source_name}: a posting of the Workable API has no title ({str(job)[:80]!r})"
            )

        shortcode = (job.get("shortcode") or "").strip()
        depts = job.get("department") or []
        dept = depts[0].strip() if depts else ""

        location = _location(job)
        workplace = _WORKPLACE_WORDS.get(str(job.get("workplace") or "").strip().lower(), "")

        detail_url = (
            f"https://apply.workable.com/{slug}/j/{shortcode}/" if shortcode else listing_url
        )

        raw_snippet = " ".join(x for x in [title, dept, location, workplace] if x)
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": location,
                "department": dept,
                "listing_url": listing_url,
                "detail_url": detail_url,
                "apply_url": detail_url,
                "raw_snippet": raw_snippet,
            }
        )
    return out


def _declared_total(data: dict[str, Any]) -> int | None:
    """The board's `total`, or None if the response does not carry a number."""
    total = data.get("total")
    if isinstance(total, int) and not isinstance(total, bool):
        return total
    return None


def _place(loc: dict[str, Any]) -> str:
    city = str(loc.get("city") or "").strip()
    country = str(loc.get("country") or "").strip()
    return ", ".join(x for x in [city, country] if x)


def _location(job: dict[str, Any]) -> str:
    """Every location the posting shows, one segment each.

    Falls back to the single `location` when the posting shows none, which is
    what this reader read before SP4f, so a posting whose locations are all
    hidden reads as it always has.
    """
    shown = [_place(loc) for loc in job.get("locations") or [] if not loc.get("hidden")]
    names = list(dict.fromkeys(n for n in shown if n))
    return " | ".join(names) if names else _place(job.get("location") or {})
