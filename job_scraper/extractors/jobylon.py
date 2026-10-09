"""Extractor for career pages that list their jobs from a Jobylon feed.

Jobylon is a Swedish recruitment platform. A customer publishes a feed of its
open jobs and embeds Jobylon's widget in its own careers page; the widget then
fills the page in the visitor's browser, which is why a static fetch of such a
page shows no postings and the probe reports a rendered board on no supported
platform. The feed URL is in the page's own HTML, in the inline script that
starts the widget:

    new JobListing({ feedUrl: "https://feed.jobylon.com/feeds/<id>/?format=json", ... })

It is the customer-facing feed that Jobylon issues for exactly this, it asks for
no token or cookie, and its host's robots.txt allows everything but a few named
aggregator bots (SP10, checked 2026-10-09). The feed is a bare JSON array, one
object per job, so this reader needs no browser and no walk: the employer's
page is never fetched at all, and the feed URL is the registry's argument.
(If the employer rotates the feed, the old URL fails and the source fails
loudly; the new one is in the same inline script.)

The feed states no total and has no pager. The widget's own "N rader" line is
counted from this same array, so there is nothing to check a short read against;
one request is the whole board, and a body that is not an array raises.

What the widget shows, and so what this reads, is set by the page's `structure`
list, not by Jobylon, so a customer's columns differ. This board's:

    Ort           departments > department > name   (Jobylon's "department" is the site)
    Affärsområde  layers_1 > layer > text
    Avdelning     layers_2 > layer > text            (first one, as the widget shows)

`location` is the "Ort" names (falling back to the geocoded `locations` cities
when a job has no department), joined as segments for Layer 0. `department` is
"Avdelning" and the business area goes into `raw_snippet`. Reading them by the
page's own selectors, not by their words, is the rule for a Swedish listing.

`descr` (the pitch and the duties) and `skills` (despite the name, the
qualifications, the application and the start date) together are the posting's
text, as HTML, and are supplied as `description_text` (SP4d): Layer 5 reads them
and fetches nothing. The detail pages are static (they carry JSON-LD
JobPosting), so a fetch would have worked; it would cost one request per new
job to read what the feed already holds.

The detail URL is `https://emp.jobylon.com/jobs/<id>/`, not the feed's
`urls.ad`. That link carries a slug of the title (`383650-<company>-<title>`),
and the slug follows the title when someone edits it. The URL is the dedupe
key, so an edited title would store the same job again and delist the old one.
Both forms serve the same page (checked on one posting). `workplaceTypes`
(on-site, hybrid, remote) is the platform's workplace field and goes into
`raw_snippet` (SP4f), as Ashby's does.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from bs4 import BeautifulSoup

_AD_URL = "https://emp.jobylon.com/jobs/{id}/"

# The workplace types that say something Layer 0 can read, in the words its
# remote keywords and hybrid gate look for. "on-site" adds nothing.
_WORKPLACE_WORDS = {"remote": "Remote", "hybrid": "Hybrid"}


def _plain(html: str) -> str:
    return BeautifulSoup(html, "lxml").get_text(" ", strip=True) if html else ""


def _layer_texts(job: dict[str, Any], key: str) -> list[str]:
    """`layers_1 > layer > text`, as the widget's selector reads it."""
    return [
        text
        for entry in job.get(key) or []
        if (text := str(((entry or {}).get("layer") or {}).get("text") or "").strip())
    ]


def _location(job: dict[str, Any]) -> str:
    names = [
        str(((entry or {}).get("department") or {}).get("name") or "").strip()
        for entry in job.get("departments") or []
    ]
    if not any(names):
        # The geocoded places, for a job that names no site. The widget would
        # show it an empty "Ort"; Layer 0 should see the places the feed holds.
        names = [
            str((((entry or {}).get("location") or {}).get("city")) or "").strip()
            for entry in job.get("locations") or []
        ]
    return " | ".join(dict.fromkeys(n for n in names if n))


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str,
    feed_url: str,
) -> list[dict[str, Any]]:
    # A body this reader cannot read must fail, not return [], which reads as
    # "no vacancies" (priority 2). An empty array is a different thing: a board
    # with nothing open answers with one.
    try:
        data = json.loads(fetch_text(feed_url))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source_name}: the Jobylon feed at {feed_url} is not JSON") from exc
    if not isinstance(data, list):
        raise ValueError(f"{source_name}: the Jobylon feed at {feed_url} is not a list of jobs")

    out: list[dict[str, Any]] = []
    for position, job in enumerate(data, start=1):
        job_id = job.get("id") if isinstance(job, dict) else None
        title = str((job or {}).get("title") or "").strip() if isinstance(job, dict) else ""
        if not isinstance(job_id, int) or isinstance(job_id, bool) or not title:
            # Skipping would turn a feed whose shape changed into a shorter
            # list that reads as fewer vacancies.
            raise ValueError(
                f"{source_name}: job {position} of the Jobylon feed has no numeric id or no "
                f"title ({str(job)[:80]!r})"
            )
        location = _location(job)
        dept = next(iter(_layer_texts(job, "layers_2")), "")
        area = next(iter(_layer_texts(job, "layers_1")), "")
        workplace = _WORKPLACE_WORDS.get(str(job.get("workplaceTypes") or "").strip().lower(), "")
        url = _AD_URL.format(id=job_id)
        apply_url = str((job.get("urls") or {}).get("apply") or "").strip() or url
        text = " ".join(
            t for t in (_plain(job.get("descr") or ""), _plain(job.get("skills") or "")) if t
        )
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": location,
                "department": dept,
                "listing_url": listing_url,
                "detail_url": url,
                "apply_url": apply_url,
                "raw_snippet": " ".join(x for x in [title, dept, area, location, workplace] if x),
                "description_text": text,
            }
        )
    return out
