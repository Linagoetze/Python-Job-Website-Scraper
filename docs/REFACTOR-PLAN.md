# Refactor plan

Living document. Every Claude Code session reads this first and updates it last.
Lives at `docs/REFACTOR-PLAN.md`.

## The big picture

This codebase grew one Claude Code session at a time. Each session solved its
immediate problem correctly without asking whether the surrounding structure
still fitted. The result is accretion in three places:

1. **`storage/csv_store.py`** (389 lines): six full read-parse-rewrite passes of
   the same file per run, a schema migration function, and Excel formulas stored
   in the internal data file so that three modules must parse spreadsheet syntax
   to recover a URL.
2. **The filter ladder**: five sequential regex passes, each added to fix a
   specific false positive, interacting in ways nothing tests.
3. **`config/title_exclude_keywords.csv`**: grows every time a bad match slips
   through. Keyword lists do not generalise.

The plan below fixes the data-loss risks first, builds a test net, then removes
the accretion. It is ordered so that each package is safe to stop after.

**Guiding decisions:**

- Replace hand-rolled CSV state with SQLite.
- Replace the growing keyword ladder with an LLM scoring stage.
- Keep the extractor layer and the registry as they are. They are the strongest
  part of the codebase.
- Preserve the owner's review history. The existing 265-row blocklist means
  "already seen", not "rejected", and must not be imported as rejections.

## Status

| WP | Title | Time | Model | Effort cue | Status | Branch |
|----|-------|------|-------|-----------|--------|--------|
| 0 | Fixture capture script | 1 hr | Sonnet 5 | none | done | `wp0-fixtures` |
| 0c | Sanitise fixtures | 1 hr | Opus 5 | `think hard` | done | `wp0c-sanitise-fixtures` |
| 1 | Atomic writes and delisting guard | 1.5 hr | Sonnet 5 | `think` | done | `wp1-data-safety` |
| 2 | Test net and tooling | 3-4 hr | Opus 5 | `think` | done | `wp2-test-net` |
| 3 | Company field from config | 1 hr | Sonnet 5 | none | done | `wp3-company-field` |
| 4 | SQLite, part 1: schema and dual write | 2.5 hr | Fable 5 | `think hard` | done | `wp4-sqlite-schema` |
| 5 | SQLite, part 2: cut over, delete CSV store | 4 hr | Fable 5 | `ultrathink` | done | `wp5-sqlite-cutover` |
| 5b | Replace the blocklist-everything routine | 2 hr | Opus 5 | `think hard` | done | `wp5b-review-workflow` |
| 5c | Review ranges and `--reject-all` | 0.5 hr | Fable 5 | none | done | `wp5c-review-ranges` |
| 6 | Persist detail descriptions | 1.5 hr | Sonnet 5 | `think` | done | `wp6-persist-descriptions` |
| 7 | LLM scoring stage | 3.5 hr | Fable 5 | `think hard` | not started | `wp7-llm-scoring` |
| 8 | Retire the keyword ladder | 2.5 hr | Opus 5 | `think hard` | not started | `wp8-retire-ladder` |
| 9 | Playwright reuse and HTTP caching | 3 hr | Fable 5 | `think hard` | not started | `wp9-fetch-performance` |
| 10 | Politeness and observability | 1.5 hr | Sonnet 5 | `think` | not started | `wp10-politeness` |

Total roughly 27 hours. One package per week is about three months. Two evenings
a week is six or seven weeks. Nothing breaks if you stop after any package.

## Decisions log

Record any decision a future session would otherwise have to re-derive.

- Conditional hybrid-gated locations (cities too far to commute to daily,
  admitted only when the role is hybrid — `filtering.py`'s
  `build_hybrid_pattern`/`matches_rules`, `experience_filter.py`'s
  `_resolve_hybrid`) are a deliberate feature the owner wrote before this plan
  existed, not accidental scope creep. Keep it; do not propose deleting it as
  part of the filter-ladder cleanup in WP8.
- **Statuses after WP5.** `'rejected'` covers both "a filter now excludes this
  stored row" and (from WP5b) "the owner said no" — one status, deliberately,
  to stay inside the WP4 vocabulary. Consequence: nothing automatic ever flips
  `'rejected'` back, so loosening a rule does not resurrect rows it previously
  rejected (they are still in the table for manual revival). The automated
  re-filter pass (`pipeline.refilter_stored_jobs`) touches **only `'new'`
  rows** — `'seen'` is review history and must not be silently rewritten to
  `'rejected'`, or the seen-vs-rejected distinction the blocklist import
  preserves would erode run by run.
- **Delisting after WP5** is `jobs.misses`: reset on sighting, incremented per
  successful scrape of the source without a sighting, flipped to `'delisted'`
  at N consecutive misses (default 2, `--delist-after`). Only `'new'`/`'seen'`
  flip; shortlisted/rejected keep their status while misses accrue.
  `--allow-empty-delist` now means "this source genuinely emptied — delist its
  unreviewed jobs *now*", bypassing the threshold; without it a zero-row
  scrape still counts for nothing at all.

---

# How to run one session

Every package follows the same eight steps. Do not run two packages in one
session: the context cost of the second is what causes Claude Code to start
patching rather than thinking.

**1. Start clean.**

```
cd ~/path/to/job_scraper_project
git checkout main
git pull
git checkout -b wp1-data-safety
```

**2. Open a fresh Claude Code session** in the project folder.

**3. Set the model.** Type `/model` and choose the one in the table above.

**4. Paste the package prompt.** The effort cue is already the first line of
each prompt below, so pasting the whole block is enough.

**5. Ask it to account for itself.** When it finishes, paste:

```
Summarise what you changed, file by file. For each file say what it does now
that it did not before. Then list anything you changed that was NOT asked for in
the work package, and anything in the package you did not do. Be honest about
both.
```

**6. Check the work.**

```
git diff main --stat
.venv/bin/pytest
.venv/bin/python -m job_scraper.run
```

The first prints changed lines per file: sanity-check the size, not the code.
The second must pass. The third must produce a funnel summary in your normal
range. If tests fail, paste the output back into the same session.

**7. Get an adversarial second opinion.** Open a **new** session, switch to
Fable 5, paste:

```
Read CLAUDE.md and docs/REFACTOR-PLAN.md.

Another session just implemented work package N on the current branch. I cannot
read code, so I need you to review it for me. Run `git diff main` and check:

- Does it do what the package asked, and only that?
- Are there bugs, or cases the code does not handle?
- Does it violate any principle in CLAUDE.md?
- Is anything half-finished, stubbed, or commented out?
- Did it weaken or delete any test to make things pass?

Do not fix anything. Report in plain English, assuming I am not a programmer.
```

**8. Push and merge.**

```
git status
git push -u origin wp1-data-safety
```

`git status` must say "working tree clean". Then open the repo on github.com,
click through the yellow banner to open a pull request, and merge it. Back in
Terminal:

```
git checkout main
git pull
```

Before pushing, skim this plan file for real city or company names that may have
crept into the decisions log. The repo is public.

---

# Session prompts

---

## WP0 — Fixture capture script

