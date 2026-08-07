"""Run fetch → extract → filter → store."""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from job_scraper import JobRecord

from job_scraper.blocklist import load_blocklist_keys
from job_scraper.config_loader import load_rules, load_sources
from job_scraper.experience_filter import apply_combined_title_filter, apply_detail_filter
from job_scraper.filtering import (
    _HYBRID_PENDING_REASON,
    apply_language_filter,
    apply_non_english_text_filter,
    build_hybrid_pattern,
    load_title_exclude_keywords,
    matches_rules,
)
from job_scraper.http import fetch_rendered, fetch_text
from job_scraper.extractors.registry import get_extractor
from job_scraper.storage.csv_store import (
    _dedupe_key,
    _read_existing_keys,
    append_jobs_csv,
    clean_existing_rows,
    sort_jobs_csv,
)

logger = logging.getLogger(__name__)


@dataclass
class RunSummary:
    sources_total: int
    sources_skipped: int
    sources_processed: int
    jobs_extracted: int
    jobs_kept: int
    jobs_keyword_excluded: int
    jobs_language_excluded: int
    jobs_non_english_excluded: int
    jobs_title_excluded: int
    jobs_blocklist_excluded: int
    jobs_already_stored: int
    jobs_new_checked: int
    jobs_stored_rechecked: int
    jobs_detail_excluded: int
    jobs_phd_excluded: int
    jobs_hybrid_excluded: int
    jobs_kept_new: int
    rows_written: int
    rows_delisted: int


