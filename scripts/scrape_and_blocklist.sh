#!/bin/bash
# Step one automation: scrape configured career pages, then mark every job the
# scrape surfaced as seen so the next run's spreadsheet shows only new ones.
#
# Since WP5 nothing is deleted: "blocklisting" is now a status flip in the
# SQLite store, and blocklist_all is a safe no-op when there is nothing new.
set -euo pipefail

# Run from the project root, one level up from this script.
cd "$(dirname "$0")/.."

# Prefer the project venv — it's the only interpreter with playwright/langdetect.
PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') scrape_and_blocklist starting ==="

# 1. Scrape (stores any new, non-rejected jobs and regenerates jobs.xlsx)
"$PY" -m job_scraper.run

# 2. Mark everything surfaced as seen.
"$PY" -m job_scraper.tools.blocklist_all

echo "=== done ==="