```
Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP0 only.

WP2 needs saved copies of real career-page responses so the extractor tests have
something to run against without hitting live sites. I do not want to capture
these by hand.

Write a script scripts/capture_fixtures.py that:
- Reads job_scraper/config/sources.yaml.
- Takes a list of source names as command-line arguments.
- For each one, fetches its listing URL using the project's own fetchers, so
  static sources use fetch_text and dynamic sources use fetch_rendered.
- Saves the raw response to tests/fixtures/<source_name>.html or .json,
  choosing the extension based on the content.
- Prints a clear one-line result per source, including the byte size, so I can
  see at a glance whether anything came back empty.
- Is polite: one source at a time, with a short pause between them.

Then run it for these six sources and show me the output:
givewell, kognity, storytel, busuu, dsv, impactpool

Add a section to docs/REFACTOR-PLAN.md under WP0 explaining how to re-run the
script when a fixture goes stale.

Branch wp0-fixtures. Commit, do not push. Update the plan file.
```

After it runs, look at the printed sizes. Anything under a few kilobytes
probably failed. Tell the same session and let it investigate.

### Result

`scripts/capture_fixtures.py` fetches with the same fetcher the pipeline uses
(`fetch_text` for `static`, `fetch_rendered` for `dynamic`), guesses HTML vs
JSON by attempting a JSON parse, sanitises HTML (see below), and writes
`tests/fixtures/<name>.html` or `.json` atomically (temp file + `os.replace()`).
One source at a time, three-second pause between them.

Sizes after the sanitising pass described below:

```
givewell:   tests/fixtures/givewell.json    (14,119 bytes, 20 jobs; re-captured)
kognity:    tests/fixtures/kognity.html     (12,566 bytes, was 12,727)
storytel:   tests/fixtures/storytel.html    (86,941 bytes, was 99,914)
busuu:      tests/fixtures/busuu.html      (133,876 bytes, was 139,645)
dsv:        tests/fixtures/dsv.html         (31,671 bytes, was 57,961)
impactpool: tests/fixtures/impactpool.html (107,966 bytes, was 126,917)
```

### Sanitising captured HTML (WP0c)

Captured HTML now passes through `sanitise_html` before it is written. It uses
BeautifulSoup, never a regex, and removes only `<script>` elements that carry
no job data. A script is kept when its `type` is a data MIME type
(`application/ld+json`, `application/json`) or when its body or `id` contains a
payload marker: `window.__appData`, `__NEXT_DATA__`, `__NUXT__`,
`__INITIAL_STATE__`, `__APOLLO_STATE__`. It also blanks the value of hidden
`*token*` form inputs, which are CSRF tokens indistinguishable from credentials
to a scanner and read by no extractor.

**Stripping all scripts would be a bug.** `extractors/ashby.py` finds jobs by
searching the raw HTML for `window.__appData` and decoding the JSON that
follows, so a blanket strip would silently reduce `kognity.html` to zero jobs.
Grepping the extractors for the word "script" does not reveal this — `ashby.py`
never uses the word. `tests/test_capture_fixtures.py` guards it by running each
extractor over its fixture and asserting the job count is above zero. Never
replace that with an assertion that fixtures contain no `<script>`.

### Secret-scanning incident (2026-08-07)

GitHub secret scanning flagged `tests/fixtures/givewell.html`. The value was
Greenhouse's own public Google Picker key, embedded in their front-end
`window.ENV` config — a third party's public key, not a credential of this
project. **No rotation and no history rewrite were needed.** The fix is the
sanitiser above: the capture script was saving whole pages including
third-party inline config. The six existing fixtures were re-sanitised in
place, without re-fetching. `test_fixture_contains_no_secret_shaped_values`
now fails the build if a secret-shaped value reappears in any fixture.

### Fixtures must match the URL the extractor actually reads

Several extractors never parse the listing page. Greenhouse, Lever and
SmartRecruiters call a JSON API instead, and paginating extractors ask for
`?page=1` or `?startrow=0` rather than the bare URL. The first version of the
capture script always saved the listing URL, so `givewell.html` was a page its
extractor never looks at — `greenhouse.extract` raised `JSONDecodeError` on it.

Of the six fixtures, only **givewell** was wrong for this reason. `wave` (Lever)
and `oecd` (SmartRecruiters) would be wrong the same way if they were ever
captured. All six now parse: givewell 20 jobs, kognity 5, storytel 6, busuu 6,
dsv 10, impactpool 40.

The fix: rather than duplicating each extractor's URL-building, `capture_one`
runs the real extractor with a recording fetcher and keeps the first response
the extractor asks for, stopping it there so only one request is made. It then
re-parses the saved payload and prints the job count, so a cookie wall or an
unrendered dynamic page is visible at capture time rather than at WP2 time.
givewell was re-captured as `givewell.json` on 2026-08-07 and the stale
`givewell.html` deleted.

When a source's artefact type changes like that, `capture_one` deletes the
fixture it previously wrote under the other extension. An orphaned fixture
looks valid and is parsed by nothing.

### Detecting a rendering fetcher

`workday.py` decides whether to wait for job cards to appear before reading the
DOM. It used to test `fetch_text is fetch_rendered`, which is false for any
wrapper — including the capture script's recording fetcher, so Workday fixtures
were captured on the plain settle delay. `http.py` now marks `fetch_rendered`
with `renders = True` and exposes `is_rendering_fetcher()`; wrappers copy the
mark. Extractors must wrap the fetcher they were given rather than substituting
`fetch_rendered`, or the caller's wrapper is silently discarded.

Prefer this over identity checks in WP9, which replaces `fetch_rendered` with a
reused-browser implementation and would break every such check.
`niras.py` has the same identity check, but both of its branches are identical,
so it is inert — tidy it in WP9.

Verified live on 2026-08-07 by re-capturing busuu, the one dynamic source of
the six. The re-captured page differed from the committed fixture only in
React's randomly generated DOM ids, and parsed to the same six jobs with every
field identical, so the fixture in the repo was not captured too early and the
churn was discarded. That run is also the only end-to-end exercise of the
current `capture_one` against a live site; everything else is covered by tests
with the network faked out.

### Re-running when a fixture goes stale

A career site redesign will eventually break a golden-file test from WP2
before it breaks the real pipeline — that's the fixture doing its job. To
refresh one or more fixtures:

```
python scripts/capture_fixtures.py <source_name> [<source_name> ...]
```

Source names must match an entry in `job_scraper/config/sources.yaml`. The
script overwrites the existing fixture for each name given — check the printed
byte size against the old one before trusting it (a page returning a login
wall or a cookie-consent interstitial is still "successful" HTTP-wise but
much smaller than the real listing). After refreshing, re-run the golden-file
tests (WP2) and update the expected job counts/fields if the site's structure
genuinely changed.

---

## WP1 — Atomic writes and delisting guard

