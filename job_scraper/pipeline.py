"""Run fetch → extract → filter → store."""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from job_scraper import JobRecord
from job_scraper.config_loader import load_rules, load_sources
from job_scraper.drops import (
    LAYER_DETAIL,
    LAYER_REVIEW_STATUS,
    LAYER_RULES,
    LAYER_SENIORITY,
    LAYER_TITLE_KEYWORD,
    RULE_REVIEW_REJECTED,
    exclusion,
    refiltered,
)
from job_scraper.experience_filter import (
    UNVERIFIED_KEY,
    apply_combined_title_filter,
    apply_detail_filter,
)
from job_scraper.extractors.registry import get_extractor
from job_scraper.filtering import (
    _HYBRID_CONFIRMED_REASON,
    _HYBRID_PENDING_REASON,
    _LOCATION_EMPTY_ADMITTED_REASON,
    _UNRESOLVED_PENDING_REASON,
    DROP_RULE_KEY,
    build_hybrid_pattern,
    build_location_pattern,
    build_non_place_pattern,
    load_title_exclude_keywords,
    matches_rules,
)
from job_scraper.http import fetch_rendered, fetch_text
from job_scraper.storage.db import JobStore, dedupe_key_for_job, job_to_row, utc_now_iso

logger = logging.getLogger(__name__)

# Consecutive successful scrapes of a source without sighting a job before it
# is marked delisted. One miss can be a paginated listing hiccup or a posting
# briefly pulled for editing; two runs in a row is a real disappearance.
DEFAULT_DELIST_AFTER = 2

# Runs of exclusions kept in the drop log. A full scrape logs thousands of
# rows, and the question the log answers — "did that rule change help?" — is
# asked against recent runs, not against the whole history.
DEFAULT_KEEP_DROP_RUNS = 10


def _exclusions(jobs: list[JobRecord], layer: str) -> list[dict[str, Any]]:
    """Drop-log rows for jobs a filter excluded, each naming the rule that fired.

    The rule travels on the job dict; a filter that forgot to attach one is
    logged as unattributed rather than silently dropping the row, since a
    missing row is exactly the blindness this log exists to remove.
    """
    return [
        exclusion(job, layer, str(job.get(DROP_RULE_KEY) or "unattributed")) for job in jobs
    ]


@dataclass
class RunSummary:
    sources_total: int
    sources_skipped: int
    sources_processed: int
    jobs_extracted: int
    jobs_kept: int
    jobs_keyword_excluded: int
    jobs_title_excluded: int
    jobs_blocklist_excluded: int
    jobs_already_stored: int
    jobs_new_checked: int
    jobs_stored_rechecked: int
    jobs_detail_excluded: int
    jobs_phd_excluded: int
    jobs_hybrid_excluded: int
    jobs_location_excluded: int
    jobs_kept_new: int
    rows_written: int
    rows_delisted: int
    # Every stored job this run confirmed is still listed, whatever its review
    # status, and how many jobs in the table are still unreviewed. The two
    # differ widely on a normal run, and that gap is not a bug: most of what is
    # still listed has already been reviewed.
    jobs_still_listed: int
    jobs_unreviewed: int
    exclusions_logged: int


