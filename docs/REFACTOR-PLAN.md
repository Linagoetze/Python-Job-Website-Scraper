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
| 7 | LLM scoring stage | 3.5 hr | Fable 5 | `think hard` | done | `wp7-llm-scoring` |
| 8a | Drop log: record every exclusion | 2.5 hr | Opus 5 | `think hard` | done | `wp8a-drop-log` |
| 8c | Offline evaluation harness | 2.5 hr | Opus 5 | `think hard` | done | `wp8c-eval-harness` |
| 8d | Unresolvable locations | 2.5 hr | Opus 5 | `think hard` | done | `wp8d-unresolvable-locations` |
| 8e | Extractor location gaps | 2 hr | Sonnet 5 | `think` | not started | `wp8e-extractor-locations` |
| 8 | Trim the ladder, prune the keywords | 2.5 hr | Opus 5 | `think hard` | not started | `wp8-trim-ladder` |
| 8b | README reconciliation | 1 hr | Sonnet 5 | none | not started | `wp8b-readme` |
| 9 | Playwright reuse and HTTP caching | 3 hr | Fable 5 | `think hard` | not started | `wp9-fetch-performance` |
| 10 | Politeness and observability | 1.5 hr | Sonnet 5 | `think` | not started | `wp10-politeness` |

Total roughly 30 hours. One package per week is about three months. Two evenings
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
- **Drop-log rule strings are a contract, not log text (WP8a).** The `rule`
  column in `run_exclusions` is what `--rule` filters on and what the per-rule
  counts group by, so changing a rule string silently splits one rule into two
  across the retention window and makes a before/after comparison lie. Add new
  rules freely; reword an existing one only deliberately. The filters attach
  the rule to the excluded job under `filtering.DROP_RULE_KEY`; a layer that
  forgets logs `unattributed` rather than dropping the row, because a missing
  row is the exact blindness the log exists to remove.
- **WP7 follow-up (2026-08-17): scoring stays off, and API billing is not a
  substitute question.** The owner is not opening a Developer Platform account
  for now. A Claude Pro/subscription login is a separate product from the
  Anthropic API: subscription auth does not authenticate `anthropic.Anthropic()`
  and does not substitute for an `ANTHROPIC_API_KEY`, which is billed
  separately per token. Consequence: `rules.json`'s `scoring_enabled` (default
  `false`) is now authoritative and `--score` (replacing `--no-score`) forces
  the stage on for one run, overriding the config — but the stage stays off by
  default until the owner actually opens API billing, and `score_new_jobs` is
  now only ever called when scoring is wanted, so an intentionally-off stage
  never logs an ERROR. `anthropic` moved from a module-level import to a lazy
  one inside `score_new_jobs` (`job_scraper/scoring.py`), so a normal run
  never pays its import cost; kept installed (not commented out) in
  `requirements.txt` since `tests/test_scoring.py` mocks it and CI installs
  from that file. Do not delete `scoring.py`, the `score*` columns, or
  `scored_description_sha256` — they are the correct dormant state until
  billing is set up, and re-adding them later would mean a migration.
- **The gold set measures the ladder, it does not estimate the live
  population (WP8c).** `data/curated/labels.csv` was assembled from the drop
  log plus the review table, so it deliberately over-samples what the filters
  rejected. Its precision and recall are comparable *between two rule
  configurations over the same file* — which is the only comparison a rule
  change needs — and are not an estimate of what a real run yields. Anyone
  quoting "precision 0.257" as the scraper's precision is quoting it wrong.
- **`review` is the positive class and beta defaults to 2 (WP8c).** A false
  positive costs a line in a spreadsheet; a false negative costs a job the
  owner never learns exists, which is the "never lose data" priority in metric
  form. Any future metric added here keeps that asymmetry or states plainly
  that it does not.
- **Three causes hide behind "location drops" (WP8d/WP8e).** Reading WP8c's
  false-negative listing found that the 33 lost location jobs are not one bug.
  (1) The extractor captured no location at all — a genuine extractor fault,
  and WP8e. (2) The listing page never named the cities (`"2 locations"`), so
  the extractor is faithfully copying a placeholder and there is nothing on
  that page to capture. (3) The field names a region rather than a city:
  `filtering._GENERIC_LOCATION_TOKENS` lists `"home based"` but not
  `"home base"`, so `"Home base - EMEA"` reads as a city nobody has heard of.
  (2) and (3) share one root cause and are WP8d — `matches_rules` knows
  "empty" and "a specific city" and has no third state for "present, and not a
  place".
- **An unresolvable location defers to Layer 2, and fails closed (WP8d).**
  Owner's decision, 2026-08-19: treat it exactly as a conditional hybrid city
  is treated — pass Layer 0 with a pending reason, settle it against the
  fetched description in `_resolve_hybrid`'s image, and drop it when the
  description confirms nothing on the list. Consequence, and it is the point:
  this does **not** hand back the lost jobs. It stops them being killed by a
  placeholder string and gets them judged on the posting instead. The price is
  a detail fetch for jobs that previously died at Layer 0, which WP8d must
  measure and report rather than assume is small.
- **The gold set measures Layer 0 changes and is blind to extractor changes
  (WP8d/WP8e).** `labels.csv`'s `location` column holds what the extractor
  produced at labelling time. So `eval.py` scores a `filtering.py` change
  exactly, and reports *no improvement* for a fixed extractor — it is still
  replaying the old broken value. WP8d is therefore measurable with the
  harness and WP8e is not, which is why they are separate packages in that
  order. Rows are keyed by `dedupe_key` and the review/discard judgement is
  about the job rather than the location string, so WP8e needs the `location`
  column refreshed from the store, not the set re-labelled.
- **`non_place_locations` extends the code tokens, it does not replace them
  (WP8d).** Owner's decision, 2026-08-19: one new `rules.json` key, seeded with
  regions *and* bare country names, matched whole-word per segment. A
  `rules.json` without the key still recognises the `"N locations"` shape and
  `home based`, so the feature cannot be switched off by a config file that
  predates it. Consequence for anyone editing the list: a term only matters
  when striking it out leaves no letter behind in that segment, so adding
  `"Spain"` does not turn `Barcelona, Spain` into a placeholder.
