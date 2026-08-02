"""Append normalized jobs to CSV with deduplication by canonical URL."""

from __future__ import annotations

import csv
import html
import re
from pathlib import Path
from typing import Any

from job_scraper.urlutil import canonical_detail_url, dedupe_key_from_url, normalize_http_url


FIELDNAMES = [
    "source_name",
    "title",
    "company",
    "location",
    "detail_hyperlink",
    "apply_hyperlink",
    "run_id",
]

# Sources where the same real job recurs under different URLs (re-advertised
# postings or multi-feed aggregation), so URL dedup alone leaves duplicates.
# These get an extra content-based collapse keyed on (employer + title).
_CONTENT_DEDUPE_SOURCES = {"impactpool", "jobsinlund"}

_NON_ALNUM = re.compile(r"[^\w]+", re.UNICODE)

# Columns removed in a previous schema version — trigger migration if found
_REMOVED_COLUMNS = {
    "department", "detail_url", "apply_url",
    "raw_snippet", "matched_reasons", "experience_level",
    "listing_url",
}


def _excel_hyperlink_formula(url: str) -> str:
    """Single-arg HYPERLINK so the formula has no commas (safer for CSV)."""
    u = normalize_http_url(url)
    if not u:
        return ""
    return "=HYPERLINK(\"" + u.replace('"', '""') + "\")"


def _url_from_hyperlink_formula(formula: str) -> str:
    """Extract the URL from a =HYPERLINK("url") formula string."""
    m = re.match(r'=HYPERLINK\("([^"]+)"', formula)
    return m.group(1) if m else ""


def _dedupe_key(row: dict[str, Any]) -> str:
    """Stable deduplication key from a job dict (extractor output or CSV row).

    Checks detail_url / apply_url first (present on fresh extractor output),
    then falls back to parsing the detail_hyperlink formula (present on CSV rows).
    """
    u = normalize_http_url(
        (row.get("detail_url") or "").strip() or (row.get("apply_url") or "").strip()
    )
    if not u:
        u = normalize_http_url(_url_from_hyperlink_formula(str(row.get("detail_hyperlink") or "")))
    return dedupe_key_from_url(u)


def _normalize_row_fields(row: dict[str, Any]) -> dict[str, Any]:
    # Compute detail URL from explicit fields or recover from hyperlink formula
    raw_du = (row.get("detail_url") or "").strip() or (row.get("apply_url") or "").strip()
    raw_au = (row.get("apply_url") or "").strip()
    src = str(row.get("source_name") or "").strip()
    listing = str(row.get("listing_url") or "").strip()
    du = canonical_detail_url(src, listing, raw_du) if raw_du else ""
    if not du:
        du = _url_from_hyperlink_formula(str(row.get("detail_hyperlink") or ""))
    au = normalize_http_url(raw_au) if raw_au and raw_au != du else ""
    m: dict[str, Any] = {k: row.get(k, "") for k in FIELDNAMES}
    m["detail_hyperlink"] = _excel_hyperlink_formula(du)
    m["apply_hyperlink"] = _excel_hyperlink_formula(au) if au else ""
    return m


