"""One-off script: re-apply all filters to the existing jobs.csv and regenerate jobs.xlsx.

Usage: python -m job_scraper.tools.retrofilter
"""

from job_scraper.config_loader import (
    default_jobs_csv_path,
    default_jobs_xlsx_path,
    default_title_keywords_path,
    load_rules,
)
from job_scraper.filtering import load_title_exclude_keywords
from job_scraper.storage.csv_store import clean_existing_rows
from job_scraper.storage.xlsx_store import write_xlsx


def main() -> None:
    csv_path = default_jobs_csv_path()
    xlsx_path = default_jobs_xlsx_path()

    rules = load_rules()
    title_keywords = load_title_exclude_keywords(default_title_keywords_path())

    counts = clean_existing_rows(csv_path, rules, title_keywords)

    # `mammut_fixed` counts repaired location fields, not removals, so it must
    # stay out of the total.
    total = sum(v for k, v in counts.items() if k != "mammut_fixed")
    print(f"Removed {counts['title_keywords']} rows matched by title keywords")
    print(
        f"Removed {counts['rules']} rows by rules, "
        f"{counts['title']} by seniority, "
        f"{counts['language']} by language speaker pattern"
    )
    print(f"Removed {counts['non_english_text']} rows with non-English title/description")
    print(f"Total rows removed: {total}")

    write_xlsx(csv_path, xlsx_path)
    print("jobs.xlsx regenerated")


if __name__ == "__main__":
    main()
