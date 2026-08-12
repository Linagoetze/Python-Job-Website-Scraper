#!/bin/bash
# DEPRECATED (WP5b). Replaced by:
#
#     python -m job_scraper.run                    # scrape, write jobs.xlsx
#     python -m job_scraper.review --seen-all      # after you have read it
#
# The point of this script was that marking everything seen straight after the
# scrape kept the next run's table short. It did that by deciding, on your
# behalf and before you had looked, that every job was dealt with — so a run
# you never opened was indistinguishable from one you had reviewed.
#
# The store now records first_seen/last_seen per job, so the spreadsheet can
# show you what is unreviewed without anything being marked in advance. Run
# `review --seen-all` when you have actually read it, or `review --shortlist N`
# / `--reject N` to record a decision on a single row.
#
# Kept, and still working, until the new flow is confirmed. Nothing has been
# deleted.
set -euo pipefail

# Run from the project root, one level up from this script.
cd "$(dirname "$0")/.."

# Prefer the project venv — it's the only interpreter with playwright/langdetect.
PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') scrape_and_blocklist starting ==="
echo "NOTE: this script is deprecated. It marks every job seen before you have"
echo "      read it. Use 'python -m job_scraper.run', then"
echo "      'python -m job_scraper.review --seen-all' once you have."

# 1. Scrape (stores any new, non-rejected jobs and regenerates jobs.xlsx)
"$PY" -m job_scraper.run

# 2. Mark everything surfaced as seen.
"$PY" -m job_scraper.tools.blocklist_all

echo "=== done ==="
