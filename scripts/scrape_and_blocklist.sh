#!/bin/bash
# Step one automation: scrape configured career pages, then move every job the
# scrape produced into the permanent blocklist so none are surfaced again.
#
# Safe to run repeatedly: append_to_blocklist is idempotent, and the blocklist
# step is skipped when the scrape produced no jobs.
set -euo pipefail

# Run from the project root, one level up from this script.
cd "$(dirname "$0")/.."

# Prefer the project venv — it's the only interpreter with playwright/langdetect.
PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') scrape_and_blocklist starting ==="

# 1. Scrape (fills data/jobs.csv with any new, non-blocklisted jobs)
"$PY" -m job_scraper.run

# 2. Blocklist everything the scrape kept — but only if there's anything to move.
rows=$("$PY" -c "import csv; from job_scraper.config_loader import default_jobs_csv_path; print(sum(1 for _ in csv.DictReader(default_jobs_csv_path().open(encoding='utf-8-sig'))))")
if [ "$rows" -gt 0 ]; then
    "$PY" -m job_scraper.tools.blocklist_all
else
    echo "No new jobs to blocklist."
fi

echo "=== done ==="