```
think

Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP1 only.

Two data-loss bugs to fix.

BUG 1: Non-atomic writes.
Every write to data/jobs.csv opens the live file with mode "w". A crash or
interrupt mid-write truncates it. Affected: _rewrite_file,
_collapse_content_duplicates, clean_existing_rows, sort_jobs_csv in
job_scraper/storage/csv_store.py.
Fix: write to a temp file in the same directory, then os.replace(). Route every
write through a single helper.

BUG 2: An empty scrape deletes stored history.
In pipeline.py, source_scraped_keys[name] is set whenever an extractor returns,
including when it returns []. clean_existing_rows then deletes every stored row
for that source as "delisted". A silently broken selector is indistinguishable
from "no vacancies", so selector drift wipes real data with no error.
Fix:
- Do not delist a source that returned zero rows this run.
- Log at ERROR level when a source returns zero rows, naming the source.
- Add a --allow-empty-delist flag for the case where a source genuinely emptied.

Write tests for both before or alongside the fix. Do not change anything else in
csv_store.py; the larger restructuring is WP4/WP5.

Branch wp1-data-safety. Commit, do not push. Update the plan file.
```

### Result

**Bug 1 (non-atomic writes).** `_rewrite_file` in `storage/csv_store.py` was the
single place every full-file rewrite already routed through
(`_collapse_content_duplicates`, `_migrate_csv_schema_if_needed`,
`_dedupe_file_if_needed`, `clean_existing_rows`, `sort_jobs_csv` all call it),
so no caller needed touching. It now writes to a `tempfile.mkstemp` file in the
same directory as the target (same filesystem, so `os.replace()` is atomic),
and unlinks the temp file if writing raises. `append_jobs_csv`'s append-mode
write (`"a"`) was left alone — it wasn't in the bug list and a partial append
can't truncate pre-existing history the way a truncating `"w"` can.

**Bug 2 (empty scrape delisting history).** In `pipeline.py`, a source is only
added to `source_scraped_keys` when its extractor returns at least one row. A
zero-row result now logs at ERROR naming the source and is excluded from
`source_scraped_keys` by default, so `clean_existing_rows` has no entry to
delist against and leaves that source's stored rows untouched. Added
`--allow-empty-delist` (`run.py` → `run_pipeline(allow_empty_delist=...)`) for
the case where a source has genuinely gone to zero vacancies: it adds the
source with an empty key set, which delists as before.

Tests: `tests/test_csv_store_atomicity.py` (temp file cleaned up on success,
original file untouched and no stray temp file after a simulated write
failure, `os.replace` invoked with a sibling path) and
`tests/test_pipeline_delisting.py` (zero rows keeps stored jobs and logs
ERROR naming the source; `--allow-empty-delist` still delists; a normal
non-empty scrape still delists rows genuinely missing from the fresh
listing). Full suite: 123 passed.

Not touched, per the package scope: no other restructuring of
`csv_store.py` — that's WP4/WP5.

---

## WP2 — Test net and tooling

```
think

Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP2 only.

Goal: enough test coverage to refactor storage safely, plus linting and CI.
The fixtures from WP0 are in tests/fixtures/.

1. Golden-file extractor tests. For each fixture, assert the parsed output:
   count of jobs, and the full dict of the first job. These catch selector drift
   at test time rather than at run time. Never fetch live sites in a test.
   Note: a fixture for an API-based extractor (Greenhouse, Lever,
   SmartRecruiters) must be the API response, not the listing page — see WP0.
   tests/test_capture_fixtures.py already has the parse-check scaffolding and
   covers capture_one itself with the network faked out; extend rather than
   duplicate it.

2. Pipeline test. Test run_pipeline end to end with a fake extractor and a fake
   fetch function. Assert the RunSummary funnel counts are internally
   consistent.

3. Round-trip test. extractor dict -> CSV -> _dedupe_key must be stable, so a
   job stored in one run is recognised as already-stored in the next.

4. Tooling. Add ruff to pyproject.toml with a sensible config for this codebase
   and fix what it flags, mechanically only. Add a .github/workflows/ci.yml that
   runs ruff and pytest on push and pull request. Note in the plan file that CI
   cannot run the pipeline itself because sources.yaml, rules.json and the data
   files are gitignored.

Branch wp2-test-net. Commit, do not push. Update the plan file.
```

### Result

150 tests pass, up from 123. `ruff check .` is clean.

**Shared fixture table.** `_FIXTURE_CASES` moved out of
`tests/test_capture_fixtures.py` into `tests/fixture_cases.py`, which three
test modules now import. It also owns the `sys.path` amendment that makes
`scripts/capture_fixtures.py` importable, and re-exports both the module and
`single_response_fetch`, so no other test touches `sys.path`.

That re-export is not tidiness. The first version left
`test_capture_fixtures.py` with two imports that had to stay in order — the
`sys.path` line first, `import capture_fixtures` second — and ruff's import
sorter immediately hoisted the second above the first, breaking collection with
`ModuleNotFoundError`. Ordering constraints an import-sorter cannot see are a
trap; the fix is to have nothing to sort. Keep it that way.

**Golden-file tests** (`tests/test_extractors_golden.py`). Job count plus the
complete first-job dict for all six fixtures: givewell 20, kognity 5, storytel
6, busuu 6, dsv 10, impactpool 40. `test_every_fixture_has_a_golden` fails if a
newly captured fixture is added without pinning it. Five sources produce eight
keys; impactpool produces nine — it sets `company`, being an aggregator.

Two extractor quirks are pinned as they are, not fixed (WP2 observes):

- **storytel**: `teamtailor.py`'s first match on that page is a department
  heading, so job 1 has `title` and `raw_snippet` of `"Product & Tech ·
  Stockholm"` and an empty `location`. Layer 0 drops it at run time, so it has
  never been visible in the output.
- **dsv**: `successfactors_html.py` puts the posting date (`"7 Aug 2026"`) in
  `department`.

`test_fixture_still_parses` was deliberately kept alongside the goldens. It is
the weaker assertion, but it fails for a different reason and its message points
at the sanitiser rather than at selector drift.

**Pipeline funnel test** (`tests/test_pipeline_funnel.py`). `run_pipeline` end
to end over a fake extractor and fake fetcher, asserting the arithmetic
`format_summary` performs but nothing checked: sources processed + skipped =
total; the keyword/title/language/blocklist chain equals already-stored +
new-checked + stored-rechecked; Layer 2 intake minus exclusions equals
`jobs_kept_new`; `rows_written == jobs_kept_new` against an empty store. A
second test pins that each stage actually excluded the job aimed at it, so the
invariants cannot pass trivially with every counter at zero.

**Round-trip test** (`tests/test_csv_roundtrip.py`). extractor dict → CSV →
`_dedupe_key`, for all six fixtures plus three cases the fixtures do not reach:
Oatly (where `canonical_detail_url` adds a locale prefix on write and only
`dedupe_key_from_url` folds the variants back together), an `apply_url`-only
job, and an http→https job. Plus: a job with no URL is skipped rather than
stored keyless.

### Two behaviours WP2 found and pinned rather than fixed

Both are pre-existing, both are in code WP5 rewrites, and both now have a test
that will notice when they change.