- **A deferred state is not a recall win, and the eval report says so
  (WP8d).** `eval.py` flags `pending_location` jobs the way it already flags
  `pending_hybrid` ones. Layer 2 settles both and fails closed, and the harness
  makes no HTTP request, so the recall it reports over deferred jobs is a
  ceiling. On the 2026-08-19 gold set the entire 0.365 → 0.432 gain is
  provisional; quoting it as achieved recall is quoting it wrong.
- **`UNVERIFIED_KEY` replaces `hybrid_unverified` (WP8d).** Both fail-closed
  deferred states can be dropped by a network hiccup rather than a judgement,
  and neither may be persisted as `'rejected'`. One key on the job dict, not
  one per filter — a second flag is one `pipeline.py` forgets to check, and the
  cost of forgetting is a job permanently lost to a timeout.
- **WP8's original premise died with WP7, and its prompt was rewritten
  (2026-08-20).** As first written, WP8 opened "with LLM scoring in place" and
  justified every deletion with "subsumed by the scorer". The scorer is off —
  `scoring_enabled` is `false`, and the WP7 entry above says it stays off until
  the owner opens API billing — so deleting a layer today hands its job to
  nothing, not to the scorer. Consequences, all now in the rewritten prompt:
  `title_exclude_keywords.csv` is **not** deleted, because nothing replaces it;
  it is pruned against the harness instead. The two language layers are still
  deleted, but on the smaller and honest grounds that they cost complexity and
  buy almost nothing. Two of the prompt's three performance fixes were already
  done or overstated. If API billing is ever opened, revisit the keyword CSV:
  that is the moment the original argument becomes true.

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

### Follow-up fix: a failed hybrid check must not permanently reject a job

Caught by another session's review, and correct. Storing every Layer-2
exclusion as `'rejected'` was right for the ordinary cases (too senior, PhD
required — the fetch always succeeds when those fire) but wrong for one:
a conditional-location job's hybrid check fails *closed* when the detail
page cannot be read at all (network error, no URL, no pattern configured),
which `apply_detail_filter` already treated as an exclusion for that run
— existing, deliberate behaviour, unchanged since before WP5. The bug was
persisting that exclusion. Because `'rejected'` is permanent by design
(nothing automatic un-rejects a job, and rejected jobs never appear in
`jobs.xlsx`), a single transient timeout at exactly the wrong moment would
silently and permanently drop the job — the one thing CLAUDE.md's priority
list rules out first.

Fix: `apply_detail_filter` now distinguishes "read the page and it wasn't
hybrid" (`hybrid_found is False`, a real judgement, safe to persist) from
"couldn't determine" (`hybrid_found is None` — no URL, fetch/parse
exception, or no pattern), marking the latter `hybrid_unverified=True` on
the job dict. `pipeline.py`'s Layer-2-rejection upsert excludes anything
carrying that flag, so an unverified job is excluded for the run (fail-closed
is unchanged) but never written to the store — reproducing the exact
pre-WP6 behaviour for this one case: dropped this run, retried next run, at
the cost of one detail fetch each time it recurs. No new skip mechanism, no
change to the "too senior"/"PhD required" persistence this package exists
for.

New test: `tests/test_pipeline_store.py::test_failed_hybrid_check_is_not_
permanently_rejected` — a conditional-city job whose fetch always raises is
excluded on both of two consecutive runs, fetched (and failing) both times,
and never appears in the database at all.

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

### Result

**Superseded by the 2026-08-17 follow-up below:** `--no-score` was replaced
with `--score`, and `scoring_enabled: false` in `rules.json`/
`rules.example.json` is now the authority for whether the stage runs at all —
see the decisions log entry above. The rest of this section is left as
originally written, as the historical record of what WP7 shipped.

224 tests pass (up from 208), `ruff check .` clean. New: `job_scraper/scoring.py`,
`job_scraper/config/profile.example.md`, `tests/test_scoring.py`. Touched:
`storage/db.py` (score columns + `record_score`), `storage/xlsx_store.py`
(score columns, sort), `run.py` (`--no-score`, stage call, summary lines),
`config_loader.py` (`default_profile_path`/`load_profile`), `requirements.txt`,
`tests/test_xlsx_store.py`.

**One-off action for the owner before the next run:**

```
cp job_scraper/config/profile.example.md job_scraper/config/profile.md
```

then fill in the bracketed placeholders (background, targets, location, hard
constraints) and export `ANTHROPIC_API_KEY` in the shell that runs the
scraper. Until both exist the stage logs an ERROR, reports itself skipped in
the run summary, and the run otherwise completes normally — unscored jobs
simply sort to the bottom of the spreadsheet.

Decisions:

- **New dependency `anthropic>=0.100.0`**, approved by the owner this session
  (asked per CLAUDE.md before adding). The alternative — raw HTTP through
  `requests` — would have meant hand-rolling retry/backoff the SDK already
  does.
- **Model: `claude-opus-5`** (`scoring.SCORING_MODEL`, a constant — change it
  there), called with `output_config.effort: "low"` (scoring against a rubric
  is routine work; low effort is markedly cheaper on this model and still
  strong) and a structured-outputs JSON schema, so the response is guaranteed
  to parse. The rubric rides in the system prompt with a `cache_control`
  breakpoint: every call after the first in a run reads it from cache at a
  tenth of the input price.
- **The stage lives in `run.py`, not `pipeline.py`.** Scoring is not a filter
  layer (CLAUDE.md forbids a sixth without asking) and it must not be able to
  fail a scrape: `run_pipeline` commits first, then scoring runs, then the
  xlsx export — so the spreadsheet is sorted by fresh scores, and a scoring
  crash can never lose scraped data. `--no-score` just skips the call.
- **Never re-score, keyed on content:** `scored_description_sha256` records
  the SHA-256 of the description each score judged. A candidate is a status
  `'new'` job whose stored description hash differs — so an unchanged job
  costs nothing on later runs, and a changed description (the only observable
  "the job changed" signal the store has) earns exactly one new call.
  Reviewed statuses are never scored: money spent on a job the owner already
  decided about is wasted.
- **`score` is a nullable INTEGER**, not a defaulted one: 0 is a real
  (terrible) score and must stay distinguishable from "not scored". The
  columns are added by the same `ALTER TABLE`-on-open pattern as
  `misses`/WP6, verified against a WP6-era database; `upsert_jobs` never
  touches them, so scores survive every subsequent scrape.
