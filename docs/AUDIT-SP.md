# Sources plan audit

An independent read of the repository on `main` at commit `b467f67`, at the
close of the sources plan (every SP package, SP17b included). Written, like
`docs/AUDIT.md`, for a reader who does not read code. Dated 2026-10-28.

**This audit changed nothing.** No file was edited apart from this one, which is
the only file added, on the branch `sp-final-audit`. The store was opened
read-only. The one live scrape was a `--dry-run`, and before and after it the
size and modification time of `data/jobs.sqlite3`, `data/jobs.xlsx`,
`data/jobs_sources.csv` and all 29 files in `data/curated/` were recorded: all 32
are identical. The HTTP cache grew from 34.2 MB to 35.5 MB, as a run's cache
does. Three throwaway tests were written in a temporary directory outside the
repository to reproduce what is claimed below; none was committed.

## How it was checked

| What | Result |
|---|---|
| `git status` on `main` at the start | clean, up to date with `origin/main` |
| `pytest` | **1,617 passed**, 0 failed, 0 skipped, 0 expected failures, 20.8 seconds |
| `ruff check .` | clean |
| `ruff format --check .` | 118 files, all clean |
| `python -m job_scraper.run --help` | exits 0 |
| `python -m job_scraper.eval` | precision **0.353**, recall **0.836**, F2 0.656, 12 false negatives, 1,686 labelled jobs (73 `review`). The same as the last baseline quoted (SP10: 0.353, 0.836, 12) |
| `--help` on every maintenance command the README lists | all exit 0: `drops`, `eval`, `review`, `tools.blocklist_all`, `tools.import_blocklist`, `tools.retrofilter`, `tools.source_yield`, `tools.sources` and its 5 subcommands and 5 `candidate` subcommands, and the four Python scripts. `scrape_and_blocklist.sh` has no `--help` (it would scrape), so it was read instead |
| One live run: `python -m job_scraper.run --dry-run` | exit 0, 5 minutes 8 seconds, 56 of 56 sources, 7,963 postings, nothing written |
| The store | opened with `file:data/jobs.sqlite3?mode=ro` only: 2,312 jobs, 41 runs |
| Dead code | an AST scan of every top-level name, then grep for callers (no tool such as vulture is installed) |
| Public repo safety | `git ls-files`, `git check-ignore`, and `git grep` against the names and boards in the two curated lists (counts only below) |

## The short version

Every package's result holds up against the code: the modules, tests, registry
lines and README sentences they promised are there, and the September audit's
four findings are closed. Nothing loses data today, the dry run wrote nothing,
and no extractor failed. The three things most worth the owner's time are
small: a source (`fjallraven`) has returned nothing for ten runs while the
warning that names it scrolls past, as three did before September; the golden
tests only look at each saved page's first posting, so a reader that breaks
every later row still passes; and the re-filter pass would permanently reject
rows a run keeps if `include_keywords` were ever set. The rest is drift in the
paper trail: the plan's own Status table, the README and `CLAUDE.md` have each
fallen a few packages behind the code.

---

## 1. Baseline

All gates are green (table above). The test count matches the README's 1,617
exactly. The eval figures match the last recorded baseline (SP10's, taken after
SP4h); SP4h itself quoted 0.355 and F2 0.657 on the gold set as it then stood,
and the set has gained rows since. The maintenance commands all read their
arguments before acting, as the README promises.

## 2. Plan against code

Each row checks the package's Result claim against the code and tests, not the
plan's own account.

