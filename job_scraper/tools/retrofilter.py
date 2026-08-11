"""One-off script: re-apply all filters to stored unreviewed jobs and
regenerate jobs.xlsx. Failing rows are marked 'rejected', never deleted.

Usage: python -m job_scraper.tools.retrofilter
"""

from job_scraper.config_loader import (
    default_jobs_db_path,
    default_jobs_xlsx_path,
    default_title_keywords_path,
    load_rules,
)
from job_scraper.filtering import build_hybrid_pattern, load_title_exclude_keywords
from job_scraper.pipeline import refilter_stored_jobs
from job_scraper.storage.db import JobStore
from job_scraper.storage.xlsx_store import write_xlsx


def main() -> None:
    db_path = default_jobs_db_path()
    xlsx_path = default_jobs_xlsx_path()

    rules = load_rules()
    title_keywords = load_title_exclude_keywords(default_title_keywords_path())
    hybrid_pattern = build_hybrid_pattern(rules)

    with JobStore(db_path) as store:
        counts = refilter_stored_jobs(store, rules, title_keywords, hybrid_pattern)

    print(f"Marked {counts['title_keywords']} rows rejected by title keywords")
    print(
        f"Marked {counts['rules']} rows rejected by rules, "
        f"{counts['title']} by seniority, "
        f"{counts['language']} by language speaker pattern"
    )
    print(f"Marked {counts['non_english_text']} rows rejected for non-English title/description")
    print(f"Total rows marked rejected (kept in the database): {sum(counts.values())}")

    write_xlsx(db_path, xlsx_path)
    print("jobs.xlsx regenerated")


if __name__ == "__main__":
    main()