- **Failure is per job, in tiers:** a refusal, truncation, invalid JSON or
  out-of-range payload marks that one job failed (hash not recorded, so it
  retries next run) and moves on. Auth, permission and rate-limit errors
  abort the remaining loop — every further request would hit the same wall —
  while keeping the scores already recorded. A missing key or profile skips
  the whole stage with an ERROR log and a "Scoring skipped:" line in the
  summary, never an aborted run.
- **`flags` is stored as `'; '`-joined plain text** (one canonical
  representation — the store holds plain data, not JSON to be parsed by
  readers).
- **xlsx:** the review sheet gains `score` (written as a number, blank when
  NULL), `score_reasoning` and `score_flags` columns, and sorts score
  descending; the old source/recency order is the tiebreak within a score
  band and the whole order when nothing is scored. Row-number addressing for
  the review commands is unaffected — `export_rows` records whatever order
  was written.
- **Cost estimate** accumulates the per-response `usage` counts and prices
  them at claude-opus-5 list rates (constants in `scoring.py` — update them
  if the model changes), shown in the run summary as
  "Estimated scoring cost".
- **`.gitignore` already covered `job_scraper/config/profile.md`** — it was
  added preemptively in `444cfeb` (WP4 session), so this package had nothing
  to add; verified with `git check-ignore`.
- Tests inject a `FakeClient`; the real client is only constructed after the
  ANTHROPIC_API_KEY check, and the key-missing test deletes the variable
  first, so the suite can never make a live call.

---

## WP8a — Drop log: record every exclusion

Inserted before WP8. WP8 asks for a before/after diff of what each version of
the ladder keeps; that diff is unbuildable while every exclusion is discarded
the moment it is counted. This package is the instrument WP8 measures with.

```
think hard

Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP8a only.

Right now every filter exclusion is invisible. Layer 0 in pipeline.py calls
continue on a failing job and writes nothing. Layers 1a to 1c return excluded
lists that are counted for the summary and then discarded. So I have no way to
find a false negative, and no way to tell whether a rule change helped.

1. New table run_exclusions(run_id, dedupe_key, title, company, source_name,
   location, layer, rule, excluded_at). One row per exclusion, written inside
   the existing run transaction so it commits or rolls back with everything
   else.
2. "rule" must name the specific thing that fired, not the layer: the exact
   keyword and its match type for the title filter, the specific seniority
   term, the detected language code for langdetect. Break the location layer
   down finely. Distinguish: an empty or missing location field; a location
   field naming a city not on my list; a remote keyword present but overridden
   because the field named a specific city; a conditional city without hybrid
   confirmation.
3. Titles and metadata only. It must not trigger a single extra HTTP request,
   and it must not fetch detail pages.
4. Prune rows older than N runs (default 10) so the table cannot grow forever.
5. Add a small CLI: --show-drops [--layer X] [--rule Y] [--source Z] that
   prints the last run's exclusions, and a --drops-csv path that exports them.
6. Fix two run-summary labels while you are in there. "Jobs now in table"
   reads like a table size but is an unreviewed count: rename it "Unreviewed
   jobs in table". Add a "Still listed this run" line above it counting every
   job sighted this run regardless of review status.

Branch wp8a-drop-log. Commit, do not push. Update the plan file.
```

### Result

246 tests pass (up from 228), `ruff check .` clean. New: `job_scraper/drops.py`,
`tests/test_drop_log.py`. Touched: `filtering.py`, `experience_filter.py`,
`pipeline.py`, `storage/db.py`, `run.py`, `tools/retrofilter.py`,
`tests/test_pipeline_funnel.py`, `tests/test_run_scoring_gate.py`, `README.md`.

**How a rule reaches the log.** The filters stay data-in/data-out: an excluded
job is returned as a copy carrying the rule that dropped it under
`filtering.DROP_RULE_KEY` (`"drop_rule"`), and `pipeline.py` turns those into
log rows and applies the layer name. No callbacks, no collector object threaded
through the ladder, and `len(excluded)` still means what it meant, so every
existing funnel counter is untouched. The key never reaches the store's `jobs`
table — `job_to_row` copies named columns only. `matches_rules` needed nothing
new: it already returned a reason list, and on rejection that single reason
*is* the rule.

**Naming the specific keyword without recompiling anything per job.** The
combined alternation stays the decision and is unchanged;
`build_title_keyword_matchers` compiles one small pattern per configured entry
at setup, alongside it, and those are consulted **only for the jobs the
combined pattern already rejected**. So attribution costs nothing on the
common path, and the compile-once rule holds. The seniority filter shares the
same helper under its own prefix — both are a list of terms against a title.
When two keywords match one title the rule names the first in *file* order,
not the leftmost in the title: the file is the order the owner reads their own
list in.

**The location breakdown (item 2), and where the fourth case actually lives.**
`_location_drop_rule` splits the old single "locations: no match" into: no
location given / remote keyword overridden by a named city / conditional city
with the hybrid gate unconfigured / city not on the list, tested in that order
for the reasons in its docstring. The fourth case the prompt asked for — a
conditional city without hybrid confirmation — is **not** a Layer 0 drop when
the gate is configured: Layer 0 admits it provisionally and Layer 2 settles it
against the description. So it is logged at Layer 2 as `hybrid: conditional
city, description is not hybrid`, kept distinct from `hybrid: conditional city,
could not read the description`, which is the fail-closed-on-a-network-error
case WP6's follow-up made deliberately non-persistent. Both appear under
`--rule hybrid`. `locations: conditional city, hybrid gate not configured`
covers the remaining Layer 0 case, where the whole conditional list is inert
because `conditional_location_keywords` is empty.

**Item 3 (no extra HTTP) is pinned by a test**, not just by inspection:
`test_logging_costs_no_extra_http_request` counts every fetch in a run and
asserts it equals Layer 2's own intake, and that no job dropped before Layer 2
was ever fetched. Everything the log stores was already in hand when the job
was excluded.

**Layer 2 exclusions are logged too**, which costs nothing — that fetch has
already happened — and is the only way the hybrid cases and the years
threshold become visible. The re-filter pass over stored unreviewed rows is
logged as well, under the same layer names behind a `refilter/` prefix: those
are real exclusions and belong in the log, but they are a different population
from this run's scrape and must not be added into its funnel.

**Retention.** `prune_exclusions(keep_runs)` runs at the end of every run,
counting over the runs that actually logged exclusions rather than over `runs`,
so a run that dropped nothing cannot push a useful one out of the window.
`--keep-drop-runs` (default 10) is on `run.py`.