def refilter_stored_jobs(
    store: JobStore,
    rules: dict[str, Any],
    title_keywords: list[tuple[str, str]],
    hybrid_pattern: Any = None,
    non_place_pattern: Any = None,
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Re-apply the filter layers to stored unreviewed jobs, marking failures.

    The database replacement for the old CSV clean_existing_rows: when the
    rules or keyword lists change, previously stored rows that no longer pass
    are marked 'rejected' rather than deleted, so nothing is ever lost.

    Only status 'new' rows are re-filtered. 'seen', 'shortlisted' and
    'rejected' are review history — a rule change must not silently rewrite
    what the owner already decided — and 'delisted' rows are not shown anyway.

    Returns (per-filter rejection counts, drop-log rows). The drop-log rows are
    tagged with the `refilter/` layer prefix: a stored row rejected by a
    tightened rule is a real exclusion and belongs in the log, but it is a
    different population from this run's scrape.
    """
    jobs = store.jobs_with_status(("new",))
    counts = {"rules": 0, "title": 0, "title_keywords": 0}
    rejected_keys: list[str] = []
    drops: list[dict[str, Any]] = []

    kept: list[dict[str, Any]] = []
    for job in jobs:
        # A stored conditional-city job re-enters matches_rules without its
        # matched_reasons; the pending reason it gets passes, so the persisted
        # hybrid_confirmed flag is not needed here — Layer 2 owns that check.
        # A stored job with an unresolvable location field passes the same way,
        # and for the same reason: Layer 2 already settled it once, and a
        # re-filter pass has no description to settle it against.
        ok, reasons = matches_rules(
            job, rules, hybrid_pattern, non_place_pattern=non_place_pattern
        )
        if ok:
            kept.append(job)
        else:
            rejected_keys.append(job["dedupe_key"])
            drops.append(exclusion(job, refiltered(LAYER_RULES), reasons[0]))
    counts["rules"] = len(jobs) - len(kept)

    kept, kw_excluded, title_excluded = apply_combined_title_filter(kept, title_keywords, rules)
    counts["title_keywords"] = len(kw_excluded)
    counts["title"] = len(title_excluded)
    rejected_keys += [j["dedupe_key"] for j in kw_excluded + title_excluded]
    drops += _exclusions(kw_excluded, refiltered(LAYER_TITLE_KEYWORD))
    drops += _exclusions(title_excluded, refiltered(LAYER_SENIORITY))

    if rejected_keys:
        store.set_status(rejected_keys, "rejected")
    return counts, drops


def run_pipeline(
    *,
    sources_path: Path,
    rules_path: Path,
    out_db_path: Path,
    title_keywords_path: Path | None = None,
    allow_empty_delist: bool = False,
    delist_after: int = DEFAULT_DELIST_AFTER,
    keep_drop_runs: int = DEFAULT_KEEP_DROP_RUNS,
) -> RunSummary:
    run_started_at = utc_now_iso()
    sources = load_sources(sources_path)
    rules = load_rules(rules_path)
    title_keywords = load_title_exclude_keywords(title_keywords_path) if title_keywords_path else []
    # Compiled once for the whole run and passed down — never rebuilt per job.
    hybrid_pattern = build_hybrid_pattern(rules)
    non_place_pattern = build_non_place_pattern(rules)
    location_pattern = build_location_pattern(rules)

    jobs_extracted = 0
    jobs_kept = 0
    kept_rows: list[JobRecord] = []
    # One entry per exclusion, accumulated as the layers run and written in the
    # store transaction below, so the run's drops commit with its decisions.
    drops: list[dict[str, Any]] = []
    processed_sources: list[dict] = []
    skipped = 0
    processed = 0
    source_scraped_keys: dict[str, set[str]] = {}
    force_delist_sources: set[str] = set()
    # One (name, rows_found, ok, error) entry per *attempted* extraction, for
    # the source_health table. Sources skipped for config reasons (no URL,
    # unknown strategy, no extractor) never reached the site, so they get no row.
    source_health: list[tuple[str, int, bool, str | None]] = []

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
            # Deliberately absent from source_scraped_keys: a failed source is
            # not a successful scrape, so its stored jobs accrue no misses and
            # can never drift towards delisting.
            logger.warning("Skipping source %r: %s", name, exc)
            source_health.append((name, 0, False, str(exc)))
            skipped += 1
            continue
        source_health.append((name, len(rows), True, None))

        # Config-supplied company for single-employer sources. Aggregators
        # (impactpool, jobsinlund) set "company" per job themselves and have
        # no "company" key in sources.yaml, so this only fills the gap the
        # extractor left — the extractor's own value always wins.
        configured_company = str(src.get("company") or "").strip()
        if configured_company:
            for row in rows:
                if not row.get("company"):
                    row["company"] = configured_company

        processed += 1
        processed_sources.append({"source_name": name, "listing_url": url})
        jobs_extracted += len(rows)
        if not rows:
            # A zero-row result is indistinguishable from a broken selector, so
            # it must not count as a successful scrape and start delisting the
            # source's stored jobs. Log loudly and only delist if the owner has
            # explicitly said this source genuinely emptied.
            logger.error(
                "Source %r returned zero rows this run; not delisting its stored jobs "
                "(pass --allow-empty-delist if it has genuinely emptied)",
                name,
            )
            if allow_empty_delist:
                force_delist_sources.add(name)
        else:
            source_scraped_keys[name] = {k for r in rows if (k := dedupe_key_for_job(r))}

        for job in rows:
            ok, reason_list = matches_rules(
                job, rules, hybrid_pattern, non_place_pattern=non_place_pattern
            )
            if not ok:
                # The reason names the specific case — which keyword, or which
                # of the location cases — so a false negative here is findable
                # instead of vanishing into one "off-criteria" total.
                drops.append(exclusion(job, LAYER_RULES, reason_list[0]))
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
    unresolved_admits = sum(
        1 for j in kept_rows if _UNRESOLVED_PENDING_REASON in (j.get("matched_reasons") or [])
    )
    if unresolved_admits:
        # Every one of these is a detail fetch this run would not have made
        # before WP8d, and most will fail closed at Layer 2. Logged so the cost
        # is visible in the run rather than inferred from the drop log.
        logger.debug(
            "Layer 0 (rules): %d jobs admitted with an unresolvable location field, "
            "pending a Layer 2 read of the description",
            unresolved_admits,
        )
    empty_location_admits = sum(
        1
        for j in kept_rows
        if _LOCATION_EMPTY_ADMITTED_REASON in (j.get("matched_reasons") or [])
    )
    if empty_location_admits:
        # Unlike the two counts above, this is not a Layer 2 cost — see
        # _LOCATION_EMPTY_ADMITTED_REASON's comment in filtering.py. Logged
        # anyway, so the volume WP8f admits is as visible as what WP8d defers.
        logger.debug(
            "Layer 0 (rules): %d jobs admitted with no location given at all",
            empty_location_admits,
        )

    sources_csv_path = out_db_path.parent / "jobs_sources.csv"
    # First write of the run — the output directory may not exist yet (fresh
    # clone, or --output-db pointing somewhere new).
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
    drops += _exclusions(keyword_excluded, LAYER_TITLE_KEYWORD)
    drops += _exclusions(title_excluded, LAYER_SENIORITY)
    if jobs_keyword_excluded:
        logger.debug("Layer 1a (title keyword filter): excluded %d jobs", jobs_keyword_excluded)
    if jobs_title_excluded:
        logger.debug("Layer 1 (title filter): excluded %d senior-level jobs", jobs_title_excluded)

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

    # Everything from here on is one store transaction: the run's health rows,
    # upserts, delisting and re-filtering commit together or roll back together,
    # so a crash mid-run cannot leave a half-written run behind.
    with JobStore(out_db_path) as store:
        run_id = store.begin_run(run_started_at)
        for name, rows_found, ok, error in source_health:
            store.record_source_health(run_id, name, rows_found, ok, error)

        stored = store.job_index()

        # Layer 1d — review-status exclusion, replacing the CSV blocklist: a
        # stored job the owner (or a filter change) rejected stays out of the
        # funnel and costs no detail fetch, but its row is never deleted.
        blocked_jobs = [
            j
            for j in kept_rows
            if (k := dedupe_key_for_job(j)) and stored.get(k, {}).get("status") == "rejected"
        ]
        jobs_blocklist_excluded = len(blocked_jobs)
        drops += [
            exclusion(j, LAYER_REVIEW_STATUS, RULE_REVIEW_REJECTED) for j in blocked_jobs
        ]
        if jobs_blocklist_excluded:
            blocked_keys = {dedupe_key_for_job(j) for j in blocked_jobs}
            kept_rows = [j for j in kept_rows if dedupe_key_for_job(j) not in blocked_keys]
            logger.debug(
                "Layer 1d (review status): excluded %d rejected jobs", jobs_blocklist_excluded
            )

        # Layer 2 — detail-page experience extraction.
        # Skip jobs already stored: they passed Layer 2 in a prior run. A
        # conditional-city job whose hybrid arrangement was confirmed from a
        # detail page has that recorded in hybrid_confirmed, so it is skipped
        # like any other stored job; only a stored conditional job that has
        # never been confirmed (e.g. stored before the column existed) is
        # re-fetched, and the confirmation is persisted when it succeeds.
        def _is_stored(job: JobRecord) -> bool:
            return dedupe_key_for_job(job) in stored

        def _is_hybrid_pending(job: JobRecord) -> bool:
            if _HYBRID_PENDING_REASON not in (job.get("matched_reasons") or []):
                return False
            return not stored.get(dedupe_key_for_job(job), {}).get("hybrid_confirmed")

        def _needs_detail(job: JobRecord) -> bool:
            return not _is_stored(job) or _is_hybrid_pending(job)

        # new_jobs is everything that needs a detail-page fetch: genuinely new
        # jobs, plus stored conditional-city jobs whose hybrid confirmation is
        # not yet persisted. The two are reported separately so "new,
        # detail-checked" reconciles against "new rows written".
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
            location_pattern=location_pattern,
        )

        jobs_phd_excluded = sum(
            1 for j in detail_excluded if j.get("experience_level") == "phd_required"
        )
        jobs_hybrid_excluded = sum(
            1
            for j in detail_excluded
            if j.get("experience_level") == "non_hybrid_conditional_location"
        )
        jobs_location_excluded = sum(
            1 for j in detail_excluded if j.get("experience_level") == "unresolvable_location"
        )
        jobs_years_excluded = (
            len(detail_excluded) - jobs_phd_excluded - jobs_hybrid_excluded
            - jobs_location_excluded
        )
        jobs_detail_excluded = len(detail_excluded)
        drops += _exclusions(detail_excluded, LAYER_DETAIL)

        if jobs_detail_excluded:
            logger.debug(
                "Layer 2 (detail filter): excluded %d jobs "
                "(%d requiring 3+ years, %d requiring PhD, %d non-hybrid in a conditional "
                "location, %d whose unresolvable location named no listed place)",
                jobs_detail_excluded,
                jobs_years_excluded,
                jobs_phd_excluded,
                jobs_hybrid_excluded,
                jobs_location_excluded,
            )

        # Store everything sighted and passing: new jobs insert as 'new';
        # stored jobs (including the rejected ones still listed) refresh their
        # descriptive fields and last_seen while keeping status and first_seen.
        def _row(job: JobRecord) -> dict[str, Any] | None:
            confirmed = _HYBRID_CONFIRMED_REASON in (job.get("matched_reasons") or [])
            return job_to_row(dict(job, hybrid_confirmed=1 if confirmed else 0))

        upserts = [r for j in [*kept_new, *cached_jobs, *blocked_jobs] if (r := _row(j))]
        rows_written, refreshed = store.upsert_jobs(upserts, run_id)
        logger.debug("Store: %d rows inserted, %d refreshed", rows_written, refreshed)

        # Jobs Layer 2 excluded also get stored, as 'rejected', description
        # included — otherwise nothing records that they were already judged
        # and the detail page is re-fetched on every subsequent run. Once
        # stored, the existing Layer 1d review-status check picks them up on
        # the next run and Layer 2 never sees them again, the same as any
        # other rejected job.
        #
        # Exception: a job dropped by one of the two fail-closed deferred
        # states — a conditional location's hybrid arrangement, or WP8d's
        # unresolvable location field — that could not actually be verified
        # this run (network hiccup, no URL, no pattern configured — see
        # apply_detail_filter's UNVERIFIED_KEY flag) is excluded for this
        # run only. 'rejected' is permanent by design (nothing automatic ever un-rejects a job,
        # and rejected jobs never appear in jobs.xlsx), so writing one here
        # would let a single transient fetch failure silently and
        # permanently drop a job — exactly what CLAUDE.md's "never lose
        # data" rule forbids. Leaving it unstored reproduces the pre-WP6
        # behaviour for this case: dropped for this run, retried next run.
        detail_rejected_rows = [
            r for j in detail_excluded if not j.get(UNVERIFIED_KEY) and (r := _row(j))
        ]
        if detail_rejected_rows:
            inserted_rejected, refreshed_rejected = store.upsert_jobs(
                detail_rejected_rows, run_id, initial_status="rejected"
            )
            logger.debug(
                "Store: %d Layer 2 rejections recorded as 'rejected', %d refreshed",
                inserted_rejected,
                refreshed_rejected,
            )

        rows_delisted = store.note_misses_and_delist(
            source_scraped_keys, delist_after, force_delist_sources
        )
        if rows_delisted:
            logger.debug(
                "Marked %d jobs delisted (no sighting in %d consecutive successful runs)",
                rows_delisted,
                delist_after,
            )

        # Re-filter stored unreviewed rows against the current rules (the old
        # clean_existing_rows, minus the deletions).
        refilter_counts, refilter_drops = refilter_stored_jobs(
            store, rules, title_keywords, hybrid_pattern, non_place_pattern
        )
        drops += refilter_drops
        for filter_name, count in refilter_counts.items():
            if count:
                logger.debug(
                    "Marked %d stored jobs rejected by the %s filter", count, filter_name
                )

        exclusions_logged = store.record_exclusions(run_id, drops)
        pruned = store.prune_exclusions(keep_drop_runs)
        logger.debug(
            "Drop log: recorded %d exclusions, pruned %d rows older than the last %d runs",
            exclusions_logged,
            pruned,
            keep_drop_runs,
        )

        # After the re-filter pass, which can move a stored row out of 'new'.
        jobs_still_listed = store.count_sighted_in_run(run_id)
        jobs_unreviewed = store.count_with_status(("new",))

        store.finish_run(run_id)

    return RunSummary(
        sources_total=len(sources),
        sources_skipped=skipped,
        sources_processed=processed,
        jobs_extracted=jobs_extracted,
        jobs_kept=jobs_kept,
        jobs_keyword_excluded=jobs_keyword_excluded,
        jobs_title_excluded=jobs_title_excluded,
        jobs_blocklist_excluded=jobs_blocklist_excluded,
        jobs_already_stored=len(cached_jobs),
        jobs_new_checked=len(truly_new_jobs),
        jobs_stored_rechecked=len(stored_rechecked_jobs),
        jobs_detail_excluded=jobs_detail_excluded,
        jobs_phd_excluded=jobs_phd_excluded,
        jobs_hybrid_excluded=jobs_hybrid_excluded,
        jobs_location_excluded=jobs_location_excluded,
        jobs_kept_new=len(kept_new),
        rows_written=rows_written,
        rows_delisted=rows_delisted,
        jobs_still_listed=jobs_still_listed,
        jobs_unreviewed=jobs_unreviewed,
        exclusions_logged=exclusions_logged,
    )
