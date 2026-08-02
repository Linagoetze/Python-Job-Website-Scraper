"""Allow `python -m job_scraper` as a shorthand for `python -m job_scraper.run`."""

from job_scraper.run import main

if __name__ == "__main__":
    main()