1. **Content dedup churns a row on every run, for ever.**
   `_collapse_content_duplicates` keys on source + company + title. Two
   impactpool postings advertise the same role for the same employer under
   different URLs, so one is collapsed away — and its URL key then no longer
   exists in the store, so the next run does not recognise it, appends it, and
   collapses it again. Measured: 40 offered, 39 stored, then exactly 1 row
   written on every subsequent run indefinitely. Consequences are cosmetic but
   real — "New rows written" never settles to zero, and the two postings
   alternate in the table. Pinned by
   `test_content_dedupe_churns_a_row_on_every_run`. If WP5 makes it settle,
   **delete that test rather than weakening it**: that is the fix landing.

2. **Layer-2 rejections are re-fetched every run.** A job excluded by the
   detail-page filter is never written to the store, so nothing records that it
   was already judged, and it costs a detail fetch on every subsequent run.
   `jobs_new_checked` therefore does not fall to zero on a repeat run. WP6's
   stored descriptions are what would let the pipeline skip it.

### Tooling

`ruff` in `pyproject.toml`, `select = ["E", "F", "W", "I", "UP", "B"]`,
`line-length = 100`. Rules that argue about design rather than mechanics were
left out on purpose: the structure of this codebase is the refactor plan's
business, not the linter's.

`target-version` is deliberately **not** set. Ruff infers it from
`requires-python = ">=3.10"`, so the `UP` rules can never rewrite code into
syntax newer than the floor the project claims. (Note the standing
inconsistency: CLAUDE.md says Python 3.13 while `requires-python` says 3.10.
Worth settling, but not in this package.)

51 findings, all fixed mechanically: 16 auto-fixed (import ordering, five unused
imports, one deprecated `typing.Callable`), 34 lines rewrapped, and one dead
local removed — `title_tag` in `extractors/niras.py`, superseded by the
`children_text` logic directly beneath it. No behaviour changed. No rule was
suppressed to make the run pass, and there are no `per-file-ignores`: the only
suppression in the tree is an inline `# noqa: E402` at each of the two
`sys.path`-amending imports, which documents itself at the point it applies and
stops applying if the import moves.

### CI, and what it cannot do

`.github/workflows/ci.yml` runs `ruff check .` and `pytest -q` on push and pull
request, on Python 3.13. No `playwright install` — `http.py` imports playwright
lazily and no test renders a page, so the browser binaries would be a ~400 MB
download for nothing.

**CI cannot run the pipeline itself.** `job_scraper/config/sources.yaml`,
`job_scraper/config/rules.json`, `data/jobs.csv` and `data/jobs.xlsx` are all
gitignored, so a clean checkout has no search config and no store;
`run_pipeline` would fail at `_require()` before fetching anything. What CI
verifies is the code and the committed fixtures. A green build does not mean a
live run works — step 6 of the session checklist above, running
`.venv/bin/python -m job_scraper.run` and eyeballing the funnel, is still the
only thing that checks that, and still has to be done by hand.

---

## WP3 — Company field from config

```
Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP3 only.

Problem: the "company" column is populated by only 2 of 30 extractors
(impactpool, jobsinlund). It is empty everywhere else. This breaks _content_key
in csv_store.py, which dedupes on source + company + title, and it leaves the
output table with a blank column.

Fix, without touching 30 extractor modules:
- Add an optional "company" key to each entry in sources.yaml and
  sources.example.yaml. For single-employer sources this is the employer name.
  For aggregators (impactpool, jobsinlund) leave it unset.
- In pipeline.py, stamp the configured company onto each extracted record, but
  only where the extractor has not already set one. The extractor wins.
- Update the JobRecord docstring in __init__.py to say which layer sets company.

Add a test that the extractor value wins over the config value.

Branch wp3-company-field. Commit, do not push. Update the plan file.
```

### Result

`sources.yaml` and `sources.example.yaml` both gained an optional `company:
<name>` key per entry, set for every single-employer source and left unset
for the two aggregators (`impactpool`, `jobsinlund`), whose extractors already
put a per-job company in the extracted data. The header comment in both files
documents the key and the extractor-wins rule.

`pipeline.py`'s source loop now reads `src.get("company")` once per source
and, for each row the extractor returned, sets `row["company"]` only when the
extractor left it falsy. No extractor module was touched. This runs before
`jobs_extracted` is counted, so it has no effect on the funnel arithmetic WP2
pinned.

`JobRecord.company`'s docstring in `__init__.py` now says which layer sets it:
the extractor for aggregators, `pipeline.run_pipeline` from `sources.yaml`
otherwise, extractor always winning.

Test: `tests/test_pipeline_company_field.py`, two cases against a fake
extractor returning one job with `company: ""` and one with
`company: "Extractor Co"` — the blank one picks up the configured company,
the set one keeps its own value. Full suite: 152 passed (up from 150),
`ruff check .` clean.

Not touched, per scope: none of the 30 extractor modules, and
`storage/csv_store.py`'s `_content_key`/dedupe logic itself — this package
only stops it from being handed a blank company for 28 of 30 sources; the two
known-churny dedupe issues WP2 pinned (content-dedup and Layer-2 re-fetch) are
WP5/WP6 work and were left alone.

---

## WP4 — SQLite store, part 1: schema and dual write

```
think hard

Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP4 only.

This is the first half of replacing storage/csv_store.py. Do not delete anything
this session. CSV remains the source of truth until WP5.

Create job_scraper/storage/db.py with a SQLite store:

  jobs(dedupe_key TEXT PRIMARY KEY, source_name, company, title, location,
       detail_url, apply_url, first_seen, last_seen, last_run_id,
       status, experience_level)
  runs(run_id INTEGER PRIMARY KEY, started_at, finished_at)
  source_health(source_name, run_id, rows_found, ok, error)

Notes:
- status covers 'new', 'seen', 'shortlisted', 'rejected', 'delisted'.
- Use a context manager, WAL mode, and parameterised queries throughout.
- first_seen/last_seen are ISO-8601 UTC strings.

Also write a one-off migration script (job_scraper/tools/migrate_to_sqlite.py)
that reads an existing jobs.csv and populates the database, parsing URLs out of
the =HYPERLINK() formulas.

Have the pipeline write to BOTH stores this session, CSV first and authoritative.
Add a test comparing the two stores' contents after a fake run.

Add data/*.db and data/*.sqlite3 to .gitignore in this same commit. My repo is
public and the database will contain every job I have ever seen.

Do not change the filter layers. Do not change xlsx_store.py.

Branch wp4-sqlite-schema. Commit, do not push. Update the plan file with the
schema you settled on and why.
```

Before merging WP4, run `git tag pre-sqlite` on `main` and `git push --tags`.
That is your rollback point if the migration goes badly.

### Result

168 tests pass (up from 152), `ruff check .` clean. New: `storage/db.py`,
`tools/migrate_to_sqlite.py`, `tests/test_sqlite_store.py`,
`tests/test_dual_store.py`. Touched: `pipeline.py` (health collection + mirror
call), `run.py` (`--output-db`), `config_loader.py` (`default_jobs_db_path` →
`data/jobs.sqlite3`), `csv_store.py` (one read-only helper), `.gitignore`.

### The schema, and why

Exactly the three tables from the prompt, with these decisions:

- **`status` is a TEXT column with a CHECK constraint**, not a lookup table:
  five fixed values, one user, and a constraint violation is the loud failure
  wanted if a typo'd status ever reaches the store. `upsert_jobs` also
  validates in Python so the error is a readable `ValueError` on the normal
  path; the CHECK is the backstop for hand-written SQL.