| Package | Claim | Verified | Evidence |
|---|---|---|---|
| SP0 | `data/curated/` backed up, then a private git repository inside it | yes | `data/curated/.git` exists: 24 commits since 2026-09-15, tracking both YAML lists, `labels.csv` and `blocklist.csv` |
| SP0b | Decisions log split out; refactor plan archived; `CLAUDE.md` and README updated | partly | `docs/DECISIONS.md` exists and is read; `CLAUDE.md`'s architecture block has since drifted (F10) |
| SP1 | YAML lists, writer CLI, append-only, atomic, backed up | yes | `curated.py` `_write_atomically`, `backup_path_for`; `tools/sources.py`; YAML files present, CSV and xlsx gone |
| SP2 | Recovery script, 6 candidates added, `record-check`, `--undated` | yes | `scripts/recover_skipped_sources.py`, `tests/test_recover_skipped_sources.py`, `candidate record-check --help` |
| SP2b | `recheck` and `activate` | yes | `curated.recheck`, `curated.activate`, `tests/test_candidate_lifecycle.py` |
| SP3 | `sources probe`, seven rungs, writes nothing | yes | `probe.py`; `RobotsPolicy.explain`; `http.fetch_page`; `tests/test_source_probe.py` |
| SP3b | Workday walks the whole board; detail URLs unchanged | yes | `workday.py` JSON walk with `pagination.reconcile`; `path` golden at 64 |
| SP3c | airbus narrowed by a facet that must be shown applied | yes | `CappedTotalError`, `FacetNotAppliedError`, `_check_applied`; airbus read 9 rows in run 41 |
| SP4 | Captures polite; five ATS readers pinned; Personio fails loudly | yes | `capture_fixtures.py` opens `polite_fetching`; `test_personio_fails_loudly_on_a_malformed_feed`; `test_capture_does_not_sanitise_xml` |
| SP4b | Audit with strict xfails as targets | yes | `tests/test_filter_audit.py`; all its xfails have turned green, none left |
| SP4c | Layer 5 says "unreadable"; 500-character threshold | yes | `MIN_READABLE_CHARS = 500`, `unchecked (page unreadable)` in `experience_filter.py` |
| SP4d | Readers supply descriptions; Ashby on its posting API | yes | `supplied_description`; `ashby.py` reads the API |
| SP4e | Years read as a requirement, not the smallest number | yes | `_read_years_requirement`; its test classes |
| SP4f | Remote and regional admission; empty field deferred | yes | `filtering._remote_admission`, `build_remote_region_pattern`; `remote_regions` in `rules.example.json` |
| SP4g | Re-filter pass re-reads years and PhD; dry run; own run kind | yes | `judge_experience` shared; `set_experience_levels`; `runs.kind`; `drops --refilter` |
| SP4h | `contains` match type; `donor` removed | yes | `contains` in `filtering.py`; seven `contains` rows in `title_exclude_keywords.csv` |
| SP5 | Batch 1: three sources live, the rest reported | yes, by design partial | three SmartRecruiters registry lines and fixtures; later batches never run (Status row still "batch 1") |
| SP6 | Every reader has a saved page; three bugs fixed | yes | 25 readers, 25 with a fixture case; `coefficient.py` and `oatly.py` gone |
| SP7 | Failed, refused, tombstoned, one-page warnings | yes | `RunSummary` fields; `page_sizes.ONE_PAGE_RUNS = 5`; `curated.tombstoned_sources`; `tests/test_source_warnings.py` |
| SP8 | Teamtailor image cards, row guard, probe check; four follow-ups | yes | `teamtailor._check_row`, `rowcheck.title_read_as_location` (also used by `probe.py`); `exact_acronyms`; `norrsken.py` gone |
| SP9 | Accordion reader | yes | `extractors/pure_earth.py`, registry line, fixture, raise tests |
| SP10 | Generic Jobylon feed reader | yes | `extractors/jobylon.py`, registry line, fixture |
| SP11 | No reader, by the owner's choice | yes | no `csod.py`, no fixture, no Platform entry |
| SP12 | Read-only yield report | yes | `tools/source_yield.py` opens the store with `mode=ro` |
| SP13 | Workable walks its board | yes | `workable.py` walk; `test_workable_reads_the_eleventh_posting_from_the_second_page`; `INVENTED_PAGES`; `tests/fixtures/README.md` |
| SP14 | Lead-ins, the tie before a figure, a colleague's years | yes | `TestSP14Findings` and three further classes in `tests/test_experience_filter.py` |
| SP15 | "Remote work to some extent" counts as hybrid; 6 rows revived | yes (code) | `tests/test_hybrid_wording.py`; `test_a_revived_hybrid_row_is_neither_refetched_nor_rejected_again`. What became of the 6 rows is a question (Q1) |
| SP16 | Generic HiBob reader; GET headers | yes | `extractors/hibob.py`, Platform entry in `probe.py`, `headers=` in `http.py`, localhost test |
| SP17 | Facet read from the configured URL; every row proves the country | yes | `successfactors_html._facets_of`, `_check_country`, `FacetNotAppliedError` |
| SP17b | Second dsv entry; old page kept | yes | `dsv_denmark` registry line and test; `tests/fixtures/dsv.unfiltered.html` |

**The plan's own bookkeeping** (all in `docs/SOURCES-PLAN.md`):

- The header sentence (line 3) lists the done packages and leaves out **SP7 and
  SP14**, both done. SP5 is partial by design and SP17b is not named.
- The Status table's **SP17 row says "not started"** (line 120) though its
  result is written, and **SP17b has no row** at all.
- **SP5 is still at batch 1.** No second batch was run; whether one is wanted is
  the owner's call.
- SP14's result (line 5455) still says "SP13 is still the Workable walk, not
  started"; SP13 landed the next day.
- "What is still unknown" (line 5959) lists three open questions, all three
  answered (by SP3's probe, SP2 and SP0).
- **Unticked to-dos, sorted:**
  - *Still the owner's:* Time Machine (SP0); is `labels.csv` current enough
    (SP4b; it was last written on 2 October and the sources added since have no
    labels); SP12's type-B decisions and C bug reports (OP4 waits on them); a
    real Workable board past ten postings (SP13, optional); SP14's three
    rejected rows to revive or not; SP5's recurring items (names in chat,
    approvals, refreshing the curated mirror) for any future batch.
  - *Done in effect, box not ticked:* SP0b's two confirmations; SP3b's "run at a
    civilised hour"; SP3c's "check airbus in the next run" (airbus reads its
    country every run); SP6's oatly row and coefficient row (the store holds no
    `new` row for either source); SP8 to SP11's three items (each session was
    given its URL and each decision is recorded); SP15's "look at the 6 revived
    rows" (they were all `rejected` before the next run; see Q1); SP17's
    "expect a -99% warning on the next real run" (run 41 on 27 October was that
    run: dsv 2,226 to 19).
  - *Stale:* SP3b's "if the detail URLs cannot be kept identical" (they were);
    SP4b's label request for a retired source and its "treat these rows as not
    experience-checked" (SP4d fixed them); SP4e's optional look at five texts
    and SP4f's optional rejections (both overtaken by SP4g); SP6's "pick two or
    three each time" and "keep the tally" (every reader is covered).
- `docs/OPTIONAL-PLAN.md`: the Status table and workaround list are accurate.
  OP1 to OP3 are not started, OP4 has no prompt. OP2's one to-do is ticked;
  OP3's is a stop-and-ask inside the package, so it is not overdue.

## 3. Dead weight

**Code that nothing in the program calls** (callers counted across
`job_scraper/`, `scripts/` and `tests/`):

| Name | Callers | Note |
|---|---|---|
| `experience_filter.apply_title_filter` (line 173) | tests only (7 references) | superseded by `apply_combined_title_filter`, which the run, the re-filter pass and eval all use |
| `filtering.apply_title_keyword_filter` (line 152) | tests only (16) | the same |
| `urlutil.same_board` (line 129) | tests only (10) | a one-line wrapper over `board_identity` |
| `capture_fixtures.single_response_fetch` (line 156) | tests only (5) | a test helper living in a script; harmless |
| `curated._REQUIRED_EXCLUDED` (line 54) | none | defined, never used; `tools/sources.py` spells the same tuple out three times instead (lines 351, 428 and the exclude path). Two ways of saying one thing, which `CLAUDE.md` warns about |
| `job_scraper.__version__` | none | conventional; leave |

No CLI flag is unread. Every key in `rules.example.json` is read by the code
(the four `_comment` keys are comments), and `rules.json` has no key the
example lacks. `sources.example.yaml` uses four of the five keys the code reads;
the fifth, `ignore_robots`, is explained in its header.