def run_pipeline(
    *,
    sources_path: Path,
    rules_path: Path,
    out_csv_path: Path,
    title_keywords_path: Path | None = None,
    allow_empty_delist: bool = False,
) -> RunSummary:
    sources = load_sources(sources_path)
    rules = load_rules(rules_path)
    title_keywords = load_title_exclude_keywords(title_keywords_path) if title_keywords_path else []
    # Compiled once for the whole run and passed down — never rebuilt per job.
    hybrid_pattern = build_hybrid_pattern(rules)

    jobs_extracted = 0
    jobs_kept = 0
    kept_rows: list[JobRecord] = []
    processed_sources: list[dict] = []
    skipped = 0
    processed = 0
    source_scraped_keys: dict[str, set[str]] = {}

    for src in sources:
        name = str(src.get("name") or "").strip()
        url = str(src.get("url") or "").strip()
        strategy = str(src.get("strategy") or "").strip().lower()

        if not name or not url:
            logger.warning("Skipping invalid source entry: %s", src)
            skipped += 1
            continue

        if strategy == "static":
            fetch_fn = fetch_text
        elif strategy == "dynamic":
            fetch_fn = fetch_rendered
        else:
            logger.info("Skipping source %r: unknown strategy %r", name, strategy)
            skipped += 1
            continue

        extractor = get_extractor(name)
        if extractor is None:
            logger.info("Skipping source %r: no extractor registered", name)
            skipped += 1
            continue

        logger.info("Extracting %r from %s", name, url)
        try:
            rows = extractor(url, fetch_fn)
        except Exception as exc:
            # Deliberately absent from source_scraped_keys: a failed source must
            # not be treated as "scraped, found nothing" or its stored rows
            # would be delisted by clean_existing_rows.
            logger.warning("Skipping source %r: %s", name, exc)
            skipped += 1
            continue
        processed += 1
        processed_sources.append({"source_name": name, "listing_url": url})
        jobs_extracted += len(rows)
        if not rows:
            # A zero-row result is indistinguishable from a broken selector, so
            # it must not silently delist everything already stored for this
            # source (see clean_existing_rows). Log loudly and only delist if
            # the owner has explicitly said this source genuinely emptied.
            logger.error(
                "Source %r returned zero rows this run; not delisting its stored jobs "
                "(pass --allow-empty-delist if it has genuinely emptied)",
                name,
            )
            if allow_empty_delist:
                source_scraped_keys[name] = set()
        else:
            source_scraped_keys[name] = {k for r in rows if (k := _dedupe_key(r))}

        for job in rows:
            ok, reason_list = matches_rules(job, rules, hybrid_pattern)
            if not ok:
                continue
            job = dict(job)
            job["matched_reasons"] = reason_list
            kept_rows.append(job)
            jobs_kept += 1

    conditional_admits = sum(
        1
        for j in kept_rows
        if any(r.startswith("locations: conditional") for r in j.get("matched_reasons") or [])
    )
    if conditional_admits:
        logger.debug(
            "Layer 0 (rules): %d jobs admitted from a conditional location, pending hybrid check",
            conditional_admits,
        )

    sources_csv_path = out_csv_path.parent / "jobs_sources.csv"
    # First write of the run — the output directory may not exist yet (fresh
    # clone, or --output pointing somewhere new).
    sources_csv_path.parent.mkdir(parents=True, exist_ok=True)
    with sources_csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["source_name", "listing_url"])
        writer.writeheader()
        writer.writerows(processed_sources)
    logger.info("Wrote %d sources to %s", len(processed_sources), sources_csv_path)

    # Layer 1a + Layer 1 — combined title pass (keyword exclusions and seniority)
    kept_rows, keyword_excluded, title_excluded = apply_combined_title_filter(
        kept_rows, title_keywords, rules
    )
    jobs_keyword_excluded = len(keyword_excluded)
    jobs_title_excluded = len(title_excluded)
    if jobs_keyword_excluded:
        logger.debug("Layer 1a (title keyword filter): excluded %d jobs", jobs_keyword_excluded)
    if jobs_title_excluded:
        logger.debug("Layer 1 (title filter): excluded %d senior-level jobs", jobs_title_excluded)

    # Layer 1c — non-English text filter (runs before 1b to drop non-English first)
    kept_rows, non_english_excluded = apply_non_english_text_filter(kept_rows)
    jobs_non_english_excluded = len(non_english_excluded)
    if jobs_non_english_excluded:
        logger.debug("Layer 1c (non-English text filter): excluded %d jobs", jobs_non_english_excluded)

    # Layer 1b — language filter ("[Language] Speaker/speaking" in title)
    kept_rows, language_excluded = apply_language_filter(kept_rows)
    jobs_language_excluded = len(language_excluded)
    if jobs_language_excluded:
        logger.debug("Layer 1b (language filter): excluded %d jobs", jobs_language_excluded)

    # Layer 1d — blocklist exclusion (jobs the user permanently rejected).
    # Runs before the Layer 2 detail fetch so blocklisted jobs cost no HTTP requests.
    blocklist_keys = load_blocklist_keys()
    if blocklist_keys:
        before = len(kept_rows)
        kept_rows = [j for j in kept_rows if _dedupe_key(j) not in blocklist_keys]
        jobs_blocklist_excluded = before - len(kept_rows)
    else:
        jobs_blocklist_excluded = 0
    if jobs_blocklist_excluded:
        logger.debug("Layer 1d (blocklist filter): excluded %d jobs", jobs_blocklist_excluded)

    # Build source_name → fetch_fn map so dynamic sources use fetch_rendered
    source_fetch_map: dict[str, Any] = {
        str(src.get("name") or "").strip(): (
            fetch_rendered
            if str(src.get("strategy") or "").strip().lower() == "dynamic"
            else fetch_text
        )
        for src in sources
        if str(src.get("name") or "").strip()
    }

    # Layer 2 — detail-page experience extraction
    # Skip jobs already stored in jobs.csv: they passed Layer 2 in a prior run.
    # A job awaiting a hybrid decision must never take the cache shortcut: only
    # the detail fetch can settle it, and matched_reasons is not a jobs.csv column,
    # so a stored conditional-city job comes back PENDING every run and has to
    # re-earn its confirmation. Bounded cost — it is only the conditional cities,
    # and only those that do not say "hybrid" in the title.
    existing_keys = _read_existing_keys(out_csv_path)

    def _is_stored(job: JobRecord) -> bool:
        return _dedupe_key(job) in existing_keys

    def _is_hybrid_pending(job: JobRecord) -> bool:
        return _HYBRID_PENDING_REASON in (job.get("matched_reasons") or [])

    def _needs_detail(job: JobRecord) -> bool:
        return not _is_stored(job) or _is_hybrid_pending(job)

    # new_jobs is everything that needs a detail-page fetch: genuinely new jobs,
    # plus already-stored conditional-city jobs re-earning their hybrid
    # confirmation (see the module docstring above). The two are reported
    # separately below so "new, detail-checked" reconciles against "new rows
    # written" — a re-check is not a new row.
    new_jobs = [j for j in kept_rows if _needs_detail(j)]
    cached_jobs = [j for j in kept_rows if not _needs_detail(j)]
    truly_new_jobs = [j for j in new_jobs if not _is_stored(j)]
    stored_rechecked_jobs = [j for j in new_jobs if _is_stored(j)]
    if cached_jobs:
        logger.debug("Layer 2: skipped %d already-stored jobs", len(cached_jobs))

    logger.debug("Layer 2 (detail filter): fetching detail pages for %d jobs…", len(new_jobs))
    kept_new, detail_excluded = apply_detail_filter(
        new_jobs,
        fetch_text,
        source_fetch_map=source_fetch_map,
        hybrid_pattern=hybrid_pattern,
    )

    jobs_phd_excluded = sum(1 for j in detail_excluded if j.get("experience_level") == "phd_required")
    jobs_hybrid_excluded = sum(
        1
        for j in detail_excluded
        if j.get("experience_level") == "non_hybrid_conditional_location"
    )
    jobs_years_excluded = len(detail_excluded) - jobs_phd_excluded - jobs_hybrid_excluded
    jobs_detail_excluded = len(detail_excluded)

    if jobs_detail_excluded:
        logger.debug(
            "Layer 2 (detail filter): excluded %d jobs "
            "(%d requiring 3+ years, %d requiring PhD, %d non-hybrid in a conditional location)",
            jobs_detail_excluded,
            jobs_years_excluded,
            jobs_phd_excluded,
            jobs_hybrid_excluded,
        )

    kept_rows = kept_new + [dict(j, experience_level="cached") for j in cached_jobs]

    rows_written = append_jobs_csv(out_csv_path, kept_rows)

    # Clean pre-existing rows that no longer pass current filters (single pass)
    cleaned = clean_existing_rows(
        out_csv_path, rules, title_keywords, source_scraped_keys, blocklist_keys=blocklist_keys
    )
    if cleaned["blocklist"]:
        logger.debug("Removed %d pre-existing blocklisted rows", cleaned["blocklist"])
    if cleaned["rules"]:
        logger.debug("Removed %d pre-existing rows that failed location/keyword rules", cleaned["rules"])
    if cleaned["title"]:
        logger.debug("Removed %d pre-existing senior jobs from %s", cleaned["title"], out_csv_path)
    if cleaned["title_keywords"]:
        logger.debug("Removed %d pre-existing jobs matching title keywords", cleaned["title_keywords"])
    if cleaned.get("non_english_text"):
        logger.debug("Removed %d pre-existing jobs with non-English text", cleaned["non_english_text"])
    if cleaned["language"]:
        logger.debug("Removed %d pre-existing jobs with language-speaker titles", cleaned["language"])
    if cleaned["mammut_fixed"]:
        logger.debug("Fixed location field for %d pre-existing Mammut rows", cleaned["mammut_fixed"])
    if cleaned["delisted"]:
        logger.debug("Removed %d pre-existing rows no longer listed on their source page", cleaned["delisted"])

    sort_jobs_csv(out_csv_path)

    return RunSummary(
        sources_total=len(sources),
        sources_skipped=skipped,
        sources_processed=processed,
        jobs_extracted=jobs_extracted,
        jobs_kept=jobs_kept,
        jobs_keyword_excluded=jobs_keyword_excluded,
        jobs_language_excluded=jobs_language_excluded,
        jobs_non_english_excluded=jobs_non_english_excluded,
        jobs_title_excluded=jobs_title_excluded,
        jobs_blocklist_excluded=jobs_blocklist_excluded,
        jobs_already_stored=len(cached_jobs),
        jobs_new_checked=len(truly_new_jobs),
        jobs_stored_rechecked=len(stored_rechecked_jobs),
        jobs_detail_excluded=jobs_detail_excluded,
        jobs_phd_excluded=jobs_phd_excluded,
        jobs_hybrid_excluded=jobs_hybrid_excluded,
        jobs_kept_new=len(kept_new),
        rows_written=rows_written,
        rows_delisted=cleaned["delisted"],
    )