- **`runs.run_id` is `AUTOINCREMENT`** so a run id can never be reused, since
  `jobs.last_run_id` and `source_health.run_id` reference them (real FOREIGN
  KEYs, `PRAGMA foreign_keys=ON`). Note: the SQLite run id is *not* the CSV
  `run_id` column — the CSV counter is per-store and the shadow store keeps
  its own history. They converge only in the sense that WP5 discards the CSV one.
- **`source_health` is keyed `(source_name, run_id)`** and gets one row per
  *attempted* extraction: success (`ok=1`, `rows_found` = extracted count,
  before any filter), or failure (`ok=0`, `error` = the exception text).
  Sources skipped for config reasons (no URL, unknown strategy, no extractor)
  never reached the site and get no row — WP10's "row count collapsed" warning
  should not be diluted by rows that mean "config typo".
- **Timestamps** are `datetime.now(timezone.utc).isoformat(timespec="seconds")`
  strings, e.g. `2026-08-10T14:03:07+00:00` — sortable, greppable, unambiguous.
- **Statuses are review state, and a scrape never overwrites them.** On
  re-sighting, an upsert bumps `last_seen`/`last_run_id` and refreshes the
  descriptive fields but leaves `status` and `first_seen` alone, with one
  exception: a `'delisted'` job that reappears goes back to `'seen'` (it is
  evidently listed again — and `'seen'` not `'new'`, because the owner has
  already had it in the spreadsheet). An empty `experience_level` never
  overwrites a stored one ("not determined this run" ≠ "none"); the pipeline's
  `"cached"` placeholder is filtered out before the mirror for the same reason.
- **`JobStore` is a context manager; one `with` block is one transaction.**
  Everything a run writes commits together or rolls back together, so a crash
  mid-run cannot leave a half-written run — the SQLite equivalent of WP1's
  atomic-write rule. WAL mode on every connection.

### How the dual write works (and dies)

CSV first and authoritative: only after `append_jobs_csv`,
`clean_existing_rows` and `sort_jobs_csv` have all finished does
`pipeline._mirror_to_sqlite` read the **final** CSV back
(`csv_store.read_store_rows`, which recovers plain URLs from the
`=HYPERLINK()` formulas) and sync the database to it: present rows are
upserted, absent rows are marked `status='delisted'` — never deleted. Whatever
the CSV's five filter passes decided this run, the mirror inherits by
construction, so it cannot drift from the authoritative store and needs no
knowledge of the filter layers. A mirror failure logs at ERROR and does not
fail the run (the authoritative store is already safely written); it must stay
loud, or WP5 would inherit a silently stale database.

`experience_level` is the one thing not recoverable from the CSV (WP1's
predecessor dropped that column), so the mirror takes it from this run's
Layer 2 results, keyed by dedupe key; stored jobs keep their previously
recorded level.

`read_store_rows` and `_mirror_to_sqlite` are transitional glue and are
expected to be **deleted in WP5** along with the rest of `csv_store.py`. WP5
also replaces the blunt mirror delisting (absent from CSV ⇒ delisted, whatever
the status) with the N-consecutive-misses rule and must reconcile delisting
against review statuses properly — `mark_delisted_except`'s docstring says so.

### Migration

`python -m job_scraper.tools.migrate_to_sqlite [--csv PATH] [--db PATH]
[--force]` reads jobs.csv (read-only), parses the hyperlink formulas, and
imports every row as **`status='seen'`, not `'new'`** — everything already in
jobs.csv has been in the owner's spreadsheet, and nothing imported should
later surface as unreviewed. The CSV holds no timestamps, so
`first_seen`/`last_seen` are both the migration time; history before that
moment is unknown and is not invented. Refuses to run against a database that
already has jobs unless `--force` (which upserts: existing rows keep their
`first_seen` and status).

### Gitignore

`data/*.db` / `data/*.sqlite3` were already committed (`444cfeb`). What was
missing: WAL sidecars — `jobs.sqlite3-wal`/`-shm` contain the same personal
data as the database and match neither pattern. Added `data/*.db-*` and
`data/*.sqlite3-*`.

### Not done / not touched, per scope

Filter layers, `xlsx_store.py`, and everything scheduled for deletion in
`csv_store.py` are untouched; the only csv_store change is the additive
read-only `read_store_rows`. `RunSummary` and the printed funnel are
unchanged — the mirror's counts appear in the debug log only, until SQLite is
authoritative.

---

## WP5 — SQLite store, part 2: cut over and delete the CSV store

```
ultrathink

Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP5 only.

Make SQLite authoritative and delete the accreted CSV logic.

1. Point pipeline.py at db.py. Remove the dual write.
2. Rewrite xlsx_store.py to read from the database and generate the
   =HYPERLINK() formulas at export time. Excel formulas must no longer exist
   anywhere except in the xlsx writer.
3. Delete from the codebase, once nothing references them:
   _migrate_csv_schema_if_needed, _dedupe_file_if_needed,
   _collapse_content_duplicates, _next_run_id, sort_jobs_csv,
   _excel_hyperlink_formula, _url_from_hyperlink_formula, _REMOVED_COLUMNS.
4. Replace clean_existing_rows with a database equivalent that sets status
   rather than deleting rows. Nothing should ever be hard-deleted; delisted and
   rejected jobs stay in the table.
5. Rewrite blocklist.py to set a review status. IMPORTANT: my current
   scrape_and_blocklist.sh routine blocklists EVERY job after each run, so my
   265-row blocklist.csv means "already seen", not "rejected". Do not import it
   as rejections. Import every existing blocklist.csv row as status 'seen', and
   preserve all 265 rows.
6. Replace the delisting rule from WP1 with the better version now possible:
   mark delisted only after N consecutive successful runs without seeing the job.
   Default N=2, configurable.
7. Store the hybrid confirmation for conditional-location jobs (see the
   decisions log) as a column, e.g. on the `jobs` table. Today
   `matched_reasons` is not a jobs.csv column, so the pending/confirmed marker
   never survives a run and the pipeline re-fetches the detail page for every
   stored conditional-city job on every run to re-earn it. Once there is
   somewhere to persist the confirmation, that job should be skipped like any
   other already-stored job.

Expect to remove roughly 250 lines net. If you find yourself keeping a CSV
helper "just in case", say so and explain why rather than keeping it silently.

Branch wp5-sqlite-cutover. Commit, do not push. Update the plan file.
```

### Result

178 tests pass (was 168 before the cutover; 21 CSV-pinning tests deleted with
the code they pinned, 31 added). `ruff check .` clean. **Net −784 lines.**

Deleted outright: `storage/csv_store.py` (all 455 lines — every function on
the prompt's list plus the rest of the module, which existed only to serve
them) and its tests (`test_csv_store_atomicity`, `test_content_dedupe`,
`test_csv_roundtrip`, `test_dual_store`). The round-trip properties the CSV
tests guarded (Oatly locale variants, apply-url-only jobs, http→https, keyless
rows dropped) were ported to `tests/test_store_roundtrip.py` first — they are
properties of the keying, not of the CSV.