**Scripts:**

| Script | Status | Recorded fate |
|---|---|---|
| `recover_skipped_sources.py` | finished one-shot (SP2) | README calls it a one-off; no retirement recorded. Its tests keep it working |
| `migrate_curated_to_yaml.py` | finished one-shot; the owner ran it on 15 September | the owner's (`CLAUDE.md`). `curated.py` still carries the refusal machinery for unmigrated files (`LEGACY_FILES`, `require_migrated`), and `tests/fixtures/curated/` (the pre-SP1 `.xlsx` and `.csv`) exists only for this script's tests |
| `refresh_label_locations.py` | live: rerun after an extractor fix | DECISIONS line 176. Missing from the README's Layout table (F9) |
| `scrape_and_blocklist.sh` | retired, kept | DECISIONS line 486 (CU3): stays until the owner says otherwise. Its comment still mentions `langdetect`, removed in WP8 |

**Tests and fixtures.** All 87 fixture files are loaded by a test. No test
covers deleted behaviour except the two superseded title filters above. No test
is skipped or expected to fail. `tests/fixtures/curated/` is needed exactly as
long as the migration script is kept.

**Extractors.** All 25 reader modules are in the registry, and all 56 registry
entries are in `sources.yaml` and were reached by run 41 and by the dry run.
Paginated readers share `pagination.py`'s exceptions and `reconcile`; each
writes its own message, which is the module's stated design. `rowcheck.py` is
shared by two readers and the probe. No meaningful duplication found.

**Requirements.** Every third-party import is listed. The one direct import not
listed, `urllib3` in `http.py`, is guarded by `try/except ImportError` and
arrives with `requests`; nothing to do.

**Local leftovers** (untracked or ignored; counts only):

- 19 `.bak` files in `data/curated/`: 16 of the candidates list (15 September
  to 27 October), 1 of the tombstone (2 October), 2 of `labels.csv` (1 and 2
  October). The prompt expected 17; the extra two are the gold set's. Every
  YAML write since 15 September is also a commit in the curated repository, so
  the 17 YAML backups are covered by its history; the two `labels.csv` ones may
  not be, because the label script does not commit.
- Compiled files: 9 `.pyc` for Python 3.10 (modules that still exist), 5 for
  extractors deleted since September, 1 for a temporary test file that no
  longer exists, and `scripts/__pycache__` (3 files).
- 4 `.DS_Store` files.
- `data/jobs_sources.csv` is still written by every real run (pipeline line
  822) and **nothing reads it**. Its README purpose, spotting a source that has
  quietly started failing, is now served by the Failed sources block (SP7).
- The curated repository has one uncommitted change (`labels.csv`).
- `.claude/settings.local.json` (gitignored) still allows two commands on the
  deleted `storage/csv_store.py`.

**Branches.** All 40 local branches other than `main` are merged into `main`,
and all 80 branches on `origin` are merged into `origin/main`. None holds
unmerged work.

## 4. New bugs and regressions

Read in full: `pipeline.py`, `storage/db.py` (store, delisting), `tools/retrofilter.py`,
and the changed parts of `experience_filter.py`, `filtering.py`, `http.py`,
`curated.py`, `tools/sources.py`, `pagination.py`, `rowcheck.py`, `workable.py`,
`successfactors_html.py` and `sida.py`; the rest of the changed readers by their
guards and `except` clauses.

**Never lose data.**

- *Delisting after a failed, short or mis-faceted fetch:* still impossible. A
  reader that raises is left out of the delisting keys (pipeline line 705 to
  712). `ShortWalkError` is raised by every paginated reader that knows its
  total, and `FacetNotAppliedError` (both Workday's and SuccessFactors') is a
  `ShortWalkError`, so it takes the same path. A zero-row scrape is logged as an
  ERROR and delists nothing unless `--allow-empty-delist` is passed (line 730
  to 743). Delisting needs two consecutive misses on successful scrapes, and
  only `new` and `seen` rows ever flip (db line 467 onwards). Nothing deletes a
  job row: the only `DELETE`s are the drop-log pruning and the export row map.
- *Does `--dry-run` write nothing?* To the real store and files, yes: measured.
  But not strictly: opening the store applies any missing schema columns, and
  those statements commit even in a dry run (F5). Harmless on today's store.
- *One gap, latent:* the re-filter pass at the end of every run can reject a
  row a run kept when `include_keywords` is set, because the store does not keep
  the department and snippet that the run matched on (F1). Today
  `include_keywords` is empty.

**Fail loudly.**

