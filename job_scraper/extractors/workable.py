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

_API_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}
_EMPTY_BODY = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}

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

    data = post_json(api_url, _EMPTY_BODY, headers=_API_HEADERS)

    out: list[dict[str, Any]] = []
    for job in data.get("results") or []:
        title = (job.get("title") or "").strip()
        if not title:
            continue

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