**Also deleted, beyond the prompt's list: `tools/migrate_to_sqlite.py`.** It
was the last parser of `=HYPERLINK()` formulas, so keeping it would have kept
Excel syntax outside the xlsx writer. Verified safe before deleting (row
counts only, no content read): the database already held exactly the 11 jobs
in jobs.csv — the WP4 dual write had done the migration's job — and the 265
blocklist rows were *not* in the database, so the import that actually
matters is the blocklist one (below). jobs.csv itself is untouched on disk as
a frozen pre-cutover archive, and the tool remains in git history.

**One-off action for the owner, once, before the next scrape:**

```
.venv/bin/python -m job_scraper.tools.import_blocklist
```

This reads `data/curated/blocklist.csv` (read-only — the file is not modified)
and imports all 265 rows as **status `'seen'`, never `'rejected'`**, matching
what the blocklist-everything routine actually meant. Idempotent; existing
rows are never demoted from a review status. Skipping it costs nothing
destructive, but until it runs the pipeline will re-fetch detail pages for
old postings it should know about.

How the pieces landed:

- **`pipeline.py`** talks only to `storage/db.py`. Everything after the fetch
  phase runs in one store transaction (`with JobStore(...)`), so a crash
  mid-run rolls back to the previous run's state — the SQLite descendant of
  WP1's atomic writes. The old Layer 1d blocklist file-check is now "stored
  with status `'rejected'`" (same funnel counter). `clean_existing_rows`
  became `refilter_stored_jobs`: same filter sequence, but failures are marked
  `'rejected'`, never deleted, and only `'new'` rows are touched (see the
  decisions log).
- **`storage/db.py`** gained `misses` and `hybrid_confirmed` columns (added by
  `ALTER TABLE` on a WP4-era database, preserving its history),
  `note_misses_and_delist` (the N-consecutive-misses rule), status helpers,
  and `job_to_row`/`dedupe_key_for_job` — the URL canonicalisation that used
  to live in `_normalize_row_fields`/`_dedupe_key`.
- **`storage/xlsx_store.py`** reads the store and shows **`status = 'new'`
  only**, which is what "jobs.csv minus everything blocklisted" always was —
  the owner's open-the-spreadsheet experience is unchanged. `=HYPERLINK()`
  formulas are generated here at export time and exist nowhere else. Green
  highlight = first seen in the two most recent storing runs (rows stored by
  one run share a first_seen timestamp, which stands in for the old run_id).
- **`blocklist.py`** is now status operations: `mark_all_new_seen` (what
  `tools/blocklist_all.py` and `scripts/scrape_and_blocklist.sh` call — the
  routine keeps working, it just flips statuses instead of moving rows
  between files) and `import_legacy_blocklist` (the one-off above).
- **Item 7 (hybrid confirmation)** is the `hybrid_confirmed` column. A
  conditional-city job earns its exception from the detail page once; the
  confirmation is persisted and the job is skipped like any other stored job
  thereafter. The flag only ratchets up — a run that skips the fetch cannot
  unconfirm it. Rows stored before the column existed are re-checked exactly
  once, then converge. Pinned by two tests in `tests/test_pipeline_store.py`
  that count HTTP fetches.

Behaviour changes worth knowing about, all deliberate:

- **Content dedupe is gone** (`_collapse_content_duplicates` was on the
  deletion list). The WP2-pinned churn — one impactpool row rewritten every
  run, forever — is fixed by removal: both URL-variants of a re-advertised
  posting are now stored, so the funnel settles, at the cost of the
  occasional visible duplicate title in the table. WP7's scorer (or a WP5b
  review action) is the right place to handle those; a content-key collapse
  in the store was how the churn started.
- The Mammut pipe-separated-location repair was dropped, not ported: it fixed
  rows written before an extractor fix, and no such row exists in the
  database (they were repaired before the WP4 mirror ever ran). If pipes
  reappear, that is an extractor regression and should fail loudly, not be
  silently patched in storage.
- `run.py` lost `--output` (there is no CSV store to point at); `--output-db`
  is the store, and `--delist-after` configures the miss threshold. The
  summary line "Delisted removed" now reads "Marked delisted" — nothing is
  removed any more.
- `jobs_sources.csv` is still written next to the database. It is a per-run
  report, not a store, and was out of scope.

Not done, per scope: no review commands, no xlsx archive sheet, no
`--show-everything` flag — that is WP5b, which now has the statuses it needs.

---

## WP5b — Replace the blocklist-everything routine

```
think hard

Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP5b only.

Replace my current review routine. Today, scripts/scrape_and_blocklist.sh
blocklists every job after each run so the next run only shows new ones. With
first_seen/last_seen in the database this is no longer needed, and it destroys
my history.

- Add a command that marks jobs as 'seen':
  `python -m job_scraper.review --seen-all`, and one that marks specific jobs
  shortlisted or rejected by their row number in the xlsx.
- Change the xlsx export to show unreviewed jobs by default, with a flag to show
  everything. My day-to-day experience must not get worse: I open jobs.xlsx and
  see only what is new.
- Add an "archive" sheet in the xlsx with every job ever seen, so nothing is
  hidden from me.
- Deprecate scripts/scrape_and_blocklist.sh with a comment pointing at the new
  command. Do not delete it until I confirm the new flow works.

Add tests. Branch wp5b-review-workflow. Commit, do not push. Update the plan file.
```

### Result

194 tests pass (up from 178), `ruff check .` clean. New: `job_scraper/review.py`,
`tests/test_review.py`. Touched: `storage/db.py`, `storage/xlsx_store.py`,
`run.py`, `tools/blocklist_all.py`, `scripts/scrape_and_blocklist.sh`,
`tests/test_xlsx_store.py`, `README.md`.

**The commands.**

```
python -m job_scraper.review --seen-all          # "I have read all of these"
python -m job_scraper.review --shortlist 4 7     # by row number in jobs.xlsx
python -m job_scraper.review --reject 5
python -m job_scraper.review --show-all          # re-export with everything on the review sheet
```

`--shortlist` and `--reject` may be combined in one invocation, which is the
supported way to record several decisions against one sheet. Row-addressed
decisions apply before `--seen-all`, so a job shortlisted in the same command
is not swept up by it. Every row acted on is echoed with its title and company,
because the row number is the one thing in this design that can be mistyped
silently.

### How a row number addresses a job, and why it is stored

The obvious implementations are both wrong. Re-deriving the export's sort order
at review time addresses a *different* job if a scrape ran in between (the
sheet is ordered by source then first_seen, so one new posting shifts every row
below it). Reading the row back out of the xlsx means parsing `=HYPERLINK()`
outside the xlsx writer, which is exactly the coupling WP5 removed.

So the export records what it wrote: a new `export_rows(row_number,
dedupe_key)` table, replaced wholesale on every export, holding only the most
recent one. A row number therefore resolves against the spreadsheet the owner
is actually looking at, or fails. It is plain data — integers and keys, no
spreadsheet syntax — so the "one canonical representation" rule holds.

Resolution is **all-or-nothing**: a command naming five rows, one of them a
typo, applies none of them and says which number was wrong. Reviewing before
any export ever ran fails with the command to run first, rather than doing
nothing quietly.

