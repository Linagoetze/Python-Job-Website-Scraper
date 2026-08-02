"""Job scraper package (skeleton)."""

from __future__ import annotations

__version__ = "0.1.0"

from typing import TypedDict


class JobRecord(TypedDict, total=False):
    """Common shape for job dicts flowing through the pipeline.

    Required fields are set by every extractor; optional fields are added
    by later pipeline stages (filtering, experience check, storage).
    """

    # --- Set by extractors (always present) ---
    source_name: str
    title: str
    company: str
    location: str
    department: str
    listing_url: str
    detail_url: str
    apply_url: str
    raw_snippet: str

    # --- Added by pipeline / filters ---
    matched_reasons: list[str]
    experience_level: str

    # --- Added by CSV storage ---
    detail_hyperlink: str
    run_id: str
