"""Mark every unreviewed job as seen, then regenerate jobs.xlsx.

The post-run half of the scrape-and-blocklist routine: after the owner has
looked at the spreadsheet, this flips every 'new' job to 'seen' so the next
run's table shows only jobs stored after this point. Nothing is deleted —
the rows keep their history in the database.

Safe to run repeatedly, including when there is nothing new.

Usage: python -m job_scraper.tools.blocklist_all
"""

from job_scraper.blocklist import mark_all_new_seen
from job_scraper.config_loader import default_jobs_db_path, default_jobs_xlsx_path
from job_scraper.storage.xlsx_store import write_xlsx


def main() -> None:
    db_path = default_jobs_db_path()
    xlsx_path = default_jobs_xlsx_path()

    marked = mark_all_new_seen(db_path)
    table_total = write_xlsx(db_path, xlsx_path)

    print(f"Marked {marked} jobs as seen in {db_path} (rows kept, nothing deleted)")
    print(f"jobs.xlsx regenerated ({table_total} unreviewed jobs shown)")


if __name__ == "__main__":
    main()