The stored row number is the **worksheet** row (data starts at 2), and it is
also written into a `#` column. Both readings of "row number in the xlsx" —
the `#` cell and Excel's own gutter — therefore agree. The column is not
redundant: sorting the table in Excel rearranges rows under the gutter numbers
but carries `#` along with its posting.

**The one footgun**, and it is inherent rather than incidental: a successful
review re-exports, which renumbers the rows, while the owner's open copy of
jobs.xlsx still shows the old numbers. Mitigated three ways — several
decisions can go in one command, every row acted on is echoed with its title,
and the output ends with "Reopen it before using row numbers again". `--no-export`
skips the regeneration and keeps the numbers on screen valid.

### The xlsx: two sheets

- **Jobs** — the review sheet. Unreviewed (`'new'`) jobs only, which is what
  the owner has always opened the file to see, so the day-to-day experience is
  unchanged apart from the `#` column. `--show-all` (on both `run` and
  `review`) puts every job here instead and adds a `status` column, which
  earns its place only in that mode — in the default view every row would say
  `'new'`. Row numbers address whatever is on this sheet, so `--show-all` is
  also how a decision gets recorded against an already-reviewed job.
- **Archive** — every job ever stored, whatever its status, with `first_seen`
  and `last_seen`, newest first. This is the "nothing is hidden from me"
  guarantee: a job that has left the review sheet is on this one.

Writing the file and recording the row mapping happen inside one store
transaction, with the mapping written after a successful save — a failed
export leaves the previous spreadsheet *and* its still-valid row numbers in
place, rather than advancing the numbering past a file the owner never
received.

**Beyond the package, deliberately:** `write_xlsx` now saves via a temp file in
the same directory and `os.replace()`, like every other write in the codebase
since WP1. It was the last non-atomic write in the tree, and this package both
touches that line and adds a second command that writes the file.

### Deprecated, not deleted

`scripts/scrape_and_blocklist.sh` keeps working. Its header now explains what
replaced it and why (it decided every job was dealt with *before* the owner had
looked, so an unopened run was indistinguishable from a reviewed one), and it
prints the same note when run. `tools/blocklist_all.py` — the second half of
that routine, and the same operation as `--seen-all` — got the same treatment.
Both stay until the owner confirms the new flow. `blocklist.mark_all_new_seen`
is still the library call behind the deprecated tool; `review` reaches
`store.mark_new_as_seen()` directly because it must share the transaction with
the row-addressed decisions.

### Verified against the real store

Read-only, on a copy in a scratch directory, touching nothing under `data/`:
the owner's store holds 265 `'seen'`, 13 `'rejected'` and 2 `'shortlisted'`
jobs and no `'new'` ones, so the review sheet exports empty and the archive
sheet carries all 280 — which is the intended behaviour, and confirms the
WP5 blocklist import landed. A live `python -m job_scraper.run` was **not**
performed: it fetches ~30 real career sites, and it is step 6 of the session
checklist above, for the owner to run.

---

## WP5c — Review ranges and `--reject-all`

Small follow-on to WP5b, requested after first contact with the commands: the
owner reviews in a two-decision model ("shortlist these, reject the rest") and
wanted ranges rather than typing every row number.

### Result

206 tests pass (up from 194), `ruff check .` clean. Touched: `review.py`,
`storage/db.py` (one generalised helper), `tests/test_review.py`, `README.md`.

- **Ranges.** `--shortlist`/`--reject` accept `9-12` alongside single numbers,
  inclusive at both ends. Parsing (`review.parse_rows`) happens before any
  status is touched, so a malformed or backwards range keeps the all-or-nothing
  promise: nothing is applied.
- **`--reject-all`** marks every unreviewed job `'rejected'`, applied after the
  row-addressed decisions, so `--shortlist 4 --reject-all` keeps row 4 and
  rejects the rest. It rides on `db.mark_all_new(status)`, which
  `mark_new_as_seen` now delegates to; only `'new'` rows are ever swept, so
  earlier decisions survive. `--seen-all --reject-all` together is refused —
  they would race for the same rows.
