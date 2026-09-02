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
| 8e | Extractor location gaps | 2 hr | Sonnet 5 | `think` | done — 8/11 sources fixed (41/54 rows), 3 confirmed genuinely location-less | `wp8e-extractor-locations` |
| 8f | Empty location passthrough | 1.5 hr | Sonnet 5 | none | done — recall 0.432 → 0.554, RULE_LOC_EMPTY deleted | `wp8f-empty-location-passthrough` |
| 8g | ISS location extraction | 2 hr | Sonnet 5 | `think` | done — fetcher bypass fixed in both extractors, `iss.html` + `niras.html` captured, ISS locations and NIRAS titles fixed, DSV department fixed; eval unchanged by design until the labels refresh | `wp8g-iss-location` |
| 8 | Trim the ladder, prune the keywords | 2.5 hr | Opus 5 | `think hard` | done — layers 1c/1b deleted, 8 keywords pruned, `Architect` narrowed; recall 0.647 → 0.868 live (owner's `rules.json` edit made and verified 2026-08-27), precision up too | `wp8-trim-ladder` |
| 8h | Renumber the ladder | 1 hr | Sonnet 5 | `think` | done — display now Layer 1-5 in execution order; stored ids untouched | `wp8h-renumber-ladder` |
| 8i | `--layer` refuses a display number | 0.5 hr | Sonnet 5 | none | done — a bare digit is refused before the store opens and named its stored id; every other argument unchanged; 414 tests pass | `wp8i-layer-guard` |
| 8b | README reconciliation + renumbering sweep | 2 hr | Sonnet 5 | none | done — README rebuilt against the current CLI, 54 comments renumbered; **incident: the live store was modified by mistake, see the result section** | `wp8b-readme` |
| 9 | Playwright reuse and HTTP caching | 3 hr | Fable 5 | `think hard` | done — full run 365s → 295s (browser reuse) → 132s (warm cache); 15 browser launches → 4; funnel unchanged; 443 tests pass | `wp9-fetch-performance` |
| 10 | Politeness and observability | 1.5 hr | Sonnet 5 | `think` | done — per-host cap 2 with a 1s spacing, robots.txt honoured per host, contact details moved to `rules.json`, source-health warnings, `--dry-run`, argparse front doors on both tools; 514 tests pass. Contact details filled in by the owner and verified, 2026-08-31 | `wp10-politeness` |
| 11 | J-PAL pagination, and silent short walks | 1.5 hr | Sonnet 5 | `think` | done — all six paginated walks guarded (every one of them publishes a total or a pager); `jpal` re-captured as its whole five-page walk (9 → 37 rows, confirmed live in run 18); Tetra Pak moved off the endpoint its robots.txt disallows; 583 tests pass | `wp11-pagination` |
| 12 | Run the formatter | 0.5 hr | Sonnet 5 | none | done — 37 files reformatted, AST-identical bar one docstring space; `ruff format --check` added to the Definition of done; 583 tests pass, the same 583 | `wp12-ruff-format` |

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
- **An empty location field is a fourth Layer 0 outcome, not the third one
  wearing a different rule string (WP8f).** WP8d gave `matches_rules` a state
  for "present, but names no place" (a placeholder like "2 Locations"),
  deferred to Layer 2 because the description might name a real city. An
  empty field is not that: there is no page text a Layer 2 fetch could read a
  location off, because none was ever captured. So WP8f admits it outright at
  Layer 0, permanently, under its own `_LOCATION_EMPTY_ADMITTED_REASON` —
  deliberately *not* wired into `_HYBRID_PENDING_REASON`/
  `_UNRESOLVED_PENDING_REASON`'s machinery, so it costs no detail fetch and
  Layer 2 never goes looking for it. Consequence: `RULE_LOC_EMPTY` lost its
  only call site (`_location_drop_rule` returned it exclusively for an empty
  field, and `matches_rules` now intercepts every empty field before that
  function is ever reached) and was deleted, per WP8a's rule that a drop-log
  rule string going silent is a contract change to call out, not a private
  implementation detail.
- **A wrong location and a missing one are the same bug wearing different
  clothes (WP8g).** Found while reviewing WP8f, 2026-08-20. WP8e's population
  was "sources that captured no location", and WP8f then admitted exactly that
  case. ISS is in the same population and neither package saw it, because its
  extractor does not return an empty string — it returns the literal word
  `"Title"`, a table header read as data, on all 33 of its rows in the gold
  set. An empty field now passes Layer 0; `"Title"` is judged as a city nobody
  has heard of and dropped under `RULE_LOC_UNLISTED_CITY`. Nine of those 33 are
  labelled `review`, so the leftover case costs as much as WP8f's whole gain.
  Consequence for the queue: WP8g goes **before** WP8, and the `location`
  column refresh below stops being optional — see the next entry.
- **The gold set is a measuring instrument, and it needed recalibrating before
  WP8 (2026-08-24).** WP8e, WP8f and WP8g all changed what the extractors and
  Layer 0 produce, and `labels.csv` still held the old values, so the harness
  was scoring a filter that no longer existed. `scripts/refresh_label_locations.py`
  refreshed 63 locations; the owner re-judged the 13 rows that had been labelled
  `review` without one — eight of them turned out to be in Chennai, Gdansk, New
  York or London and were never wanted, while four BearingPoint roles turned out
  to be in Malmo and genuinely were. Four ISS rows could not be refreshed at all:
  the postings vanished before WP8g fixed the extractor, so nothing can ever
  re-observe them, and they were corrected by hand from their surviving Gdansk
  siblings. Reconciling six jobs the edits had left labelled both ways took the
  set from 514 scored to 520. Net effect on the measurement, none of it earned by
  changing a filter: recall 0.554 -> 0.647. The lesson for any future extractor
  package: refresh and re-judge before quoting a number, because a stale gold set
  does not fail loudly — it just answers a question about last month's code.
- **`httpx` is declared because the test imports it, not because anthropic
  used to supply it (2026-08-21).** CI went red on every branch overnight, on a
  docs-only commit. `anthropic` 1.0.0 released and switched its HTTP client to
  `httpx2`; `requirements.txt` floats on `anthropic>=0.100.0`, so CI moved
  0.125.0 -> 1.0.0 and `tests/test_scoring.py`'s direct `import httpx` stopped
  resolving. Owner's decision: declare `httpx` rather than pin `anthropic<1.0.0`
  — every API surface `scoring.py` touches still exists in 1.0.0 and the suite
  passes against it, so pinning would freeze the SDK to hide a missing
  declaration. Two things worth remembering: a local `.venv` that predates a
  release will not reproduce this, so green locally is not green in CI; and
  `test_scoring` still builds its mock error from an `httpx.Response` while the
  SDK now speaks `httpx2` — it passes by duck-typing, and that mock is drifting
  from what the SDK would really raise. Worth folding into the WP7 scoring code
  the next time anything touches it.
- **An extractor that fetches for itself cannot be fixture-captured (WP8g).**
  The 2026-08-21 capture attempt failed with "extractor made no request".
  `capture_fixtures` hands the extractor a recording fetcher and keeps the first
  URL it asks for, so the contract is that an extractor uses the callable it is
  given. `successfactors_html` (and `niras`) break it: they build their own
  `fetch_rendered` and the recorder never fires. `workday` honours it, tests
  capability via `is_rendering_fetcher` rather than identity, and is the only
  dynamic extractor with fixtures (`busuu`, `path`) — which is the evidence, not
  a coincidence. Consequence: ISS's location bug was never catchable by a golden
  test, because ISS was never capturable. Unblocking that is WP8g step 0, and
  the general lesson is that `strategy: dynamic` belongs to the caller, not to
  the extractor.
- **The `"Title"` was an accessibility label, not a table header (WP8g,
  2026-08-21).** The package was written guessing a header row, and the fixture
  disproved it. SuccessFactors ships two row layouts: the classic table (DSV and
  every static instance here) and the modern tile (ISS), and the tile layout
  introduces each field with a `span.sr-only` naming it. The title's label sits
  inside the title's own container, which is where `find_parent([… , "div"])`
  stopped — so the location was not merely mis-picked, it was never in the
  container being searched. That is why the minimal repair does not exist:
  strip the label and the location is empty; widen the container as well and it
  is `"Property Services"`, because the tile layout puts job category first. A
  positional "first text that is not the title" heuristic cannot read a
  label-per-field layout at all. Consequence for the next extractor bug of this
  shape: check what the container actually *contains* before assuming the
  heuristic picked the wrong item out of it, and prefer whatever the markup
  labels over whatever comes first — `workday.py` had already learnt this for
  its own two layouts.
- **A screen-reader label is never data, for any source (WP8g).** The `sr-only`
  strip went into the shared fallback rather than the ISS branch, on the
  principle that a field label is not a location for DSV either — it merely does
  not appear in DSV's markup today. Cost of doing it generally: nothing
  measurable (DSV's job rows contain zero `sr-only` elements). Cost of doing it
  as an ISS special case: the next SuccessFactors site to ship the tile layout
  repeats all 33 lost rows before anyone notices.
- **Fixing a location bug does not license fixing the department beside it
  (WP8g).** DSV's `department` holds a posting date; `span.jobFacility` is right
  there and would correct it. It was left alone, because `department` is part of
  `filtering._haystack` and WP8 is about to measure what each keyword costs
  against exactly that text. Correcting the field now would shift WP8's baseline
  underneath it for something this package was not sent to fix. The general form:
  when a field feeds the harness, improving it is a measurement change, and it
  belongs to the package doing the measuring.
- **Reasoning identifies the layout; only a fixture shows the data (WP8g,
  2026-08-21).** Four SuccessFactors sources, four fixtures captured in this
  package, and the score is stark: the layout was predicted correctly every
  time, and the *data* was wrong in three of four. ISS returned a label instead
  of a location; NIRAS returned the whole card instead of a title; Coloplast
  dropped 6 of 25 postings silently and blanked every department. Only
  novo_nordisk — the one predicted clean — was clean. Two of those bugs were
  invisible until the fetcher bypass was fixed, and the third was invisible
  until someone looked at the markup rather than at the gold set. The gold set
  can prove a source is *healthy enough to produce plausible strings*; it cannot
  show what the page held that never reached it. Consequence: treat "this source
  has no fixture" as an open question about correctness, not as a low-priority
  chore — and never let a paragraph of reasoning stand in for a capture when a
  capture is one command away.
- **Before honouring a fetcher, check what the caller actually passes (WP8g,
  2026-08-21).** Converting an extractor from "always render for myself" to
  "use what I am given" is only inert if the caller hands it a rendering
  fetcher. `successfactors_html` and `niras` both looked like the same two-line
  change; only `niras` carried the risk, because its comment said "Always use
  Playwright" and its docstring said the static HTML is just a filter shell. Had
  `sources.yaml` listed it `static`, the tidy-up would have turned a working
  source into zero jobs reported as "no vacancies" — priority 2's exact failure,
  on a source with no fixture to catch it. It is `dynamic`, so the change was
  safe, but the check is the point: the `strategy` entry is the precondition,
  not a detail. Both directions are now covered by construction — a plain
  fetcher is used as given rather than silently upgraded.
- **The labels refresh is a prerequisite for WP8, not housekeeping (WP8g).**
  The rule three entries up still holds: `eval.py` replays `labels.csv`'s
  stored `location`, so a fixed extractor scores as no improvement until the
  column is refreshed from the store. WP8e and WP8f could both live with that,
  because neither needed the harness to see the fixed values. WP8 cannot: it
  prunes `title_exclude_keywords.csv` by measuring which keywords cost wanted
  jobs, and a wanted job still blocked at Layer 0 by a stale `"Title"` never
  reaches the keyword layer to be counted. Pruning on that evidence would keep
  a keyword whose real cost is higher than measured. So the order is WP8g, then
  a real run, then the refresh, then WP8's baseline.
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
- **Every per-rule cost the eval harness prints is an *attribution*, not a
  marginal cost (WP8, 2026-08-27).** A rule is credited with a drop when it is
  the first configured term to match; removing it changes a verdict only if
  nothing further down the ladder also catches that job. On the WP8 baseline
  only 38 of 112 keywords changed any verdict when removed, and `SEA`, `AI` and
  `architect` — all named as costly — changed none. `Security`/`architect` and
  `architect`/seniority `Architect` mask each other exactly, so removing either
  half alone measures zero and removing both measures the real cost. **Never
  prune from the printed table.** Remove the rule, re-run, diff. This is also
  why WP8's step 3 could not be answered until step 2 had landed.
- **`Architect` was a genuine false positive; `Lead` was not (WP8,
  2026-08-27).** The original prompt named both. The gold set supports only
  `Architect`: 4 of its 16 architect titles are labelled review, and "ASIC SoC
  **Security** Architect" is review while "ASIC SoC **System** Architect" is
  discard — a topic distinction the seniority layer was making by accident. It
  is narrowed to `Solution Architect` + `System Architect`, moved into
  `title_exclude_keywords.csv` where job families belong. For `Lead` the
  claimed example ("Lead Generation Analyst") **does not occur in the gold set
  at all**; 20 of 21 `\bLead\b` titles are discard and removing it returns 13
  unwanted jobs for zero wanted. Left alone. Do not re-propose it without new
  labelled evidence.
- **Do not reorder the filter ladder for speed. Measured and rejected
  (2026-08-27).** The idea is plausible and will be proposed again, so here are
  the numbers. The whole text ladder costs **79 ms for 8,000 postings**; real
  runs (10, 11, 12) take **211-238 seconds**. Filtering is ~0.035% of a run —
  everything else is HTTP. The intuitive reorder, cheap regex before expensive
  location parsing, is **slower**: 83.8 ms against 79.2 ms, because Layer 0
  discards 2,057 of the 8,000 up front and the title scan then only sees 5,943.
  More important than the timing: **order cannot change the outcome.** These
  layers are conjunctive predicates, so the kept set is an intersection and is
  order-independent — verified, identical survivors either way. What reordering
  *does* change is **attribution**, the one thing WP8 spent a package learning
  to read correctly. It would move the per-layer table and the drop log without
  changing a single verdict, and make future rows non-comparable with the
  ~49,000 already stored. Two orderings that matter are already right: Layer 2
  (detail) is last because it is the only one costing an HTTP request, and
  Layer 1d runs before it so already-rejected jobs never trigger a fetch
  (pinned by `test_logging_costs_no_extra_http_request`). Note also that 1a and
  1 are deliberately fused into one title scan in `apply_combined_title_filter`;
  separating them to reorder would give that up. **The real performance
  conversation is WP9** — browser reuse and HTTP caching attack the four
  minutes, not the 79 milliseconds.
- **`rules.json` stayed untouched, so WP8 landed in two stages — both now done
  (2026-08-27).** The seniority list lives in the gitignored `rules.json`, which
  CLAUDE.md puts on the never-touch list. WP8 therefore committed the keyword CSV
  and `rules.example.json` only, and left the owner one hand edit: drop
  `"Architect"` from `seniority_exclude_titles`. **The owner made that edit the
  same day, and it is verified: `seniority_exclude_titles` now holds 23 terms and
  no `Architect`, and `python -m job_scraper.eval` scores recall 0.868 against the
  520-row gold set.** So the live configuration is the 0.868 one, not the 0.824
  one. A later session measuring 0.824 is looking at a `rules.json` where the edit
  was lost or reverted — check the list before re-proposing the change.

  Worth keeping as a method note, since it cost a wrong answer in the WP8h
  session: the plan said "one hand edit outstanding" and stayed saying it after
  the edit was made, so a later session repeated it as still pending. **A plan
  entry describing something the owner must do by hand is stale the moment they
  do it, and nothing updates it automatically.** Read the file — `rules.json` is
  never-touch for *writes*, and always readable for a check like this.

- **Playwright's sync API cannot share a browser between threads, at all**
  (WP9, verified rather than inferred). Every object is bound to the greenlet of
  the thread that made it, so touching a `Browser` from another thread raises
  "Cannot switch to a different thread" immediately. A context per worker thread
  therefore means a *browser* per worker thread. That is why rendered fetches
  left the Layer 5 detail pool for `http.RenderPool`'s own four threads, each
  owning one browser for the whole run: it is the only shape that bounds the
  browser count without serialising rendering. `chromium.launch_server()` — the
  one way to get a single Chromium behind several connections — does not exist
  on the sync API. Do not re-derive this by trying the shared-browser version.

- **The response cache ignores `no-store` deliberately** (WP9). Six of fourteen
  sampled listing pages send `no-cache, no-store`; honouring it would re-download
  them every run, i.e. *more* load on other people's servers, which is backwards
  for priority 3. `cache_control=False` plus a 30-minute TTL, and conditional
  requests still go out with a stored ETag or Last-Modified. If a future session
  wonders why the polite-looking flag is off, this is why — it is the less polite
  setting here, not the more.

- **A cached listing cannot cost a stored job.** It returns the previous page,
  so stored jobs stay sighted and accrue no delisting misses. The worst case is
  a new posting found up to one TTL late. This is what makes caching compatible
  with priority 1, and it is the argument to re-check if anyone lengthens the
  TTL or starts caching rendered pages.

- **The 30-minute cache TTL is a considered number, not a default** (WP9, put to
  the owner and confirmed). Inside the window a run can serve a listing entirely
  from disk, so `source_health` records a successful scrape of a site that might
  be down — a bounded, milder cousin of the `stale_if_error` property below, and
  the one place the cache reports health it did not verify. Kept, on a timing
  argument: the only runs that hit the cache are ones fired within half an hour
  of the last, which is the rule-tweaking loop where the owner is at the keyboard
  and knows what just happened. A scheduled run finds the TTL long expired and
  revalidates against the site, so the unattended case — the one where a false
  health record would actually mislead — never reads from the cache at all.
  `--no-cache` covers the exception (re-running to see whether a site recovered).
  If anyone lengthens the TTL, that argument is what they are spending.

- **`stale_if_error` is off, and stays off** (WP9, the owner's call). Serving the
  previous copy when a site errors keeps a run going, but it reports a successful
  scrape of an old page into `source_health` — priority 2 wants the broken site
  to fail, and a WARNING is not a failure. The flaky-500s case it was proposed
  for belongs to `fetch_text`'s 5xx retry, which is still there and is pinned by
  the same test. It will look like free resilience to a future session; it is
  not, and this is the entry saying so.

- **An unreadable robots.txt allows the crawl** (WP10). RFC 9309 says a 5xx
  should be read as a site-wide "do not crawl", and for a search engine that is
  right. Here it is not: impactpool.org intermittently 500s, and a transient
  error that skips a source silently produces exactly the "no vacancies" shape
  priority 2 forbids. So an unreachable or erroring robots.txt is logged as a
  WARNING and the source is scraped; a *readable* one that says no is obeyed,
  because that is the case actually carrying the site owner's intent. Do not
  "harden" this into a fail-closed check without re-reading that argument.

- **The contact details live in `rules.json`, not in `http.py`** (WP10, put to
  the owner and chosen by them). The repo is public, so a real address in
  tracked code publishes it; `rules.json` is gitignored. `build_user_agent`
  assembles the header from `contact_url` / `contact_email`, and the fallback
  says `no contact configured` rather than naming a domain nobody owns — an
  invented contact is worse than an absent one, since following it teaches an
  administrator nothing. Every run without them logs a WARNING.

- **A cache hit refunds its turn at the host** (WP10). The throttle books the
  next slot before the request; a response `requests-cache` answered from disk
  put no load on the site, so it hands the booking back and the next real
  request does not queue behind it. Without the refund a warm-cache run would
  pay the full per-host delay for pages nobody was asked for — politeness
  theatre that costs the owner time and buys the site nothing.

- **`_DETAIL_WORKERS = 10` was never the politeness cap, and still is not**
  (WP10). It bounds this tool's threads; the per-host semaphore
  (`DEFAULT_PER_HOST_REQUESTS = 2`, `DEFAULT_HOST_DELAY = 1.0` in `http.py`)
  bounds what any one site sees. Ten workers now means up to ten *different*
  employers in parallel. Lowering `_DETAIL_WORKERS` to be kinder to a site is
  the wrong lever and makes every other source slower.

- **Politeness is run-scoped, like the WP9 resources** (WP10). `polite_fetching`
  installs the User-Agent, the throttle and the robots policy for the length of
  `run_pipeline` only. Outside it — tests, the fixture capture script, a one-off
  `fetch_text` — nothing applies, so no test pays a second per request and none
  of them reaches for robots.txt over the network. The pipeline is the only
  thing in this project that fetches at volume, which is what makes that scope
  the right one.

- **An empty page only ends a walk when something says how long the walk is**
  (WP11). `extractors/pagination.py` holds the policy: an extractor that can
  read a total must raise `ShortWalkError` on an empty page before that point,
  because "the listing ended" and "this page did not parse" look identical
  otherwise and the second is silent data loss. The module deliberately holds
  no shared loop: every listing announces its length differently, and only the
  extractor knows how. **Every paginated source here publishes something** — a
  pager, `totalFound`, `totalJobs`, "1-6 of 74 results", "Vacant positions: 2",
  "Results 1 to 10 of 2010", or a next-page link — so all six are guarded. The
  first pass through WP11 concluded four of them had nothing to read; that was
  a failure to look, not a fact about the sites. Where the count and a
  deduplicated walk legitimately disagree (Impactpool, an aggregator with
  postings promoted onto every page) the guard is the next-page link instead.

- **A paginated fixture must hold every page of its walk** (WP11). Replaying one
  saved page and an empty body afterwards fakes the end of the listing, which is
  how J-PAL's golden test passed on 9 of 37 postings. `capture_fixtures.py
  --pages all` records the whole walk and `recorded_pages_fetch` replays it in
  order. J-PAL is the only source captured this way today; re-capture it with
  the flag.

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

A paginated source needs its whole walk, not its first page (WP11):

```
python scripts/capture_fixtures.py --pages all jpal
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

### Result

Ran in two sessions on the same branch. The first had a fixture only for
`storytel` and fixed that one source, stopping on the other ten per the rule
against scraping live sites to investigate. The owner then ran
`scripts/capture_fixtures.py` for all ten and handed back the fixtures, so
this session finished the population against real evidence instead of
guesses. Final tally: **8 of 11 sources fixed (41 of the 54 rows recovered)**;
3 sources (13 rows) confirmed genuinely location-less on the page, which is
not an extractor bug and is left alone.

**storytel (5 rows) — the original fix, unchanged.** The page had been
redesigned since WP2 pinned its golden: titles moved from a `<div>` into a
`<span title="...">` sibling of the metadata `<div>`, and `teamtailor.py`'s
div-based title fallback started grabbing the metadata div instead — on every
row, not just the one WP2 read as a phantom "department heading". There never
was a separate phantom row. `teamtailor.py` now reads a `<span title>` before
falling back to the div.

**fjallraven (3), founders_pledge (6), futurelearn (3), planted (17), seven_perigee
(1) — 30 rows, fixed once real fixtures showed the actual markup.** All five
turned out to share a second redesign, distinct from storytel's: the title is
now bare text directly inside `<a>` (no `<span title>`, no `<h3>`), and the
metadata block is a `<div>` that is a **sibling** of `<a>`, not a child —
`teamtailor.py`'s old sibling-lookup only tried a sibling `<p>`, which no
longer exists anywhere in the captured markup. Two things had to change
beyond "look for a sibling `<div>` too":

- **Segment count is not fixed.** `founders_pledge`/`planted` show `Dept ·
  Location`; `fjallraven`/`seven_perigee` sometimes show only `Location` (no
  department chip at all — a real one-job case, not a parsing failure); and
  `futurelearn`'s board additionally prefixes a brand segment (`FutureLearn ·
  Admissions · Spain (remote)`), the multi-brand case the old docstring called
  out for its previous `<p>`-based markup. The old `_split_meta` assumed
  exactly two segments and treated a single leftover segment as *department*,
  which is backwards for `fjallraven`/`seven_perigee`. It now reads dept and
  location off the **end** of the segment list (`parts[-2]`, `parts[-1]`), so
  one segment is a location with no department, two is dept+location, three+
  drops the leading brand — one rule for all four shapes instead of a
  per-source flag.
- **The work-type tag ("Hybrid", "Fully Remote", ...) is detected structurally,
  not by string list.** The original `_WORK_TYPES` vocabulary
  (`hybrid`/`remote`/`on-site`) missed `planted`'s "Fully Remote" label, which
  would otherwise have been misread as the location on one row
  (`"Gebietsleitung Gastronomie & Foodservice"` — department/location came
  out as `"Berlin"`/`"Fully Remote"` before this fix). Every redesigned card
  wraps the work-type chip in its own `<span>` next to a `wd-icon`/`fa-wifi`
  icon regardless of its label text, so the new `_content_segments` picks it
  out by that structural marker instead of matching known strings — a label
  Teamtailor adds later, in any language, still gets classified correctly.

Golden checked by hand against the redesigned markup, not just re-run: e.g.
planted's `"Gebietsleitung..."` row now reads department `"Sales & Business
Development"` / location `"Berlin"`, correctly separating the actual base
office from the "Fully Remote" work-type tag that used to swallow it.

**bearingpoint_sweden (6 rows) — a third, unrelated redesign, same session.**
This extractor already had its own location selector (`a.find("p")`), which
had also gone stale: the page moved location from a `<p>` into a sibling
`<div class="columns job-info">` inside the same `<div class="row">` as the
title. Fixed by trying the `<p>` first (for an older-markup regional page, if
one still exists — no fixture confirms that) and falling back to
`div.job-info`.

**Three sources (13 rows) confirmed genuinely location-less, not fixed —
correctly so:**

- `against_malaria_foundation` (2) and `giving_what_we_can` (1): neither
  listing page has a location field anywhere in its markup, structured or
  otherwise — just free-text blurbs (e.g. "UK-based" in a paragraph) that
  this package's rules correctly forbid mining out of prose. Their extractors'
  `"location": ""` was never a bug; it was told the truth.
- `jpal` (confirmed 1 of its 2 rows; the second, "Research Assistant - World
  Bank Africa", is no longer on the live page and can't be checked against
  this fixture): the "Director / Associate Director - Research-Informed
  Delivery" node has no `div.job-teaser-country` in the HTML at all — an
  external partner listing J-PAL didn't tag with a country. Confirmed by
  reading the raw node, not inferred.
- `path` (8 of its 20 jobs): `workday.py`'s two location selectors both work
  correctly elsewhere in this same fixture (12 of 20 rows resolve fine, and
  the shared code is separately confirmed by the `busuu` golden), so this is
  not a code bug. All 8 empty rows share one cause, verified in the raw HTML:
  their Workday subtitle list contains only the requisition ID, with no
  location line rendered at all — these are consultancy postings PATH
  evidently listed without a location.

**Not attempted:** parsing a location out of a job title (e.g. planted's
`"... - Memmingen Germany"` suffix, which turned out to be redundant with a
real `location` field already recovered above, not a case that needed it) —
the package rules forbid this outside the extractor itself, and no source in
this population needed it once the real bug was found.

`data/curated/labels.csv` was not touched — the package rules say `eval.py`
can't score this work regardless (it replays the old empty location value),
and the refresh instruction below applies after a real run against the fixed
extractors, which is the owner's step, not this session's.

---

## WP8f — Empty location passthrough

```
Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP8f only.

An empty `location` field must stop rejecting a job on its own. WP8e confirmed
real postings hit RULE_LOC_EMPTY for want of a location nobody — extractor or
listing page — ever had.

Branch wp8f-empty-location-passthrough. Commit, do not push. Update the plan
file.
```

### Result

`matches_rules` in `job_scraper/filtering.py` gained a fourth Layer 0 outcome.
In the `locations or conditional_locations` branch, a new `elif not
loc_field.strip()` case sits right before the final rejecting `else`: an empty
field is admitted with its own reason, `_LOCATION_EMPTY_ADMITTED_REASON =
"locations: no location given (admitted)"`. Unlike WP8d's two pending
reasons, this one is not wired into `_resolve_hybrid`/
`_resolve_unresolved_location`/`UNVERIFIED_KEY` in `experience_filter.py` —
the job is settled here, permanently, and never carries a marker Layer 2
would go looking for or costs a detail fetch it would not otherwise need.

`_location_names_no_place` (WP8d's placeholder detector) and its position in
the branch chain were not touched — it already refuses an empty field by
design (`if not loc_field_cf.strip(): return False`), so the new branch and
WP8d's stay disjoint exactly as the package asked: empty, placeholder, match,
and non-matching place are four cases that can never overlap.

**`RULE_LOC_EMPTY` is deleted.** `_location_drop_rule` returned it only when
`loc_field` was empty, and the new branch now intercepts every empty case
before `_location_drop_rule` is ever called from `matches_rules` — its only
call site. The dead branch and the constant are both gone; `_location_drop_rule`'s
docstring says so. Per WP8a's decision-log entry, a drop-log rule string going
silent is a contract change, not a private implementation detail, so this is
called out here rather than left implicit: any external tooling grouping on
`RULE_LOC_EMPTY`'s string will no longer see it in the drop log or in future
`eval.py` output.

`pipeline.py` logs the volume alongside the existing `conditional_admits`/
`unresolved_admits` debug lines: `"Layer 0 (rules): %d jobs admitted with no
location given at all"`. Unlike the other two, this is explicitly not a
Layer-2-cost warning — the comment says why.

### Measured (WP8c harness, `data/curated/labels.csv`, 514 labelled jobs)

Baseline = this session's starting commit (`394f8c5`); after = this change.
No live scrape; both runs are `python -m job_scraper.eval` against the
owner's real gold set and `job_scraper/config/`.

| | before | after | Δ |
|---|---|---|---|
| review kept (true positives) | 32 | 41 | +9 |
| review dropped (false negatives) | 42 | 33 | −9 |
| discard kept (false positives) | 84 | 97 | +13 |
| precision | 0.276 | 0.297 | +0.021 |
| recall | 0.432 | 0.554 | +0.122 |
| F2 | 0.388 | 0.472 | +0.084 |

Nine previously-lost `review`-labelled jobs now survive Layer 0 outright
(not deferred — WP8f settles them, it does not merely postpone them like
WP8d's third state). The cost is thirteen more `discard`-labelled jobs also
kept, i.e. more rows the owner scrolls past — the expected shape of loosening
a rule that used to reject on sight, and the CLAUDE.md-endorsed trade (a false
positive costs a line in a spreadsheet; a false negative costs a job the
owner never learns exists).

### Tests

`tests/test_filtering.py`: `test_empty_location_is_admitted_not_deferred`
(replacing the old rejection-pinning test) asserts an empty field passes with
exactly `[_LOCATION_EMPTY_ADMITTED_REASON]` and does not also carry
`_UNRESOLVED_PENDING_REASON`; the existing placeholder tests
(`test_placeholder_location_count_is_pending_not_dropped`,
`test_the_code_shapes_work_without_any_configured_terms`) were left as-is and
confirm a placeholder field still takes WP8d's deferred path, unaffected.

`tests/test_drop_log.py`: `TestLocationDropRules.test_missing_location_field_is_admitted_not_rejected`
replaces the old drop-rule pin. The full-pipeline fixture's `no-location` job
(empty field) now reaches Layer 2 like any newly-seen job instead of dying at
Layer 0, so its downstream assertions were updated to match: the Layer-0 drop
count, the per-rule table (`no-location` no longer appears in it at all), the
`dropped_early` set in the no-extra-HTTP-request test, the location-substring
filter count, and the CSV export row count.

`tests/test_eval.py`: dropped the `RULE_LOC_EMPTY` import; `RULE_LOC_EMPTY` on
the fixture's "Office Coordinator" job (empty location, labelled `discard`) is
now kept rather than dropped, which shifts it from a true negative to a false
positive. `test_regression_pins_current_ladder`'s confusion matrix, precision/
recall/F2, and the per-layer `reached`/`dropped` counts were recomputed by
hand against the new `replay()` output (not guessed) and re-pinned; the false
negative listing itself is unchanged, since "Office Coordinator" was never a
wanted job.

Full suite: 359 passed (up from 356), `ruff check .` clean.

Not touched, per scope: `_location_names_no_place`'s ordering and WP8d's two
pending states, both as instructed; `eval.py` itself needed no code change —
the new state naturally falls out as an ordinary "kept" verdict with neither
pending flag set, which is exactly what a permanently-settled Layer 0 outcome
should look like to the harness.

---

## WP8g — ISS location extraction

**Do this before WP8, and note the labels refresh between them.** Found while
reviewing WP8f on 2026-08-20, not during a run. WP8e fixed the sources that
captured *no* location and WP8f then admitted that case at Layer 0. ISS is in
the same population and fell through both, because its extractor does not
return an empty string — it returns a wrong one.

### The evidence

In `data/curated/labels.csv` (545 rows, snapshot of 2026-08-18) **all 33 ISS
rows carry the literal string `"Title"` in the `location` column**, and all 33
are dropped at Layer 0 under `RULE_LOC_UNLISTED_CITY` — "city not on the list".
**Nine of them are labelled `review`**, i.e. jobs the owner wanted. That is the
same recall this project bought with the whole of WP8f, sitting in one source.

`"Title"` is a table column heading, so the reading is that the extractor is
picking up a header cell as if it were data.

### The mechanism — confirmed against the fixture, and not what was guessed

`successfactors_html._parse_listing` guesses the location as "the first text in
the nearest `li`/`tr`/`div` ancestor that is not the title"
(`successfactors_html.py`, the `texts[0]` heuristic). ISS is the only one of the
four SuccessFactors sources fetched with Playwright (`strategy: dynamic`,
`page_step=20`), and its rendered markup evidently puts the header row inside
the container that heuristic walks up to.

This was inferred from the labelled data plus a reading of the code. **No fixture
existed for ISS when this package was written and the live site was not
fetched**, so the first job of the package is to confirm the mechanism against
the captured HTML rather than assume it.

**Confirmed 2026-08-21, and the guess above was wrong in a way that mattered.**
`"Title"` is not a table column heading. ISS serves the *modern SuccessFactors
tile layout*, and the string comes from an accessibility label:

```html
<li class="job-tile ...">           <!-- the real row -->
 <div class="job-tile-cell"><div class="row job job-row">
  <div class="col-md-12 sub-section ...">
   <div class="oneline"><div class="tiletitle">
     <span class="sr-only">Title</span>          <!-- texts[0] -->
     <span class="section-title title" role="heading"><a href="/job/...">…</a></span>
   </div></div>
   <div class="oneline">
     <div class="section-field department ...">
       <span class="section-label sr-only">Job Category</span>
       <div id="…-department-value">Property Services</div>
     </div>
     <div class="section-field multilocation ...">
       <span class="section-label sr-only">Other Locations</span>
       <div id="…-multilocation-value">København K, DK, 1402</div>
     </div>
     …country, date…
```

Two things follow, and the second is the one the guess missed:

1. Every field is introduced by a `span.sr-only` naming it, for screen readers.
   The title's label sits *inside the title's own container*, so it is the first
   text that is not the title.
2. `a.find_parent(["li", "tr", "div"])` stopped at `div.tiletitle`, which holds
   the title and that label **and nothing else**. The real location was never in
   scope — it is two `div.oneline` siblings further up. So this was never a case
   of the heuristic picking the wrong candidate from a container; it was the
   heuristic reading a container that had no location in it.

That second point rules out the obvious minimal repair. Stripping `sr-only` and
leaving the container alone gives an empty location; stripping `sr-only` and
widening the container to `li.job-tile` gives `"Property Services"`, because the
tile layout orders job category before location. **The positional heuristic
cannot read this layout under any correction**, which is what forced a
structural fix rather than a patch.

Counts from `tests/fixtures/iss.html` (348,899 bytes, 20 jobs, one page):
20 tiles, each rendered three times (desktop/tablet/phone) for 60
`section-field` blocks per kind — `department`, `multilocation`, `country`,
`date`, present on every tile, no exceptions and no empties. Dedupe by
`detail_url` already collapses the three renderings.

The other three SuccessFactors sources are healthy on the same code path — DSV,
Novo Nordisk and Coloplast all produce real locations in the gold set
(`"Landskrona, Skane, SE, 261 51"`, `"Kalundborg, Region Zealand, DK"`). So this
is an ISS-specific markup difference, not a flaw in the shared extractor's
design, and the fix must not regress them. `tests/fixtures/dsv.html` and its
golden test are the guard.

### The fixture could not be captured, and that was step 0 — now resolved

The owner ran the capture on 2026-08-21 and it failed:

```
iss: FAILED (extractor made no request)
```

**Fixed in `8f63bd8`; the owner re-ran it the same day and it succeeded:**

```
iss: saved tests/fixtures/iss.html (348,899 bytes, 20 jobs) from https://jobs.issworld.com/search/?startrow=0
```

Both checks pass: 348 KB is a fully rendered page rather than a cookie wall, and
20 jobs is exactly `page_step`, so the AJAX list had loaded before the capture
read it. The `startrow=0` in the recorded URL is the proof the recorder saw the
extractor's own request rather than the bare listing URL.

Not a network problem. `successfactors_html.extract` takes a `fetch_text`
callable and then, when `dynamic=True`, **throws it away** and builds its own
`partial(fetch_rendered, wait_for_selector=...)` instead. `capture_fixtures`
works by handing the extractor a recording fetcher and keeping the first URL it
asks for; an extractor that fetches through its own private callable records
nothing, so `capture_one` reports that no request was made.

`workday.py` already does this correctly, and its comment names this exact
failure mode — test the fetcher's *capability* with `is_rendering_fetcher()`
and wrap the callable you were given, "rather than substituting fetch_rendered:
the caller's wrapper may be doing something (the fixture capture script records
the URL through it)". `busuu` and `path` are the two dynamic sources that do
have fixtures, and both are Workday. That is the pattern to copy.

So ISS's location bug was never capturable, which is why no fixture existed to
catch it. Two bugs, one behind the other.

`niras.py` has the same bypass, and worse: its `if fetch_text is fetch_rendered`
branches are byte-identical, so the conditional decides nothing. `niras` and
`iss` are the only two sources this affects — every other dynamic source uses an
extractor that honours the fetcher it is given. Note it, do not fix it here: it
has no fixture either, so a fix cannot be verified in this package.

### The owner's command, once step 0 lands

```
python scripts/capture_fixtures.py iss
```

Writes `tests/fixtures/iss.html` (sanitised, through Playwright since `iss` is
`strategy: dynamic` in `sources.yaml`) and prints the byte size and the number
of jobs parsed. Check both: a cookie wall or an unrendered page is a successful
HTTP response and a much smaller file. A capture reporting 0 jobs, or rows that
do not show the `"Title"` bug, changes the package — say so rather than
proceeding.

This is the only step that touches the network, and the package rules forbid
the session running it. Expect to stop and ask for it mid-package.

```
think

Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP8g only.

Two bugs, one behind the other. Read the WP8g section in the plan file first.

The ISS extractor puts the literal word "Title" in every posting's location
field. All 33 ISS rows in the gold set do this, all 33 are dropped as "city
not on the list", and 9 of them are jobs the owner wanted.

There is no fixture yet, because the second bug prevents making one.

0. UNBLOCK THE CAPTURE FIRST. successfactors_html.extract discards the
   fetch_text it is handed when dynamic=True and calls fetch_rendered itself,
   so scripts/capture_fixtures.py records nothing and reports "extractor made
   no request". Fix it the way workday.py already does: test capability with
   is_rendering_fetcher() and wrap the callable you were given. Read workday's
   comment before you write it — it describes this exact failure.

   The `dynamic` registry flag and sources.yaml's `strategy: dynamic` now say
   the same thing twice. Decide whether the flag still earns its place and say
   which you chose; do not quietly leave both if only one is load-bearing.

   Verify: the dsv golden test still passes (dsv is static and shares this
   code path), then STOP and ask the owner to run
   `python scripts/capture_fixtures.py iss`. Do not fetch it yourself.

1. CONFIRM THE CAUSE against the fixture before changing a line. Read the raw
   markup around a job link and establish what the container actually holds
   and why texts[0] is the header. If the cause is not what the plan file
   guesses, follow the fixture and say so in the plan.

2. FIX IT in job_scraper/extractors/successfactors_html.py. The bar is that
   ISS rows come out with a real place in `location`, or with an honest empty
   string where the listing genuinely gives none — WP8f admits empty fields
   now, so an honest blank is an acceptable outcome and a wrong value is not.

   Do not special-case ISS by source_name if the markup supports a structural
   fix that serves all four SuccessFactors sources. A header cell is not a
   location for DSV either; it just does not currently appear in DSV's markup.

3. DO NOT REGRESS the other three. dsv is the one with a fixture and a golden
   test; novo_nordisk and coloplast share the code path with no fixture, so
   reason about them explicitly in the plan file rather than only running the
   suite.

4. WIRE THE FIXTURE IN. Add an `iss` entry to tests/fixture_cases.py
   FIXTURE_CASES with its registry arguments (page_step=20,
   base_search_url="https://jobs.issworld.com/search/"), so the golden-file
   and capture-script tests pick it up like every other fixture. Add a test
   pinning the specific bug: no ISS row's location may be the word "Title".

5. DO NOT EXPECT eval.py TO SHOW A GAIN, and do not report one. Per the
   decisions log, the harness replays labels.csv's stored location column, so
   a fixed extractor scores as no change until the owner refreshes it. Running
   it to show *no regression* is fine; quoting a recall improvement is wrong.
   Say in the plan that the 9 jobs are the expected gain once refreshed.

Do not touch filtering.py, the location rules, or WP8d/WP8f's states. Do not
fix niras.py's identical bypass — note it. If the fixture shows a further
unrelated extractor bug, note that at the end too rather than fixing it.

Branch wp8g-iss-location. Commit, do not push. Update the plan file.
```

### Result — done 2026-08-21, branch `wp8g-iss-location`

Two commits, both local, not pushed.

**Step 0 — the capture unblocked (`8f63bd8`).** `successfactors_html.extract`
now tests `is_rendering_fetcher(fetch_text)` and wraps *the callable it was
handed* with the selector wait, instead of building its own
`partial(fetch_rendered, …)`. Copied from `workday.py`, comment and all.

**The `dynamic` flag is gone; `sources.yaml`'s `strategy` is what remains.**
Asked to decide which of the two duplicated facts earns its place, and the
answer is not symmetric: `pipeline.py` and `scripts/capture_fixtures.py` both
choose the fetcher from `strategy` *before* the extractor is called, so
`strategy` is load-bearing whether or not the flag exists. The registry flag
only ever let the extractor overrule that choice after the fact, and overruling
it **was** the bug. So the parameter is deleted from `extract`, the `dynamic=True`
line is deleted from the `iss` registry entry, and the module docstring now says
plainly that the fetcher is the caller's business. Nothing else passed it.

**Step 1 — the cause, and the plan file was wrong.** See the confirmed
mechanism above: a `span.sr-only` accessibility label, not a table header, plus
a container that never contained the location. Recorded rather than quietly
fixed, because the wrong guess would otherwise have justified a much smaller
change that does not work.

**Step 2 — the fix (`f4afd89`), structural and not keyed on `source_name`.**
`successfactors_html.py` now:

- walks up to the row holding the whole posting (`li`/`tr`) rather than the
  innermost `div` that happens to wrap the link;
- prefers the labelled `div.section-field.<kind>` blocks when the row has them
  (`location`/`multilocation`, `department`), falling back to the positional
  heuristic when it does not — the same shape as `workday.py`'s "prefer the
  dedicated locations element, fall back to subtitle" for its own two layouts;
- strips screen-reader-only text from the positional fallback as well.

That last one is the general guard the package asked for: a field label is not a
location for DSV either, it just does not appear in DSV's markup today. `_tile_field`
returns `None` for "this layout does not label its fields" and `""` for "the field
is here and genuinely empty" — WP8f admits the empty case at Layer 0, so the
distinction has to survive as far as the caller.

Result on the fixture: **20 of 20 ISS rows carry a real place**, none empty —
`København K, DK, 1402`, `Porto, PT, 4000-457`, `Warszawa, PL, 00-841`, the same
`City, CC, postcode` shape DSV and Novo Nordisk produce. Departments come out as
real job categories (`Cleaning`, `Finance`, `IT`) rather than blank.

**Step 3 — the other three SuccessFactors sources.**

*DSV* has the fixture and the golden test. Its rows are the classic table
layout: `tr.data-row` → `td.colLocation` → `span.jobLocation`. Measured on
`tests/fixtures/dsv.html`: **0 `section-field` and 0 `sr-only` elements inside
its 10 job rows**, so it takes the unchanged fallback branch, and its container
was already `tr` before the change (`find_parent(["li","tr","div"])` and
`find_parent(["li","tr"])` agree here). Golden output is byte-identical,
including `department: "7 Aug 2026"`.

That date-as-department was a real pre-existing quirk. It was first left alone
on the grounds that `department` feeds `filtering._haystack` and WP8 measures
keyword costs against it — **and that reasoning was wrong.** `eval.py`'s
`MISSING_FIELDS` is `("raw_snippet", "department")`: the gold set has no
department column at all, so the harness is blind to the field in both
directions and correcting it cannot move WP8's baseline. Owner's call, same
session: fix it now rather than open another package. See the fourth commit.

*Novo Nordisk* and *Coloplast* have no fixture, so the argument has to be made
rather than run — and it can be made from the gold set, which is evidence, not
assumption. Both produce correct locations today (`Kalundborg, Region Zealand,
DK`), and today's code is the positional heuristic. That output is only possible
on the classic layout: on the tile layout the same heuristic returns the sr-only
label, which is precisely the ISS symptom, and neither source shows it. So both
are on DSV's branch. For them the change is therefore exactly two things, both
inert:

- the container walk, which only differs when the nearest `div` is *tighter*
  than the nearest `li`/`tr`; on a table layout the nearest `div` ancestor of
  the link is already the row or above it, so the walk lands in the same place;
- the `sr-only` strip, which can only remove text that a screen-reader label
  contributed — and if either page had such a label in its rows it would already
  be reporting `"Title"`-style junk as the location. Neither does.

If either ever migrates to the tile layout, they now land on the structural
branch and keep working, which is the point of not special-casing ISS.

**Superseded — read the follow-on section below.** The layout half of this
argument held for both sources. The "both changes are inert" half did not: it
predates the classic structural read added later in the session, and coloplast
turned out to differ on every row, for two reasons the reasoning could not have
reached. The prediction is left here rather than edited away, because the gap
between it and the capture is the most useful thing this package produced.

**Step 4 — the fixture is wired in.** `iss` added to `FIXTURE_CASES` in
`tests/fixture_cases.py` with `page_step=20` and
`base_search_url="https://jobs.issworld.com/search/"`, so the capture-script
tests, the golden-file tests and the CSV round-trip test all pick it up. An
`iss` golden entry was required by `test_every_fixture_has_a_golden`, which is
the mechanism working as designed.

Plus `test_iss_location_is_never_the_field_label`, pinning this specific bug
across **every** row rather than the golden's first job only: the failure was
uniform across all 33 gold-set rows, and a heuristic that regressed for rows
2..n while row 1 stayed correct would slip past a first-job assertion. Checked
that the pin can actually fail — run against `main`'s extractor it reports
"20 of 20 ISS rows have the column label 'Title' as their location".

**Step 5 — eval shows no change, as predicted, and that is the correct
outcome.** Before and after are identical: precision 0.297, recall 0.554, F2
0.472. Per the decisions-log entry, `eval.py` replays `labels.csv`'s stored
`location` column, so a fixed extractor cannot score differently until that
column is refreshed. This was run to demonstrate **no regression** and nothing
more. **The expected gain is the 9 ISS jobs labelled `review`, and it will
appear only after the owner's run and the labels refresh below.** Any recall
number quoted for WP8g before that refresh is measuring the old strings.

Full suite: 365 passed (up from 359 — the new pin, the `iss` golden, and four
parametrised fixture tests that now include `iss`). `ruff check .` clean,
`python -m job_scraper.run --help` works.

Not touched, per scope: `filtering.py`, the location rules, and WP8d/WP8f's
states; DSV's department quirk, above; `niras.py`, below.

### Folded in, and what is left

- **`niras.py`'s bypass is fixed, the fixture is captured, and it hid a second
  bug — exactly the shape ISS had** (owner's call, same session). Its
  `if fetch_text is fetch_rendered:` / `else:` branches were **byte-identical**
  — both built `partial(fetch_rendered, …)` — so the conditional decided
  nothing and the capture recorder never fired.

  This was held back until the owner confirmed one fact, because unlike
  `successfactors_html` it was not unconditionally safe: `niras.extract` said
  "Always use Playwright" and its docstring says the static HTML is only the
  filter shell, so honouring a plain fetcher would have parsed the shell and
  returned **zero jobs** — a silent empty list dressed as "no vacancies".
  Confirmed 2026-08-21: `sources.yaml` has `niras` as `strategy: dynamic`, so
  the caller already passes a rendering fetcher and the conversion is inert at
  runtime. Verified both directions: a rendering fetcher still gets the selector
  wait and the recorder now fires; a plain fetcher is now used as given rather
  than silently replaced.

  **The capture then showed the second bug immediately.** `title` was "the first
  child's text", but the anchor's only element child is the wrapping
  `div.box-content`, so every title arrived with the entire card appended:

  ```
  7.004 Expert Communication institutionelle Country: Tunisia Employment:
  Temporary Commencement: 02/09/2024 Position length: 300 Deadline: Sep 1, 2026
  ```

  Same root cause as ISS, and the same fix: the card labels its title
  `p.headline`, so read the label instead of guessing positionally. Both rows
  were affected. The measured filtering cost today is **nil** — the injected
  metadata happens to match no exclude keyword — but `title` is what the store
  keeps and the spreadsheet shows, and the pollution is latent for any future
  keyword matching `Temporary`, a date, or `Position length`. The module
  docstring's markup sketch was wrong too (it showed `<generic>` children that
  do not exist) and now matches the real card.

  Wired into `FIXTURE_CASES` with a golden. **Two jobs is correct, not a
  truncated capture**: no filter input is checked and the page's own counter
  reads "Vacant positions: 2", so the extractor found everything there is.

  With this, **no extractor in the project fetches through a private callable**.
  Verified rather than assumed: no module under `job_scraper/extractors/` still
  imports or calls `fetch_rendered`; the three that mention it (`workday`,
  `successfactors_html`, `niras`) do so only in the comment explaining why they
  do not. Every source is now capturable, and both sources that were never
  capturable turned out to be carrying a data bug behind the bypass — which is
  the argument for capturing the rest rather than waiting for symptoms.

- **`tests/fixtures/niras.html` is 1.2 MB, and it should stay that way.**
  Investigated rather than left as a nag: **88% of the file is 19 inline
  `<svg>` elements** (1.10 MB of 1.25 MB); styles are 2%, scripts already
  stripped. Adding `<svg>` to `sanitise_html` would cut it to roughly 150 KB,
  costs nothing today (no extractor selects `svg`, no test pins one, and no
  `<svg>` inside a job link in *any* of the 13 fixtures that have one is
  text-bearing) — **and it is still the wrong trade.**

  The reason is the sanitiser's own doctrine. It strips analytics scripts
  because nothing parses them, but deliberately keeps scripts carrying job data,
  so that a fixture stays a faithful record of what the extractor sees. `<svg>`
  is in the second category, not the first: it contributes to `get_text()`, and
  several extractors read exactly that. An SVG bearing a `<title>` or `<text>`
  node — none do today, nothing stops one tomorrow — would then parse one way
  live and another from the fixture, which is the one failure a fixture exists
  to prevent. 3.2 MB across the whole directory is not worth that.

  Revisit only if the directory becomes a real problem, and if so strip `<svg>`
  at capture time for *all* sources at once, so no fixture is quietly different
  from its neighbours.

### Loose ends closed afterwards (2026-08-21)

Asked to finish what the session had left open. Four of the five are now closed;
the fifth needs the network and is the owner's.

- **`ruff` ambiguity resolved.** CI runs bare `ruff check .` after
  `pip install -r requirements.txt`, which floats on `ruff>=0.16.0`; the
  session's `.venv/bin/ruff` is 0.16.1 on Python 3.13.12. Same tool, same
  config, so the clean result stands. The residual risk is the floating pin
  itself, and it is the known one: per the `httpx` entry above, a `.venv` that
  predates a release does not reproduce CI, so green locally is not green in CI.
  Nothing specific to this package.

- **NIRAS's parsing is now audited, not just its title.** Three tests in
  `tests/test_niras_extractor.py`, all derived from the captured fixture rather
  than hand-written markup — the extractor's docstring had the page's shape
  wrong once already, so a test written from the same assumption would have
  agreed with the bug. They cover: every row's title being the headline rather
  than the card body (the golden only sees row 1); a card with its `Country:`
  line deleted from the real markup, which must give an honest `""` and not the
  neighbouring metadata; and the card's field list being exactly
  Country/Employment/Commencement/Position length/Deadline. That last one
  settles `department`: **the card has no department field, so `""` is the
  correct answer rather than a gap**, and a card that gains one becomes a
  visible failure instead of a silently blank column. Both bug pins were checked
  against the pre-fix extractor and both fail on it.

- **The 1.2 MB fixture** — investigated and deliberately kept; see above.

- **The eval caveat needs no fix, only stating plainly**, which it now is: the
  harness replays the stored `location` and cannot see `raw_snippet` or
  `department` at all, so identical numbers across this package were guaranteed
  and prove only that the ladder still works. **What actually guards extractor
  output is the golden files**, and that is where every claim in this package
  was verified.

- **`novo_nordisk` and `coloplast` are now captured, and the inference about
  them was half wrong.** Both are the classic table layout on `tr.data-row`,
  with no `section-field` and no `sr-only`, exactly as argued. But the argument
  that the package was *inert* for them was written before the classic
  structural read was added at the owner's request, and it did not survive:

  - **novo_nordisk: inert, confirmed.** All 100 rows byte-identical to `main`.
  - **coloplast: all 19 rows changed**, and the capture then exposed two bugs
    that no amount of reasoning would have found.

  **Bug 1 — silent job loss, the project's worst failure mode.** Coloplast hosts
  sub-brands, and their postings link as `/Kerecis/job/…` and `/Atos/job/…`.
  `_parse_page` matched only hrefs *starting* `/job/`, so **6 of 25 rows were
  dropped with no error and no empty field** — the page simply appeared to have
  19 jobs. The href match now allows one optional leading segment
  (`^(?:/[^/]+)?/job/`): deliberately one, so it admits a brand prefix without
  matching arbitrary deep paths. Verified across all four fixtures — it recovers
  exactly those 6 and changes nothing for DSV, Novo Nordisk or ISS. A knock-on:
  the count rising 19 → 25 also un-breaks pagination, which was stopping early
  because `len(jobs) < page_step` was true only because of the dropped rows.

  **Bug 2 — the department column has a third class name.** DSV and Novo Nordisk
  label it `span.jobFacility` under a "Category" heading; Coloplast uses
  `span.jobDepartment` under "Job Family". Reading only `jobFacility` blanked
  every Coloplast row. The selector now accepts both. Two of the 25 rows are
  still blank, and that is honest — their markup is literally
  `<span class="jobDepartment"></span>`.

  Both are pinned: goldens for both sources, plus
  `test_coloplast_keeps_sub_brand_postings`, which fails if the href match ever
  narrows back. Checked that it does fail on the old pattern rather than
  assuming.

  **The lesson, stated plainly because this package keeps re-learning it:**
  every one of the four sources that got a fixture in this package was carrying
  a bug that inference had not found — ISS's locations, NIRAS's titles,
  Coloplast's dropped rows and blank departments. The one source predicted to be
  clean (novo_nordisk) was clean. Reasoning correctly identified the *layout*
  every time and missed the *data* every time. Capture the fixture.

### After the session, before WP8

Two owner steps, in this order:

1. A real run, so the store holds ISS rows with corrected locations.
2. Refresh the `location` column in `data/curated/labels.csv` from the store,
   keyed on `dedupe_key` — the review/discard judgements are about the jobs and
   must not be re-labelled. See "the gold set is blind to extractor changes" in
   the decisions log.

Only then take WP8's baseline. Skipping the refresh means WP8 prunes the keyword
list against a gold set where these 9 wanted jobs are still blocked at Layer 0
and never reach the keyword layer to be counted.

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

### Baseline, measured 2026-08-24 — use this, not WP8d's table

WP8d's before/after table is no longer the baseline. **WP8e**, WP8f, WP8g and
the gold-set location refresh have all landed since. WP8e matters most of the
four and is the easiest to overlook, because its gain was invisible until the
refresh: it populated locations for sources that had none, and four BearingPoint
roles that turned out to be in Malmo — a listed city — had been sitting in the
gold set as unlocatable ever since. WP8g contributed one source, ISS, and
mostly by correcting wrong values rather than recovering jobs. The refresh moved
the gold set itself: `scripts/refresh_label_locations.py` corrected 63 stale locations, the
owner re-judged the 13 that had been labelled without one, and six jobs that had
ended up labelled both ways were reconciled. The set now scores **520 labelled
jobs — 68 review, 452 discard**, up from 514, and every number below is measured
against it with `python -m job_scraper.eval` on the owner's real config.

|  | value |
|---|---|
| review kept / dropped | 44 / 24 |
| discard kept / dropped | 92 / 360 |
| precision | 0.324 |
| recall | 0.647 |
| F2 | 0.539 |

| layer | reached | dropped | wanted lost |
|---|---|---|---|
| 0-rules | 520 | 132 | 4 |
| **1a-title-keyword** | 388 | **202** | **20** |
| 1-seniority | 186 | 42 | 0 |
| 1c-non-english | 144 | 3 | 0 |
| 1b-language | 141 | 5 | 0 |

**The keyword layer causes 20 of the 24 remaining false negatives.** It is the
only layer in the ladder still costing wanted jobs in volume, which is what makes
step 2 the substance of this package and steps 1 and 3 housekeeping around it.

Per-keyword cost, already broken out by the harness — re-derive rather than
trust it, but this is where to start:

| lost | of drops | keyword |
|---|---|---|
| 4 | 12 | `Specialist` (word) |
| 4 | 4 | `Security` (word) |
| 3 | 6 | `leader` (word) |
| 2 | 2 | `SEA` (word) |
| 1 | 14 | `engineer` (prefix) |
| 1 | 7 | `architect` (word) |
| 1 | 6 | `AI` (word) |
| 1 | 6 | `lab` (prefix) |
| 1 | 3 | `owner` (word) |
| 1 | 1 | `clerk` (word), `intern` (word) |

Two of those drop *only* wanted jobs: `Security` (4 of 4) and `SEA` (2 of 2).
WP8c's guess about `SEA` matching "Baltic Sea" is confirmed on the current set.
A keyword whose every drop was wanted is the easy case; the argument to weigh is
`Specialist` and `engineer`, which cost wanted jobs while also doing real work.

Read the recall figure with the caveat the harness prints: of the 44 review jobs
kept, 5 are deferred to Layer 2 on an unresolvable location and 3 on a hybrid
gate. Layer 2 fails closed, so those are a ceiling, not banked recall.

```
think hard

Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP8 only. Read the WP8
preamble in the plan file before this prompt: an earlier version of it assumed
the LLM scorer was running, and it is not, and its baseline predates three
packages and a gold-set refresh.

Confirm the baseline first — it is recorded in the preamble, dated 2026-08-24.
Re-run it rather than trusting the table, and say so if it has moved. Do not run
the live pipeline to get it:

  python -m job_scraper.eval

WP8c built that harness for exactly this package. Compare configurations with
--compare against a copy of job_scraper/config/ rather than scraping twice.

1. DELETE apply_non_english_text_filter (Layer 1c) and apply_language_filter
   (Layer 1b), with their config keys, tests and drop-log layer constants.

   Be honest about why in the plan file. The argument is NOT that the scorer
   covers them, because it is switched off. It is that they are two layers of
   complexity that between them fire eight times on the 520-row gold set, cost zero
   wanted jobs, and one of them (non-English) is already inert on the
   refilter_stored_jobs path because stored rows carry no raw_snippet, while
   langdetect is nondeterministic enough that the harness has to seed it.
   Deleting them keeps roughly eight more unwanted jobs and loses nothing.
   Measure the real number with --compare and report it. If it comes back
   materially worse than that, stop and say so rather than pressing on.

2. DO NOT delete config/title_exclude_keywords.csv. Nothing subsumes it with
   scoring off. Prune it instead, with evidence:

   - The current numbers say the keyword layer costs 20 wanted jobs. Use
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
   seniority layer currently drops 42 jobs and loses zero wanted ones, so the
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

### Result — done 2026-08-27, branch `wp8-trim-ladder`

352 tests pass, `ruff check .` clean, `--help` still works.
(`tests/test_scoring.py` does not collect: `anthropic` is not installed in this
environment. It fails identically on `main` — pre-existing, not this package.)

**The baseline held.** `python -m job_scraper.eval` reproduced the 2026-08-24
table exactly — 520 labelled jobs, 68/452, precision 0.324, recall 0.647,
F2 0.539, and every per-layer row unchanged. Nothing had moved underneath it.

#### The finding that reorganised this package

**The per-keyword table above counts attribution, not cost.** A keyword is
credited with a drop when it is the *first configured* term to match the title.
Removing it only changes a verdict if nothing further down the ladder also
catches that job. Measured marginally — remove one keyword, re-run, diff — only
**38 of 112 keywords change any verdict at all**, and three of the names the
table singles out change none:

| keyword | credited | marginal | what actually catches it |
|---|---|---|---|
| `SEA` (word) | 2 lost | **0** | both titles also say "Senior" — the seniority layer |
| `AI` (word) | 1 lost | **0** | another keyword on the same title |
| `architect` (word) | 1 lost | **0** | `Architect` in `seniority_exclude_titles` |

So the instruction to re-derive rather than trust the WP8c figures was the
right one, and it inverted the answer: **`SEA` is not a worst offender on this
data, it is inert.** WP8c's guess about "Baltic Sea" and "Air & Sea" is still
correct as a description of the pattern — it does match both — but both titles
carry "Senior" and the seniority layer would drop them anyway. The same masking
runs the other way between `Security` and `architect`, which is why removing
either alone recovers one job and removing both recovers one job.

This is why every number below is a marginal measurement against a stated
baseline, and why the seniority question in step 3 could only be answered after
step 2 had removed the keywords masking it.

#### 1. Layers 1c and 1b, deleted

Not because the scorer covers them — it is still off, and `scoring_enabled` is
still `false`. Because their entire measured output on 520 rows is eight
discard-labelled jobs.

| | before | after | delta |
|---|---|---|---|
| precision | 0.324 | 0.306 | −0.018 |
| recall | 0.647 | 0.647 | +0.000 |
| F2 | 0.539 | 0.529 | −0.010 |
| discard kept | 92 | 100 | +8 |
| wanted lost | 24 | 24 | 0 |

Eight more unwanted jobs reach the review pile; no wanted job is lost. That is
exactly the "roughly eight" the prompt predicted, so nothing here warranted
stopping.

Two corrections to the prompt's own reasoning, both in the deletion's favour:

- **"Already inert on the `refilter_stored_jobs` path" is too strong.** Stored
  rows do carry no `raw_snippet` — there is no such column in the `jobs` table
  — but `apply_non_english_text_filter` falls back to the title, and detects
  whenever that exceeds 50 characters. All three of its gold-set drops fired on
  title alone. The layer was weakened on that path, not disabled.
- **The better argument is what those three drops were.** One of them is a
  misdetection: *"Regional Readiness Consultant - Latin America Region"*, an
  English title, read as Italian. On title-only input — which is all the
  refilter path and the gold set ever supply — langdetect got one in three
  wrong, and it seeds itself randomly, which is why WP8c had to pin it before
  it could measure anything at all. A layer that is one-third wrong on the only
  evidence available, non-deterministic, and worth two discards is not carrying
  its complexity.

Layer 1b was the more accurate of the two: five drops, all "Swedish-speaking
Customer Support" roles in Malta, Barcelona and Cyprus, all correctly unwanted.
It is deleted for cost, not error — five rows is not a layer.

**There were no config keys to delete.** The prompt anticipated some; neither
layer ever had one. `rules.json` gates the seniority filter and the location
rules only.

Also removed with them: `langdetect` from `requirements.txt` (its only caller
is gone), the `LAYER_NON_ENGLISH` / `LAYER_LANGUAGE` drop-log constants, the
two `RunSummary` counters and their lines in the run summary, and the eval
harness's `_seed_langdetect`. `test_replay_is_deterministic` stays — the
replayed ladder is now deterministic by construction, and that test is what
would notice if a later layer reintroduced a coin flip.

The eval fixture's two rows for these layers are **kept, not deleted**: both
are labelled discard, so they now land as false positives, and the regression
pin moved from `(4, 2, 3, 5)` to `(4, 4, 3, 3)`. That is the price of the
deletion, recorded where a future refactor will trip over it.

#### 2. The keyword list, pruned — not deleted

`config/title_exclude_keywords.csv` stays. Nothing subsumes it with scoring
off. 112 entries → 106.

Each row is the marginal effect of removing **that one keyword**, against the
post-step-1 baseline (precision 0.306, recall 0.647, F2 0.529):

| keyword | +wanted | +unwanted | Δprecision | Δrecall | ΔF2 | verdict |
|---|---|---|---|---|---|---|
| `Specialist` (word) | +4 | +6 | +0.006 | +0.059 | **+0.035** | removed |
| `leader` (word) | +3 | +3 | +0.008 | +0.044 | **+0.028** | removed |
| `intern` (word) | +1 | 0 | +0.005 | +0.015 | **+0.011** | removed |
| `Security` (word) | +1 | 0 | +0.005 | +0.015 | **+0.011** | removed |
| `clerk` (word) | +1 | 0 | +0.005 | +0.015 | **+0.011** | removed |
| `owner` (word) | +1 | +2 | +0.001 | +0.015 | +0.008 | removed |
| `lab` (prefix) | +1 | +2 | +0.001 | +0.015 | +0.008 | removed |
| `engineer` (prefix) | +1 | +8 | −0.011 | +0.015 | +0.001 | **kept** |
| `SEA` (word) | 0 | 0 | 0.000 | 0.000 | 0.000 | **kept** |
| `AI` (word) | 0 | 0 | 0.000 | 0.000 | 0.000 | **kept** |

`intern`, `Security` and `clerk` are the easy cases: every job they cost was
wanted and they hold back nothing. `Specialist` and `leader` are the ones worth
arguing about, and they are also the two biggest wins — four UN/UNOPS
specialist roles and three "Agile Project Leader"/"Channels Leader" roles, for
six and three unwanted rows respectively. At beta=2 that trade is clearly
right; it would not be at beta=0.5.

**Kept deliberately:**

- `engineer` (prefix) buys one wanted job for eight unwanted. F2 +0.001 is
  inside the noise and precision drops 0.011 — it pays in the metric the ladder
  is already worst at. One job is not worth it.
- `SEA` and `AI` earn nothing either way. The plan's own standard is that
  removing a keyword that costs nothing is churn, so they stay. `SEA` is on
  record as a latent misfire that this gold set happens to mask; if a future
  "Air & Sea Coordinator" arrives without a seniority word, it will fire, and
  this entry is where to look.

**The graduate-entry family, removed on the owner's instruction after the
measured prune (2026-08-27).** Four more entries went, on the owner's judgement
rather than on harness evidence — `trainee`, `internship`, `Praktikant`,
`Praktikum`. The list is 106 → 102. What each one actually cost:

| keyword | +wanted | +unwanted | ΔF2 | measured? |
|---|---|---|---|---|
| `trainee` (word) | 0 | 0 | 0.000 | no gold-set title contains it |
| `Praktikant` (word) | 0 | 0 | 0.000 | no gold-set title contains it |
| `Praktikum` (word) | 0 | 0 | 0.000 | no gold-set title contains it |
| `internship` (word) | 0 | **+1** | **−0.001** | yes — a small regression |

Three of the four are invisible to the harness. The fourth is not: removing
`internship` admits *"Supply Chain Internship Roster 2026 … Copenhagen"*, a
posting **the owner labelled discard**. Precision 0.343 → 0.341, F2 0.664 →
0.663, recall unchanged. That is a real if tiny cost, recorded rather than
rounded away.

The reasoning is out-of-sample and the gold set cannot see it: `intern` was
removed on evidence (it cost a wanted UNDP HR internship), and these four are
the same graduate-entry family. The owner has said they may revisit this.
**To reverse it, restore these four lines to
`config/title_exclude_keywords.csv`** — `trainee,word`, `internship,word`,
`Praktikant,word`, `Praktikum,word` — and re-run `python -m job_scraper.eval`;
the expected numbers are precision 0.343, recall 0.868, F2 0.664. Note the file
is CRLF-terminated.

`Ausbildung` (prefix), `Aushilfe` (prefix), `Student` (prefix) and
`Werkstudent` (prefix) remain: same broad family, not asked for, and equally
unmeasured on this set.

**`architect` (word) is replaced, not removed.** Bare "architect" is a job
family, not a level: 4 of the gold set's 16 architect titles are labelled
review, and every architect title the owner rejected *on level* already says
Senior, Lead or Principal. In its place go `Solution Architect` and
`System Architect` — the two compounds that, measured, only ever caught jobs
the owner did not want.

**Step 2 as committed** (`--compare` against the post-step-1 config):

| | before | after | delta |
|---|---|---|---|
| precision | 0.306 | 0.331 | **+0.026** |
| recall | 0.647 | 0.824 | **+0.176** |
| F2 | 0.529 | 0.635 | **+0.106** |
| false negatives | 24 | 12 | −12 |
| newly dropped | — | — | **0** |

Nothing that used to be kept is now dropped.

#### 3. Seniority: `Architect` is real, `Lead` is not

The prompt was right to demand evidence before acting, and the evidence splits.

**`Lead` — claim not supported; the list is unchanged.** The original prompt's
example was "Lead Generation Analyst". **No such title exists in the gold set.**
Of the 21 titles containing `\bLead\b`, 20 are labelled discard, and they are
genuine senior roles: "S&OP Lead", "Portfolio Manager: Growth & Strategy Lead",
"Remote Handling Group Lead". Removing `Lead` returns 13 unwanted jobs and zero
wanted ones (Δprecision −0.024, ΔF2 −0.018). The one wanted title with "Lead"
in it — *"Lead ASIC SoC Architect for High-Performance Security"* — is
recovered by the architect fix below, not by touching `Lead`. **Left alone.**

`Senior`, `Director` and `Head of` were checked the same way and are likewise
clean (0 wanted, 17 / 10 / 4 unwanted respectively).

**`Architect` — demonstrated, and narrowed rather than deleted.** This could
only be seen after step 2: `architect` in the CSV and `Architect` in
`seniority_exclude_titles` mask each other perfectly, and removing *either
alone changes nothing at all*. Removing both recovers three wanted jobs:

- Software Architect (Airbus)
- ASIC SoC Security Architect, Lund (Axis)
- ASIC SoC Security Architect: Embedded Crypto & TEEs (Axis)

The gold set makes the category error plain: **"ASIC SoC Security Architect" is
review and "ASIC SoC System Architect" is discard.** Those differ by one word,
and it is not a seniority word. The layer was doing topic filtering by
accident.

Narrowings measured, all against the pruned-keyword baseline:

| option | +wanted | +unwanted | Δprecision | Δrecall | ΔF2 |
|---|---|---|---|---|---|
| drop `Architect` outright | +3 | +6 | +0.000 | +0.044 | +0.021 |
| narrow to `Solution Architect` | +3 | +4 | +0.004 | +0.044 | +0.024 |
| **narrow to `Solution Architect` + `System Architect`** | **+3** | **0** | **+0.012** | **+0.044** | **+0.029** |
| + Chief/Principal/Enterprise Architect | +3 | 0 | +0.012 | +0.044 | +0.029 |

The last row buys nothing: "Chief Architect" is already caught by the `chief`
keyword and "Principal Architect" by `Principal`, so those entries would be
unmeasured decoration. The third row is the proposal, and it is a pure gain —
recall up, precision up, nothing newly dropped.

The two compounds are placed in `title_exclude_keywords.csv`, not in
`seniority_exclude_titles`. They are job families, and a list called
"seniority" should hold levels — that miscategorisation is what produced the
false positive in the first place.

One visible consequence worth expecting: the committed state's per-layer table
now reports **`1-seniority` losing 4 wanted jobs**, where the 2026-08-24
baseline reported 0. Nothing got worse — those four were always being lost,
attributed to the keyword layer that reached them first. Pruning the keywords
moved the attribution to where the drop actually happens. Three of the four are
the architect roles the hand edit below recovers; the fourth is *"Lead ASIC SoC
Architect for High-Performance Security"*, which `Lead` catches and which stays
dropped by design.

#### The one change the owner has to make by hand

`job_scraper/config/rules.json` is on CLAUDE.md's never-touch list, so this
session did not edit it. `rules.example.json` has been updated to match.
**Remove `"Architect"` from `seniority_exclude_titles` in your `rules.json`.**

Until that edit lands, the committed state gives the **stage 1** numbers; after
it, **stage 2**:

| | baseline | stage 1 (committed) | stage 2 (after the hand edit) |
|---|---|---|---|
| precision | 0.306 | 0.331 | **0.343** |
| recall | 0.647 | 0.824 | **0.868** |
| F2 | 0.529 | 0.635 | **0.664** |
| false negatives | 24 | 12 | **9** |

Against the *original* WP8 baseline of 2026-08-24 (precision 0.324, recall
0.647, F2 0.539), the finished package is precision **0.343**, recall
**0.868**, F2 **0.664** — and 15 of the 24 false negatives are gone, with
nothing newly dropped at any step.

Read the recall figure with WP8c's standing caveat, which this package does not
change: some kept jobs are deferred to Layer 2, which fails closed, so recall
remains a ceiling rather than banked.

#### 4. Performance — nothing done, deliberately

- `build_hybrid_pattern` is compiled once and passed down, exactly as the
  corrected prompt says. Not touched.
- `_build_title_keyword_pattern` is called once per batch inside
  `apply_combined_title_filter`, which runs **twice per run** (`run_pipeline`
  and `refilter_stored_jobs`). Timed on the real 106-keyword list: 45.6 µs for
  the combined pattern and 91.8 µs for the per-keyword matchers, so **0.275 ms
  per run, total**. That is unmeasurable next to a single rate-limited HTTP
  request. Hoisting it would mean threading a compiled pattern through both
  call sites and the eval harness to save a quarter of a millisecond. **Not
  done, and not worth revisiting.**

#### Not touched, per scope

The location rules, the review statuses, the numeric experience extraction, and
WP8d's deferred-location state are all unchanged. The README still documents
layers 1c and 1b and still prints them in its sample summary — that is WP8b's
job, and doing it here would mean doing it twice.

#### Left for later

- The masking effect is a general property of the ladder, not a fact about
  these keywords: **any** per-rule cost figure the harness prints is an
  attribution, and the marginal cost can only be got by removing the rule and
  re-running. `format_costly_rules` could say so in its own header; it
  currently invites exactly the misreading this package started with.

---

## WP8h — Renumber the ladder

**Do this before WP8b, not after.** WP8b rewrites the README's layer table, and
that table is where the numbering is most visible. Renumbering afterwards means
rewriting the same table twice — the same argument that put WP8 before WP8b.

The labels are historical and always have been: they record the order the
filters were *added*, not the order they run. WP8 made that worse by deleting
1c and 1b, leaving a ladder labelled **1a, 1, 1d, 2** — no layer 0 in the
display, a bare `1` running *after* `1a`, and two gaps. `drops.py` carries a
comment apologising for this, which is a fair sign the scheme has failed.

Proposed, in execution order:

| stored id (unchanged) | display | name |
|---|---|---|
| `0-rules` | **Layer 1** | Location and rules |
| `1a-title-keyword` | **Layer 2** | Title keywords |
| `1-seniority` | **Layer 3** | Seniority |
| `1d-review-status` | **Layer 4** | Review status |
| `2-detail` | **Layer 5** | Detail page |
| `1c-non-english` | — | retired by WP8, history only |
| `1b-language` | — | retired by WP8, history only |

Note for whoever writes this: the owner's sketch was "Layer 1: location, Layer
2: seniority keywords". The real order puts **title keywords second and
seniority third** — they are separate layers that share one title scan in
`apply_combined_title_filter`. The whole point of renumbering is that the
numbers follow execution order, so this is the order to use.

### The one design decision, and why

**Do not rename the stored values.** The `run_exclusions.layer` column already
holds **48,921 rows across 6 runs** in the current vocabulary, including 212
`1c-non-english`, 114 `1b-language` and 1 `refilter/1c-non-english` for layers
that no longer exist. Three reasons that column stays as it is:

1. It is a log of what actually happened. Rewriting it to say a run used names
   it never used contradicts the first priority in CLAUDE.md.
2. The retired layers have no equivalent in the new scheme, so a migration
   could not map them to anything honest.
3. CLAUDE.md already answers this: *one canonical representation* — the store
   holds plain data, presentation concerns live at the presentation edge.

So the ordinal and the human name are **presentation**, derived from one
ordered table in `drops.py` and consumed by `drops.py`, `run.py`, `eval.py` and
the README. A stored id with no entry in that table renders as retired rather
than crashing, which is what makes the historical rows readable instead of
mysterious.

`--layer` is a substring match ("only exclusions whose layer contains this
text"), so it keeps working against the stored ids either way. Decide
deliberately whether it should also accept the new display numbers, and say
which in the plan.

### Scope

Small and contained. The constants are defined once in `drops.py:38-42`; the
only hard-coded literals outside it are `tests/test_drop_log.py:346,364,412`.
`storage/db.py` has an explanatory comment mentioning the layer vocabulary.
`eval.py`'s `LADDER` already encodes execution order and should be the same
source of truth rather than a second copy of it.

```
Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP8h only.

Give the filter ladder sensible ordinals. The labels are historical — 1a runs
before 1, there are gaps where WP8 deleted 1c and 1b, and nothing displays as
layer 0 sensibly. Renumber the *display* to 1-5 in execution order, per the
table in this section.

Do not rename the values stored in run_exclusions.layer, and do not migrate
existing rows. That column is a log of what happened and already holds ~49,000
rows in the old vocabulary, two of whose layers no longer exist. Keep the
stored ids as opaque stable identifiers and put the ordinal and the display
name in one ordered table at the presentation edge, per CLAUDE.md's "one
canonical representation". A stored id not in that table must render as
retired, not raise.

Update every place a layer is shown to a person: run.py's summary, drops.py's
report and its --layer help, eval.py's per-layer table. eval.py's LADDER
already encodes execution order — make it and the display table one source of
truth, not two.

Decide deliberately whether --layer should accept the new display numbers as
well as the stored ids, and record which in the plan file.

Leave README.md alone — WP8b owns it and runs next.

Add a test that pins the display order and that a retired stored id renders
without raising.

Branch wp8h-renumber-ladder. Commit, do not push. Update the plan file.
```

### Result

376 tests pass (up from 364), `ruff check .` clean. Touched: `job_scraper/drops.py`
(the new table), `job_scraper/eval.py`, `job_scraper/run.py`, `job_scraper/pipeline.py`
and `job_scraper/experience_filter.py` (log lines), `job_scraper/storage/db.py`
(docstring), `tests/test_drop_log.py`, `tests/test_eval.py`, and a new
`tests/test_run_summary.py`.

**This section was rewritten after review.** The first attempt shipped an
alignment regression in the run summary, left the `--verbose` log lines on the old
numbering, and recorded a check it had not actually performed. Both bugs and the
false assurance are described in full below rather than quietly corrected — the
plan file is only useful if it records what happened.

**The one table.** `drops.py` gained a frozen `Layer(id, display, name)` dataclass
and `LAYERS: tuple[Layer, ...]`, in execution order, still keyed by the unchanged
stored ids:

| stored id | display | name |
|---|---|---|
| `0-rules` | 1 | Location and rules |
| `1a-title-keyword` | 2 | Title keywords |
| `1-seniority` | 3 | Seniority |
| `1d-review-status` | 4 | Review status |
| `2-detail` | 5 | Detail page |

Named `LAYERS`, not `LADDER` — `eval.py` already owns that name for its smaller,
replayable subset, and giving the full table the same name one import away would
have made "which LADDER" a question every reader had to answer. Two functions read
it: `layer_ordinal(id)` (raises `KeyError` for a retired id — it has no ordinal to
give, and the callers that use it, `run.py`'s summary, only ever pass a current id)
and `layer_display(id)` (never raises: unknown ids render `"{id} (retired)"`, and a
`refilter/`-prefixed id renders its base layer's label with `" (re-filter)"`
appended). `eval.LADDER` is now `tuple(layer.id for layer in LAYERS if layer.id not
in _UNREPLAYABLE_IDS)` — derived from `LAYERS`, not a second hand-kept copy, per the
prompt's instruction and pinned by `test_ladder_is_drawn_from_the_display_table_not_a_second_copy`.

**Every render site now goes through `layer_display`**, not just the one the prompt
named. Once `eval.py`'s per-layer table used it, leaving `format_costly_rules`,
`format_false_negatives`'s per-layer grouping, and `_verdict_text` (the
before/after lines in a `--compare` diff) printing raw stored ids like
`1a-title-keyword` alongside a per-layer table saying `Layer 2: Title keywords`
would have been a worse inconsistency than the one being fixed. All four now agree.
`drops.py`'s `format_rule_counts` does the same; `format_exclusions` never printed
a layer column and needed no change.

**`run.py`'s summary** didn't reference the stored ids at all before this — its
funnel lines are free text ("off-criteria (location/keywords)", "title keyword",
…) that happen to correspond to layers one-to-one (the three detail-page lines all
share Layer 5). Since the prompt named it explicitly as a place a layer is shown,
each of those seven lines now carries its ordinal in a left gutter (`L1  − …`),
read from `layer_ordinal` rather than hard-coded, so a future reorder updates this
display too.

**The ordinal goes in a gutter, not in front of the label, and this cost a
round of review.** The first attempt wrote `− 1  off-criteria (location/keywords)`
— the ordinal appended to the label inside `cut()`. Two things were wrong with it,
both caught by the WP8h review rather than by me:

1. **It broke the funnel's alignment, and I claimed to have checked that it
   hadn't.** The earlier version of this section said column widths "were checked
   by hand against 5-digit counts". That check was either not done or done wrong:
   the ladder gutter costs six characters, `_NUMCOL` was left at 46, and the two
   longest labels overran it. `location unresolvable in the text` collided with
   its own number at **four** digits — `…in the text−1,100`, an ordinary run — and
   `off-criteria (location/keywords)` at five. Recording the false assurance was
   worse than shipping the bug: it told the next reader the question had been
   settled. Fixed by widening `_NUMCOL` to 52, and by making `row()` guarantee a
   two-space gap (`max(_NUMCOL - len(value), len(text) + 2)`) so an over-long label
   pushes its number right instead of abutting it — degrading gracefully at any
   magnitude rather than exactly at the width someone happened to measure.
2. **`− 1  off-criteria` reads as "minus one".** The minus sign belongs to the
   count, not the ordinal, and putting a digit straight after it inverted that at a
   glance. The marker now sits in its own gutter ahead of the sign: `L1  − …`.

`tests/test_run_summary.py` is new and pins this: the exact golden rendering, the
specific four-digit line that broke, the label-never-abuts-its-number invariant at
six figures, and that all five ordinals appear in execution order. There was no
test on `format_summary`'s text before — which is precisely why the regression
reached review — and the lesson is the same one WP8 recorded about the eval
harness: **a layout claim that is not pinned by a test is an assertion, not a
check.**

**The old numbering was still live in the `--verbose` logs, and that was the real
miss.** The package's instruction was "every place a layer is shown to a person",
and I updated the three modules the prompt named while leaving the diagnostic log
lines in `pipeline.py` and `experience_filter.py` on the old vocabulary. The
write-up's claim that "nothing else referenced a layer id" was technically true and
substantively misleading: those lines print layer *numbers*, not stored ids, and
they are exactly the human-facing labels the package existed to fix. The result
was a straight name collision — in one `--verbose` run, "Layer 2" meant the
detail-page filter in the logs and the title-keyword filter in the summary and
reports. That is worse than the muddle WP8h set out to remove. All ten log sites
in `pipeline.py`, both in `experience_filter.py`, and two report strings in
`eval.py` (`"Layer 2 would still settle"`, which meant the detail layer) now read
their number from the table via a new `drops.layer_short(id)` → `"Layer 5"`. Every
one was exercised at DEBUG level to confirm the `%s`/argument counts match.

**Still on the old vocabulary, deliberately: code comments and CLAUDE.md.**
About 54 comments and docstrings across eight files say "Layer 0"/"Layer 2"
meaning the stored-id vocabulary, and CLAUDE.md:32 does too — plus one line of
shipped prose in `job_scraper/config/rules.example.json`, the only one of these a reader
meets without opening the source. None is shown to a person running the tool
(the example config is read, not rendered), and sweeping
them would have buried this package's real fixes in a sixty-hunk comment diff.
**Folded into WP8b's prompt** (2026-08-27) rather than left as a loose follow-up:
that package is already rewriting the README's layer table, so the renumbering
lands in one place, and its prompt now carries the mapping, the grep, and the
instruction to commit the mechanical rename separately from the prose. WP8b's
estimate went from 1 hr to 2 hr to pay for it.

The collision this leaves open until then is worth naming precisely: "Layer 2"
currently means the title-keyword filter in every rendered output and the
detail-page filter in every code comment. Anyone reading `filtering.py` before
WP8b lands should trust `drops.LAYERS`, not the prose around them.

**Decision: `--layer` keeps matching stored ids only, not display numbers.**
`--layer` is (and stays) a case-insensitive substring match against the column
SQLite actually holds. Display numbers were considered and rejected: `1` is a
substring of six different stored values, so a bare digit would need a second
matching mode layered on top of substring matching, and the two modes would
silently disagree about what `--layer 1` means. Instead the `--layer` help text
now lists all five `id (Layer N: Name)` pairs inline, so
`python -m job_scraper.drops --help` is where "which id is Layer 3?" gets
answered, without teaching the flag two ways to match.

**That decision was right and half-finished — WP8i completes it.** Documenting
the mapping in `--help` serves the person who reads `--help`. It does nothing for
the person who reads `Layer 3: Seniority` on screen, types `--layer 3`, and is
told "recorded no matching exclusions" — which reads as a finding about the
ladder rather than a rejected query. **WP8h made that worse rather than
inheriting it**: before this package the on-screen labels *were* the stored ids,
so a typed digit matched what you had just read; now the display teaches an input
that does not work. Every digit 0-5 is wrong today, half of them silently and
half of them confidently — see WP8i for the measured table. Rejecting a bare digit
loudly does **not** reopen the decision above; it is the same decision plus an
error case, and it is what should have shipped here.

Provenance worth recording, because it explains why this took two packages: the
owner had already chosen the loud rejection at 09:30 on 2026-08-27, eleven
minutes before this package started, in a commit that never reached `main` — it
was made on `wp8b-plan-refresh` after that branch's PR had already merged, then
orphaned when the branch was deleted. So WP8h read the plan's *open question*
form and answered it independently. The decision was lost in transit, not
overruled. The commit is preserved as the local tag
`rescued/wp8h-layer-decision`; its reasoning is folded into WP8i below and
nothing further is needed from it.

**A pre-existing bug in that help text, fixed while adjacent.** Three places
advertised `--layer locations` as a worked example — the module docstring, the
argparse epilog, and `storage/db.py`'s `exclusions()` docstring, which claimed it
"finds '0-rules' drops named 'locations: ...'". It does not and never did: each
filter matches its own column, the location cases live in `rule`, and no stored
layer id contains the string "locations", so the documented command returns
nothing. This was broken on `main` before WP8h and the first attempt edited the
text right beside it without noticing. All three now say `--rule locations`, and
db.py's docstring states outright that `--layer locations` matches nothing. Out
of the package's scope strictly read, but it is documentation-only, in strings
this package was already rewriting, and leaving a known-false example in place
after touching the line would have been the wrong call.

**Ties in the drops report sorted by the stored id's alphabet.** Found in the
second review. `rule_counts` broke equal counts with the `(layer, rule)` tuple,
and the stored ids sort `'0-rules'`, `'1-seniority'`, `'1a-title-keyword'` — so
the report printed the ladder as Layer 1, 3, 2, 4, 5. Pre-existing, and invisible
for as long as the stored ids were the only thing on screen; WP8h is what put the
ordinals there and made it wrong. Fixed with `drops.layer_sort_key`, derived from
the same `LAYERS` table: ladder order, a layer's own rows before its `refilter/`
ones, retired ids last as a group. Count still dominates — the report's first job
is still "which rule fired most?" — and that is pinned by its own test.

**Column width in the drops report.** The `layer` column was first widened 18 → 30,
which was still too narrow: the longest label a stored id can produce is a
re-filtered one, `"Layer 1: Location and rules (re-filter)"` at 39 characters, and
30 truncated it to `"Layer 1: Location and rules (…"`. The `(re-filter)` marker is
the whole point of that suffix — WP8a keeps the two populations separable — so it
must not be the part that gets trimmed. Now 39.

**Tests.** `layer_short` is covered alongside its two siblings — the ordinals it
returns, that it agrees with `layer_ordinal` for every row of the table, and that
it raises on a retired id (it is the one of the three that must refuse, since
`layer_display` is the one that has to survive history). Report ordering has four
tests: ladder order on equal counts, count still beating ladder order, retired
last, and a `refilter/` row sorting beside its base layer.
`tests/test_drop_log.py` gained a "the display ordinal" section:
the full `(id, display, name)` table pinned in order, a retired id rendering
without raising (`1c-non-english`, `1b-language`), a current id, a
`refilter/`-prefixed id (both current and retired underneath), `layer_ordinal`
raising `KeyError` on a retired id, and the CLI's rule-count report showing
`"Layer 1: Location and rules"` rather than `"0-rules"`. `tests/test_eval.py`
gained the `LADDER`-is-derived test above and had its one text-format assertion
(`test_report_names_every_false_negative`, previously checking for the literal
strings `"1d-review-status"`/`"2-detail"`) updated to check for
`layer_display(...)` output instead — a deliberate update to match the intended
formatting change, not a weakened test.

Not touched, per scope: `README.md` (WP8b's), and the hard-coded stored-id
literals in `tests/test_drop_log.py` outside the new section (`layer="2-detail"`,
`"0-rules"` in a fixture row and a `--layer` CLI arg) — all exercise the stored-id
matching path directly and are correct left alone. Note for WP8b: the plan's old
scope note said there were three such literals; the new tests add a fourth, so
that count is stale.

**One naming decision worth keeping straight.** The table is `drops.LAYERS`, not
`drops.LADDER`, because `eval.LADDER` already exists for the smaller replayable
subset and two `LADDER`s one import apart would be exactly the confusion the
renumbering is meant to remove. Two comments in the first attempt then referred
readers to "`LADDER`" when they meant `LAYERS` — pointing at the very thing the
naming decision disambiguates. Both fixed.

---

## WP8i — `--layer` refuses a display number

**Do this before WP8b.** WP8b documents `--layer` in the README, and this package
changes both its behaviour and its error message. Documenting it first means
writing that passage twice — the same argument that put WP8h before WP8b.

**Do not fold it into WP8b.** WP8b's prompt says "docs only: change no
behaviour", and this is a behaviour change. Two packages, two commits, two
things a reviewer can check independently.

WP8h put display numbers on screen. The stored ids they map to are unchanged, so
those numbers are not searchable — and `--layer` answers a meaningless query with
an empty table rather than an error. Measured against a store holding all eight
historical layer values:

| typed | the user means | what actually comes back | exit |
|---|---|---|---|
| `0` | — | Layer 1's rows | 0 |
| `1` | Layer 1 | rows from Layers 2, 3, 4 **and** a retired layer | 0 |
| `2` | Layer 2 | **Layer 5's rows** | 0 |
| `3` | Layer 3 | nothing: "recorded no matching exclusions" | 0 |
| `4` | Layer 4 | nothing, same message | 0 |
| `5` | Layer 5 | nothing, same message | 0 |

**Not one digit gives a right answer.** Three return silence, three return a
confidently wrong table. `--layer 2` printing a table headed `Layer 5: Detail
page` is the worst of them, because nothing on screen prompts a second look. And
`--layer 1` is not a near miss either: six of the eight stored values contain the
character `1`, `refilter/1c-non-english` among them.

This is CLAUDE.md's priority 2 in a reporting tool. An empty table meaning "you
asked a meaningless question" is indistinguishable from one meaning "nothing was
dropped there", and the second is a conclusion the owner might act on — loosening
a rule that never fired, or trusting a layer that is quietly eating jobs.

### The decision, and what it is not

**The owner chose this on 2026-08-27: reject a bare number, name the substitute.**
The decision was made before WP8h and lost in transit rather than overruled — see
the provenance note at the end of WP8h's result section.

It does **not** reopen WP8h's decision that `--layer` matches stored ids only.
Teaching `--layer` the display numbers was considered and rejected twice, for the
same reason both times: a bare digit would mean something categorically different
from every other argument, and it would break `--layer 1` for anyone matching
stored ids today. Rejecting an input is not the same as matching it differently.
This package adds an error case and changes no input that works.

```
Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP8i only.

`python -m job_scraper.drops --layer 3` today runs the query, matches "3"
against no stored layer id, and prints "recorded no matching exclusions" with
exit code 0. That is indistinguishable from "Layer 3 dropped nothing", which is
a conclusion someone might act on. `--layer 2` is worse: it returns Layer 5's
rows under a heading that says Layer 5, and exits 0. See the table above for all
six digits; none of them is right.

Make `--layer` refuse a bare display number instead of answering it wrongly.

- An argument that is **entirely digits** is refused before any query runs.
  Message to stderr, non-zero exit. Do not print an empty table, and do not
  print a partial one.
- The message names the stored id to use instead, so it teaches the mapping
  rather than just scolding:
  "--layer 3 is a display number, not a stored layer name. Did you mean
  --layer seniority? (layer 3 is stored as '1-seniority')"
- A digit outside the ladder gets the range, not a lookup: "there is no layer 9;
  the ladder is 1-5".
- **Anything not entirely digits is untouched.** `--layer 1a`, `--layer
  seniority`, `--layer 0-rules` and `--layer refilter/` keep working exactly as
  they do today, as plain case-insensitive substring matches. Restricting the
  rule to bare digits is the whole point: this is a new error case, not a
  change to any input that works.
- Retired layers stay searchable by their stored ids. `--layer 1c` must still
  find the 212 historical `1c-non-english` rows — WP8 deleted the layer, not its
  history — and `1c` is not all digits, so it should fall out of the rule above
  rather than need a special case. Check that it does.

Derive the number→id mapping from `drops.LAYERS`; do not hand-write a second
table. `layer_ordinal` already goes one way, and note that the id you want in
the message is the stored id, not the display name.

Do not touch `--rule` or `--source`. They match different columns, a bare digit
in either is a legitimate search (a rule string can contain "3"), and nothing
about them is misleading.

Update the `--layer` help text so it states the rule rather than only listing
the mapping.

Tests: `--layer 3` exits non-zero and names '1-seniority'; `--layer 9` gives the
range; `--layer 1a`, `--layer seniority` and `--layer 1c` all still return rows;
and the refusal happens before the store is opened, so a bad argument costs no
query.

Leave README.md alone — WP8b owns it and runs next.

Branch wp8i-layer-guard. Commit, do not push. Update the plan file.
```

### Result (2026-08-27)

**Done as specified.** `--layer` now refuses an argument that is entirely ASCII
digits before `JobStore` is opened, with the message on stderr and a non-zero
exit. Nothing is printed — not an empty table, not a partial one. Every other
argument reaches the store exactly as it did.

`drops.layer_query_error(value)` is the whole rule: `None` if the argument is
usable, otherwise the message. It is a pure function of a string, so the refusal
is testable without a database, and `main` does nothing with it but raise.

**The mapping is derived, not written twice.** `_BY_DISPLAY` inverts `LAYERS` the
way `_BY_ID` indexes it, so `layer_ordinal` and this guard read the same table.
The parametrised test walks `drops.LAYERS` rather than listing the five numbers,
so a future renumbering cannot leave a stale suggestion behind.

**The suggested argument is the stored id with its numeric prefix dropped** —
`layer_search_hint('1-seniority')` is `seniority` — because that prefix records
when the filter was *added* and is exactly the thing a display number gets
confused with. The message names both, so it teaches the mapping rather than
only refusing: "Did you mean --layer seniority? (layer 3 is stored as
'1-seniority')". A test pins that each hint survives the digits rule itself and
matches only its own id; a hint that matched two stored ids would trade one
wrong answer for another.

**A number outside 1-5 gets the range, not a lookup** ("There is no layer 9; the
ladder is 1-5"), with the bounds read off the ends of `LAYERS`. `0` lands here
too, which is right: it used to return Layer 1's rows because `0-rules` contains
the character.

**Matched with `re.fullmatch(r"[0-9]+", ...)`, not `str.isdigit()`**, which is
`True` for `'³'` — `int` then raises on it, so the guard would have crashed on
the one input it exists to handle politely.

**Retired ids needed no special case, as predicted.** `1c` is not all digits, so
it falls out of the rule; a test records a `1c-non-english` row and runs the CLI
against it to prove the historical rows are still reachable. `1a`, `seniority`,
`0-rules`, `refilter/`, `2-detail` and the empty string are all pinned untouched.

**Help text** now states the rule ("It matches the stored id, never the display
number, so a bare number is refused rather than answered") instead of only
listing the mapping. `--rule` and `--source` are unchanged: they match other
columns, where a bare digit is a legitimate search.

Not touched, per scope: `README.md` (WP8b's, and it runs next). Note for WP8b:
the `--layer` passage it writes must describe the refusal, not just the mapping.

### Review follow-up (2026-08-27)

Three findings from a review session, all fixed in a second commit.

**A stray space defeated the guard.** `--layer " 3"` is not entirely digits, so
it fell through to the store and printed "recorded no matching exclusions" with
exit 0 — the exact misleading outcome this package exists to remove, reachable
by a typo. `layer_query_error` now strips the argument *before* the digits test.

Only the test is stripped; the query still runs on the argument as typed. That
line is deliberate: trimming the query value too would change what a non-digit
argument matches (` seniority` would start finding rows), and the package's
whole scope is "add an error case, change no working input". So a padded
non-digit is still searched for verbatim and still finds nothing — pinned by
`test_only_the_digits_test_is_stripped_not_the_query`, so the asymmetry is a
recorded decision rather than an oversight for a later session to trip over.

**`--layer 03` echoed the padding back**: "layer 03 is stored as '1-seniority'".
The opening still quotes the argument as typed, since that is what the typist
has to recognise, but every claim about the ladder after it now uses the parsed
number. `--layer 007` likewise says "there is no layer 7".

**A test docstring overstated a count**, saying the retired `1c-non-english`
layer holds "~49,000 historical rows". 212 is that layer's own count; ~49,000 is
the whole `run_exclusions` table across every id, current ones included (see the
WP8h measurement above: 48,921 across 6 runs, of which 212 are `1c-non-english`
and 114 `1b-language`). Comment only, no behaviour.

**Noted, not fixed — pre-existing, and WP8b's to judge.** `layer_display`'s
docstring in `drops.py` has the same conflation, describing a retired id as
"still present in ~49,000 historical rows". WP8h wrote it and it is outside this
package; the true figure for the two retired ids together is 327.

**Verification.** `.venv/bin/pytest`: 414 passed. `.venv/bin/ruff check .`:
clean. `python -m job_scraper.run --help` works.

**Method note, since it cost a wrong answer.** Run the tools from `.venv/bin`,
not via `python -m` — a bare `python` here is conda base, which lacks
`anthropic` and `ruff`. `python -m pytest` therefore fails to *collect*
`tests/test_scoring.py` with `ModuleNotFoundError: No module named 'anthropic'`,
which looks like a repo problem and is not one. This session reported that as a
pre-existing environment gap after already having worked around the identical
failure for `ruff` one command earlier — the same fact twice, read as two
different things. The owner caught it.

---

## WP8b — README reconciliation

**WP8 has landed (2026-08-27), so this is now unblocked.** WP8 deleted
layers 1c and 1b, took `title_exclude_keywords.csv` from 112 entries to 102,
and moved `Architect` out of `seniority_exclude_titles`. That rewrites the
"How it works" layer table and removes the language-filter rows entirely. The
keyword CSV itself stays — the rewritten WP8 did not delete it — so that
input-file section needs updating rather than removing.

Note that WP8d also changed the README's location story: there is now a third
Layer 0 answer and a `non_place_locations` key, both already documented by that
package, so check rather than assume that section is stale.

**WP8h has also landed (2026-08-27), and it grew this package.** WP8i follows it
and precedes this one — it makes `--layer` refuse a bare display number, which
changes what the README must say about that flag, so **do not start WP8b until
WP8i has landed** or you will write the `--layer` passage twice.

**WP8i has landed (2026-08-27, PR #29), and this prompt was audited against the
code immediately after.** Five corrections went in: the sweep's totals (they
were undercounted before WP8i, not by it), a third already-correct hit that a
mechanical pass would break, one passage the sweep's grep cannot reach, the
README's layer-reference count, and a note that the `--layer` example and the
error message to quote are the same decision. The line numbers in this prompt
are dated, not stable — locate by content.

The filter ladder now *displays* as Layer 1-5 in execution order while the
stored ids in `run_exclusions.layer` keep their historical names. Every user-facing surface was
converted — the run summary, the drop-log report, the eval report, the `--verbose`
logs — but two categories were deliberately left on the old vocabulary, because
sweeping them inside WP8h would have buried that package's real fixes in a
sixty-hunk documentation diff. They are now this package's:

- **The README**, which is the most visible layer table in the project and is
  being rewritten here anyway. It currently has ~15 layer references, several of
  which now actively collide with the new scheme.
- **~54 code comments and docstrings** across eight files, plus CLAUDE.md's own
  architecture line — and, easy to miss, one line of shipped prose in
  `job_scraper/config/rules.example.json` that a reader copies when building their own
  `rules.json`.

Both are inert — no instruction in either would lose data if followed — but the
collision is worse than ordinary staleness: "Layer 2" now means the title-keyword
filter in every rendered output and the detail-page filter in every comment. A
reader who trusts a comment gets the wrong layer. That is the thing to fix, and
it is why this package is now nearer two hours than one.

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
- Nothing documents `python -m job_scraper.eval` (WP8c). It is a read-only,
  offline command the owner will forget exists if the README never names it.
  (`python -m job_scraper.drops` **is** already documented, around README:173
  — WP8a added it. This bullet used to name both; check before rewriting.)
  When you document `eval`, carry the warning from the decisions log with it:
  **its per-rule cost column reports attribution, not marginal cost.** A rule
  is credited with a drop when it is the first to match, so removing it changes
  nothing if something downstream also catches the job. WP8 nearly pruned three
  keywords that cost nothing on exactly this mistake. A README that introduces
  the command without that caveat invites the next person to repeat it.

New drift created by WP8 itself — all four are in the README now:

- The layer table lists `1c | Non-English text` and `1b | Language-speaker`.
  Both layers are deleted. The surviving ladder is rules → title keyword →
  seniority → blocklist → detail.
- The sentence after it explains that the labels are historical "which is why
  1c comes before 1b". **WP8h has since renumbered the display, so do not
  rewrite this passage around the surviving pair — delete the apology and
  renumber the table instead.** See the renumbering section below, which
  supersedes this bullet.
- The sample run-summary block (around README:128) prints `− non-English text`
  and `− language-speaker`, which `format_summary` no longer emits. **Do not
  just delete the two lines:** the `→ N passed title filters` running total
  used to hang off the language-speaker row and now hangs off `senior-level
  title`. The block needs regenerating as a whole.
- "the exact rule that fired, down to which keyword, which seniority term,
  which language code" (around README:170) — the drop log can no longer name a
  language code; `LAYER_NON_ENGLISH` and `LAYER_LANGUAGE` are gone.

Not affected, so do not go looking: `langdetect` never appeared in the README,
the `seniority_exclude_titles` example and the `"Lead"`/`"Leadership"` note are
both still accurate, and the keyword-CSV examples (`design`, `tax`) both
survived the prune.

THE RENUMBERING SWEEP (from WP8h, folded in here)

WP8h renumbered the ladder's *display* to Layer 1-5 in execution order and left
the stored ids in `run_exclusions.layer` alone. `job_scraper/drops.py`'s `LAYERS`
table is the single source of truth; read it first. The mapping is:

    0-rules           -> Layer 1: Location and rules
    1a-title-keyword  -> Layer 2: Title keywords
    1-seniority       -> Layer 3: Seniority
    1d-review-status  -> Layer 4: Review status
    2-detail          -> Layer 5: Detail page

Two retired ids, `1c-non-english` and `1b-language`, exist only in history and
render as "(retired)". Do not resurrect them in prose.

1. The README's layer table and every layer reference in it (17 as of
   2026-08-27; `grep -nE 'Layer|layer' README.md` finds them). Renumber to 1-5,
   delete the paragraph apologising that the labels are historical — it no
   longer applies to what a reader sees — and regenerate the sample run-summary
   block, which WP8h reformatted: each dropped-jobs line now carries its ordinal
   in a left gutter ("L1  − off-criteria …") and the column widths changed.
   Run the command and copy real output; do not hand-draw it.

2. Two README examples are now actively wrong, not merely stale:
   - `--layer 2` (around README:185) is offered as an example alongside
     `--rule locations`. **After WP8i it is an error, not a wrong answer** —
     a bare digit is refused with a non-zero exit. Replace the example with a
     stored id (`--layer seniority` reads best) and state the rule in one line:
     `--layer` matches stored ids, never display numbers, and a bare number is
     refused with a message naming the id to use. Run WP8i's error path and
     quote the real message rather than paraphrasing it — note that the message
     itself suggests `--layer seniority`, so quoting it and choosing the
     replacement example are one decision, not two.
   - `--rule locations` is correct and `--layer locations` is not. WP8h fixed
     three places that advertised the latter; check the README does not too.

3. The comments and docstrings still using the old numbering — 57 lines across
   eight files, of which 3 are already correct (see the warning below), so
   54 to change. Find them with:

       grep -rnE 'Layer (0|1a|1b|1c|1d|1|2)\b' job_scraper/

   filtering.py 21, experience_filter.py 11, pipeline.py 11, eval.py 6,
   scoring.py 2, drops.py 3, extractors/successfactors_html.py 2,
   config/rules.example.json 1 — all relative to job_scraper/, the grep root above.

   **`job_scraper/config/rules.example.json` is the one to do first and most
   carefully.**
   Its `_non_place_locations_comment` is not an internal comment at all — it is
   prose shipped in the example config, the thing a reader copies to build
   their own `rules.json`, and it explains WP8d's deferral as "passes Layer 0
   provisionally and Layer 2 settles it". Both numbers are now wrong to a
   reader looking at any output. The live `rules.json` is gitignored and on
   CLAUDE.md's never-touch list, so change the example only, and mention in
   your summary that the owner may want to copy the wording across by hand.

   The rest is a mechanical rename of prose only. **Change no code, no string
   literal that is rendered to a user, and no stored id.** WP8h already
   converted every rendered string; if this grep hits something inside a
   `logger.*` call or a report line, stop and check why before touching it.

   **The grep also matches three already-correct new usages.** Find them by
   content, not by the line numbers below — WP8i shifted `drops.py` by ~66
   lines and the next package will shift it again:

   - `drops.py`, `format_rule_counts` ("Layer 1: Location and rules
     (re-filter)", explaining the report's column width — was :195 when this
     was written, :286 after WP8i).
   - `drops.py`, `layer_sort_key`'s docstring ("printed the ladder as Layer 1,
     3, 2, 4, 5"). **This is the dangerous one.** It describes the *old* sort
     bug using *new* display numbers, so it reads like stale text and is not:
     renumbering it destroys the sentence. WP8h's count missed it, which is
     why the totals above changed without any package editing the line.
   - `eval.py:806` ("Layer 2: Title keywords — ti…", a truncation example).

   All three are new-scheme labels and must be left exactly as they are. Read
   every hit rather than sed-ing the tree; this is precisely the kind of
   rename where a blind pass is worse than no pass.

4. One passage the grep cannot find, because it never writes "Layer N":
   `JobStore.exclusions`' docstring in `job_scraper/storage/db.py` (:595 as of
   2026-08-27) ends "the location cases live in `rule`, so `--layer locations`
   matches nothing — `layer` holds ids like '0-rules'". Still true, but half
   the story since WP8i: a bare digit no longer matches nothing, it is refused
   before the query runs. Add that clause; do not restate the whole rule, which
   belongs in the CLI's help text.

5. CLAUDE.md:32 — "experience_filter.py Layer 1 (title) and Layer 2 (detail
   page)" is now Layer 3 and Layer 5. CLAUDE.md:51's "There are already five"
   is still true and reads better than ever; leave it.

Do this sweep as its own commit, separate from the README rewrite, so the
mechanical rename can be reviewed at a glance rather than read line by line
alongside prose changes.

Check the whole file against the current CLI while you are in there: every
flag documented should exist, and `python -m job_scraper.run --help` is the
authority. Do not invent example output — if a block needs new numbers, say
where they came from or mark it illustrative.

Branch wp8b-readme. Commit, do not push. Update the plan file.
```

### Result (2026-08-27)

414 tests pass, `ruff check .` clean, `python -m job_scraper.run --help` works.
Two commits, as the prompt asked: the mechanical renumbering sweep first, then
the README rewrite.

**Read the incident section at the end of this before anything else.** A command
in this session modified `data/jobs.sqlite3` and `data/jobs.xlsx`. It is not a
documentation problem and it needs a decision from the owner.

**The sweep.** All 54 lines renumbered; the three already-correct hits the
prompt named were located by content and left alone (`format_rule_counts`'s
column-width note, `layer_sort_key`'s docstring, `eval.format_comparison`'s
truncation example). Verified afterwards by re-running the grep: every remaining
`Layer N` in `job_scraper/` is a new-scheme number.

Four judgement calls inside what the prompt called a mechanical rename:

1. **Old "Layer 1" did not always mean the seniority layer.** In
   `filtering.py`'s hybrid and unresolvable-location comments, and in
   `experience_filter.py`'s `apply_detail_filter` docstring, "Layer 1" was used
   loosely for "the stage that reads the listing text" — but the code doing that
   reading is `matches_rules`, which was Layer 0. Those references are *correct
   unchanged* under the new scheme, since the rules layer is now Layer 1. They
   look untouched in the diff and are not: renumbering them to 3 would have been
   the wrong answer twice over.
2. `filtering.py`'s "keyword and seniority filters never get a turn; they die
   before Layer 1" *is* the ladder sense, and became "before Layer 2" — the
   keyword layer is the first of the two it names.
3. `drops.py`'s module docstring said "layers 1a to 1c returned excluded lists".
   Two of those ids are retired and the prompt forbids resurrecting them in
   prose, so it is now "the layers after it", which is true and names nothing
   dead.
4. `rules.example.json` was done first and alone, as instructed, and the JSON
   re-parsed afterwards. **The owner may want to copy the new wording into their
   own gitignored `rules.json` by hand** — it is on CLAUDE.md's never-touch list
   and was not touched.

Two extra prose corrections went into the sweep commit, both handed over by
earlier packages and both one-liners in text already being edited:
`layer_display`'s docstring credited the two retired layers with ~49,000
historical rows (that is the whole table; the two of them hold 327), and
`db.py`'s `run_exclusions` comment still said `rule` can name "a language code",
which it has not been able to do since WP8 deleted both language layers. That
one is the same drift as the README bullet, in a file the sweep's grep cannot
reach because it never writes "Layer N".

**`--layer`: the prompt's prediction about the error message was wrong, and the
choice it was meant to settle still comes out the same.** The prompt says to
quote the real refusal and notes that "the message itself suggests `--layer
seniority`, so quoting it and choosing the replacement example are one
decision". It is one decision, but not that one: the stale README example was
`--layer 2`, and `--layer 2` suggests `--layer title-keyword` — layer 2 is the
title-keyword layer. `--layer seniority` is what `--layer 3` suggests. Since
`--layer seniority` does read best, the README demonstrates the refusal with
`--layer 3` and quotes that message verbatim, so the quoted suggestion and the
worked example agree. Both messages were run rather than reconstructed.

**The run-summary block was regenerated by `format_summary`, with illustrative
counts, and the README says so.** The prompt asks for real output. A real run
means fetching nine third-party career pages, and `http.py` still ships the
placeholder `contact=you@example.com` user agent that the README's own
"Scraping responsibly" section tells you to replace — so putting live traffic on
other people's servers to produce a sample block was the wrong trade. The
alternative the prompt allows was taken instead: the *rendering* is genuine
(built by calling `format_summary` on a `RunSummary`, so the gutter, the column
widths and the three Layer 5 lines are exactly what the code emits), and the
counts are a coherent invented run, labelled as such in the README. The shape is
modelled on the real measurements recorded in the decisions log — 8,000
postings, ~2,000 dropped at Layer 1 — so it reads like a mature store rather
than the empty-table first run the old block showed.

**Drift the prompt did not list, found by checking the file against `--help`.**
The prompt's "check the whole file against the current CLI" turned up more than
the four missing flags:

- The xlsx Jobs sheet has had nine columns since WP7, not the six the README
  listed — `score`, `score_reasoning` and `score_flags` were never documented,
  nor was the fact that the sheet sorts best-score-first once they are filled.
- Nothing documented the scoring stage at all, so `--score` could not be added
  to the options table without a sentence on `profile.md` and
  `ANTHROPIC_API_KEY`. Both are now named.
- "Deleting `data/` costs you the run history and the dedupe state, nothing
  else" was written when the store was a regenerable CSV. It now also costs
  every review decision the owner has ever recorded.
- The layout table's `data/curated/` row predated WP8c's `labels.csv`, and its
  `scripts/` row said "shell automation" for a directory that is now mostly
  Python.
- `requirements.txt` has installed `ruff` since WP2; the setup section still
  said it gets you `pytest`.

**Layer 4's name.** The README called it "Blocklist — postings you have already
rejected by hand". `drops.LAYERS` calls it Review status, and it fires on any
stored `rejected` row, including ones a tightened rule rejected rather than the
owner. The table now uses the real name.

### The incident: the live store was modified (2026-08-27)

**What happened.** Verifying the prompt's instruction that "every flag
documented should exist", this session ran `--help` against all seven commands
the README names, in one loop. `retrofilter` and `blocklist_all` have **no
argument parser**: `main()` reads no `sys.argv` at all, so `--help` is not a
flag they reject, it is text they never look at. Both ran against
`data/jobs.sqlite3`.

**The damage.** `blocklist_all` ran first and called `mark_all_new_seen()`,
flipping every `new` job to `seen`, then regenerated `jobs.xlsx` — which emptied
the review sheet and, with it, the `export_rows` table the review commands
address. `retrofilter` ran after it and therefore found no `new` rows: it marked
nothing rejected, and only regenerated the xlsx a second time. So the loss is
one thing, not two: **the record of which postings were unreviewed.** No row was
deleted, no review decision the owner had actually made was altered
(`mark_all_new` touches `new` only, never `shortlisted` or `rejected`), and the
archive sheet still holds every posting.

**What the store looks like now**, read-only: 370 rejected, 212 delisted, 190
seen, 2 shortlisted, 0 new, `export_rows` empty.

**Recovered and closed (2026-08-31). Nothing below is a to-do.** The owner
backed up the store, flipped the 117 rows identified here back to `new`,
reviewed them and rejected them. All 222 postings first stored on 2026-08-27
are now `rejected` (220) or `delisted` (2), with none left `seen`; run 15 has
since scraped normally. **The table below is a record of how the set was
identified, not an instruction to act on** — re-running its query against the
store today matches nothing, because the rows it describes have moved on.

**How the set was reconstructed.** Nothing records *when* a row became
`seen`, so the flipped set could not be recovered exactly. The best available
proxy was `first_seen`: 117 of the 190 seen rows were first stored by that
day's runs 12, 13 and 14, and those were the likely unreviewed population —

| first_seen | run | rows |
|---|---|---|
| 2026-08-27T14:29:18Z | 14 | 4 |
| 2026-08-27T07:51:05Z | 13/14 | 7 |
| 2026-08-27T06:40:12Z | 13/14 | 106 |

The remaining 73 seen rows were first stored on 2026-08-24 or earlier and were
most likely already reviewed; 57 of them appear in the pre-cutover
`blocklist.csv`, which corroborates the boundary from a second direction. There
was no backup to check against: `tmutil destinationinfo` reported no
destinations configured, there were no local APFS snapshots beyond OS updates,
and no other copy of the store or the spreadsheet existed on the machine. The
reconstruction was therefore the only route, and it is the one that was taken.

**The lesson, and it is not "be careful".** `--help` is safe on every other
command in this project because they all use argparse. It is unsafe on exactly
the two that do not, and nothing about typing it says so. CLAUDE.md's never-touch
rule was followed for file edits and defeated by a command that looked like a
query. Two consequences, one taken and one not:

- **Taken:** the README's maintenance section now warns, above both commands,
  that they parse nothing and that `--help` runs them.
- **Not taken, and worth a package:** give both tools an `argparse` front door,
  even one with no options, so an unrecognised argument exits non-zero without
  doing anything. That is a behaviour change and WP8b was scoped docs-only. See
  the note under WP10.

---

## WP9 — Playwright reuse and HTTP caching

```
think hard

Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP9 only.

Two performance problems in http.py.

1. fetch_rendered launches and tears down a fresh Chromium on every call. For a
   dynamic source's detail pass — Layer 5, the stored id '2-detail'; WP8h
   renumbered the display, so read any "Layer 2" in an old comment as this one
   — that is one browser launch per job, up to 10 concurrently given
   _DETAIL_WORKERS = 10. Restructure so one browser is
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

### Result

**Wall-clock, full 50-source run, measured on this Mac against a copy of the live
store** (never the live one — `--output-db` and `--output-xlsx` pointed at a
scratch copy, which is how WP8b's incident is not repeated):

| Run | What changed | Wall clock | vs before |
|---|---|---|---|
| before | `main` at `c9785f7` | **365.1 s** | — |
| after, cold cache | render pool only, cache 100% miss | **294.8 s** | −19% |
| after, warm cache | render pool + 390/395 pages cached | **131.6 s** | −64% |

The cold run is the honest measure of part 1 on its own: its cache logged
`0 hit, 0 revalidated, 395 fetched`, so every one of those 70 seconds came from
reusing browsers. The warm run is what a re-run inside the TTL now costs — the
case the owner actually hits when adding a source and running again.

`user` time fell 35.9 s → 25.5 s → 16.3 s, so this is not just less waiting; it
is less work.

**The funnel is unchanged.** The warm run's summary is identical to the cold
run's, line for line, down to `Exclusions logged 7,909` — a cached page extracts
to exactly what the live page extracted. Against the *baseline* the numbers move
by five jobs in 7,996 (7,996 → 7,991 seen, 449 → 448 still listed), which is
twenty minutes of real postings being taken down between two runs, not a
behaviour change: the same 50 sources processed, the same 0 skipped, and a
byte-identical set of warnings all three times.

---

#### Part 1 — one browser per render thread, not one per page

**The prompt's first option cannot be built.** "Give each worker thread its own
context" of a shared browser is not available in the sync API. Measured, not
assumed:

```
cross-thread use of Browser -> Error: Cannot switch to a different thread
    Current:  <greenlet.greenlet object at 0x102864bc0 ...>
    Expected: <greenlet.greenlet object at ...>
```

Every Playwright object is bound to the greenlet of the thread that created it,
so a `Browser` touched from another thread raises before it does anything. One
browser shared by ten detail workers is therefore not a thing that exists, and
"a context per worker thread" necessarily implies *a browser per worker thread*.
`chromium.launch_server()`, which would have given one real Chromium process
behind several connections, is async-API only (`hasattr` is `False` on the sync
one); `chromium.connect()` exists, so driving a separately-spawned
`playwright launch-server` would work, and was rejected as a subprocess and a
websocket of new failure modes for a saving already had more cheaply.

**So: the second option — rendered fetches move off the detail thread pool.**
`http.RenderPool` owns four dedicated render threads. Callers (the source loop on
the main thread, the Layer 5 detail workers on theirs) put a URL on a queue and
block on a `Future`; a render thread that owns its browser from first use until
`close()` does the work. `render_pool()` is a context manager the pipeline opens
around the whole run.

Measured on the warm run: **4 browser launches for 15 rendered pages**, against
15 launches for 15 pages before. Threads start lazily, so a run with no dynamic
source still launches nothing.

**Per-call cost removed** (`about:blank`, so this is overhead only, no page):

| | before | after |
|---|---|---|
| driver start + `chromium.launch()` | 0.17 s **per page** | once per render thread |
| `new_context()` + `new_page()` + goto | 0.03 s | 0.03 s |

**The ceiling on concurrent rendered fetches drops from 10 to 4, deliberately.**
`_DETAIL_WORKERS = 10` used to mean ten simultaneous Chromiums during a detail
pass; four reused ones is a better trade — and ten concurrent *launches* contend
for CPU in a way one launch does not.

**Be clear about what that costs, because it is not only a smaller number.** The
browsers are now resident for a different *duration*, not just in a different
quantity. Before, a Chromium existed for the second it was rendering and then
died. Now, from the first dynamic source until the run ends, up to four sit in
memory — roughly 600 MB held across the long stretches where nothing is being
rendered at all (39 of the 50 sources are static). On a Mac with the run taking
minutes, that is the right trade and it is why the pool is four and not ten. On
a smaller machine it is the first number to turn down. It is also the direction WP10 is already going (per-host cap of 2).
`_DETAIL_WORKERS` still governs static fetches; a comment at its definition now
says so, because the two numbers now mean different things.

**Details that matter for priority 1 and 2:**

- A render error is set on the caller's `Future` and re-raised in the calling
  thread, so Layer 5's fail-open behaviour is exactly what it was. A dead page
  costs its own fetch and nothing else: the browser stays up, pinned by a test.
- A browser that actually crashes is relaunched on next use rather than failing
  every remaining page on that thread.
- Shutdown relays **one** sentinel thread-to-thread. Queueing one per thread —
  the obvious way — lets the idle threads take them all while a fourth is
  mid-render, and that one then blocks on an empty queue for ever.
- `close()` drains anything still queued and fails it, so a caller that raced
  shutdown gets an exception rather than a `Future` nobody will ever set.
- If the Playwright driver itself will not start, every queued request is failed
  loudly instead of hanging.
- **`fetch_rendered()` outside a `render_pool()` block is unchanged** — it
  launches, renders, tears down, exactly as before. Scripts, tests and
  `scripts/capture_fixtures.py` needed no edit.

#### Part 2 — response cache

**`requests-cache` 1.3.3**, proposed and approved before adding. It subclasses
`requests.Session`, so `_TLSAdapter` still mounts and `fetch_text`'s retry and
curl fallback are untouched — pinned by a test, since a cache that quietly
dropped the TLS adapter would only show up on the one host that needs it.

Opened by `run_pipeline` for the length of a run, cached at
`data/http_cache.sqlite3`, TTL **30 minutes**.

**It covers more than the package asked for, and that should be said plainly.**
WP9 said "listing pages"; the cache sits on `fetch_text`, which is also what
Layer 5 uses for detail pages, so those go through it too. Left that way
deliberately rather than filtered down to listing URLs: a detail page is already
fetched at most once per job because the store skips jobs it has seen, so
caching them costs almost nothing and helps exactly one real case — the
hybrid-recheck path, which re-reads a stored job's description. But it is wider
than the brief and the README now says so.

**`cache_control` is deliberately off.** Sampling fourteen static listing pages:

| what the site sends | count |
|---|---|
| `no-cache, no-store` | 6 |
| cacheable (`max-age`, `must-revalidate`, or nothing) | 8 |
| carries an ETag or Last-Modified | 4 |

Honouring `no-store` would mean re-downloading those six every run — *more* load
on somebody else's server, not less, which is the wrong way round for priority 3.
It is a blanket CDN default aimed at browsers holding sensitive pages, not a
statement about a public jobs list, and our own TTL is half an hour. Turning it
off costs no conditional requests: requests-cache still sends `If-None-Match` /
`If-Modified-Since` from a stored validator once the TTL lapses, and counts the
304 as a hit. Both directions are pinned by tests against a local server that
records the headers it was sent.

**`stale_if_error` is off** — turned on in this package, then turned off at the
owner's instruction the same session, and the reasoning is worth keeping.

It hands back the previous copy when a site errors. The argument for it was the
impactpool 500s: a cached page beats a failed source, it can never invent data,
and every stale service logged at WARNING. The argument against it won: a run
that serves an hours-old page still reports a *successful* scrape of that source
into `source_health`, and priority 2 says a broken site must fail rather than be
papered over. A warning is not the same as a failure.

Nothing is lost by removing it. The flaky-500s case is handled where it belongs,
by `fetch_text`'s existing 5xx retry ladder (five attempts, backing off 2s to
8s); what that retry cannot rescue is a genuine outage, and a genuine outage is
exactly the thing the owner wants to see. A test pins it: a cached copy exists,
is findable, and the 500 still raises — and it also asserts the retry ran, so a
future session cannot delete the retry believing the cache covers it.

**Do not re-propose it.** It looks like free resilience and it is not; it buys
uptime with the honesty of `source_health`.

**Where the cache does report something it did not check.** Inside the TTL a
listing comes off disk without the site being touched, so that source goes into
`source_health` as a successful scrape whether or not it is actually up. This is
the same shape as `stale_if_error` above, bounded to half an hour and to a copy
that was fresh when it was taken. Put to the owner explicitly and kept — see the
decisions log for the timing argument, which is that the runs which hit the cache
are the attended ones.

**A cache hit cannot lose a job.** It returns the *previous* listing, so stored
jobs stay sighted and accrue no delisting misses; the worst it can do is delay
discovery of a new posting by up to the TTL, or keep a withdrawn one alive one
run longer.

**Escape hatches:** `--no-cache` bypasses it entirely (a run started with it
never constructs a `CachedSession`), `--cache-ttl SECONDS` retunes it. The cache
is never silent: every run logs `HTTP cache: N hit, N revalidated (304),
N fetched`. Expired rows are pruned on the way out, which is
what bounds the file — 12.5 MB after a full cold run.

**Rendered pages are not cached, by design.** The cache wraps `fetch_text`;
`fetch_rendered` goes nowhere near it. ETag and If-Modified-Since are HTTP
notions with no meaning for a page assembled by JavaScript, and caching rendered
HTML is a different feature with a different correctness argument. It is why the
warm run is 131 s rather than lower: 15 rendered pages × the fixed
`settle_ms = 4_000` is 60 s of deliberate sleeping, 46% of that run. **That fixed
settle, not browser churn, is now the largest single lever left** — a candidate
for a later package, and it needs per-source evidence before anyone shortens it.

#### A bug this package introduced and caught

`cache_name` was first passed as `str(path.with_suffix(""))`. requests-cache
appends `.sqlite` to an extension-less name, so the file landed as
`data/http_cache.sqlite` — which `.gitignore`'s `data/*.sqlite3` does **not**
match, and which holds the body of every career page fetched. Caught by looking
at `data/` after the first run. The path is now passed verbatim, and a test pins
the filename rather than only the behaviour.

#### Tests

`tests/test_http_fetch.py`, 29 tests, no network: a `http.server` on localhost
serves the cache tests and a real headless Chromium drives the pool tests
(mocking Playwright would assert nothing about the threading rules that are the
whole subject). Covers: no caching without the block; a hit inside the TTL
costing zero requests; revalidation carrying the stored ETag; the cache
surviving between blocks; a failing site raising even with a cached copy to
hand; a zero TTL revalidating rather than caching nothing; per-block stats; the
TLS adapter and the session being restored; the filename; one browser serving
many pages; nothing launched when nothing is rendered; eight concurrent callers
bounded by four threads; errors reaching the caller; a failure not poisoning the
pool; nesting; the no-pool path; and the `renders` capability mark.

**CI needed a change, and it was missed until GitHub caught it.** `ci.yml` said
"no `playwright install`: nothing in the test suite renders a page, so the
browser binaries would be downloaded on every run for nothing". This package
made that false and did not update the workflow, so the first push failed with
`7 failed, 436 passed` — every render-pool test, on `BrowserType.launch:
Executable doesn't exist`. Lint was clean; it showed as two failures only because
CI runs on both `push` and `pull_request`. The workflow now installs chromium
alone, cached on the resolved playwright version, and the comment records why the
earlier decision was reversed rather than silently deleting it.

The lesson generalises past this package: **a decision recorded as a comment
because it depended on an invariant is a decision that has to be re-read when the
invariant changes.** That comment stated its own reasoning plainly and was still
missed, because nothing in the local loop exercises it — `pytest` passes on a
machine that ran the README's `playwright install chromium`, which is every
developer machine and no CI runner.

**Verification.** `.venv/bin/pytest`: 443 passed (414 before). `.venv/bin/ruff
check .`: clean. `python -m job_scraper.run --help` works.

#### Noted, not fixed — outside this package

- **`probably_good` returns zero rows**, on the baseline run as well as both
  after-runs, so it predates WP9 and is not caused by it. The source logs the
  loud zero-row error and correctly declines to delist. Its extractor or the
  site's markup needs a look.
- `DEFAULT_USER_AGENT` still says `example.com`. That is WP10's first bullet and
  it asks the owner for the real value, so it was left alone.


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
- Give `tools/retrofilter.py` and `tools/blocklist_all.py` an argparse front
  door, even one with no options. Neither parses `sys.argv` today, so an
  argument is not rejected — it is never read, and the command runs. WP8b lost
  the owner's unreviewed-job set to `blocklist_all --help`; see the incident
  section there. `--help` should print the docstring and exit 0, and anything
  unrecognised should exit non-zero having changed nothing.

Two notes before you touch run.py's output, both from WP8h:

- `format_summary` now has a golden test, `tests/test_run_summary.py`, pinning
  its exact rendering. It exists because WP8h shipped a layout regression that
  no test caught — two labels ran into their own numbers at four digits. If you
  add a line to the funnel, regenerate the EXPECTED block from real output and
  say in the plan that you did. **Do not weaken or delete that test to make a
  failure go away**; the invariant tests beside it (no label abutting its
  number, ordinals in execution order) must keep passing untouched.
- The funnel's dropped-jobs lines carry the ladder ordinal in a left gutter
  ("L5  − …"), read from `drops.LAYERS` via `layer_ordinal`. A health warning
  is not a ladder layer, so give it its own shape rather than borrowing an
  ordinal — a warning that looks like a filter line will be read as one.

Branch wp10-politeness. Commit, do not push. Update the plan file.
```

### Prompt drift found before starting (2026-08-31)

The prompt was audited against the code as it stands after WP9. Four things had
moved; none changed what the package does, and all four are corrected above in
substance:

- **The two tools are at `job_scraper/tools/`,** not `tools/`. The prompt's
  paths predate the move.
- **`_DETAIL_WORKERS` lives in `experience_filter.py`,** not `http.py`.
- **WP9 took rendered fetches off the detail pool.** A rendered page is now
  queued to `http.RenderPool`'s four threads, so a cap applied only to the
  detail pool would have missed every dynamic source. The throttle therefore
  sits at the fetchers — `fetch_text` and the render pool's `page.goto` — where
  both paths pass through it.
- **WP9's response cache changes what "a request" means.** A page answered from
  disk must not consume a host's turn; see the refund decision in the log.

### What was done

**The User-Agent.** `DEFAULT_USER_AGENT` no longer contains `example.com`. The
owner was asked where the real details should live and chose `rules.json` (the
repo is public; `rules.json` is gitignored), so `build_user_agent` /
`user_agent_from_rules` assemble the header from two new keys, documented in
`rules.example.json`. The owner filled the real values into `rules.json` and
verified them on 2026-08-31 (this session did not touch that file — CLAUDE.md
forbids it), so runs now identify themselves properly. A clone without those
keys warns and identifies itself as `job-scraper/0.1 (no contact configured)`.

**The per-host cap.** `HostThrottle` in `http.py`: two requests at a time per
host, one second between the starts of two requests to the same host, hosts
independent of each other. `_DETAIL_WORKERS` stays at 10 — it is a cap on this
tool, not on any one site, and now means ten different employers in parallel. A
site that states a longer `Crawl-delay` in robots.txt gets it (the stdlib parser
accepts only whole seconds there, and needs `parser.modified()` called or it
returns None — that cost a debugging round and is commented in `robots.py`).

**robots.txt.** New `job_scraper/robots.py`: one fetch per host per run, cached
behind a per-host lock so concurrent first hits do not fetch it eight times. The
pipeline asks once per source and skips a disallowed one with a single clear
line — a skipped source records no `source_health` row, so it can never look
like a source that collapsed. `fetch_text` and `fetch_rendered` also check, so a
detail page under a disallowed path is kept but reported — see the review
follow-up below for what that claim originally said and why it was wrong. Override
per source with `ignore_robots: true` in `sources.yaml` (documented in
`sources.example.yaml`); it exempts the whole host, deliberately.

**Source health warnings.** `JobStore.source_health_regressions` compares each
source's row count against its own last *successful* run and reports anything
that lost more than half; `run_pipeline` logs each one and puts them on
`RunSummary.health_warnings`.

**`--dry-run`.** `JobStore(path, dry_run=True)` rolls its transaction back
instead of committing, `jobs_sources.csv` is not written, `run.py` skips the
xlsx export and the scoring stage (its verdicts would be discarded and its
tokens would not). The funnel it prints is the one a real run would print.

**The two tools.** `retrofilter` and `blocklist_all` now call `_parse_args()`
first: `--help` prints the docstring and exits 0, anything unrecognised exits
non-zero having done nothing. `tests/test_tool_front_doors.py` proves it with
every route to the store monkeypatched to raise — WP8b's mistake is not one to
repeat inside a test suite. The two tools were **not** invoked for real against
`data/jobs.sqlite3` to check, for the same reason.

### The golden summary test

`tests/test_run_summary.py` is untouched, and its `EXPECTED` block did **not**
need regenerating: neither new block renders on a healthy, writing run. The
health warnings and the dry-run notice append only when they have something to
say, so the pinned layout is byte-for-byte what it was. The invariant tests
beside it still pass unchanged.

The health block deliberately does not borrow a ladder ordinal (the WP8h note in
the prompt):

```
────────────────────────────────────────────────────
!  Source health: 1 source returned far fewer rows than last time
!  impactpool: 4 rows this run, was 120 (-97%)
```

`!` rather than `L5  −`, no right-aligned number column, and an ASCII `-97%`
rather than the funnel's `−`. A source that shrank is not a filter that fired,
and `tests/test_source_health.py` asserts the warning lines carry no `L`
ordinal and no drop marker.

### Follow-up after the owner's first dry run (2026-08-31)

That run turned up three things, one of which was a defect in this package.

**`ignore_robots: true` could not express the case that actually occurred.**
The OECD source's listing lives on `careers.smartrecruiters.com` and its
postings on `api.smartrecruiters.com`, whose robots.txt carries the blanket
`Disallow` an API host usually does. The override exempted only the host in
`sources.yaml`, so the flag the refusal message told the owner to set would not
have worked — and the message did not name the host that needed exempting. Both
fixed: `ignore_robots` now takes `true` *or* a list of hosts
(`_robots_overrides` in `pipeline.py`), and `RobotsDisallowed` names the host.
Five tests added. `RobotsPolicy.exempt()` was dead on arrival and is deleted.

**UNDP disallows its job board to everyone**
(`jobs.undp.org/robots.txt` vs `cj_view_jobs.cfm`). That is not a rule aimed at
the wrong crawler, it is the site saying no; the source stays skipped. Recorded
here so nobody re-adds it as a bug later.

**J-PAL's 44 → 9 was real, and is not this package's doing.** Diagnosed offline
from the WP9 response cache rather than by re-scraping: page 0 returned 9 jobs
and pager links through `?page=4`, while `?page=1` returned 200 with a 93 KB
body containing no job nodes and no pager at all, so the extractor stopped after
two pages. The site's paged responses changed; nothing in WP10 alters what a
server returns except the User-Agent. **Worth its own package** —
`extractors/jpal.py` trusts `_last_page` and a page that silently yields nothing.

**Found while investigating (WP9's, not WP10's), fixed on 2026-09-02:** every
test that called `run_pipeline` opened `http_cache()` with no path, which
resolves to the *real* `data/http_cache.sqlite3`. So the suite read, wrote and
pruned the owner's live response cache — it emptied a 37 MB cache during this
session (a cache is regenerable, so nothing was lost but a re-download), and a
populated cache made the suite four times slower (8 s → 35 s). See "Follow-up:
the response cache the tests were using" below.

### Numbers

- 496 tests pass (443 before), suite ~8 s. `ruff check .` passes.
- New: `tests/test_politeness.py` (25), `tests/test_source_health.py` (10),
  `tests/test_dry_run.py` (6), `tests/test_tool_front_doors.py` (12).
- The six existing `run_pipeline` call sites in tests now pass
  `check_robots=False`. Their fetchers are stubs and their hosts do not exist,
  so the alternative was a DNS lookup per test for an answer nothing asserts.
  The real path is covered against a localhost origin in `test_politeness.py`.
- No new dependencies: `urllib.robotparser` is stdlib and `requests` was already
  there.

### Not done, and why

- **No `--host-delay` or global `--ignore-robots` flag.** The prompt asked for a
  per-source override and that is what exists. A global switch is the one a
  tired owner reaches for at 11pm, and it turns the check off for every site
  rather than the one that was wrong.
- **A dry run still creates `data/jobs.sqlite3` if it does not exist**, empty,
  because the schema DDL commits before the transaction opens. It contains no
  run, no job and no drop row. Worth knowing before anyone claims `--dry-run`
  touches the filesystem not at all.

### Follow-up: the response cache the tests were using (2026-09-02)

The finding above, fixed.

**`run_pipeline` now takes `cache_path`.** It defaults to `None`, which means
the real cache in `data/`, so a run started from `run.py` behaves exactly as it
did. Every one of the eight test call sites passes `tmp_path /
"http_cache.sqlite3"` instead. The parameter reads like `out_db_path`, which is
the shape the finding asked for.

**A parameter alone was not enough.** Nothing stopped the ninth call site from
forgetting it, and forgetting it is silent: the run works, it just works on the
owner's file. So `tests/conftest.py` — the suite's first — wraps `http_cache`
for the duration of every test and raises if it is opened with no path, or with
the live path spelled out. It is patched on both `job_scraper.http` and
`job_scraper.pipeline`, because `pipeline.py` imported the name directly and
rebinding it on `http` alone would have left the exact call that caused this
unguarded. `tests/test_cache_path.py` pins both halves: the cache lands where
it was told to, and a run that names no path is refused under test.

**`--dry-run` deliberately keeps using the real cache.** The question was
whether a dry run should get a scratch cache too, since it currently warms and
prunes the owner's. It should not, on priority 3: a dry run followed by the
real run is the normal way this flag gets used, and a scratch cache would make
those two runs download every page twice from other people's servers. Sharing
the cache means the second run pays nothing. The two things a scratch cache
would protect against are not losses — the file holds public pages and is
regenerable, and the prune on block exit is the housekeeping that keeps it from
growing without bound, not a deletion of anything a run still wants. What
`--dry-run` promises is that the *store* is not written, and that is unchanged.

**Numbers.** 498 tests pass (496 before), suite ~8 s. `ruff check .` passes.
New: `tests/conftest.py`, `tests/test_cache_path.py` (2).

### Review follow-up (2026-09-02)

A separate session reviewed the branch. Six points, all acted on; the first
three were real holes rather than polish.

**1. Three sources bypassed the whole package.** `nutrition_international`,
`simprints` (both Workable) and `tetrapak` called `requests.post` directly, so
they identified as `python-requests`, read no robots.txt and paid no per-host
spacing — Tetra Pak's paginated loop, ten postings at a time with no pause,
hardest of all. The politeness work sat in `fetch_text`, and these three never
went through it. Fixed by `http.post_json`, which does the robots check, takes
the host's turn and sends the run's User-Agent; both extractors now call it. A
guard test walks `extractors/*.py` and fails on any direct `requests` /
`urllib.request` / `httpx` use, so the next POST-only board cannot reopen the
hole quietly.

**2. `--dry-run` was untested where it protects the spreadsheet.** The pipeline
half had six tests; the half in `run.py` — the export and the paid scoring call
— had none, so reordering two lines would overwrite `jobs.xlsx` with every
database test still green. `tests/test_run_dry_run.py` (7 tests) uses the
harness already sitting in `test_run_scoring_gate.py`.

**3. The plan claimed a blocked detail page "fails loudly". It did not.** Layer
5 fails open by design, so a refused detail page kept its job — with no
experience check, no PhD check and no stored description — behind a `debug`
line nobody sees on a normal run. Because robots.txt blocks a *pattern*, that
happens to every posting on a source at once, and the funnel still looks
healthy. `_DetailSignals.robots_refused` now separates a policy refusal from a
network failure, and `apply_detail_filter` logs one WARNING naming the count
and the host to exempt. Fail-open behaviour itself is unchanged: nothing is
lost, it is only no longer silent.

**4. A revalidated response refunded a turn it had used.** `requests-cache`
flags a 304 as `from_cache` — the body did come from disk — but the conditional
request went to the site. `_refunds_its_turn` now refunds only a response that
crossed no network at all.

**5. robots.txt was fetched without the fetchers' TLS handling.** No certificate
bundle, no curl fallback, so on the hosts with awkward SSL the read would fail,
log a warning and be read as "no restrictions" — the check absent from exactly
the sites that are fussiest. `RobotsPolicy` now takes the fetcher as a
parameter and `polite_fetching` passes `http._fetch_robots`, which has both.
The plain default keeps `robots.py` importable without `http`.

**6. Small.** README said 491 tests; it now says 511. `sources.yaml` and
`rules.json` were each parsed twice per run (once for the politeness config,
once inside `_run_pipeline`); both are now read once and passed down.

**Numbers after the follow-up.** 511 tests pass (498 before), suite ~8 s.
`ruff check .` passes. `tests/test_run_summary.py` still untouched.

### Second review pass (2026-09-02)

Two points on the fixes themselves, both taken.

- **`post_json` did not retry a 5xx**, so one transient 500 on page seven of
  Tetra Pak's pagination would abandon every page after it and report a short
  list rather than an error — the exact shape `source_health` warnings exist to
  catch, arriving by a route the commit message had claimed was closed ("the
  same treatment as every other fetch"). It retries now, on the same policy as
  `fetch_text`, which is shared as `_retry_delay` so a test can flatten it
  rather than wait twenty seconds. Still no curl fallback, and that is
  deliberate: that hatch is for hosts whose TLS the Python stack cannot
  negotiate, and none of the three POST boards is one.
- **The extractor guard could pass vacuously.** It globbed a path relative to
  the working directory, so from anywhere but the project root it matched no
  files and reported safety it had not checked. It now locates the package from
  `extractors.__file__` and asserts it found more than twenty modules, so a
  guard that is looking in the wrong place fails instead of passing.

514 tests pass.

---

## WP11 — J-PAL pagination, and silent short walks

```
think

Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP11 only.

WP10's source-health warning fired on its first real run: `jpal` returned 9
rows, down from 44. That was diagnosed offline from the WP9 response cache and
is a real shortfall, not a WP10 side effect. What the cache held:

- `https://www.povertyactionlab.org/careers` — 200, 9 job nodes, pager links
  through `?page=4`.
- `https://www.povertyactionlab.org/careers?page=1` — 200, a 93 KB body, zero
  job nodes, and no pager links at all.

So `extract` walked page 0, saw a pager, fetched page 1, parsed nothing from it,
asked `_last_page` where the end was, got 0, and stopped — returning 9 of about
44 postings without failing. The cache that held this evidence has since been
pruned; capture a fresh fixture with `scripts/` rather than assuming those
numbers still describe the site.

The narrow bug is in `extractors/jpal.py`. The general one is the shape:
**a paginated walk that ends because a page yielded nothing cannot tell "we
reached the end" from "this page did not parse".** Priority 2 says the second
must fail, not silently shorten the list. Five extractors have a `while True`
page loop — `jpal`, `smartrecruiters`, `tetrapak`, `unops`, `niras`,
`successfactors_html` — so decide deliberately whether this is one fix or a
shared helper, and say which in the plan.

- Work out why `?page=1` parses to nothing: a changed pager, a different URL
  shape, or a page that needs rendering. Fix `jpal` accordingly. Do not scrape
  the live site to build a fixture by hand — use the capture script.
- Make a mid-walk empty page loud. A first page that yields nothing is already
  handled (the pipeline's zero-row guard refuses to delist); a *later* page
  that yields nothing while the pager says there is more is the case with no
  guard at all, and it should raise so the source fails rather than shortens.
- Check the other paginated extractors against the same question. Report what
  you find; fix only what is actually broken, and note the rest.
- `_last_page` returning 0 from a page with no links is indistinguishable from
  a genuine single-page listing. Whatever you do about that, it has to keep the
  single-page case working — `probably_good` and others are one page by nature.

Do not touch the filter ladder or the run summary; this is an extractor
package. Watch that the WP10 politeness layer stays intact: extractors must
fetch through `job_scraper.http`, and `tests/test_politeness.py` has a guard
test that fails if one reaches for `requests` directly.

Branch wp11-pagination. Commit, do not push. Update the plan file.
```

### Result (2026-09-02)

**What page 1 actually was.** Nothing about the URL shape or the pager had
changed, and the page does not need rendering: a fresh capture walks
`?page=0` … `?page=4` and returns 37 postings, nine to a page but for the last.
So the 93 KB body with no job nodes *and no pager* was a transient failure of
the site's own view, not drift. The out-of-range response rules the alternative
out — `?page=99` returns "Your search returned no results" and still carries
its seven pager links. A J-PAL page inside the pager's range always has both
postings and a pager; a page with neither has not rendered.

That makes this the same animal as the Impactpool flaky 500s: an intermittent
site fault, which must fail rather than shorten. There was no jpal-specific
parsing bug to fix, so the fix is entirely in what the walk does about it.

**Shared helper or one fix: neither, exactly.** `extractors/pagination.py` holds
the *policy* — one exception type, `ShortWalkError`, and one function that
phrases the refusal — but no shared loop. What counts as "more was promised"
differs per listing (a pager, `totalFound`, `totalJobs`) and only the extractor
knows it; a shared walk would have to take that as a callback and would be a
worse version of the three-line guard it replaced. The exception type is the
part worth sharing, because the pipeline treats it the same way each time.

**J-PAL.** The pager is the authority, and the ambiguity behind the original bug
is now settled where it arises rather than worked around by the caller. The
listing is one Drupal view among several on the page, `div.view-id-jobs`, and it
distinguishes its own three states: `.view-content` (postings rendered),
`.view-empty` (rendered, genuinely none), and neither (the view did not render
at all). The walk reads that directly, so it no longer has to infer anything
from a missing pager — which both a single-page listing and a broken page have.
`_last_page` returns `None` for "no pager here" rather than 0, and the count
never shrinks.

Consequence worth flagging: a missing jobs view now raises on **page 0** too,
where it used to fall through to the pipeline's zero-row guard and be reported
as a healthy source with nothing on it. A career page with genuinely no
vacancies renders `.view-empty` and still returns cleanly, so the two are no
longer the same observation. Single-page listings are otherwise untouched — no
pager means one fetch and no complaint, which `probably_good` and friends rely
on.

**The other paginated extractors.** All five were broken the same way, and all
five are fixed. The first pass left four alone on the grounds that they had no
total to check themselves against; that was wrong, and looking properly found a
published count or a next-page link on every one of them.

| Extractor | What says there is more | Guard |
|---|---|---|
| `jpal` | the pager lists every page | page in range yielding nothing raises |
| `smartrecruiters` | `totalFound` | empty `content` before the total raises |
| `unops` | "1-6 of 74 results", and `aria-label="74 results"` | the finished walk is measured against the total |
| `niras` | "Vacant positions: 2" (`span.filter-result-text`) | same |
| `successfactors_html` | "Results 1 to 10 of 2010" (classic aria-label), "Showing 1 to 20 of 62 Jobs" (tile) | same, covering all three ways out of the loop |
| `impactpool` | a link to the next page | a page that was linked to and came back empty raises |

**The last three check the walk, not the page.** A page that renders three of
its six rows is *short*, not empty, and a short page is how most of these walks
legitimately end — so nothing inside the loop can tell those apart. The total
can: `pagination.reconcile` runs once the loop has ended, however it ended, and
fails the source unless the walk holds everything the listing said it had. That
also subsumes the all-duplicates exit on SuccessFactors, where a board serving
page one again has stuck rather than finished.

Two of those need their reasoning written down, because the obvious guard is
wrong for both.

**Impactpool is guarded by its link, not by its count.** It publishes "3973 jobs
match your search", and comparing that with what the walk collects would fail
the source on every single run: it is an aggregator, promoted postings repeat
across pages, and the deduplicated result is legitimately smaller — 3381 against
3973 on 2026-09-02. The next-page link has no such slack. Note the walk still
fetches the page after the last one rather than stopping when the link is
absent: trusting the link to *end* the walk would let a restyled pager truncate
it silently, which is the whole failure being fixed here.

**SuccessFactors is guarded by its count, and that rests on an invariant worth
stating.** Comparing a finished walk with "of 2010" is only safe if this
extractor sees exactly the postings the site is counting. It does: on all four
captured instances the rows parsed off a page equal the upper bound in that
page's own label — DSV 10, ISS 20, Novo Nordisk 100, Coloplast 25. That
agreement is pinned by a test, and it carries the weight of the whole
reconciliation: if it ever breaks, every walk on that instance ends one row
short of the total and the source fails on every run.

Where a total cannot be read on the day, `pagination.unverifiable_end` logs a
warning instead of raising. An extractor that cannot see how long the listing is
has no grounds to call a short walk a failure — but a source that has quietly
lost its guard should say so while its count still looks plausible.

**The fixture harness was part of the bug.** `jpal`'s golden test passed on 9
jobs because the replay fetcher served the saved page once and an empty body
afterwards — it faked the end of the walk, which is precisely the lie the
extractors now refuse. A fixture of one page cannot exercise a walk, and that is
why the shortfall was invisible to a 500-test suite. So:

- `scripts/capture_fixtures.py --pages all` records every response an extractor
  asks for, saving them as `<name>.html`, `<name>.p1.html`, … Default is still
  one response, so no other source's capture changes.
- Replay is positional — `recorded_pages_fetch` serves the saved pages in order
  — and a stale page file left by a longer previous walk is deleted on capture,
  because a positional replay would happily serve yesterday's page 4 as today's.
- `jpal.html` and four page files are the real walk, 440 KB, and the golden
  count is 37 rather than 9.

**Where a walk is too long to store, the fixture pins the parser instead.** DSV
is 201 pages and Impactpool about a hundred; capturing those the way J-PAL is
captured would be tens of megabytes of someone else's HTML. Their fixture cases
now call `_parse_page` rather than `extract`, and the walk around it is covered
synthetically in `tests/test_pagination.py`. Two consequences:

- `impactpool` and `successfactors_html` gained a real `_parse_page`, and
  deduplication was split: a posting shown twice on one page is settled by the
  parser (DSV renders every row three times, once per breakpoint), a posting
  shown on two pages by the walk. The golden counts did not move.
- What the fixtures pin about pagination is the *evidence*, not the walk: tests
  assert that each captured page still states its total (DSV 2010, ISS 62, Novo
  Nordisk 350, Coloplast 331, NIRAS 2) and that Impactpool's page still links to
  its next. A restyled label would otherwise fail nothing and quietly drop the
  source back to an unguarded walk.

**Re-capturing J-PAL** needs the flag:
`python scripts/capture_fixtures.py --pages all jpal`. Forget it and the fixture
is one page, the extractor raises on the faked end, and the golden test fails
loudly — which is the right way round.

**Tetra Pak moved off the endpoint its robots.txt disallows.** Not a pagination
fault, but it surfaced here: the source failed in run 18 with `robots.txt
forbids …/services/recruiting/v1/jobs`. Their robots.txt says
`Disallow: /services/` to every automated visitor, and WP10's check was right to
refuse it. The bespoke `tetrapak.py` had been calling that endpoint since before
the politeness layer existed, which is why nobody noticed.

The same postings are published at `/search/`, which robots.txt allows, and
Tetra Pak is a SuccessFactors board like DSV, ISS, Novo Nordisk and Coloplast.
So the source now goes through `successfactors_html` against
`https://jobs.tetrapak.com/search/?q=&locationsearch=Sweden` — the location
filter that used to sit in the old module's request body, moved into the URL —
and `tetrapak.py` is deleted. Live check: **7 postings, all Lund**, with
locations and departments populated (run 17, the last success, had 8).

An `ignore_robots` exemption was the alternative and was rejected: robots.txt is
the only machine-readable way a site can say "not this path", and deciding they
did not mean it is how an exemption list starts. One honest caveat: `/search/`
needs rendering, and the headless browser fills the page from `/services/`
itself. Our robots check only sees the top-level URL. A browser loading a page
the way a visitor's browser does is a different act from a script harvesting an
API, but it is not *no* act, and if the owner disagrees the remaining choices
are a deliberate exemption or dropping the source.

**Two consequences of the switch, both worth knowing before the next run.**

- **Every Tetra Pak posting gets a new dedupe key.** The key comes from
  `detail_url`, and the route changed it from `/job-detail/{id}/{slug}` to
  `/job/{Slug}/{id}-en_GB`. The eight rows stored under the old URLs will be
  delisted and the seven current ones will arrive as new. Nothing is deleted —
  but the first run after this will show Tetra Pak churning, and that is why,
  not a site change.
- **One entry in `sources.yaml` must change by hand**, because that file is
  gitignored and holds the owner's real config:

      - name: tetrapak
        url: https://jobs.tetrapak.com/search/?searchResultView=LIST
        strategy: dynamic        # was: static

  Until that is done the source fetches without rendering, finds no job links
  and returns nothing — loudly, since the zero-row guard refuses to delist on it.

**Verified against the live sites**, not just fixtures:

- `jpal` returned **37 rows, ok**, in run 18 (2026-09-02) — the fix confirmed in
  a real run, not only in the capture. Its recent history is 61 → 53 → 44 → 38 →
  37 over two weeks, which is the board shrinking, not the walk.
- `unops` walked its 13 pages and returned **74**, matching run 18 exactly and
  matching its own "74 results" — so the reconciliation does not fire on a
  healthy walk. `niras` returned **2**, also matching. Both were re-checked
  after the walk-level guard replaced the page-level one.
- `tetrapak` through its new route returned **7**, all Lund.
- `impactpool`'s pager was checked directly: page 1 links to page 2, and a page
  past the end (`?page=200`) returns no postings and no next link.
- Not walked live: `smartrecruiters` (an ad-hoc harness lacks the pipeline's
  per-source `ignore_robots`, and the API is robots-disallowed without it) and
  the SuccessFactors boards (201 pages). Their guards rest on unit tests and on
  the totals pinned against captured markup.

**Numbers.** 583 tests pass (518 before), suite ~13 s. `ruff check .` passes.
`python -m job_scraper.run --help` unchanged. The filter ladder, the run summary
and `tests/test_run_summary.py` were not touched. Nothing new fetches for
itself: `pagination.py` does no I/O and the politeness guard test still passes.

### Second review pass (2026-09-02)

Another session read the branch and found three real defects, all in code this
package added. All three are the same shape as the bug the package exists to
fix — a walk that stops without saying so — which is worth noting: the guards
were easier to get subtly wrong than the thing they guard.

**1. UNOPS's total-reader crashed on ordinary page wording, and could switch
itself off.** The pattern was "a number, then the word results", which is not
specific enough for a page of prose. "Search results" matched with no number in
it and `int("")` raised. Worse and quieter: "10 results per page" would have
been read as a total of 10, which any walk beats, so the guard would have been
off while still appearing to work. Now two patterns, both requiring a digit: the
running text is read only in the "of N results" form the summary uses, and an
aria-label only when the whole label is the count.

**2. J-PAL lost a page that rendered an empty listing mid-walk — but the obvious
fix was wrong, and the live check caught it.** The reported hole is real: the
new code caught the jobs view being *absent* and let its "no results" state
pass on any page, carrying on to the next and losing that page's postings
quietly. The obvious fix — raise on any empty page inside the pager's range —
was written, and failed against the live site within the minute: J-PAL returned
`ShortWalkError` on `?page=4` for a board that was perfectly healthy.

What that turned up is worth recording, because it would have failed runs. The
board went from 37 postings to 36 during the day, and **J-PAL's pages are
edge-cached separately**, so page 0 was still a copy claiming five pages while
page 3's pager — fetched seconds later — said four. Following page 0's pager
walks one page past the end, where the site correctly answers "Your search
returned no results". An over-claiming pager is normal, not a fault.

So the discriminator is not the pager the walk started with; it is the pager on
*the empty page itself*. If that page lists itself, it is a page of the listing
gone missing and the source fails. If its own pager says the listing ended
earlier, the walk is simply one page past the end and stops. Both directions of
cache skew are covered: an under-claiming pager still cannot shorten a walk,
because the page count never shrinks. Verified live afterwards: 36 postings, no
error.

**3. Impactpool could still hit its 200-page cap and return quietly.** Every
other exit from that loop was guarded and this one was not, while J-PAL's
equivalent cap did raise — the two now behave the same.

**And a fair hit on the fixtures.** Five golden tests had been switched from the
whole extractor to its parsing half, justified by "the walk is too long to
store". True for DSV (201 pages) and Impactpool (~100); copy-pasted onto ISS (4
pages), Novo Nordisk (4) and Coloplast (14), where it plainly was not. Two
things changed:

- `novo_nordisk` is captured as its whole four-page walk and runs the real
  extractor again. The honest argument for the other three was never page count
  — it is that the SuccessFactors walk is *one shared module*, so capturing five
  instances' walks tests the same loop five times. One does it end to end; the
  others pin the four skins' parsing, which is what actually differs. That is
  what their comments now say.
- **UNOPS is captured at last**, as its whole thirteen-page walk. It had no
  fixture at all, which is precisely how defect 1 survived: the one source with
  a hand-rolled total-reader was the one whose reader had never met real markup.
  The captured walk parses 74 postings and reconciles against its own stated 74.

Fixture directory 3.5 MB → 5.0 MB. `novo_nordisk`, `unops` and `jpal` all need
`--pages all` when recaptured; the golden comments say so.

Also fixed: the summary table said 552 tests where the result section said 554
(neither is current — see Numbers), and `capture_one` shadowed its own `source`
parameter with a URL.

**Left for later.** `ruff format` has never been run on this repo, so 36 files
disagree with it and the ones this package touched could not be formatted
without burying the diff in unrelated churn. That is WP12.


---

## WP12 — Run the formatter

`ruff check` has been the gate since WP2 and passes. `ruff format` has never
been run, so the repo is formatted by hand and 36 of its 86 tracked Python
files disagree with the formatter — mostly line breaks the formatter would join,
because they were written to a narrower margin than the configured 100.

This is not a defect. Nothing is broken, and it is deliberately its own package
because the diff is large, entirely mechanical, and would make any real change
it was bundled with unreviewable. WP11 hit this: files it touched could not be
formatted without also reformatting lines it never went near.

```
Read CLAUDE.md and docs/REFACTOR-PLAN.md, then work on WP12 only.

`ruff check .` passes and always has. `ruff format` has never been run, so 36
files are formatted by hand and disagree with it. Fix that, in a way the owner
can review in one sitting.

- Run `ruff format .`. One commit, nothing else in it. The diff will be large
  and every hunk should be whitespace, line breaks, or trailing commas — read
  it, and if any hunk changes a string, a number or the order of anything,
  stop and say so rather than committing it.
- `pytest` must pass with exactly the same number of tests before and after.
  Say both numbers. A formatter cannot change behaviour, so if a test moves,
  something else is wrong.
- Add the formatter to the gate so it does not drift again: a `ruff format
  --check .` line in the Definition of done in CLAUDE.md, beside `ruff check`.
- Check whether the fixture files and `docs/` are affected — they should not
  be, and `ruff format` should not be pointed at anything outside the Python
  source.

Do not change any code while you are in there. If you notice something that
wants fixing, note it at the end of your response; a formatting commit that
also fixes a bug is a formatting commit nobody can review.

Two things not to touch: `tests/test_run_summary.py` pins exact rendering and
`tests/test_extractors_golden.py` pins exact extractor output. The formatter
will rewrap the string literals in both. That is fine — the *values* must not
change, and the tests passing afterwards is what proves it.

Branch wp12-ruff-format. Commit, do not push. Update the plan file.
```

### Result (2026-09-02)

Done on branch `wp12-ruff-format`, one commit for the reformat and one for the
gate. 37 of 86 tracked Python files changed, 263 insertions and 289 deletions,
all of it line breaks, indentation and trailing commas.

**583 tests before, 583 after.** `ruff check` still passes, `ruff format
--check .` now reports all 90 files clean, and `--help` still works.

#### How "no hunk changes a value" was checked

Not by reading 552 lines of diff and hoping. Every tracked file was parsed with
`ast.parse` and dumped with `ast.dump(..., include_attributes=False)` before and
after, then compared. That drops line and column numbers but keeps every
literal, name and ordering, so an identical dump is proof no string, number or
order moved — and it sees through implicit string concatenation, which the
formatter joins in `eval.py` and `config_loader.py` without changing the
concatenated value.

85 of 86 files came back byte-identical. One did not.

#### The one real value change, and the decision

`tests/test_source_health.py:47` is a docstring whose first character is a
quote:

```diff
-    """"More than 50%" is the threshold, so the boundary case stays quiet."""
+    """ "More than 50%" is the threshold, so the boundary case stays quiet."""
```

Ruff inserts a space so the opening does not read as a four-quote sequence.
This is deliberate formatter behaviour, but it genuinely changes the string:
the value gains a leading space.

Put to the owner rather than committed silently, per the package's own rule.
**Decision: accept the space.** It is a test function's docstring and nothing
reads it. The three module docstrings that *are* read, passed as
`description=__doc__` by the two `scripts/` front doors and three
`job_scraper/tools/` ones, are unchanged.

Rejected: `# fmt: skip` on that line, which was verified to work and would have
preserved the value exactly, but puts a hand-written code change inside a commit
whose whole value is being mechanical.

**Worth knowing for the next formatter-adjacent package:** `ruff format` is not
purely whitespace. Docstrings are the exception — it also strips trailing
whitespace inside them and normalises their indentation. If a future package
ever asserts on a docstring, this is where it will bite.

#### Blast radius

- `tests/fixtures/` holds 39 HTML, 2 CSV and 2 JSON files and **no Python at
  all**, so the formatter cannot reach it. Confirmed by `find`, not assumed.
- `docs/` **is** in scope, which was a surprise and is worth writing down. Since
  ruff 0.16, `ruff format` also formats fenced ` ```python ` blocks inside
  Markdown. Four tracked Markdown files are scanned — `CLAUDE.md`, `README.md`,
  `docs/REFACTOR-PLAN.md` and `job_scraper/config/profile.example.md` — and they
  are the four that made ruff report 90 files against 86 tracked `.py`. All four
  were already clean. The first draft of this very section then broke the gate by
  fencing a diff as `python`; it is fenced as `diff` now. **A code block in this
  plan is real input to the formatter.**
- `ruff format .` was therefore left pointed at the repo root deliberately: the
  Markdown is worth keeping formatted too, and there is nothing else outside the
  Python source for it to find.
- A live git worktree sits at `.claude/worktrees/priceless-carson-70dbe2`, on
  branch `wp10-test-cache-isolation`, holding a second copy of all 85 source
  files. Ruff skips it from the repo root — it is a separate VCS root — but
  formats it happily when pointed at it directly (34 files). Every `.py` under
  `.claude/` was checksummed before and after and is unchanged. **Do not run
  `ruff format .claude/...`, and prefer the named source directories if this
  ever needs repeating.**
- The plan said 36 files; it was 37 under ruff 0.16.4. The count is a moving
  target across ruff versions, which is the argument for the `--check` gate.

#### The gate

`ruff format --check .` added to the Definition of done in `CLAUDE.md`, beside
`ruff check .`.

Not added to `.github/workflows/ci.yml`, whose `Lint` step still runs only
`ruff check .` — the package asked for the CLAUDE.md line specifically. That
file is the checklist a session reads; CI is what actually enforces. Adding
`ruff format --check .` to the `Lint` step is a one-line follow-up and is the
thing that would stop this drifting for real.

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
