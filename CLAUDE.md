# CLAUDE.md

Persistent instructions for Claude Code working in this repository.
Read this file and `docs/REFACTOR-PLAN.md` at the start of every session.

## What this project is

A personal job scraper. It fetches configured career pages, extracts postings,
filters them against local rules, and writes a reviewable spreadsheet. It is run
manually or on a schedule by one person, on one Mac. It is not a service and has
no users other than the owner.

Priorities, in order:

1. **Never lose data.** A stored job that silently disappears is worse than a
   scrape that fails loudly.
2. **Fail loudly.** A broken extractor must produce an error, not an empty list
   that looks like "no vacancies".
3. **Be a good citizen.** These are other people's career sites. Rate limits,
   honest user agent, no hammering.
4. **Then** speed and elegance.

## Architecture

```
job_scraper/
  run.py              CLI entry point, summary rendering
  pipeline.py         Orchestration: fetch -> extract -> filter -> store
  http.py             requests + Playwright fetchers
  config_loader.py    Path defaults, YAML/JSON loading
  filtering.py        Rules, location, title keywords
  experience_filter.py Layer 3 (title) and Layer 5 (detail page)
  urlutil.py          URL normalisation and dedupe keys
  blocklist.py        Permanently rejected postings
  extractors/         One module per ATS or site, registry.py maps names
  storage/            CSV store (internal) and xlsx store (presentation)
```

Data flow: `sources.yaml` -> extractor -> `JobRecord` dicts -> filter layers ->
store -> `data/jobs.xlsx`.

## Design principles

These exist because the codebase has drifted in specific ways. Respect them.

- **Redesign, do not patch.** This project grew one session at a time, and three
  areas show it: `storage/csv_store.py`, the filter ladder in `pipeline.py`, and
  `config/title_exclude_keywords.csv`. When asked to change any of these, read
  the whole module and consider whether the structure still fits before adding
  to it.
- **No new filter layers without asking.** There are already five. Adding a
  sixth regex pass is almost always the wrong answer. Say so and propose an
  alternative.
- **One canonical representation.** The internal store holds plain data. Excel
  `=HYPERLINK()` formulas and other presentation concerns belong only in
  `storage/xlsx_store.py`.
- **Compile once.** Regexes and patterns are built at setup and passed down,
  never rebuilt per job inside a loop.
- **Writes are atomic.** Write to a temp file, then `os.replace()`. Never open
  the live data file with mode `"w"`.
- **Config over code.** New sources should need a `sources.yaml` entry and at
  most a registry line, not a new bespoke module, unless the site genuinely
  demands one.

## Working rules

- **Scope discipline.** Do the work package you were given. If you find an
  unrelated bug, note it at the end of your response rather than fixing it.
- **Tests before refactors.** If a package changes behaviour in a module with no
  test coverage, write the characterisation test first.
- **Ask before adding dependencies.** State what you want and why, and wait.
- **Never touch** `.venv/`, `.git.backup/`, `data/*.csv`, `data/*.xlsx`,
  `data/curated/`, or the gitignored `sources.yaml` / `rules.json`. These hold
  real personal data and local state.
- **Never invent test fixtures from live sites** by scraping during a session.
  Use the saved fixtures in `tests/fixtures/`.

## Code conventions

- Python 3.13, `from __future__ import annotations`, full type hints.
- British English in comments, docstrings, and log messages.
- Comments explain *why*, not *what*. The existing codebase does this well;
  match it.
- Prefer small pure functions that take data and return data. Side effects
  (HTTP, file writes) live at the edges.
- `logging`, never `print`, except in `run.py`'s user-facing summary.

## Git workflow

- Work on a branch named for the package, e.g. `wp1-atomic-writes`.
- Commit locally in logical chunks using Conventional Commits
  (`fix:`, `feat:`, `refactor:`, `test:`, `chore:`, `docs:`).
- **Do not run `git push`.** Do not create pull requests. The owner reviews the
  diff and pushes manually. This is not negotiable.
- Do not amend or rebase commits that already exist on `main`.

## Definition of done for any package

- [ ] `pytest` passes.
- [ ] `ruff check .` passes (once WP2 has added it).
- [ ] `python -m job_scraper.run --help` still works.
- [ ] `docs/REFACTOR-PLAN.md` updated: package marked done, decisions recorded.
- [ ] Changes committed on the package branch, not pushed.