- **The `'seen'` status stays.** The owner asked whether it could go entirely;
  decision: no. It is what the 265 legacy blocklist rows mean ("shown, not
  declined"), and removing it would erase the seen-vs-rejected distinction the
  WP5 import deliberately preserved. `--reject-all` gives the two-label
  workflow without the schema change; `'seen'` simply stops accumulating if
  the owner never uses `--seen-all` again. Consequence, documented in the
  README: `'rejected'` is permanent by design, so mass-rejecting means
  revisiting an old posting requires `--show-all` and a manual re-mark.

---

## WP6 — Persist detail descriptions

```
think

Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP6 only.

Layer 2 in experience_filter.py fetches each new job's detail page, extracts two
booleans and an integer, then discards the text. That text is needed for LLM
scoring in WP7, and re-fetching it later would be slow and impolite.

- Add a description_text column to the jobs table (or a separate job_details
  table if you prefer, argue for your choice).
- Store the stripped plain text from _strip_html, capped at a sensible length.
- Store fetched_at so staleness is visible.
- Skip the fetch entirely when a usable description is already stored and the
  job has not changed.

Add a test that a second run over the same job performs no HTTP request.

Branch wp6-persist-descriptions. Commit, do not push. Update the plan file.
```

### Result

208 tests pass (up from 206), `ruff check .` clean. Touched:
`storage/db.py`, `experience_filter.py`, `pipeline.py`,
`tests/test_pipeline_store.py`, `tests/test_pipeline_funnel.py`.

**Columns on `jobs`, not a separate table.** `description_text` and
`description_fetched_at` were added directly to the `jobs` table (via the same
`ALTER TABLE`-on-open pattern WP5 used for `misses`/`hybrid_confirmed`), not a
`job_details` table. The relationship is 1:1 and always was — one detail page
per job — so a second table would only add a join every read site would have
to perform, for no normalisation benefit. `description_text` is capped at
`_MAX_DESCRIPTION_CHARS = 20_000` in `experience_filter.py` before it ever
reaches the store.

**Upsert semantics match `experience_level`'s existing rule.** An empty
`description_text` never overwrites a stored one — `upsert_jobs`'s `CASE`
logic is the same shape already used for `experience_level`, extended to keep
`description_fetched_at` moving in lockstep with `description_text` rather
than as an independent field. This matters because most rows upserted each
run (`cached_jobs`, `blocked_jobs`) carry no fresh description at all — Layer
2 was skipped for them — and must not blank out what an earlier run captured.

**How the fetch is actually skipped, and why no new "is it stale" check was
needed.** `apply_detail_filter` (Layer 2) now returns `description_text` and
`description_fetched_at` on every job it touches, kept or excluded, and
`pipeline.py` persists both. The pre-existing skip path (`_needs_detail`,
unchanged) already skips Layer 2 for anything already in the store; what was
missing was that a job Layer 2 *rejected* (too senior, PhD required) was
never stored at all, so it was indistinguishable from a job never seen before
and got re-fetched every run forever — this was pinned as known behaviour in
WP2 and re-pinned as a known gap in WP5's `test_second_run_stores_nothing_new`.

The fix does not add a new skip mechanism: Layer-2-rejected jobs are now
upserted with `status='rejected'` (a new `store.upsert_jobs(..., initial_status
="rejected")` call in `pipeline.py`, right after the main upsert). On the next
run they are caught by the review-status filter (Layer 1d) — the same
mechanism that already skips a job the owner manually rejected — before they
ever reach Layer 2 again. One canonical "skip this job" path, not two.

**What "the job has not changed" means in practice.** Nothing elsewhere in
the store re-verifies a persisted Layer 2 result against the live page either
(`experience_level`, `hybrid_confirmed` are equally durable once stored, with
no re-check trigger beyond "the row didn't exist before"). Consistent with
that, "unchanged" here means "the same dedupe key already has a non-empty
`description_text`" — there is no cheap way to know the page changed without
fetching it, so this is not a gap specific to WP6. A job whose only Layer 2
attempt failed (network error) is kept fail-open with `description_text=''`
and, being stored, is never retried either — the same shape of limitation
`experience_level='unspecified'` already has for a fetch that fails outright.

**Tests.** `test_first_run_stores_jobs_with_run_metadata` now also asserts
the description and its timestamp landed.
`test_second_run_performs_no_http_request_for_an_already_stored_job` is the
package's required test: a second run over the same (kept) jobs makes zero
further fetches. `test_layer2_rejected_job_is_stored_and_not_refetched` is
the more interesting case — a job Layer 2 excludes for being too senior is
stored as `'rejected'` with its description on the first run, then costs no
fetch on the second, caught instead by the review-status filter.
`test_pipeline_funnel.py::test_second_run_stores_nothing_new` — previously
pinning the bug as current behaviour — now pins the fix: `jobs_new_checked`
falls to zero on the second run instead of staying at the two rejected jobs
forever, and the accounting moves to `jobs_blocklist_excluded`, which is
exactly the sort of arithmetic shift step 6 of the session checklist (running
the pipeline for real) would otherwise have to catch by eye.

Not touched, per scope: `xlsx_store.py` (no export changes — descriptions are
not shown to the owner, they are WP7's scorer's input) and `_needs_detail` in
`pipeline.py`, which needed no change at all because the review-status skip
already existed for a different reason and turned out to be exactly the right
mechanism to reuse.

---

## WP7 — LLM scoring stage

```
think hard

Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP7 only.

Add a scoring stage that reads stored descriptions and calls the Anthropic API.
This replaces a manual step I currently do outside the pipeline.

- New module job_scraper/scoring.py.
- Read the API key from the ANTHROPIC_API_KEY environment variable. Never write
  a key to disk or to any config file.
- Score only jobs that are new or whose description changed. Never re-score.
- Prompt takes a rubric from a new gitignored config file,
  job_scraper/config/profile.md (ship profile.example.md), containing my
  background and what I am looking for. Do not invent its contents: create the
  example with clear placeholders and ask me to fill it in.
- Add job_scraper/config/profile.md to .gitignore in this same commit. My repo
  is public.
- Return structured JSON: score 0-100, seniority_fit, relevance, reasoning,
  flags. Validate the response and fail gracefully per job rather than aborting
  the run.
- Store score and reasoning. Sort the xlsx by score descending.
- Add a --no-score flag to skip the stage.
- Add a cost estimate to the run summary.

Mock the API in tests. Do not make live calls from the test suite.

Branch wp7-llm-scoring. Commit, do not push. Update the plan file.
```

---

## WP8 — Retire the keyword ladder

```
think hard

Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP8 only.

With LLM scoring in place, the five-layer regex ladder can shrink. Before
changing anything, run the current pipeline and the new one over the same stored
jobs and show me a diff of what each keeps, so the decision is evidence-based.

Candidates for removal, in this order:
- apply_non_english_text_filter: already inert in refilter_stored_jobs (the
  old clean_existing_rows) because stored rows have no raw_snippet, and
  langdetect on short snippets is unreliable and nondeterministic. The scorer
  handles language better.
- apply_language_filter (the "X speaker" pattern): subsumed by the scorer.
- title_exclude_keywords.csv: subsumed by the scorer.

Keep: location rules, the review statuses, and the numeric experience
extraction. These are cheap, deterministic, and correct.

Also fix, wherever the remaining code lives:
- build_hybrid_pattern is called per job inside matches_rules. Compile once and
  pass it down.
- _build_title_keyword_pattern recompiles on every call.
- The seniority list has false positives: "Lead" matches "Lead Generation
  Analyst", "Architect" matches roles that may be junior. Propose fixes, do not
  just delete entries.

Branch wp8-retire-ladder. Commit, do not push. Update the plan file with the
before/after diff summary.
```

---

## WP9 — Playwright reuse and HTTP caching

```
think hard

Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP9 only.

Two performance problems in http.py.

1. fetch_rendered launches and tears down a fresh Chromium on every call. For a
   dynamic source's Layer 2 pass that is one browser launch per job, up to 10
   concurrently given _DETAIL_WORKERS = 10. Restructure so one browser is
   launched per run and reused, with a context or page per fetch. Mind that the
   Playwright sync API is not safe to share across threads: either give each
   worker thread its own context correctly, or move rendered fetches out of the
   thread pool. Explain your choice.

2. No HTTP caching. Every run refetches every listing page. Add caching with a
   short TTL for listing pages plus ETag and If-Modified-Since support. Propose
   the library before adding it.

Measure before and after. Report wall-clock time for a full run in the plan file.

Branch wp9-fetch-performance. Commit, do not push. Update the plan file.
```

---

## WP10 — Politeness and observability

```
think

Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP10 only.

These are other people's career sites and the current code is not a good guest.

- DEFAULT_USER_AGENT in http.py still contains example.com. Ask me for a real
  contact URL and address, do not guess.
- _DETAIL_WORKERS = 10 is a global cap, so ten simultaneous requests can hit one
  host. Make the cap per-host instead, default 2, with a small delay between
  requests to the same host.
- Add a robots.txt check with a per-host cache, and a config option to override
  it per source for sites where the check is wrong.
- Use the source_health table from WP4: at the end of each run, warn about any
  source whose row count dropped by more than 50% against its previous
  successful run.
- Add --dry-run that fetches and filters but writes nothing.

Branch wp10-politeness. Commit, do not push. Update the plan file.
```

---

# Keeping the public repo clean

The repo is public. Every private file has a tracked `.example` twin and the
real one is gitignored. Keep applying that pattern.

Verified clean at the start of this plan: `sources.yaml`, `rules.json`,
`blocklist.csv`, `jobs.csv` and `candidate_sources.xlsx` have never appeared in
any commit.

Still to watch:

- `job_scraper/config/title_exclude_keywords.csv` is tracked and shows real role
  exclusions. Optional: give it the `.example` treatment too.
- `profile.md` from WP7 will contain a CV. Must be gitignored.
- The SQLite database from WP4 will hold every job ever seen. Must be gitignored.
- This plan file is public. Skim it for real cities and companies before pushing.
- Fixtures from WP0 are third-party career pages. Fine to publish, but large.