def _dedupe_rows_first_wins(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        k = _dedupe_key(row)
        if not k:
            continue
        if k in seen:
            continue
        seen.add(k)
        out.append(row)
    return out


def _normalize_text(s: str) -> str:
    """Lowercase, HTML-unescape, and reduce to space-separated alphanumeric tokens.

    Keeps Unicode letters (e.g. Swedish å/ä/ö) so titles like
    'Division Manager Food & Pharma' and 'M365 Copilot &amp; Compliance konsult.'
    normalize to a stable, punctuation-free form.
    """
    u = html.unescape(s or "")
    u = _NON_ALNUM.sub(" ", u.lower())
    return " ".join(u.split())


def _content_key(row: dict[str, Any]) -> str:
    """Content dedup key for sources in _CONTENT_DEDUPE_SOURCES, else ''.

    Keyed on source + normalized employer + normalized title so the same posting
    under different URLs collapses, while two different employers sharing a
    generic title (e.g. 'Product Manager') stay separate.
    """
    source = str(row.get("source_name") or "").strip().lower()
    if source not in _CONTENT_DEDUPE_SOURCES:
        return ""
    title = _normalize_text(str(row.get("title") or ""))
    if not title:
        return ""
    company = _normalize_text(str(row.get("company") or ""))
    return f"{source}\x00{company}\x00{title}"


def _collapse_content_duplicates(path: Path) -> int:
    """Collapse content-duplicate rows, keeping the most recent (highest run_id).

    Only rows from _CONTENT_DEDUPE_SOURCES participate; all other rows pass
    through untouched in their original order. Returns the number of rows
    removed. Purely subtractive — surviving rows (and their run_ids) are
    unchanged.
    """
    if not path.is_file() or path.stat().st_size == 0:
        return 0
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return 0
        rows = list(reader)
    if not rows:
        return 0

    # Pick the winning row index per content key (max run_id, first wins on tie).
    best: dict[str, int] = {}
    for i, row in enumerate(rows):
        k = _content_key(row)
        if not k:
            continue
        if k not in best or int(row.get("run_id") or 0) > int(rows[best[k]].get("run_id") or 0):
            best[k] = i

    kept: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        k = _content_key(row)
        if k and best.get(k) != i:
            continue
        kept.append(row)

    removed = len(rows) - len(kept)
    if removed:
        _rewrite_file(path, kept)
    return removed


def _rewrite_file(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write("\ufeff")
        w = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _migrate_csv_schema_if_needed(path: Path) -> None:
    """Rewrite existing CSV when removed columns are still present or run_id is missing."""
    if not path.is_file() or path.stat().st_size == 0:
        return
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = set(reader.fieldnames or [])
        has_removed = bool(fieldnames & _REMOVED_COLUMNS)
        needs_run_id = "run_id" not in fieldnames
        needs_apply_hyperlink = "apply_hyperlink" not in fieldnames
        needs_company = "company" not in fieldnames
        if not has_removed and not needs_run_id and not needs_apply_hyperlink and not needs_company:
            return  # already up to date
        rows = list(reader)
    migrated = [_normalize_row_fields(r) for r in rows]
    if needs_run_id:
        for r in migrated:
            if not r.get("run_id"):
                r["run_id"] = "0"
    migrated = _dedupe_rows_first_wins(migrated)
    _rewrite_file(path, migrated)


def _dedupe_file_if_needed(path: Path) -> None:
    """Normalize URLs, refresh HYPERLINK column, drop duplicate job keys."""
    if not path.is_file() or path.stat().st_size == 0:
        return
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "detail_hyperlink" not in reader.fieldnames:
            return
        rows = list(reader)
    if not rows:
        return
    out = _dedupe_rows_first_wins([_normalize_row_fields(r) for r in rows])
    _rewrite_file(path, out)


def _read_existing_keys(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    keys: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return keys
        for row in reader:
            k = _dedupe_key(row)
            if k:
                keys.add(k)
    return keys


def clean_existing_rows(
    path: Path,
    rules: dict[str, Any],
    title_keywords: list[tuple[str, str]],
    source_scraped_keys: dict[str, set[str]] | None = None,
    blocklist_keys: set[str] | None = None,
) -> dict[str, int]:
    """Re-filter pre-existing CSV rows in a single read-filter-write pass.

    Applies all filter layers (rules, seniority titles, title keywords,
    language patterns) and fixes stale Mammut locations. If source_scraped_keys
    is provided, also removes rows for any source that was successfully scraped
    this run but whose URL no longer appears in the fresh listing. If
    blocklist_keys is provided, removes rows whose dedupe key is blocklisted.
    Returns a dict of removal/fix counts keyed by filter name.
    """
    from job_scraper.experience_filter import apply_title_filter  # avoid circular import
    from job_scraper.filtering import (  # avoid circular import
        apply_language_filter,
        apply_non_english_text_filter,
        apply_title_keyword_filter,
        matches_rules,
    )

    counts: dict[str, int] = {
        "rules": 0,
        "title": 0,
        "title_keywords": 0,
        "language": 0,
        "non_english_text": 0,
        "mammut_fixed": 0,
        "delisted": 0,
        "blocklist": 0,
    }

    if not path.is_file() or path.stat().st_size == 0:
        return counts

    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        return counts

    # Fix stale Mammut location values (pipe-separated → city only)
    for row in rows:
        if row.get("source_name") == "mammut":
            loc = str(row.get("location") or "")
            if "|" in loc:
                parts = [p.strip() for p in loc.split("|")]
                row["location"] = parts[2] if len(parts) >= 3 else loc
                counts["mammut_fixed"] += 1

    # Rules filter (location / keyword matching)
    kept = [r for r in rows if matches_rules(r, rules)[0]]
    counts["rules"] = len(rows) - len(kept)

    # Seniority title filter
    kept, excluded = apply_title_filter(kept, rules)
    counts["title"] = len(excluded)

    # Title keyword filter (CSV-driven role-type exclusions)
    kept, excluded = apply_title_keyword_filter(kept, title_keywords)
    counts["title_keywords"] = len(excluded)

    # Non-English text filter — runs first so non-English jobs are dropped before
    # the speaker-pattern check (matching the pipeline order)
    kept, excluded = apply_non_english_text_filter(kept)
    counts["non_english_text"] = len(excluded)

    # Language filter (speaker/speaking pattern)
    kept, excluded = apply_language_filter(kept)
    counts["language"] = len(excluded)

    # Blocklist filter — remove rows the user has permanently rejected
    if blocklist_keys:
        before = len(kept)
        kept = [r for r in kept if _dedupe_key(r) not in blocklist_keys]
        counts["blocklist"] = before - len(kept)

    # Delisted filter — remove rows for sources scraped this run that no longer appear
    if source_scraped_keys:
        fresh: list[dict[str, Any]] = []
        for row in kept:
            sn = str(row.get("source_name") or "")
            if sn in source_scraped_keys:
                k = _dedupe_key(row)
                if k not in source_scraped_keys[sn]:
                    counts["delisted"] += 1
                    continue
            fresh.append(row)
        kept = fresh

    total_removed = counts["rules"] + counts["title"] + counts["title_keywords"] + counts["language"] + counts["non_english_text"] + counts["blocklist"] + counts["delisted"]
    if total_removed or counts["mammut_fixed"]:
        _rewrite_file(path, kept)

    return counts


def _next_run_id(path: Path) -> int:
    """Return max(existing run_id) + 1, or 1 if the file is empty/absent."""
    if not path.is_file() or path.stat().st_size == 0:
        return 1
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        max_id = max((int(r.get("run_id") or 0) for r in reader), default=0)
    return max_id + 1



def sort_jobs_csv(path: Path) -> None:
    """Sort CSV alphabetically by source_name, newest run_id first within each company."""
    if not path.is_file() or path.stat().st_size == 0:
        return
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return
    rows.sort(key=lambda r: (r.get("source_name", "").lower(), -int(r.get("run_id") or 0)))
    _rewrite_file(path, rows)


def append_jobs_csv(path: Path, rows: list[dict[str, Any]]) -> int:
    """
    Append rows to CSV at *path*, skipping rows whose canonical URL was already stored.
    Returns number of rows written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    _migrate_csv_schema_if_needed(path)
    _dedupe_file_if_needed(path)
    existing = _read_existing_keys(path)
    current_run_id = str(_next_run_id(path))
    to_write: list[dict[str, Any]] = []
    for row in rows:
        key = _dedupe_key(row)
        if not key:
            continue
        if key in existing:
            continue
        existing.add(key)
        row = dict(row)
        row["run_id"] = current_run_id
        to_write.append(_normalize_row_fields(row))

    if not to_write:
        _collapse_content_duplicates(path)
        return 0

    write_header = not path.is_file() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as f:
        if write_header:
            f.write("\ufeff")
        w = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        if write_header:
            w.writeheader()
        w.writerows(to_write)

    # Collapse content duplicates (e.g. same job re-advertised under a new URL),
    # keeping the most recent row. Newly written rows carry the highest run_id,
    # so they win and the older twins are dropped.
    _collapse_content_duplicates(path)
    return len(to_write)
