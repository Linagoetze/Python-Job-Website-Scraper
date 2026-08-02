"""Persistent blocklist of job postings the user has rejected.

Jobs are keyed by the same canonical-URL dedupe key used everywhere else
(`_dedupe_key` / `dedupe_key_from_url`), so a blocklisted posting stays blocked
even across URL-slug variants (e.g. Oatly's numeric-id keying). The blocklist
lives independently of `jobs.csv` so rejected jobs are never re-scraped, even
though they remain live on their career pages.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from job_scraper.config_loader import default_curated_dir
from job_scraper.storage.csv_store import _dedupe_key, _url_from_hyperlink_formula

# Human-auditable columns. `dedupe_key` is the one the filter reads; the rest are
# there so the file can be reviewed and edited by hand (delete a line to un-block).
FIELDNAMES = ["dedupe_key", "source_name", "company", "title", "detail_url"]


def default_blocklist_path() -> Path:
    return default_curated_dir() / "blocklist.csv"


def load_blocklist_keys(path: Path | None = None) -> set[str]:
    """Return the set of blocklisted dedupe keys (empty if file missing/empty)."""
    p = path or default_blocklist_path()
    if not p.is_file() or p.stat().st_size == 0:
        return set()
    keys: set[str] = set()
    with p.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "dedupe_key" not in reader.fieldnames:
            return keys
        for row in reader:
            k = (row.get("dedupe_key") or "").strip()
            if k:
                keys.add(k)
    return keys


def _detail_url_for(row: dict[str, Any]) -> str:
    """Recover a job's detail URL from explicit fields or the HYPERLINK formula."""
    u = (row.get("detail_url") or "").strip() or (row.get("apply_url") or "").strip()
    if not u:
        u = _url_from_hyperlink_formula(str(row.get("detail_hyperlink") or ""))
    return u


def append_to_blocklist(path: Path, rows: list[dict[str, Any]]) -> int:
    """Add rows to the blocklist, skipping blanks and keys already present.

    Idempotent — safe to run repeatedly. Returns the number of new keys added.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_blocklist_keys(path)
    to_write: list[dict[str, Any]] = []
    seen = set(existing)
    for row in rows:
        key = _dedupe_key(row)
        if not key or key in seen:
            continue
        seen.add(key)
        to_write.append(
            {
                "dedupe_key": key,
                "source_name": str(row.get("source_name") or "").strip(),
                "company": str(row.get("company") or "").strip(),
                "title": str(row.get("title") or "").strip(),
                "detail_url": _detail_url_for(row),
            }
        )

    if not to_write:
        return 0

    write_header = not path.is_file() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as f:
        if write_header:
            f.write("\ufeff")
        w = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        if write_header:
            w.writeheader()
        w.writerows(to_write)
    return len(to_write)