**Reading it back** is `python -m job_scraper.drops` — a separate module, not a
flag on `run.py`, because `run.py` always scrapes and the whole point is that
inspecting the log costs nothing. Bare invocation prints the per-rule counts
(the "did that change help?" view); `--show-drops` prints one line per excluded
job; `--layer`/`--rule`/`--source` narrow *whichever* view was asked for, so
the counts and the listing can never describe different sets; `--drops-csv`
exports the same rows. The filters match case-insensitive substrings — nobody
should have to type a rule string verbatim.

### Item 6: the two closing totals

`format_summary` lost its `table_total` parameter and now reads both numbers
off `RunSummary` (`jobs_still_listed`, `jobs_unreviewed`, plus
`exclusions_logged`). That also fixed a latent mislabelling: `table_total` came
from `write_xlsx`'s return, which under `--show-all` is *every* job, so the
line would have said "unreviewed" while showing the whole table. Both counts are
taken after the re-filter pass, which can move a row out of `'new'`.

### What the first real run showed (2026-08-17)

Run against a **copy** of the store in a scratch directory, leaving `data/`
untouched. 4,474 postings seen, 4,418 exclusions logged, 59 distinct rules —
and the arithmetic reconciles with the printed funnel exactly.

The headline: **3,742 of the 3,801 location drops are one rule**, `locations:
city not on the list`, and 2,085 of those are a single source (`dsv`, which
lists globally). That is the ladder working, not a leak. The two cases worth
acting on are much smaller and were previously invisible inside the same total:

```
  3,742  locations: city not on the list
     54  locations: no location given
      5  locations: remote keyword overridden by a named city
      0  locations: conditional city, hybrid gate not configured
      0  hybrid: conditional city, description is not hybrid   (Layer 2)
```

The 54 "no location given" are not spread evenly — two sources account for
half of them (16 and 10; `python -m job_scraper.drops --show-drops --rule "no
location"` names them, and this file is public so they are not named here). A
source whose postings systematically have no location field is an extractor
gap, not a rejection, and every one of its jobs is being dropped sight-unseen.
Worth checking before WP8 touches the ladder.

