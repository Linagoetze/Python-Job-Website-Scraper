"""Extractor for Personio-hosted job boards.

Fetches the public XML feed at {base_url}/xml, which uses the <workzag-jobs>
root element (Personio's legacy XML format). Individual job URLs follow
the pattern {base_url}/job/{id}?language=en.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from typing import Any


def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str,
) -> list[dict[str, Any]]:
    base = listing_url.split("?")[0].rstrip("/")
    xml_url = f"{base}/xml"
    xml_text = fetch_text(xml_url)

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    out: list[dict[str, Any]] = []
    for position in root.findall("position"):
        job_id = (position.findtext("id") or "").strip()
        title = (position.findtext("name") or "").strip()
        if not title or not job_id:
            continue
        location = (position.findtext("office") or "").strip()
        department = (position.findtext("department") or "").strip()
        job_url = f"{base}/job/{job_id}?language=en"
        raw_snippet = " ".join(x for x in [title, department, location] if x)
        out.append(
            {
                "source_name": source_name,
                "title": title,
                "location": location,
                "department": department,
                "listing_url": listing_url,
                "detail_url": job_url,
                "apply_url": job_url,
                "raw_snippet": raw_snippet,
            }
        )
    return out
