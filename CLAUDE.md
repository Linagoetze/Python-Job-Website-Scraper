# CLAUDE.md

Persistent instructions for Claude Code working in this repository.
Read this file, `docs/DECISIONS.md` and `docs/SOURCES-PLAN.md` at the start of
every session. `docs/REFACTOR-PLAN.md` is an archive of the finished refactor —
consult it on demand (grep for a package name), not at session start.

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
  curated.py          The two curated source lists (tombstone, candidates)
  extractors/         One module per ATS or site, registry.py maps names
  storage/            SQLite store (db.py, internal) and xlsx store (presentation)
  tools/              Maintenance commands, incl. sources.py (the curated lists)
```

Data flow: `sources.yaml` -> extractor -> `JobRecord` dicts -> filter layers ->
SQLite store -> `data/jobs.xlsx` (an export of the store, not the store itself).

## Design principles

These exist because the codebase has drifted in specific ways. Respect them.

- **Redesign, do not patch.** This project grew one session at a time, and two
  areas show it: the filter ladder in `pipeline.py`, and
  `config/title_exclude_keywords.csv`. When asked to change either of these,
  read the whole module and consider whether the structure still fits before
  adding to it. (A third area, `storage/csv_store.py`, was one of these until
  it was deleted outright rather than patched — see `docs/DECISIONS.md`.)
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
- **Never touch** `.venv/`, `.git.backup/`, `data/*.csv`, `data/*.xlsx`, or the
  gitignored `sources.yaml` / `rules.json`. These hold real personal data and
  local state.
- **Never run `git clean -xfd`.** Everything irreplaceable in this repository —
  the store, `data/curated/`, the HTTP cache — is gitignored by design, and
  `-x` targets exactly that: a dry run on 2026-09-10 confirmed it would remove
  all of it with no git history behind any of it. `git clean` without `-x` is
  fine.
- **Keep the public repo clean.** Every private file needs a tracked `.example`
  twin; the real one is gitignored. Ignore rules for a directory of private
  files must deny by default (`*` plus explicit `!*.example.*`) rather than
  enumerate files one at a time — a per-file list has already let a sidecar
  slip through twice. Stage named paths; `git add -A` is the wrong instinct
  here. Full reasoning: [docs/REFACTOR-PLAN.md#keeping-the-public-repo-clean](docs/REFACTOR-PLAN.md#keeping-the-public-repo-clean).
- **Real place names in `docs/REFACTOR-PLAN.md` and in the tests are fine.**
  This is a considered decision, not an oversight: the employers followed are
  already public via the extractor registry, and the test suite is built from
  real cities. Do not launder them out on your own initiative, and do not
  assume the same latitude extends to `docs/SOURCES-PLAN.md` — that file
  deliberately keeps candidate and excluded company names out of itself; see
  its own "Publishing this file" section. What stays private everywhere:
  `sources.yaml`, `rules.json`, the store, the labelled set, the blocklist, and
  the contact details. Full reasoning:
  [docs/REFACTOR-PLAN.md#place-names-in-this-file-and-in-the-tests](docs/REFACTOR-PLAN.md#place-names-in-this-file-and-in-the-tests).
- **`data/curated/` is written only through `job_scraper/tools/sources.py`.**
  Never hand-edit a file there, never rewrite one wholesale, and never open one
  in a spreadsheet application. The tool appends one record at a time, writes
  via a temp file and `os.replace()`, and leaves a timestamped backup beside the
  file. Four owner-approved commands go beyond appending, each as narrowly as
  its case — `candidate promote` moves a candidate to the tombstone,
  `record-check` fills empty fields only, `recheck` replaces a finding after
  writing the old one into `source_of_record`, and `activate` removes a
  candidate only when its board is in `sources.yaml` — see `docs/DECISIONS.md`.
  Every other operation on that directory — deleting, reordering, editing an
  existing entry, changing a schema — belongs to the owner and must be asked
  for first. This is narrower than it looks: the two files this project has lost
  were both lost by a person with the file open, so an append-only writer with a
  backup is the safer path rather than the looser one. The tool exists as of
  SP1 (`python -m job_scraper.tools.sources`); it also commits each write to
  the private git repository inside `data/curated/`, if the owner has created
  one. **`scripts/migrate_curated_to_yaml.py` is the owner's to run, not a
  session's** — it is the one thing that writes two whole files at once.
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
- [ ] `ruff check .` passes.
- [ ] `ruff format --check .` passes.
- [ ] `python -m job_scraper.run --help` still works.
- [ ] The plan file the package belongs to (`docs/SOURCES-PLAN.md` for an SP
      package) updated: package marked done, with a result. New decisions
      recorded in `docs/DECISIONS.md`, not in a plan file.
- [ ] README and the run summary reflect any user-visible change; anything a
      later session would otherwise re-derive is in `docs/DECISIONS.md`. Seven
      prompts can forget; a checklist item does not.
- [ ] Changes committed on the package branch, not pushed.