- Ten readers have no `raise` of their own (`against_malaria`, `asana`,
  `bearingpoint`, `breezy`, `gfi_europe`, `giving_what_we_can`, `greenhouse`,
  `jobsinlund`, `lever`, `mammut`). A changed page there yields `[]`, which the
  pipeline treats as the loud failure it is: an ERROR line and the Empty
  sources block, and no delisting (CU2's design). A *partly* changed page that
  still yields some rows is caught only by the source-health warning, at half.
- The broad `except` clauses are all deliberate and recorded: Layer 5's fetch
  failure keeps the job and marks it unchecked; an unreadable robots.txt allows
  the crawl (DECISIONS line 399); render-pool teardown; the probe and capture
  script report rather than raise.
- `jobsinlund` still writes "Lund" when the API gives no city (SP4b noticed it,
  out of scope then). An invented location is a quiet wrong answer (F4).
- The empty-source warning works, and is being ignored (F2).

**Atomic writes.** Every write under `data/` goes temp file then
`os.replace()` (the spreadsheet, both curated lists, `labels.csv`), except the
two the September audit cleared: `jobs_sources.csv` and the drop-log CSV
export. They are still the only two.

**Compile once.** Every pattern built from configuration is built once per run
by a `build_*` function and passed down. The title-keyword pattern is still
built twice a run (the run and the re-filter pass), which WP8 measured at
0.275 ms and accepted. The code added by SP4e, SP4h, SP14 and SP15 compiles its
patterns at import. Two readers call `re.search` with a literal pattern inside a
loop, which Python's own pattern cache absorbs.

**Curated files** are written only by `tools/sources.py` and, for
`labels.csv`, by `scripts/refresh_label_locations.py`. That second writer is
atomic and backed up, but `CLAUDE.md`'s rule names only the first (Q3).

**Five filter layers, still.** Rules, title keywords, seniority, review status,
detail page. SP4f, SP14 and SP15 all worked inside existing layers. The
pipeline's blank-title split is not a layer (DECISIONS line 966). Nothing has
crept in.

**Config over code.** The SuccessFactors entries keep their search URL, page
size and, for `tetrapak`, the location filter in `registry.py`; the `url` in
`sources.yaml` is read for its country facet and nothing else (F7). The other
bespoke readers are genuinely site-specific.

**Consistency.** September's finding B is closed: `retrofilter` now passes the
non-place and remote-region patterns (`tools/retrofilter.py` lines 79 to 90),
exactly as the run does. The run, the re-filter pass and eval all use
`apply_combined_title_filter` and the same pattern builders, and the run and
the pass share `judge_experience`. The one remaining gap is F1.

## 5. Does it work: the dry run

```
Sources           56 / 56 processed  (0 skipped)
Jobs seen (all pages, dupes incl.)             7,963
  L1  − off-criteria (location/keywords)      −5,917   → 2,046 match your criteria
  L2  − title keyword                         −1,062
  L3  − senior-level title                      −241   →   743 passed title filters
  L4  − blocklisted (rejected)                  −688   →    55 after blocklist
        already in table (skipped)                20
        stored, rechecked                          0
        new, detail-checked                       35
  L5  − needs 3+ yrs / PhD (0 PhD)                −3
  L5  − non-hybrid (distant city)                 −1
  L5  − location unresolvable in the text        −18   →    13 new jobs kept
New rows written 13 · Marked delisted 0 · Still listed 743 · Unreviewed 29 · Exclusions 7,930
```

- **Sources:** 56 attempted, 56 succeeded, **0 failed, 0 refused**, 0 skipped.
- **Run time** 5 minutes 8 seconds. **Browser starts:** 4, for 10 rendered
  pages. HTTP cache: 0 hits, 17 revalidated, 217 fetched.
- **Warning blocks printed:** Source health (lifesum, 1 to 0, -100%) and Empty
  sources (three, below). No failed, refused, tombstoned, one-page or
  unreadable block.
- **dsv's -99% warning did not appear, and should not have.** Run 41 (27
  October) was the first real run after the narrowing and took it (2,226 rows
  in run 40, 19 in run 41). This dry run compares with 19. **Both `dsv` and
  `dsv_denmark` ran without error**, neither was empty and neither tripped the
  health rule, so each returned at least half of run 41's 19 and 15. A dry run
  prints no per-source counts, so the exact figures are not available.
- **The zero-row sources:**

| Source | This run | History in the store | New or chronic |
|---|---|---|---|
| `fjallraven` | 0 | 0 in every run since run 32 (30 September): 10 stored runs; rows before that | **chronic, about four weeks, not investigated anywhere (F2)** |
| `giving_what_we_can` | 0 | 1 row every run until run 39; 0 in runs 40 and 41 | new since 27 October |
| `lifesum` | 0 | 0 in 37 of 40 runs, 1 row in runs 39 to 41 | chronic; CU2 found the board genuinely empty and the reader correct |

- **September's three never-working sources:** `gfi_europe` was fixed in CU2
  and has returned 3 or 4 rows in every run since; `lifesum` is above; the
  third was removed by the owner, its reader deleted, and its board is now on
  the tombstone.
- **Against the last stored runs** (rows found, exclusions): run 39 10,005 and
  9,924; run 40 9,898 and 9,823; run 41 7,684 and 7,663; this dry run 7,963
  and 7,930. The fall of about 2,200 from run 40 is dsv's narrowing, as SP17
  measured. Unreviewed rows (29) are the store's 16 plus this run's 13.

## 6. Tests

- **Every reader has a saved page.** All 25 have a fixture case; no registry
  source lacks a reader with one.
