"""Extractor for Norrsken Foundation jobs page (Playwright-rendered).

HTML structure (injected by Teamtailor widget on norrsken.org):
    <a class="job-item-link careersite-job-url" href="https://careers.norrskenfoundation.org/jobs/ID-slug">
      <div class="open-positions-wrapper">
        <h3 class="job-item-title">Job Title</h3>
        <div>
          <div class="label-s black job-item-loc">Location</div>
          <div class="label-s black job-item-dept">Department</div>
        </div>
      </div>
    </a>
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bs4 import BeautifulSoup


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str = "norrsken",
) -> list[dict[str, Any]]:
    html = fetch_text(listing_url)
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    for a in soup.find_all("a", class_="careersite-job-url"):
        href = str(a.get("href", ""))
        if not href or href in seen:
            continue
        seen.add(href)

        title_el = a.find(class_="job-item-title")
        title = title_el.get_text(" ", strip=True) if title_el else a.get_text(" ", strip=True)
        if not title:
            continue

        loc_el = a.find(class_="job-item-loc")
        location = loc_el.get_text(" ", strip=True) if loc_el else ""

        dept_el = a.find(class_="job-item-dept")
        dept = dept_el.get_text(" ", strip=True) if dept_el else ""

        raw_snippet = " ".join(x for x in [title, dept, location] if x)
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": location,
                "department": dept,
                "listing_url": listing_url,
                "detail_url": href,
                "apply_url": href,
                "raw_snippet": raw_snippet,
            }
        )
    return out
