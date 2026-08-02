"""Extractor for UNDP job vacancies page.

The listing page (https://jobs.undp.org/cj_view_jobs.cfm) renders jobs as
anchor elements pointing to Oracle HCM:
    <a href="https://estm.fa.em2.oraclecloud.com/hcmUI/…/job/ID">Title</a>

The links are NOT wrapped in <tr> elements — the table structure is rendered
client-side. All anchors are present in the static HTML, so no Playwright
is needed. Metadata (location, department) is extracted from the nearest
parent container's sibling text nodes where available.

No pagination — all vacancies appear on a single page.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bs4 import BeautifulSoup


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str = "undp",
) -> list[dict[str, Any]]:
    html = fetch_text(listing_url)
    soup = BeautifulSoup(html, "lxml")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=lambda h: h and "oraclecloud.com" in h):
        title = a.get_text(" ", strip=True)
        if not title:
            continue

        detail_url = str(a["href"]).strip()
        if detail_url in seen:
            continue
        seen.add(detail_url)

        # Try to pull location / agency from the nearest container
        location = ""
        department = ""
        container = a.find_parent(["td", "li", "div", "p"])
        if container:
            parent = container.find_parent(["tr", "li", "div"])
            if parent:
                # All text nodes in the row except the job title itself
                texts = [
                    t.strip()
                    for t in parent.stripped_strings
                    if t.strip() and t.strip() != title
                ]
                # Heuristic: last text is typically location, second-to-last agency
                if texts:
                    location = texts[-1]
                if len(texts) >= 2:
                    department = texts[-2]

        raw_snippet = " ".join(x for x in [title, department, location] if x)
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": location,
                "department": department,
                "listing_url": listing_url,
                "detail_url": detail_url,
                "apply_url": detail_url,
                "raw_snippet": raw_snippet,
            }
        )

    return out