`impactpool` returned a 522 on this run and contributed nothing, which is why
the intake was 4,474 rather than the ~8,000 of a healthy run (see the standing
note about that source's flaky 5xx). The exclusion counts below are therefore a
run without the largest aggregator in the config.

### For WP8

The log is the evidence WP8's prompt asks for, and the first pass over it
already argues with the ladder:

- **`title_keyword: 'engineer' (prefix)` fires 125 times**, three times the next
  keyword — worth confirming none of those are wanted before the scorer
  inherits the job.
- Short word-match keywords `'AI'` (24) and `'IT'` (15) are the shape most
  likely to be catching titles by accident; `--show-drops --rule "'AI'"` lists
  them.
- `1c-non-english` fired 26 times across five language codes including `'af'`
  (Afrikaans, 2) and `'et'` (Estonian, 1) — langdetect guessing on short
  snippets, exactly the unreliability WP8 cites as its reason for deleting the
  layer. There is now a record to check that claim against.
- 34 of the 59 rules fired 5 times or fewer. A keyword list where most entries
  earn almost nothing is the accretion argument in numbers.

---

## WP8c — Offline evaluation harness

Inserted before WP8, after WP8a. WP8a made every exclusion visible; this makes
the ladder *measurable*. WP8's prompt asks for a before/after diff of what each
version of the ladder keeps, and "keeps" is only half an answer without a
statement of what the owner actually wanted — otherwise a rule change that
drops 200 fewer jobs looks like an improvement whether or not any of the 200
were worth seeing.

```
think hard

Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP8c only.

I have labelled a gold set at data/curated/labels.csv: columns dedupe_key,
title, company, source_name, location, label, where label is review or
discard. Add it to .gitignore.

Build an offline evaluation harness so every future rule change is measured
rather than guessed:

- New module job_scraper/eval.py and a CLI entry point. It replays the filter
  ladder over the labelled rows without any network access, and reports
  precision, recall and F-beta with beta favouring recall, plus a confusion
  matrix, per layer and overall.
- Treat review as positives. A false negative is a review row that the ladder
  dropped; those are the expensive errors and the report should list every one
  of them by title, with the rule that killed it.
- Support comparing two rule configurations in one command: point it at two
  config directories and print a diff of what each keeps and drops, with the
  precision and recall delta.
- Add a regression test that pins current behaviour, so a later refactor that
  silently changes filtering fails the suite.

Then run it against the current configuration and give me the baseline numbers
and the full false-negative list. Do not change any filter rule in this
session.

Branch wp8c-eval-harness. Commit, do not push. Update the plan file.
```

### Result

280 tests pass (up from 246), `ruff check .` clean. New: `job_scraper/eval.py`,
`tests/test_eval.py`, `tests/fixtures/eval_labels.csv`,
`tests/fixtures/eval_config/`. Touched: `config_loader.py` (one
`default_labels_path`), `tests/test_capture_fixtures.py` (its secret scan now
walks the fixture tree, since fixtures now include a directory), `.gitignore`.
**No filter rule changed**, which was the point: a session that both moves the
ruler and measures with it has measured nothing.

**The harness calls the pipeline's own filter functions**, never copies of
them. `matches_rules`, `apply_combined_title_filter`,
`apply_non_english_text_filter` and `apply_language_filter` are imported and
run in `run_pipeline`'s order. A harness that reimplements the rules measures
the harness.

**What it does not replay, and says so in the report.** Layer 1d is review
history rather than a rule. Layer 2 needs a detail page, and the harness makes
no HTTP request — `test_evaluation_makes_no_network_connection` takes
`socket.socket` away for the duration of a full evaluation rather than
asserting the claim by inspection. So the reported recall is an upper bound:
some kept jobs would still meet Layer 2. Jobs admitted from a hybrid-gated
conditional city are counted as kept and flagged as provisional for the same
reason.

**The ladder order is duplicated from `pipeline.py`, deliberately.**
Extracting the ladder into something both callers share is WP8's business;
doing it here would mean changing filtering behaviour in the session meant to
measure it. `test_ladder_order_matches_the_pipeline` pins the order so the
duplication cannot drift unnoticed.

**langdetect is seeded** (`DetectorFactory.seed = 0`) for the duration of the
replay, in the eval process only. The live layer stays as non-deterministic as
it has always been — that non-determinism is one of WP8's arguments for
deleting it — but an eval whose numbers move on their own cannot pin anything.

**The gold set is read defensively** because it is hand-maintained: the
delimiter is sniffed (the owner's export is semicolon-separated), header names
match casefolded, `source_name` may be absent, and a label is either `review`
or `discard` or the row is reported rather than guessed at. A key labelled both
ways is excluded and named, not resolved — picking a winner would invent a
ground truth the owner never stated.

**The regression pin uses fixture data, not the real files.** `rules.json` and
`labels.csv` are gitignored personal data that change whenever the owner
changes their mind; a test pinned to them would be a tripwire for the wrong
thing. `tests/fixtures/eval_config/` and `tests/fixtures/eval_labels.csv` are
sized so every layer fires once or twice, and the test asserts the confusion
matrix, the per-layer counts and all three false negatives by hand.

### Baseline, current configuration (2026-08-18)

545 labelled rows → 514 usable: 19 duplicate rows collapsed (same key, same
label) and 6 keys excluded for carrying both labels. 74 review, 440 discard.

```
                    kept        dropped
  review            27             47
  discard           78            362

  precision  0.257     recall  0.365     F2  0.337

layer               reached  dropped   lost  drop prec  recall      F2
0-rules                 514      191     33      0.827   0.554   0.331
1a-title-keyword        323      175     14      0.920   0.365   0.304
1-seniority             148       37      0      1.000   0.365   0.332
1c-non-english          111        1      0      1.000   0.365   0.333
1b-language             110        5      0      1.000   0.365   0.337
```

Read these as a comparison instrument, not as a population estimate — see the
decisions log. What they say about WP8:

- **The recall problem is Layer 0, not the keyword ladder.** 33 of the 47 lost
  jobs are location drops: 22 `locations: city not on the list` and 11
  `locations: no location given`. The second group is WP8a's extractor-gap
  finding arriving again from the other direction — those postings were never
  judged on their merits, and a quarter of the "no location given" drops are
  jobs the owner wanted. That is worth a look **before** WP8 touches anything,
  and it is not a filter-rule change.
- **The keyword layer costs 14, concentrated in six keywords.**
  `'Specialist'` (4 of its 11 drops were wanted), `'Security'` (4 of 4),
  `'leader'` (2 of 5), `'SEA'` (2 of 2), `'owner'` (1 of 3), `'intern'` (1 of
  1). `'SEA'` as a whole-word match is the shape WP8a predicted would
  misfire — it catches "Baltic Sea" and "Air & Sea". `'Security'` dropped
  nothing the owner did not want to see.
- **The three layers WP8 proposes to delete cost nothing here.** Seniority
  dropped 37 with no loss; the non-English filter fired once and the
  language-speaker filter five times, all correct on this set. The case for
  deleting them is that they do not earn their complexity, not that they are
  losing jobs — this set gives no evidence they are.
- **Precision 0.257 is the scorer's problem, not the ladder's.** Of 105 kept,
  78 are labelled discard. No amount of keyword tightening fixes that without
  costing recall, which is the argument WP7 was built on.

The full false-negative listing, with titles and the rule that killed each one,
is what `python -m job_scraper.eval` prints. It is not reproduced here: this
file is public and those are real postings the owner is tracking.

### For WP8

Run `python -m job_scraper.eval` before touching a rule, keep the number, then
copy `job_scraper/config/` to a scratch directory, edit the copy, and run
`python -m job_scraper.eval --compare job_scraper/config /path/to/copy`. The
diff names every job that changed side and the rule that used to fire, and
prints the precision/recall/F2 delta. That is the before/after diff WP8's
prompt asks for, minus the risk of running the live pipeline twice.

---

## WP8d — Unresolvable locations

**Do this before WP8.** WP8c measured the ladder and the answer came back that
the recall problem is Layer 0, not the keyword list: 33 of 47 lost jobs are
location drops. Retiring keyword layers first would be tuning the smaller half
of the problem, and would move the baseline WP8 is supposed to be measured
against. See the three-causes entry in the decisions log for why this package
covers two of those causes and WP8e covers the third.

```
think hard

Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP8d only.

WP8c's baseline showed the ladder's recall problem is Layer 0, not the keyword
list: 33 of 47 lost jobs are location drops. Reading the false-negative listing
found three distinct causes, and this package fixes two of them. The third
(extractors that capture no location at all) is WP8e and is out of scope here.

The problem: matches_rules recognises only two states for a location field —
empty, or naming a specific city. A field that says something which is not a
place ("2 locations", "Home base - EMEA", a bare region) falls into the second
state, matches nothing on the list, and the job dies at Layer 0 having never
been read.

Treat these the same way conditional hybrid cities are already treated:

1. In filtering.py, recognise a third state: a location field that is present
   but names no place. At minimum it must cover the "N locations" shape and
   region-only values. Note _GENERIC_LOCATION_TOKENS has "home based" but not
   "home base", which is why "Home base - EMEA" reads as a city today. Do NOT
   fix that by splitting location fields on dashes — real city names contain
   them.
2. Such a job passes Layer 0 with a pending reason, exactly as
   _HYBRID_PENDING_REASON works, rather than being dropped.
3. In experience_filter.py, settle it at Layer 2 against the fetched
   description, mirroring _resolve_hybrid. Fail closed, as _resolve_hybrid
   does: no listed location found in the description means the job is dropped.
4. Give it its own drop rule string and its own experience_level value so the
   drop log tells it apart from a genuine wrong-city rejection. Per the WP8a
   decision, add new rule strings freely but do not reword existing ones.
5. Make sure a resolved job is not re-fetched on every subsequent run. Hybrid
   solved this with the hybrid_confirmed column in WP5; WP6's stored
   descriptions may already cover part of it. Work out which, say which, and do
   not add a column that duplicates one that exists.
6. Update job_scraper/eval.py so the new pending state is flagged as
   provisional in the report, the way hybrid-pending jobs already are.
   Otherwise the harness will count these as kept and overstate the recall
   gain, since it cannot replay Layer 2.

What counts as "not a place" should be configurable where it sensibly can be
(config over code), but propose the shape before adding config keys.

Do not add a filter layer. This is the existing location check learning a third
answer, not a sixth pass.

Measure it: run python -m job_scraper.eval before and after and put both sets of
numbers in the plan file, along with how many jobs newly reach Layer 2 and what
that costs in detail fetches per run.

Branch wp8d-unresolvable-locations. Commit, do not push. Update the plan file.
```

### Result

306 tests pass (up from 281), `ruff check .` clean. Touched: `filtering.py`,
`experience_filter.py`, `pipeline.py`, `run.py`, `eval.py`, `config/rules.json`
and `rules.example.json`, `README.md`, and five test files.

**The third state lives in `matches_rules`, not in a new layer.** The location
branch had two answers and now has three: matched, deferred, dropped. The new
`elif` sits after the conditional-city branch and before the drop, so a
hybrid-gated city is still a hybrid-gated city and nothing that used to match
now defers.

**"Names no place" is decided by striking terms out, not by splitting.** The
prompt forbids splitting on dashes and it is right to: `Aix-en-Provence` is a
city. `_location_names_no_place` instead removes every term that names no
place — the remote keywords, `_GENERIC_LOCATION_TOKENS`, the configured
`non_place_locations`, and the `"N locations"` shape — from each segment and
asks whether a *letter* survives. `home base - emea` empties out;
`barcelona, spain` keeps Barcelona, which is the case a plain "contains a
region word" test gets wrong.

**The home-base wording is code, the region after it is config.** Both
spellings now sit in `_GENERIC_LOCATION_TOKENS`, which is struck out by plain
substring, so the tuple is sorted longest-first — with `"home base"` tried
first, `"home based"` would leave a stray `"d"` behind and read as a place.
Configured terms are matched **whole-word**, so `"home base"` in `rules.json`
would *not* have covered `"home based"`; an earlier draft of this entry claimed
it did, and that was wrong. Note what this does and does not fix: the brief's
`"Home base - EMEA"` needs both halves — the wording from code and `EMEA` from
`non_place_locations` — because no code list can enumerate the world's regions.
Bare `"Home base"` and `"Home based"` are handled with no config at all.

**Two guards on when it is worth deferring at all**, both added after review
and neither reachable with the owner's current config:

- **Nothing is deferred when `locations` is empty.** Layer 2 settles a deferred
  job by searching the description for a listed location; with none configured
  it can never settle one, so every deferred job would come back
  `unverified` — dropped for the run, never stored, and re-fetched on every run
  after it, for ever. A `conditional_locations`-only configuration is enough to
  hit that, so `matches_rules` now defers only what can actually be settled.
- **A field made only of remote keywords is "remote", not "unresolvable".**
  Under `match_in: title_and_description` the location field is part of the
  haystack, so `remote_ok` settles a bare `"Remote"` before the new branch is
  reached. Under `title_only` it is not, and without this guard every
  `"Remote"`-tagged posting on an aggregator that tags all of them would have
  bought a detail fetch. The location rules already have an answer for that
  shape; the new state must not re-label it.

**Deliberately not the inverse of `_location_names_specific_city`.** That
function answers a different question — may a remote tag stand? — and teaching
it about regions would quietly admit `Remote | Berlin, EMEA` as a genuine
anywhere role. Two questions, two classifiers, and only the new one moved.

**Config shape (owner's decision, 2026-08-19).** One new `rules.json` key,
`non_place_locations`, seeded with regions *and* bare country names. It extends
the built-in tokens rather than replacing them, so a `rules.json` without the
key behaves as it did before, and the `"N locations"` / `"Multiple locations"`
shapes stay a code regex because no list can enumerate every N. Documented in
`README.md` and `rules.example.json`. Countries were measured before being
chosen: on the gold set they defer 19 more jobs to buy at most 1 wanted one,
and the owner took that trade knowingly.

**Item 5: no new column, and none was needed.** Worked out rather than assumed.
`hybrid_confirmed` exists for one reason — `_is_hybrid_pending` forces a
re-fetch of stored conditional jobs, which exists so rows written *before* WP5
added the column get re-checked exactly once. A state introduced today has no
legacy population, so nothing forces a re-fetch and `_is_stored` alone is the
whole mechanism. Both halves are pinned end to end:
a resolved job is stored and skipped
(`test_resolved_unresolvable_location_is_not_refetched_next_run`), and a job
dropped by the fail-closed path is stored `'rejected'` and caught by Layer 1d
(`test_unresolvable_location_dropped_at_layer_2_is_not_refetched_either`).
WP6's `description_text` covers the rest: the page that settled the job is
persisted either way, so nothing re-reads it.

**`hybrid_unverified` became `UNVERIFIED_KEY = "unverified_this_run"`.** Both
deferred states fail closed, so both can be dropped by a network hiccup, and
neither may be written as a permanent `'rejected'`. That is one fact about the
run, not two facts about two filters, so it is one key rather than a second
flag `pipeline.py` would have to remember to check. The WP6 entry above still
describes it under its old name; this is the rename.

**One fetch answers both questions.** `_fetch_and_analyze` returns a
`_DetailSignals` record instead of a six-tuple — a seventh positional element
is where a tuple stops being readable — and the hybrid gate and the location
search both read the text already in hand.

### Before and after (`python -m job_scraper.eval`, 2026-08-19)

Same 514-row gold set, same config directory, only `filtering.py` and the new
`non_place_locations` key differ.

```
                     before            after
  review    kept       27               32
            dropped    47               42
  discard   kept       78               84
            dropped   362              356

  precision          0.257            0.276
  recall             0.365            0.432
  F2                 0.337            0.388

layer               reached  dropped  lost      reached  dropped  lost
0-rules                 514      191    33          514      156    23
1a-title-keyword        323      175    14          358      195    19
1-seniority             148       37     0          163       41     0
1c-non-english          111        1     0          122        1     0
1b-language             110        5     0          121        5     0
```

**Read the recall gain as a ceiling, not a result, and the harness now says so
in the report.** All five newly-kept `review` jobs are the five flagged
`pending_location`: Layer 2 has not run on them, it fails closed, and this
harness cannot replay it. If Layer 2 confirms none of them, recall returns to
0.365 exactly. What is *not* provisional is the 35 jobs that stopped dying at
Layer 0 unread, which was the point.

Two other movements are worth naming before WP8 reads these numbers:

- **`locations: city not on the list` fell from 22 lost of 145 drops to 12 of
  110.** `locations: no location given` is untouched at 11 of 46 — that is
  WP8e's population, and this package deliberately did not launder it into the
  new state.
- **The keyword layer's cost rose from 14 to 19.** Nothing about it changed:
  five jobs that Layer 0 used to kill now reach it and die there instead. The
  loss did not appear, it moved, and it is now attributed to the rule that
  actually fires. WP8's baseline should be re-taken from the "after" column.

### What it costs in detail fetches

Measured offline against run 8's drop log (8,109 exclusions, 7,387 at Layer 0),
by replaying the new Layer 0 over the rows it excluded — no network, and
`data/` untouched.

```
  603  newly pass Layer 0, out of 7,387 Layer 0 drops (560 of the 6,846 'city
       not on the list', 43 of the 487 'remote keyword overridden by a named
       city'; none of the 54 'no location given')
 -361  then dropped by 1a title keyword
  -52  then dropped by 1 seniority
   -7  then dropped by 1c non-English and 1b language
  183  reach Layer 2 = 183 extra detail fetches
```

**183 extra fetches on the first run after this change, then close to none.**
The layers between Layer 0 and Layer 2 absorb 70% of the new intake for free,
and everything Layer 2 then drops is stored `'rejected'`, so Layer 1d skips it
on every later run. The recurring cost is newly posted jobs with an
unresolvable location, not 183 a run.

The one-off is not evenly spread: 103 of the 183 are `impactpool`, 33
`canonical`, 22 `jpal`. That is a real politeness cost concentrated on one
aggregator (CLAUDE.md priority 3), and it lands in a single run at
`_DETAIL_WORKERS = 10`. If it matters, run it once at a lower worker count and
the steady state takes care of itself.

### What actually happened (run 9, 2026-08-20)

The projection above did not stay a projection. The owner's own scrape at
06:16 UTC ran while this package was on disk, so **run 9 executed the change
against the live store** — unplanned, and the first real evidence either way.
It also predates the two guards and the `"home base"` token above, none of
which alter a judgement it made (with `locations` non-empty and `match_in`
set to `title_and_description`, both guards are no-ops, and the token only ever
defers *more*).

```
  161  dropped by 'location: unresolvable field, description names no listed
       place', all stored 'rejected' and skipped from now on
    0  dropped as 'could not read the description' — no unverified drops
    2  kept, and sitting in the table for review
```

So ~163 jobs newly reached Layer 2 against the 183 projected, and the yield of
the whole package on one run is **two postings the owner can now see that Layer
0 would have destroyed unread**. That is the trade this package was always
making, now with a number on both sides of it: fail-closed means most of the
603 admitted at Layer 0 still end up rejected — the point is that they are
rejected *after* being read.

**A third shape came along with the two the prompt named.** The 43 rows above
are `Remote | <country>` and `Remote | Multiple locations` — a remote tag whose
companion segment is not a city either. The old code read the country as a duty
station overriding the remote tag; it now defers like any other unresolvable
field. Same root cause, so it belongs here, but it was not in the brief and it
is a fifth of the new Layer 0 intake.

### For WP8e

`python -m job_scraper.drops --rule 'locations: no location given'` is now a
clean signal: the placeholder and region cases that used to land in the same
bucket are gone, and the 54 `no location given` rows are genuinely empty
fields — the same 54 on run 8 and run 9, and the same 54 WP8a's first real run
reported, untouched by this package.

---

## WP8e — Extractor location gaps

The third cause behind WP8c's location losses, and the one WP8d deliberately
did not launder into its new deferred state: sources whose extractor captures
no location at all, so a real posting is indistinguishable from a genuinely
location-less one and dies as `locations: no location given`. WP8a found the
same gap from the drop-log side.

The population is now a clean signal and a stable one — the same 54 exclusions
across 11 sources on runs 8 and 9, unchanged by WP8d, because the placeholder
and region cases that used to share this bucket have moved out of it. Two
sources account for nearly half. Get the current breakdown with:

```
python -m job_scraper.drops --rule 'locations: no location given' --show-drops
```

It is not reproduced here — this file is public and those are real postings.

**There are two shapes in that list, and they need different fixes.** Most are
a posting whose location is on the page but never captured, sometimes visible
in the title suffix. But at least one source is emitting a row that is not a
job at all: a department heading scraped as a posting, which is the Teamtailor
quirk WP2 pinned and never fixed. That one is not a missing field — fixing it
deletes a phantom row rather than recovering a real posting, and it should be
counted as noise removed, not recall gained.

```
think

Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP8e only.

Some extractors never populate the location field, so a real posting is
indistinguishable from a genuinely location-less one and Layer 0 drops it as
'locations: no location given'. WP8d fixed the two neighbouring causes and left
this one alone on purpose; this package is the extractor work.

Start from the evidence, not from a guess:

  python -m job_scraper.drops --rule 'locations: no location given' --show-drops

That is 54 exclusions across 11 sources, stable across the last two runs. Work
down it by source, biggest first.

Expect two shapes and treat them differently:
- The page names a location and the extractor does not capture it — sometimes
  it is sitting in the title suffix. Fix the selector.
- The extractor emitted a row that is not a posting at all (a department
  heading). tests/test_extractors_golden.py pins one such row for the
  Teamtailor source, documented under WP2. Fixing it changes a golden file, so
  update the pin deliberately and say so; that is the fix landing, not a
  regression.

Rules for this package:

- Work against the saved fixtures in tests/fixtures/. Do NOT scrape live sites
  to investigate, and do not add fixtures by hand. If a source has no fixture
  and you need one, say so and stop rather than fetching.
- Every extractor you change needs its golden-file assertion updated in the
  same commit, with the old and new job count stated.
- Do not add a fallback that parses the location out of the title inside
  storage or the filter layers. If a location lives in the title on a given
  site, that site's extractor is where it gets split out.
- The eval harness cannot score this work: labels.csv holds the location the
  extractor produced at labelling time, so eval.py will replay the old empty
  value and report nothing gained. Do not treat a flat eval number as failure,
  and do not "fix" it by editing labels.csv.

Report at the end, per source: rows affected, whether the location was
recoverable, and — for anything you fixed — whether those postings look like
jobs the owner would actually want. A source whose postings are all outside the
configured locations is a correct drop arriving by the wrong route; fixing its
extractor is still right, but it is not a recall win and the plan file should
not claim it is.

Branch wp8e-extractor-locations. Commit, do not push. Update the plan file.
```

**After this package**, refresh the `location` column in `data/curated/labels.csv`
from the store for the affected `dedupe_key` rows, so future eval runs replay
the fixed value. The review/discard labels themselves stay valid — they are a
judgement about the job, not about the location string.

---

## WP8 — Trim the ladder, prune the keywords

**This prompt was rewritten on 2026-08-20.** The original opened "with LLM
scoring in place, the five-layer regex ladder can shrink" and justified every
deletion with "subsumed by the scorer". That premise is dead: WP7 ended with
scoring off by default and the WP7 decision-log entry says it stays off until
the owner opens API billing. Deleting the keyword list today would hand its job
to nothing at all. Three other instructions had also gone stale — see the
decisions log. What survives is a smaller, honest package: delete two layers
that genuinely earn nothing, prune the keyword list with evidence instead of
deleting it, and fix the seniority list if the evidence still supports it.

The baseline to measure against is the **"after" column** of WP8d's before/after
table, not WP8c's original numbers. WP8d moved five losses from Layer 0 into the
keyword layer without changing anything about the keyword layer; its cost reads
14 in the old numbers and 19 in the current ones.

```
think hard

Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP8 only. Read the WP8
preamble in the plan file before this prompt: an earlier version of it assumed
the LLM scorer was running, and it is not.

Take the baseline first, and do not run the live pipeline to get it:

  python -m job_scraper.eval

WP8c built that harness for exactly this package. Compare configurations with
--compare against a copy of job_scraper/config/ rather than scraping twice.

1. DELETE apply_non_english_text_filter (Layer 1c) and apply_language_filter
   (Layer 1b), with their config keys, tests and drop-log layer constants.

   Be honest about why in the plan file. The argument is NOT that the scorer
   covers them, because it is switched off. It is that they are two layers of
   complexity that between them fire six times on a 514-row gold set, cost zero
   wanted jobs, and one of them (non-English) is already inert on the
   refilter_stored_jobs path because stored rows carry no raw_snippet, while
   langdetect is nondeterministic enough that the harness has to seed it.
   Deleting them keeps roughly six more unwanted jobs and loses nothing.
   Measure the real number with --compare and report it. If it comes back
   materially worse than that, stop and say so rather than pressing on.

2. DO NOT delete config/title_exclude_keywords.csv. Nothing subsumes it with
   scoring off. Prune it instead, with evidence:

   - The current numbers say the keyword layer costs 19 wanted jobs. Use
     --compare to measure removing individual keywords, and report the recall
     and precision delta per keyword rather than in one lump.
   - WP8c named the worst offenders and one of them, 'SEA' as a whole-word
     match, catches phrases like "Baltic Sea" and "Air & Sea". Re-derive that
     list from the current baseline rather than trusting the WP8c figures.
   - Propose the pruned list and the measured cost of each removal. Removing a
     keyword that only ever dropped unwanted jobs is not an improvement; it is
     churn.

3. The seniority list still contains 'Lead' and 'Architect', and the original
   prompt called them false positives ("Lead Generation Analyst", junior
   architect roles). Check that against the gold set before acting: the
   seniority layer currently drops 41 jobs and loses zero wanted ones, so the
   harness gives no evidence the problem is real on this data. If you cannot
   demonstrate the false positive, say so and leave the list alone. If you can,
   propose narrowed patterns; do not just delete entries.

4. Performance, corrected from the original prompt:
   - build_hybrid_pattern is ALREADY compiled once and passed down; matches_rules
     takes it as a parameter. Nothing to do. Do not "fix" it again.
   - _build_title_keyword_pattern is called once per batch inside
     apply_combined_title_filter, not once per job. Decide whether hoisting it
     to setup is worth the plumbing, and if it is not, say so and move on. Do
     not restructure the call sites for a compile that happens twice a run.

Keep, as before: the location rules, the review statuses, and the numeric
experience extraction. Do not touch WP8d's deferred-location state.

Branch wp8-trim-ladder. Commit, do not push. Update the plan file with the
before/after numbers from --compare, per change rather than in one lump.
```

---

## WP8b — README reconciliation

**Do this after WP8, not before.** WP8 deletes two filter layers and prunes
the keyword CSV, which rewrites the "How it works" layer table and the
language-filter description. The keyword CSV itself stays — the rewritten WP8
no longer deletes it — so that input-file section needs updating rather than
removing. Doing the README first means doing it twice.

Note that WP8d also changed the README's location story: there is now a third
Layer 0 answer and a `non_place_locations` key, both already documented by that
package, so check rather than assume that section is stale.

Nothing here is dangerous — every stale passage is misleading but inert, and no
instruction in the README would lose data if followed. It costs confusion, not
history, which is why it does not jump the queue.

```
Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP8b only.

README.md still describes the pre-WP5 CSV store as though it were live. Bring
it back in line with the code. Docs only: change no behaviour, and if you find
a real bug, note it at the end rather than fixing it.

Known drift, found during WP8a — verify each against the code rather than
trusting this list, and look for more:

- "Postings already stored in jobs.csv skip layer 2" — it is the SQLite store.
- "the provisional marker isn't stored in jobs.csv" — false since WP5 added
  the hybrid_confirmed column, which is why that re-fetch no longer happens.
- A `### data/jobs.csv` output-file section for a file nothing writes any
  more. It is a frozen pre-cutover archive; say so, or drop the section.
- The retrofilter description: "re-applies the current filters to the existing
  jobs.csv".
- The layout table calls job_scraper/storage/ "CSV store (dedupe, schema
  migration) and the xlsx writer". csv_store.py was deleted in WP5.
- data/curated/blocklist.csv is presented as a live input. WP5b replaced that
  routine; it is now a one-off import, and the review commands are the flow.
- The options table under "Running" omits --delist-after,
  --allow-empty-delist, --score and --show-all.
- Nothing documents `python -m job_scraper.drops` or `python -m job_scraper.eval`
  (WP8a and WP8c). Both are read-only, offline commands the owner will forget
  exist if the README never names them.

Check the whole file against the current CLI while you are in there: every
flag documented should exist, and `python -m job_scraper.run --help` is the
authority. Do not invent example output — if a block needs new numbers, say
where they came from or mark it illustrative.

Branch wp8b-readme. Commit, do not push. Update the plan file.
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