- **A missing fixture now fails** (September's finding C) in the golden test
  and the parse test. Five secondary tests still skip on a missing file
  (`test_pagination.py` lines 543, 554 and 580, `test_niras_extractor.py`
  line 37, `test_store_roundtrip.py` line 39). Harmless, since the golden test
  fails first, but inconsistent.
- **Nothing was weakened since `500837a`.** No skip or xfail was added. 27
  assertion lines were removed, every one with the behaviour it checked: the old
  smallest-number years reader (replaced by SP4e), WP8f's empty-location
  admission (replaced by SP4f's Q4), and the golden's first-job comparison,
  which now also checks a supplied description.
- **Passing for the wrong reason.** The golden test pins each fixture's count
  and its *first* posting; the parse test checks only that every row has a
  title. A reproduction outside the repository wrapped the `storytel` reader so
  that every location after the first row came back blank: the golden test and
  the parse test both passed (F3). SP6's three reader bugs were all on every
  row, so the first row caught them; a bug in a second card layout would not
  be. The goldens are characterisation by design (their docstring says so); the
  field-by-field check each package made when pinning is not repeatable from
  the suite.

## 7. README for GitHub

Read top to bottom as a stranger would. Every command, flag and default in
"Options" matches `--help`. Every one of the 121 internal links and anchors in
the README and the docs resolves. The test count (1,617) and the coverage
sentence (all twenty-five readers) are right. The printed run-summary example
matches the dry run's real layout, block names and order. What does not hold:

1. **Layer numbers disagree.** "How it works" numbers the ladder 1 to 5, as
   the summary prints it, but lines 414, 856, 859 and 865 (and
   `source_yield`'s own help) say "Layer 0" for the location layer, which is
   Layer 1 everywhere a user sees it.
2. **"Deleting `data/curated/` costs you nothing the scraper needs"** (line
   698). True of a single run, and the wrong thing to tell a reader about the
   only files here that cannot be regenerated; the Layout table says
   "not regenerable" two screens later.
3. **"Every one has a tracked `.example` twin"** (line 1211): `labels.csv` has
   none (only `tests/fixtures/eval_labels.csv`, which is not named as its twin).
4. **"One working example per supported ATS"** (line 552): the example has 9
   entries and the readers cover 12 platforms; SuccessFactors, HiBob and Jobylon
   have no example.
5. **"Each is pinned to the exact output it produced"** (line 1127): it is
   pinned to the count and the first posting (F3).
6. **The Jobs sheet column list** (around line 718) omits `experience_level`,
   added in SP4c.
7. **Layout and "How this is built and maintained":** the `scripts/` row omits
   `refresh_label_locations.py`; the `docs/` row and the "Five documents" table
   omit `docs/OPTIONAL-PLAN.md` (and will need this file); the `job_scraper/`
   row omits `robots.py`, `page_sizes.py` and `blocklist.py`; `SOURCES-PLAN.md`
   is described as the current plan.
8. **`data/jobs_sources.csv`** is still sold as the way to spot a failing
   source, which the Failed sources block now does.
9. **"Documentation each package must update", row by row:** Adding a source
   (SP3) done; Maintenance commands (SP1, SP2, SP2b, SP4g, SP12) done, OP2 not
   built; Reading the run summary (SP7, SP4c, SP12) done, including the link
   to `source_yield`; test count and coverage sentence current; the "How this
   is built" table (SP0b) was right for five documents and missed
   `OPTIONAL-PLAN.md`, which no row of the table covers; the `#future-work`
   anchor resolves; the `sources.yaml` section (SP3c, SP17) done; the Layout
   table (SP1) done but see item 3; `sources.example.yaml` header (SP3) done;
   `CLAUDE.md` (SP0b, SP2b) done then, drifted since (F10); DECISIONS appended
   by every package; the plan's own Status table behind (F8).

**Length.** At 1,240 lines the README is a manual. A proposal, not a rewrite:
keep the overview, setup, running, the five-layer table, a one-screen
command list and the responsible-scraping rules in the README (about 400
lines), and move three reference sections into `docs/`: "Reading the run
summary" (about 210 lines) and "Why was something dropped?" into a run-summary
guide; the curated lists, `sources probe` and "Adding a source" (about 240
lines) into a sources guide; "Maintenance commands" and the `source_yield`
class table into an operations guide. Each README section keeps a two-line
summary and a link.

## 8. Docs and repo hygiene

**`CLAUDE.md`.**

- The architecture block (lines 25 to 40) omits `drops.py`, `eval.py`,
  `review.py`, `robots.py`, `scoring.py`, `page_sizes.py`, `__main__.py`,
  `extractors/pagination.py`, `extractors/rowcheck.py` and `config/`. It still
  calls `blocklist.py` "Permanently rejected postings"; the module now holds
  review statuses and the legacy import. It says `experience_filter.py` is
  "Layer 3 (title)", which is Layers 2 and 3.
- The startup reads name `docs/SOURCES-PLAN.md`, a 6,087-line finished plan.
  Once the plan is closed that is the same habit SP0b retired for the refactor
  plan.
- The Definition of done names `SOURCES-PLAN.md` for an SP package and nothing
  for an OP package.
- Python: `pyproject.toml` says `requires-python = ">=3.10"` and the README
  "3.10 or newer"; `CLAUDE.md` and CI say 3.13, and CI tests only 3.13. No
  syntax newer than 3.10 was found, but SP3's robots.txt episode showed the
  standard library's behaviour differs between patch releases. Which is meant
  is the owner's call (Q2).

**A closing doc structure** (proposal, F11):

- `docs/SOURCES-PLAN.md` becomes an archive like `docs/REFACTOR-PLAN.md`:
  a short note at the top, Status table and header corrected and frozen, read
  on demand by grep. Before that, move the parts that are still live policy out
  of it: "Publishing this file", the shared rules for new-extractor packages,
  and the "Documentation each package must update" table. They belong in
  `docs/DECISIONS.md` or a short process section in `CLAUDE.md`.
- `docs/OPTIONAL-PLAN.md` becomes the one live plan, read when an OP package is
  under way. It needs its own copy of the publishing rule once the sources plan
  is an archive, since it currently borrows it.
- `docs/AUDIT.md` stays as written (the README promises it is left untouched),
  with one line at its top pointing here. This report takes the same place.
- `docs/DECISIONS.md` is 2,230 lines of 169 flat entries with no headings, so
  nothing in it can be linked to or found except by grep. Group the entries
  under eight or so topic headings (store and delisting, the ladder by layer,
  extractors and fixtures, politeness, curated lists and the probe, repository
  hygiene, process), keeping each entry verbatim, and add a topic index at the
  top. Three decisions the September audit cleared live only in `AUDIT.md` or
  the refactor archive (the two report writes, the summary on stderr, the
  twice-built title pattern); give each a one-line entry.

**Public repository safety.**

- Nothing under `data/` is tracked except the three `.example` twins. No
  ignored file is tracked. No absolute home path, personal e-mail address or
  token is in any tracked file (only `example.com` and `.invalid` addresses).
  `docs/SOURCES-PLAN.md` mentions the owner's backup folder by its `~` path six
  times; it names a folder, not a file.
- **The two curated lists against tracked files** (names and boards compared
  with `git grep`; counts only). Tombstone, 8 entries: **2 appear by name in
  tracked files.** One is a source retired in SP4d and tombstoned later, named
  as its extractor in the README, `docs/DECISIONS.md`, `docs/SOURCES-PLAN.md`,
  `docs/REFACTOR-PLAN.md`, `pipeline.py`, `run.py` and two test files. The
  other is the source CU2 removed, named in `docs/REFACTOR-PLAN.md` only. Both
  were named while they were sources; whether that is within "Publishing this
  file" is a question (Q4). A third appears only inside a saved aggregator
  fixture. Candidates, 27 entries: 6 carry the name of an employer that is
  already an active source, so the name is public through the registry and
  fixtures, and no tracked file says it is also a candidate. Two more appear
  only inside saved aggregator fixtures, and one is a common English word.
- **The ignore rules have two gaps** (F6). `data/` itself is not deny by
  default, so a backup named `data/jobs.sqlite3.bak` or an export named
  `data/jobs.json` would be offered to `git add`. And the private repository's
  own `data/curated/.gitignore`, which ignores `*.example.*`, is read by the
  public repository too and overrides its `!data/curated/*.example.*`: a new
  example twin (say, for `labels.csv`) would be silently ignored.
  `git ls-files -ci --exclude-standard` lists the three existing twins as
  ignored for that reason; they stay tracked only because they already are.

---

## Findings

Ordered by `CLAUDE.md`'s priorities: data loss, silent failure, politeness,
then the rest. No politeness finding survived: robots.txt is honoured, the
per-host limits hold, dsv's 230 pages a run are down to 4, and the one
behaviour that looks impolite (an unreadable robots.txt allows the crawl) is a
recorded decision.

### F1. The end-of-run re-filter can permanently reject a job the run just kept

- **What it is.** At the end of every run, and in `retrofilter`, stored `new`
  rows are re-checked against the rules. The store does not keep a posting's
  department or listing snippet, so a row the run admitted because
  `include_keywords` matched its department fails the same check from the
  store, and is marked `rejected`.
- **Why it matters.** `rejected` is permanent and nothing brings it back. The
  README and the pass's own docstring say a verdict that depends on those
  fields is "passed through rather than rejected on a guess"; for
  `include_keywords` it is not. SP4b recorded that this changes nothing *under
  today's config*, because `include_keywords` is empty. The trap is set for the
  day it is not.
- **Evidence.** Reproduction: a job with department "Climate Unit" and
  `include_keywords: ["climate"]` passes `matches_rules`; stored and re-filtered
  through `refilter_stored_jobs`, it comes back `rejected`
  ("include_keywords: no match"). The cause is `pipeline.py` line 419 calling
  `matches_rules` on a row with no `department`.
- **Severity** worth fixing (latent). **Effort** 1 hour. **Model** Sonnet 5,
  `think`. **Fix**, with the owner confirming the direction: either the pass
  skips the keyword checks it cannot reproduce, or it refuses to run while
  `include_keywords` is set.

### F2. `fjallraven` has returned nothing for ten runs, and nobody has looked

- **What it is.** The Empty sources block names `fjallraven` on every run since
  30 September. Nothing in the plan, the decisions log or the store records a
  look. `giving_what_we_can` joined it on 27 October.
- **Why it matters.** It is September's finding A again: the warning works and
  scrolls past. Either the board is empty, or the Teamtailor reader no longer
  reads this site and a source the owner thinks is watched is not. Only a look
  tells which.
- **Evidence.** `source_health`: `fjallraven` 0 rows in runs 32 to 41, rows
  before; this dry run 0. `giving_what_we_can` 1 row in every run to run 39,
  then 0, 0 and 0 now.
- **Severity** worth fixing. **Effort** 1 hour for both. **Model** Sonnet 5,
  `think`. **Fix** (an investigation): with the owner's approval, capture each
  page once with `capture_fixtures.py` and compare with the saved fixture. Then
  record the answer in DECISIONS, as CU2 did for `lifesum`, so the block can be
  read as "known empty".

### F3. The golden tests look at each saved page's first posting only

- **What it is.** Each golden test pins how many postings a saved page yields
  and the full first posting. The parse test checks only that every posting has
  a title. Rows two onwards are never compared.
- **Why it matters.** A reader that reads the first card right and every other
  card wrong passes the whole suite, which is the quiet loss the fixtures exist
  to stop. The README claims more ("pinned to the exact output").
- **Evidence.** Reproduction: wrapping the `storytel` reader so that every
  location after the first is blank, both `test_extractor_output_matches_golden`
  and `test_fixture_still_parses` pass. `tests/test_extractors_golden.py`
  lines 1059 to 1078; `tests/test_capture_fixtures.py` line 597.
- **Severity** worth fixing. **Effort** 1.5 hours. **Model** Sonnet 5, `think`.
  **Fix**: add every-row checks to the parse test (no location equal to its
  title, unique detail URLs, no field blank on every row when the first row has
  it), or pin a digest of every row beside the first. Make the five remaining
  skip-on-missing tests fail like the golden test while there.

### F4. `jobsinlund` invents a city when the API gives none

- **What it is.** `extractors/jobsinlund.py` line 117 writes "Lund" when a
  posting has no city.
- **Why it matters.** A guessed location passes Layer 1 as a real one. Latent:
  the API query already restricts to that city, and SP4b found 0 of 759
  postings without one.
- **Evidence.** `city = (loc.get("city") or "Lund")`.
- **Severity** tidy. **Effort** 15 minutes. **Model** Sonnet 5, no cue.
  **Fix**: leave the field empty, which Layer 5 settles from the supplied
  description (SP4f's Q4).

### F5. A dry run can still change the store's schema

- **What it is.** Opening the store adds any missing columns and creates any
  missing tables, and those statements commit outside the transaction a dry run
  rolls back. `--dry-run --output-db` pointed at a new path creates a database
  file.
- **Why it matters.** "Writes nothing has to mean nothing" (the pipeline's own
  comment). No data is lost and today's store already has every column, so it
  did nothing here, but a dry run is the tool for looking before touching.
- **Evidence.** Reproduction: an old-schema `runs` table opened with
  `JobStore(dry_run=True)` gains the `kind` column. `storage/db.py` lines 207
  to 237.
- **Severity** tidy. **Effort** 30 minutes. **Model** Sonnet 5, `think`.
  **Fix**: in a dry run, refuse a store that needs a migration (or a path that
  does not exist) rather than migrating it.

### F6. Two gaps in the ignore rules

- **What it is.** `data/` is protected by a list of extensions, not deny by
  default; and the private repository's `data/curated/.gitignore` hides new
  `.example` files from the public repository.
- **Why it matters.** `CLAUDE.md` requires deny by default for a directory of
  private files, because a per-file list has let a sidecar through twice. The
  second gap fails the other way: a new example twin cannot be added without
  `-f`, and `git status` never mentions it.
- **Evidence.** `git check-ignore` reports `data/jobs.sqlite3.bak`,
  `data/jobs.json` and `data/sub/x.csv` as trackable; it reports
  `data/curated/new.example.yaml` as ignored by `data/curated/.gitignore` line 3.
- **Severity** worth fixing. **Effort** 30 minutes. **Model** Sonnet 5, no cue.
  **Fix**: `data/*` with explicit `!` exceptions in the public `.gitignore`;
  and the owner moves the inner rules into `data/curated/.git/info/exclude`,
  which only the private repository reads.

### F7. SuccessFactors sources keep their configuration in the registry

- **What it is.** The five SuccessFactors entries carry their search URL and
  page size in `registry.py`, and `tetrapak`'s location filter lives there too.
  `sources.yaml`'s `url` is read only for the country facet (SP17 found the
  reader had never read it). `dsv_denmark` repeats `dsv`'s whole entry.
- **Why it matters.** "Config over code": the URL a reader of `sources.yaml`
  sees is not the one fetched, so editing it does nothing, silently.
- **Evidence.** `extractors/registry.py`, the SuccessFactors block; DECISIONS
  line 2197.
- **Severity** worth fixing. **Effort** 2 hours. **Model** Opus 5, `think`
  (a stored key must not move). **Decision** for the owner: derive the search
  URL from `sources.yaml`, or record that the registry owns it.

### F8. The sources plan's bookkeeping is behind

- **What it is.** The header sentence, the SP17 and SP17b rows, SP14's note on
  SP13, "What is still unknown" and about twenty to-do boxes (all in section 2).
- **Why it matters.** DECISIONS line 346 already says it: a plan entry
  describing something done is stale the moment it is done, and the next
  session repeats it.
- **Evidence.** `docs/SOURCES-PLAN.md` lines 3, 120, 5455, 5959.
- **Severity** tidy. **Effort** 30 minutes. **Model** Sonnet 5, no cue.
  **Fix**, after the owner says which boxes are still theirs.

### F9. The README has drifted in nine places

- **What it is.** The list in section 7: layer numbering, the advice about
  deleting `data/curated/`, the `.example` claim, the example-per-ATS claim,
  the pinning claim, the sheet's columns, the Layout and documents tables, and
  `jobs_sources.csv`.
- **Why it matters.** The README is what a stranger reads, and one of the nine
  is advice that could cost the owner irreplaceable files.
- **Evidence.** README lines 414, 552, 698, 718, 856 to 865, 1127, 1211 to 1214,
  1219.
- **Severity** worth fixing. **Effort** 1.5 hours, plus whatever the length
  proposal is given. **Model** Sonnet 5, `think`. **Fix.**

### F10. `CLAUDE.md` has drifted

- **What it is.** The architecture block, the startup reads, the Definition of
  done, the Python version, and the curated-directory rule (section 8).
- **Why it matters.** Every session starts here.
- **Evidence.** `CLAUDE.md` lines 4, 25 to 40, 112 to 120, 125, 148.
- **Severity** worth fixing. **Effort** 30 minutes. **Model** Sonnet 5, no
  cue. **Fix** for the block and the Definition of done; **decision** for the
  Python version (Q2) and the curated rule (Q3).

### F11. The plan is closed but the documents are not arranged for it

- **What it is.** The proposal in section 8: archive the sources plan, keep
  `OPTIONAL-PLAN.md` as the live plan, point `AUDIT.md` here, give DECISIONS
  headings and an index.
- **Why it matters.** Every session pays for 6,087 lines of finished plan at
  startup, and a 2,230-line log with no headings is searchable only by someone
  who knows the word to grep for.
- **Evidence.** `CLAUDE.md` line 4; `grep -c '^## ' docs/DECISIONS.md` is 0.
- **Severity** worth fixing. **Effort** 1.5 hours. **Model** Opus 5, `think`.
  **Decision** for the owner, then a docs package.

### F12. The candidates list overlaps the active sources

- **What it is.** 6 of 27 candidates name an employer that is already an active
  source. For 1 of them the candidate's board is the active source's board.
- **Why it matters.** `sources check` matches by board, so five are probably
  other boards of the same employer, kept on purpose (OP2's workaround is to
  `recheck` them as active). The sixth is exactly what `candidate activate`
  exists to remove.
- **Evidence.** Board identity of each candidate against `sources.yaml`, read
  only.
- **Severity** tidy. **Effort** 15 minutes, the owner's. **Decision**: run
  `candidate activate` on the one, and confirm the five.

### F13. Dead code and a duplicated constant

- **What it is.** Two superseded title filters and `same_board`, used only by
  tests; `_REQUIRED_EXCLUDED` defined and unused while `tools/sources.py`
  repeats it three times; `scrape_and_blocklist.sh`'s `langdetect` comment.
- **Why it matters.** Small, but it is the two-ways-of-saying-one-thing drift
  `CLAUDE.md` names.
- **Evidence.** Section 3's table.
- **Severity** tidy. **Effort** 30 minutes. **Model** Sonnet 5, no cue.
  **Fix**: delete the three functions with their tests, use the constant.

### F14. Local leftovers and merged branches

- **What it is.** 120 merged branches, stale compiled files, `.DS_Store`
  files, 19 `.bak` files, two dead permissions in `.claude/settings.local.json`,
  an uncommitted `labels.csv` in the curated repository, and a
  `jobs_sources.csv` that nothing reads.
- **Why it matters.** Clutter only; nothing is lost by leaving it.
- **Evidence.** Section 3.
- **Severity** tidy. **Effort** 15 minutes, the owner's. **Decision** on
  `jobs_sources.csv` and the backups; the rest is the owner's commands, to run
  from the project root:

```bash
git branch --merged main | grep -vE '^\*|^\s*main$' | xargs git branch -d
```

```bash
git branch -r --merged origin/main | grep -v -e HEAD -e 'origin/main$' | sed 's#origin/##' | xargs -n1 git push origin --delete
```

```bash
find job_scraper scripts tests -name __pycache__ -type d -prune -exec rm -rf {} +
```

```bash
find . -path ./.venv -prune -o -name .DS_Store -print -delete
```

```bash
git -C data/curated status --short
```

  The first deletes only branches git agrees are merged. The second is a push,
  so it is the owner's alone. For the `.bak` files: the 17 YAML ones are
  covered by the curated repository's history and can go once
  `git -C data/curated log --stat` shows the matching commits; keep the two
  `labels.csv` ones until that file is committed. Remove the two `csv_store`
  lines from `.claude/settings.local.json` by hand.

---

## Leave these alone

- **The two report files written without a temp file** (`jobs_sources.csv`, the
  drop-log CSV export): regenerable reports, not stores. Cleared in
  `docs/AUDIT.md` section 3; the refactor plan's WP5 result records the scope
  (`docs/REFACTOR-PLAN.md` line 971). Not in DECISIONS (see F11).
- **The retired shell script, `blocklist_all` and the old blocklist file:**
  DECISIONS line 486. Do not propose deleting them.
- **The `SEA` keyword and other "costly" keywords:** an eval cost is an
  attribution, not a marginal cost (DECISIONS line 293); `SEA` is now
  case-sensitive (line 1773).
- **Hybrid-gated conditional locations:** DECISIONS line 22.
- **An unreadable robots.txt allows the crawl:** DECISIONS line 399.
- **Saved fixtures keep a site's published contact details:** DECISIONS
  line 1652.
- **A zero-row scrape never delists without `--allow-empty-delist`, and
  `rejected` is never undone automatically:** DECISIONS lines 28 and 37.
- **The re-filter pass never settles a location or hybrid state:** DECISIONS
  lines 1481 and 1493 (F1 is the keyword case those entries do not cover).
- **The title-keyword pattern built twice a run:** WP8, `docs/REFACTOR-PLAN.md`
  line 3161.
- **The ladder's order and the summary on stderr:** `docs/AUDIT.md` section 7E.
- **Blank-title postings skipped before every count:** DECISIONS line 966.
- **Readers that return an empty list on a changed page:** the pipeline makes
  zero rows the loud case (CU2); see section 4.
- **Place names in the refactor plan and the tests:** `CLAUDE.md`'s working
  rules.

## Questions for the owner

1. **The six rows SP15 revived** (27 October) were all `rejected` before run 41
   started, and the drop log holds no exclusion for them, which matches a
   `review` command (it writes no drop-log row). Was that you? If not, it is a
   bug to chase before anything else here.
2. **Python 3.10 or 3.13?** `pyproject.toml` and the README promise 3.10; only
   3.13 is tested.
3. **Does `CLAUDE.md`'s "`data/curated/` is written only through
   `tools/sources.py`" cover `labels.csv` and `blocklist.csv`?**
   `refresh_label_locations.py` writes the first, safely.
4. **Two tombstoned boards are named in tracked docs** (section 8), both from
   when they were sources. Acceptable under "Publishing this file", or to be
   reworded to extractor-only mentions?
5. **Retire the two one-shot scripts** (`recover_skipped_sources.py`, and your
   `migrate_curated_to_yaml.py` with `tests/fixtures/curated/` and
   `curated.py`'s unmigrated-file refusal)? Their work is done.
6. **Keep `data/jobs_sources.csv`?** Nothing reads it.
7. **Is the gold set current enough?** `labels.csv` was last written on 2
   October; the sources added since have no labels, so eval says nothing about
   them.
8. **SP12's type-B sources and SP14's three rows** are still waiting for your
   answer, and OP4 waits on the first.

## Suspected, not shown

- **A stored posting that comes back with a blank title accrues misses.** The
  blank-title split runs before the delisting keys are collected (pipeline
  lines 713 and 743), so a listed posting whose title the site blanks counts as
  missing and, if `new` or `seen`, is delisted after two runs. DECISIONS
  line 966 chose the order; delisting is reversible. Not reproduced.
- **A country facet on a URL whose host differs from the registry's** (for
  example with or without `www.`) is dropped without a word
  (`successfactors_html.py` line 248), and the whole board is read: no data
  lost, about 230 pages fetched. Not reproduced.
- **A Workable response that is not an object** on the first page reads as an
  empty board. It is caught as an empty source, so it is loud, but it is named
  as "empty" rather than "unreadable". Not reproduced.

## Closing table

| ID | Title | Severity | Effort | Fix or decide |
|---|---|---|---|---|
| F1 | The end-of-run re-filter can permanently reject a job the run just kept | worth fixing | 1 hr | fix (owner confirms direction) |
| F2 | `fjallraven` has returned nothing for ten runs | worth fixing | 1 hr | fix (investigate, owner approves capture) |
| F3 | The golden tests look at the first posting only | worth fixing | 1.5 hr | fix |
| F4 | `jobsinlund` invents a city | tidy | 0.25 hr | fix |
| F5 | A dry run can still change the store's schema | tidy | 0.5 hr | fix |
| F6 | Two gaps in the ignore rules | worth fixing | 0.5 hr | fix (inner file is the owner's) |
| F7 | SuccessFactors configuration lives in the registry | worth fixing | 2 hr | decide |
| F8 | The sources plan's bookkeeping is behind | tidy | 0.5 hr | fix after owner ticks |
| F9 | The README has drifted in nine places | worth fixing | 1.5 hr | fix |
| F10 | `CLAUDE.md` has drifted | worth fixing | 0.5 hr | fix and decide |
| F11 | The documents are not arranged for a closed plan | worth fixing | 1.5 hr | decide |
| F12 | The candidates list overlaps the active sources | tidy | 0.25 hr | decide (owner) |
| F13 | Dead code and a duplicated constant | tidy | 0.5 hr | fix |
| F14 | Local leftovers and merged branches | tidy | 0.25 hr | decide (owner's commands) |
