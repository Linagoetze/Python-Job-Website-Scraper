# Decisions log

This is the living decisions log for job_scraper. It answers questions a
session would otherwise have to re-derive: what was tried, what was rejected,
and what was measured rather than argued about. **Every package that lands a
decision appends to it** — this file never closes, unlike a plan.

It was extracted from `docs/REFACTOR-PLAN.md`'s own decisions log in SP0b
(2026-09-11), because that file is now a 6,000-line archive of finished work
and this is the roughly 800 lines of it a session actually needs at the start
of every session. The entries below are kept verbatim from that extraction —
this is a record, not prose to improve. Citations naming the package that
produced a decision (`WP8g`, `CU3`, ...) link to that package's full prompt and
result in `docs/REFACTOR-PLAN.md`, for the incident narrative behind the
one-line rule.

Read this file, `CLAUDE.md` and `docs/SOURCES-PLAN.md` at the start of every
session — see `CLAUDE.md`.

---

- Conditional hybrid-gated locations (cities too far to commute to daily,
  admitted only when the role is hybrid — `filtering.py`'s
  `build_hybrid_pattern`/`matches_rules`, `experience_filter.py`'s
  `_resolve_hybrid`) are a deliberate feature the owner wrote before this plan
  existed, not accidental scope creep. Keep it; do not propose deleting it as
  part of the filter-ladder cleanup in [WP8](REFACTOR-PLAN.md#wp8--trim-the-ladder-prune-the-keywords).
- **Statuses after [WP5](REFACTOR-PLAN.md#wp5--sqlite-store-part-2-cut-over-and-delete-the-csv-store).** `'rejected'` covers both "a filter now excludes this
  stored row" and (from [WP5b](REFACTOR-PLAN.md#wp5b--replace-the-blocklist-everything-routine)) "the owner said no" — one status, deliberately,
  to stay inside the [WP4](REFACTOR-PLAN.md#wp4--sqlite-store-part-1-schema-and-dual-write) vocabulary. Consequence: nothing automatic ever flips
  `'rejected'` back, so loosening a rule does not resurrect rows it previously
  rejected (they are still in the table for manual revival). The automated
  re-filter pass (`pipeline.refilter_stored_jobs`) touches **only `'new'`
  rows** — `'seen'` is review history and must not be silently rewritten to
  `'rejected'`, or the seen-vs-rejected distinction the blocklist import
  preserves would erode run by run.
- **Delisting after [WP5](REFACTOR-PLAN.md#wp5--sqlite-store-part-2-cut-over-and-delete-the-csv-store)** is `jobs.misses`: reset on sighting, incremented per
  successful scrape of the source without a sighting, flipped to `'delisted'`
  at N consecutive misses (default 2, `--delist-after`). Only `'new'`/`'seen'`
  flip; shortlisted/rejected keep their status while misses accrue.
  `--allow-empty-delist` now means "this source genuinely emptied — delist its
  unreviewed jobs *now*", bypassing the threshold; without it a zero-row
  scrape still counts for nothing at all.
- **Drop-log rule strings are a contract, not log text ([WP8a](REFACTOR-PLAN.md#wp8a--drop-log-record-every-exclusion)).** The `rule`
  column in `run_exclusions` is what `--rule` filters on and what the per-rule
  counts group by, so changing a rule string silently splits one rule into two
  across the retention window and makes a before/after comparison lie. Add new
  rules freely; reword an existing one only deliberately. The filters attach
  the rule to the excluded job under `filtering.DROP_RULE_KEY`; a layer that
  forgets logs `unattributed` rather than dropping the row, because a missing
  row is the exact blindness the log exists to remove.
- **[WP7](REFACTOR-PLAN.md#wp7--llm-scoring-stage) follow-up (2026-08-17): scoring stays off, and API billing is not a
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
  population ([WP8c](REFACTOR-PLAN.md#wp8c--offline-evaluation-harness)).** `data/curated/labels.csv` was assembled from the drop
  log plus the review table, so it deliberately over-samples what the filters
  rejected. Its precision and recall are comparable *between two rule
  configurations over the same file* — which is the only comparison a rule
  change needs — and are not an estimate of what a real run yields. Anyone
  quoting "precision 0.257" as the scraper's precision is quoting it wrong.
- **`review` is the positive class and beta defaults to 2 ([WP8c](REFACTOR-PLAN.md#wp8c--offline-evaluation-harness)).** A false
  positive costs a line in a spreadsheet; a false negative costs a job the
  owner never learns exists, which is the "never lose data" priority in metric
  form. Any future metric added here keeps that asymmetry or states plainly
  that it does not.
- **Three causes hide behind "location drops" ([WP8d](REFACTOR-PLAN.md#wp8d--unresolvable-locations)/[WP8e](REFACTOR-PLAN.md#wp8e--extractor-location-gaps)).** Reading [WP8c](REFACTOR-PLAN.md#wp8c--offline-evaluation-harness)'s
  false-negative listing found that the 33 lost location jobs are not one bug.
  (1) The extractor captured no location at all — a genuine extractor fault,
  and [WP8e](REFACTOR-PLAN.md#wp8e--extractor-location-gaps). (2) The listing page never named the cities (`"2 locations"`), so
  the extractor is faithfully copying a placeholder and there is nothing on
  that page to capture. (3) The field names a region rather than a city:
  `filtering._GENERIC_LOCATION_TOKENS` lists `"home based"` but not
  `"home base"`, so `"Home base - EMEA"` reads as a city nobody has heard of.
  (2) and (3) share one root cause and are [WP8d](REFACTOR-PLAN.md#wp8d--unresolvable-locations) — `matches_rules` knows
  "empty" and "a specific city" and has no third state for "present, and not a
  place".
- **An unresolvable location defers to Layer 2, and fails closed ([WP8d](REFACTOR-PLAN.md#wp8d--unresolvable-locations)).**
  Owner's decision, 2026-08-19: treat it exactly as a conditional hybrid city
  is treated — pass Layer 0 with a pending reason, settle it against the
  fetched description in `_resolve_hybrid`'s image, and drop it when the
  description confirms nothing on the list. Consequence, and it is the point:
  this does **not** hand back the lost jobs. It stops them being killed by a
  placeholder string and gets them judged on the posting instead. The price is
  a detail fetch for jobs that previously died at Layer 0, which [WP8d](REFACTOR-PLAN.md#wp8d--unresolvable-locations) must
  measure and report rather than assume is small.
- **The gold set measures Layer 0 changes and is blind to extractor changes
  ([WP8d](REFACTOR-PLAN.md#wp8d--unresolvable-locations)/[WP8e](REFACTOR-PLAN.md#wp8e--extractor-location-gaps)).** `labels.csv`'s `location` column holds what the extractor
  produced at labelling time. So `eval.py` scores a `filtering.py` change
  exactly, and reports *no improvement* for a fixed extractor — it is still
  replaying the old broken value. [WP8d](REFACTOR-PLAN.md#wp8d--unresolvable-locations) is therefore measurable with the
  harness and [WP8e](REFACTOR-PLAN.md#wp8e--extractor-location-gaps) is not, which is why they are separate packages in that
  order. Rows are keyed by `dedupe_key` and the review/discard judgement is
  about the job rather than the location string, so [WP8e](REFACTOR-PLAN.md#wp8e--extractor-location-gaps) needs the `location`
  column refreshed from the store, not the set re-labelled.
- **`non_place_locations` extends the code tokens, it does not replace them
  ([WP8d](REFACTOR-PLAN.md#wp8d--unresolvable-locations)).** Owner's decision, 2026-08-19: one new `rules.json` key, seeded with
  regions *and* bare country names, matched whole-word per segment. A
  `rules.json` without the key still recognises the `"N locations"` shape and
  `home based`, so the feature cannot be switched off by a config file that
  predates it. Consequence for anyone editing the list: a term only matters
  when striking it out leaves no letter behind in that segment, so adding
  `"Spain"` does not turn `Barcelona, Spain` into a placeholder.
- **A deferred state is not a recall win, and the eval report says so
  ([WP8d](REFACTOR-PLAN.md#wp8d--unresolvable-locations)).** `eval.py` flags `pending_location` jobs the way it already flags
  `pending_hybrid` ones. Layer 2 settles both and fails closed, and the harness
  makes no HTTP request, so the recall it reports over deferred jobs is a
  ceiling. On the 2026-08-19 gold set the entire 0.365 → 0.432 gain is
  provisional; quoting it as achieved recall is quoting it wrong.
- **`UNVERIFIED_KEY` replaces `hybrid_unverified` ([WP8d](REFACTOR-PLAN.md#wp8d--unresolvable-locations)).** Both fail-closed
  deferred states can be dropped by a network hiccup rather than a judgement,
  and neither may be persisted as `'rejected'`. One key on the job dict, not
  one per filter — a second flag is one `pipeline.py` forgets to check, and the
  cost of forgetting is a job permanently lost to a timeout.
- **An empty location field is a fourth Layer 0 outcome, not the third one
  wearing a different rule string ([WP8f](REFACTOR-PLAN.md#wp8f--empty-location-passthrough)).** [WP8d](REFACTOR-PLAN.md#wp8d--unresolvable-locations) gave `matches_rules` a state
  for "present, but names no place" (a placeholder like "2 Locations"),
  deferred to Layer 2 because the description might name a real city. An
  empty field is not that: there is no page text a Layer 2 fetch could read a
  location off, because none was ever captured. So [WP8f](REFACTOR-PLAN.md#wp8f--empty-location-passthrough) admits it outright at
  Layer 0, permanently, under its own `_LOCATION_EMPTY_ADMITTED_REASON` —
  deliberately *not* wired into `_HYBRID_PENDING_REASON`/
  `_UNRESOLVED_PENDING_REASON`'s machinery, so it costs no detail fetch and
  Layer 2 never goes looking for it. Consequence: `RULE_LOC_EMPTY` lost its
  only call site (`_location_drop_rule` returned it exclusively for an empty
  field, and `matches_rules` now intercepts every empty field before that
  function is ever reached) and was deleted, per [WP8a](REFACTOR-PLAN.md#wp8a--drop-log-record-every-exclusion)'s rule that a drop-log
  rule string going silent is a contract change to call out, not a private
  implementation detail.
- **A wrong location and a missing one are the same bug wearing different
  clothes ([WP8g](REFACTOR-PLAN.md#wp8g--iss-location-extraction)).** Found while reviewing [WP8f](REFACTOR-PLAN.md#wp8f--empty-location-passthrough), 2026-08-20. [WP8e](REFACTOR-PLAN.md#wp8e--extractor-location-gaps)'s population
  was "sources that captured no location", and [WP8f](REFACTOR-PLAN.md#wp8f--empty-location-passthrough) then admitted exactly that
  case. ISS is in the same population and neither package saw it, because its
  extractor does not return an empty string — it returns the literal word
  `"Title"`, a table header read as data, on all 33 of its rows in the gold
  set. An empty field now passes Layer 0; `"Title"` is judged as a city nobody
  has heard of and dropped under `RULE_LOC_UNLISTED_CITY`. Nine of those 33 are
  labelled `review`, so the leftover case costs as much as [WP8f](REFACTOR-PLAN.md#wp8f--empty-location-passthrough)'s whole gain.
  Consequence for the queue: [WP8g](REFACTOR-PLAN.md#wp8g--iss-location-extraction) goes **before** [WP8](REFACTOR-PLAN.md#wp8--trim-the-ladder-prune-the-keywords), and the `location`
  column refresh below stops being optional — see the next entry.
- **The gold set is a measuring instrument, and it needed recalibrating before
  [WP8](REFACTOR-PLAN.md#wp8--trim-the-ladder-prune-the-keywords) (2026-08-24).** [WP8e](REFACTOR-PLAN.md#wp8e--extractor-location-gaps), [WP8f](REFACTOR-PLAN.md#wp8f--empty-location-passthrough) and [WP8g](REFACTOR-PLAN.md#wp8g--iss-location-extraction) all changed what the extractors and
  Layer 0 produce, and `labels.csv` still held the old values, so the harness
  was scoring a filter that no longer existed. `scripts/refresh_label_locations.py`
  refreshed 63 locations; the owner re-judged the 13 rows that had been labelled
  `review` without one — eight of them turned out to be in Chennai, Gdansk, New
  York or London and were never wanted, while four BearingPoint roles turned out
  to be in Malmo and genuinely were. Four ISS rows could not be refreshed at all:
  the postings vanished before [WP8g](REFACTOR-PLAN.md#wp8g--iss-location-extraction) fixed the extractor, so nothing can ever
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
  from what the SDK would really raise. Worth folding into the [WP7](REFACTOR-PLAN.md#wp7--llm-scoring-stage) scoring code
  the next time anything touches it.
- **An extractor that fetches for itself cannot be fixture-captured ([WP8g](REFACTOR-PLAN.md#wp8g--iss-location-extraction)).**
  The 2026-08-21 capture attempt failed with "extractor made no request".
  `capture_fixtures` hands the extractor a recording fetcher and keeps the first
  URL it asks for, so the contract is that an extractor uses the callable it is
  given. `successfactors_html` (and `niras`) break it: they build their own
  `fetch_rendered` and the recorder never fires. `workday` honours it, tests
  capability via `is_rendering_fetcher` rather than identity, and is the only
  dynamic extractor with fixtures (`busuu`, `path`) — which is the evidence, not
  a coincidence. Consequence: ISS's location bug was never catchable by a golden
  test, because ISS was never capturable. Unblocking that is [WP8g](REFACTOR-PLAN.md#wp8g--iss-location-extraction) step 0, and
  the general lesson is that `strategy: dynamic` belongs to the caller, not to
  the extractor.
- **The `"Title"` was an accessibility label, not a table header ([WP8g](REFACTOR-PLAN.md#wp8g--iss-location-extraction),
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
- **A screen-reader label is never data, for any source ([WP8g](REFACTOR-PLAN.md#wp8g--iss-location-extraction)).** The `sr-only`
  strip went into the shared fallback rather than the ISS branch, on the
  principle that a field label is not a location for DSV either — it merely does
  not appear in DSV's markup today. Cost of doing it generally: nothing
  measurable (DSV's job rows contain zero `sr-only` elements). Cost of doing it
  as an ISS special case: the next SuccessFactors site to ship the tile layout
  repeats all 33 lost rows before anyone notices.
- **Fixing a location bug does not license fixing the department beside it
  ([WP8g](REFACTOR-PLAN.md#wp8g--iss-location-extraction)).** DSV's `department` holds a posting date; `span.jobFacility` is right
  there and would correct it. It was left alone, because `department` is part of
  `filtering._haystack` and [WP8](REFACTOR-PLAN.md#wp8--trim-the-ladder-prune-the-keywords) is about to measure what each keyword costs
  against exactly that text. Correcting the field now would shift [WP8](REFACTOR-PLAN.md#wp8--trim-the-ladder-prune-the-keywords)'s baseline
  underneath it for something this package was not sent to fix. The general form:
  when a field feeds the harness, improving it is a measurement change, and it
  belongs to the package doing the measuring.
- **Reasoning identifies the layout; only a fixture shows the data ([WP8g](REFACTOR-PLAN.md#wp8g--iss-location-extraction),
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
- **Before honouring a fetcher, check what the caller actually passes ([WP8g](REFACTOR-PLAN.md#wp8g--iss-location-extraction),
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
- **The labels refresh is a prerequisite for [WP8](REFACTOR-PLAN.md#wp8--trim-the-ladder-prune-the-keywords), not housekeeping ([WP8g](REFACTOR-PLAN.md#wp8g--iss-location-extraction)).**
  The rule three entries up still holds: `eval.py` replays `labels.csv`'s
  stored `location`, so a fixed extractor scores as no improvement until the
  column is refreshed from the store. [WP8e](REFACTOR-PLAN.md#wp8e--extractor-location-gaps) and [WP8f](REFACTOR-PLAN.md#wp8f--empty-location-passthrough) could both live with that,
  because neither needed the harness to see the fixed values. [WP8](REFACTOR-PLAN.md#wp8--trim-the-ladder-prune-the-keywords) cannot: it
  prunes `title_exclude_keywords.csv` by measuring which keywords cost wanted
  jobs, and a wanted job still blocked at Layer 0 by a stale `"Title"` never
  reaches the keyword layer to be counted. Pruning on that evidence would keep
  a keyword whose real cost is higher than measured. So the order is [WP8g](REFACTOR-PLAN.md#wp8g--iss-location-extraction), then
  a real run, then the refresh, then [WP8](REFACTOR-PLAN.md#wp8--trim-the-ladder-prune-the-keywords)'s baseline.
- **[WP8](REFACTOR-PLAN.md#wp8--trim-the-ladder-prune-the-keywords)'s original premise died with [WP7](REFACTOR-PLAN.md#wp7--llm-scoring-stage), and its prompt was rewritten
  (2026-08-20).** As first written, [WP8](REFACTOR-PLAN.md#wp8--trim-the-ladder-prune-the-keywords) opened "with LLM scoring in place" and
  justified every deletion with "subsumed by the scorer". The scorer is off —
  `scoring_enabled` is `false`, and the [WP7](REFACTOR-PLAN.md#wp7--llm-scoring-stage) entry above says it stays off until
  the owner opens API billing — so deleting a layer today hands its job to
  nothing, not to the scorer. Consequences, all now in the rewritten prompt:
  `title_exclude_keywords.csv` is **not** deleted, because nothing replaces it;
  it is pruned against the harness instead. The two language layers are still
  deleted, but on the smaller and honest grounds that they cost complexity and
  buy almost nothing. Two of the prompt's three performance fixes were already
  done or overstated. If API billing is ever opened, revisit the keyword CSV:
  that is the moment the original argument becomes true.
- **Every per-rule cost the eval harness prints is an *attribution*, not a
  marginal cost ([WP8](REFACTOR-PLAN.md#wp8--trim-the-ladder-prune-the-keywords), 2026-08-27).** A rule is credited with a drop when it is
  the first configured term to match; removing it changes a verdict only if
  nothing further down the ladder also catches that job. On the [WP8](REFACTOR-PLAN.md#wp8--trim-the-ladder-prune-the-keywords) baseline
  only 38 of 112 keywords changed any verdict when removed, and `SEA`, `AI` and
  `architect` — all named as costly — changed none. `Security`/`architect` and
  `architect`/seniority `Architect` mask each other exactly, so removing either
  half alone measures zero and removing both measures the real cost. **Never
  prune from the printed table.** Remove the rule, re-run, diff. This is also
  why [WP8](REFACTOR-PLAN.md#wp8--trim-the-ladder-prune-the-keywords)'s step 3 could not be answered until step 2 had landed.
- **`Architect` was a genuine false positive; `Lead` was not ([WP8](REFACTOR-PLAN.md#wp8--trim-the-ladder-prune-the-keywords),
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
  *does* change is **attribution**, the one thing [WP8](REFACTOR-PLAN.md#wp8--trim-the-ladder-prune-the-keywords) spent a package learning
  to read correctly. It would move the per-layer table and the drop log without
  changing a single verdict, and make future rows non-comparable with the
  ~49,000 already stored. Two orderings that matter are already right: Layer 2
  (detail) is last because it is the only one costing an HTTP request, and
  Layer 1d runs before it so already-rejected jobs never trigger a fetch
  (pinned by `test_logging_costs_no_extra_http_request`). Note also that 1a and
  1 are deliberately fused into one title scan in `apply_combined_title_filter`;
  separating them to reorder would give that up. **The real performance
  conversation is [WP9](REFACTOR-PLAN.md#wp9--playwright-reuse-and-http-caching)** — browser reuse and HTTP caching attack the four
  minutes, not the 79 milliseconds.
- **`rules.json` stayed untouched, so [WP8](REFACTOR-PLAN.md#wp8--trim-the-ladder-prune-the-keywords) landed in two stages — both now done
  (2026-08-27).** The seniority list lives in the gitignored `rules.json`, which
  CLAUDE.md puts on the never-touch list. [WP8](REFACTOR-PLAN.md#wp8--trim-the-ladder-prune-the-keywords) therefore committed the keyword CSV
  and `rules.example.json` only, and left the owner one hand edit: drop
  `"Architect"` from `seniority_exclude_titles`. **The owner made that edit the
  same day, and it is verified: `seniority_exclude_titles` now holds 23 terms and
  no `Architect`, and `python -m job_scraper.eval` scores recall 0.868 against the
  520-row gold set.** So the live configuration is the 0.868 one, not the 0.824
  one. A later session measuring 0.824 is looking at a `rules.json` where the edit
  was lost or reverted — check the list before re-proposing the change.

  Worth keeping as a method note, since it cost a wrong answer in the [WP8h](REFACTOR-PLAN.md#wp8h--renumber-the-ladder)
  session: the plan said "one hand edit outstanding" and stayed saying it after
  the edit was made, so a later session repeated it as still pending. **A plan
  entry describing something the owner must do by hand is stale the moment they
  do it, and nothing updates it automatically.** Read the file — `rules.json` is
  never-touch for *writes*, and always readable for a check like this.

- **Playwright's sync API cannot share a browser between threads, at all**
  ([WP9](REFACTOR-PLAN.md#wp9--playwright-reuse-and-http-caching), verified rather than inferred). Every object is bound to the greenlet of
  the thread that made it, so touching a `Browser` from another thread raises
  "Cannot switch to a different thread" immediately. A context per worker thread
  therefore means a *browser* per worker thread. That is why rendered fetches
  left the Layer 5 detail pool for `http.RenderPool`'s own four threads, each
  owning one browser for the whole run: it is the only shape that bounds the
  browser count without serialising rendering. `chromium.launch_server()` — the
  one way to get a single Chromium behind several connections — does not exist
  on the sync API. Do not re-derive this by trying the shared-browser version.

- **The response cache ignores `no-store` deliberately** ([WP9](REFACTOR-PLAN.md#wp9--playwright-reuse-and-http-caching)). Six of fourteen
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

- **The 30-minute cache TTL is a considered number, not a default** ([WP9](REFACTOR-PLAN.md#wp9--playwright-reuse-and-http-caching), put to
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

- **`stale_if_error` is off, and stays off** ([WP9](REFACTOR-PLAN.md#wp9--playwright-reuse-and-http-caching), the owner's call). Serving the
  previous copy when a site errors keeps a run going, but it reports a successful
  scrape of an old page into `source_health` — priority 2 wants the broken site
  to fail, and a WARNING is not a failure. The flaky-500s case it was proposed
  for belongs to `fetch_text`'s 5xx retry, which is still there and is pinned by
  the same test. It will look like free resilience to a future session; it is
  not, and this is the entry saying so.

- **An unreadable robots.txt allows the crawl** ([WP10](REFACTOR-PLAN.md#wp10--politeness-and-observability)). RFC 9309 says a 5xx
  should be read as a site-wide "do not crawl", and for a search engine that is
  right. Here it is not: impactpool.org intermittently 500s, and a transient
  error that skips a source silently produces exactly the "no vacancies" shape
  priority 2 forbids. So an unreachable or erroring robots.txt is logged as a
  WARNING and the source is scraped; a *readable* one that says no is obeyed,
  because that is the case actually carrying the site owner's intent. Do not
  "harden" this into a fail-closed check without re-reading that argument.

- **The contact details live in `rules.json`, not in `http.py`** ([WP10](REFACTOR-PLAN.md#wp10--politeness-and-observability), put to
  the owner and chosen by them). The repo is public, so a real address in
  tracked code publishes it; `rules.json` is gitignored. `build_user_agent`
  assembles the header from `contact_url` / `contact_email`, and the fallback
  says `no contact configured` rather than naming a domain nobody owns — an
  invented contact is worse than an absent one, since following it teaches an
  administrator nothing. Every run without them logs a WARNING.

- **A cache hit refunds its turn at the host** ([WP10](REFACTOR-PLAN.md#wp10--politeness-and-observability)). The throttle books the
  next slot before the request; a response `requests-cache` answered from disk
  put no load on the site, so it hands the booking back and the next real
  request does not queue behind it. Without the refund a warm-cache run would
  pay the full per-host delay for pages nobody was asked for — politeness
  theatre that costs the owner time and buys the site nothing.

- **`_DETAIL_WORKERS = 10` was never the politeness cap, and still is not**
  ([WP10](REFACTOR-PLAN.md#wp10--politeness-and-observability)). It bounds this tool's threads; the per-host semaphore
  (`DEFAULT_PER_HOST_REQUESTS = 2`, `DEFAULT_HOST_DELAY = 1.0` in `http.py`)
  bounds what any one site sees. Ten workers now means up to ten *different*
  employers in parallel. Lowering `_DETAIL_WORKERS` to be kinder to a site is
  the wrong lever and makes every other source slower.

- **Politeness is run-scoped, like the [WP9](REFACTOR-PLAN.md#wp9--playwright-reuse-and-http-caching) resources** ([WP10](REFACTOR-PLAN.md#wp10--politeness-and-observability)). `polite_fetching`
  installs the User-Agent, the throttle and the robots policy for the length of
  `run_pipeline` only. Outside it — tests, the fixture capture script, a one-off
  `fetch_text` — nothing applies, so no test pays a second per request and none
  of them reaches for robots.txt over the network. The pipeline is the only
  thing in this project that fetches at volume, which is what makes that scope
  the right one.

- **An empty page only ends a walk when something says how long the walk is**
  ([WP11](REFACTOR-PLAN.md#wp11--j-pal-pagination-and-silent-short-walks)). `extractors/pagination.py` holds the policy: an extractor that can
  read a total must raise `ShortWalkError` on an empty page before that point,
  because "the listing ended" and "this page did not parse" look identical
  otherwise and the second is silent data loss. The module deliberately holds
  no shared loop: every listing announces its length differently, and only the
  extractor knows how. **Every paginated source here publishes something** — a
  pager, `totalFound`, `totalJobs`, "1-6 of 74 results", "Vacant positions: 2",
  "Results 1 to 10 of 2010", or a next-page link — so all six are guarded. The
  first pass through [WP11](REFACTOR-PLAN.md#wp11--j-pal-pagination-and-silent-short-walks) concluded four of them had nothing to read; that was
  a failure to look, not a fact about the sites. Where the count and a
  deduplicated walk legitimately disagree (Impactpool, an aggregator with
  postings promoted onto every page) the guard is the next-page link instead.

- **A paginated fixture must hold every page of its walk** ([WP11](REFACTOR-PLAN.md#wp11--j-pal-pagination-and-silent-short-walks)). Replaying one
  saved page and an empty body afterwards fakes the end of the listing, which is
  how J-PAL's golden test passed on 9 of 37 postings. `capture_fixtures.py
  --pages all` records the whole walk and `recorded_pages_fetch` replays it in
  order. J-PAL is the only source captured this way today; re-capture it with
  the flag.

- **The `SEA` keyword stays. Measured, and it changes nothing** ([CU3](REFACTOR-PLAN.md#cu3--final-cleanup-session-3-of-3), from the
  audit's independent check). The eval report credits `SEA` with costing two
  wanted jobs, which reads as an obvious prune and has now attracted two
  separate readers. It is wrong both times, for the reason two entries up:
  that table reports **attribution**, not marginal cost. The audit did the
  measurement properly — removed the keyword, re-ran, diffed — and **no posting
  changes verdict either way**, because something further down the ladder
  catches both. Keeping it costs nothing and removing it buys nothing, so it
  stays where it is rather than being churned. **Do not re-propose removing
  `SEA` without a marginal measurement** — remove it, re-run
  `python -m job_scraper.eval`, diff, and quote the diff. A row in the printed
  table is not that measurement, and a paragraph of reasoning is not either.

- **The retired shell script, its companion tool and the old blocklist file all
  stay** ([CU3](REFACTOR-PLAN.md#cu3--final-cleanup-session-3-of-3), the owner's standing instruction). Three things look like dead
  weight to every fresh reader, and all three are staying until the owner says
  otherwise. Do not propose deleting them again:

  - `scripts/scrape_and_blocklist.sh` — the pre-[WP5b](REFACTOR-PLAN.md#wp5b--replace-the-blocklist-everything-routine) flow, clearly marked
    retired, prints its own warning, destroys nothing.
  - `job_scraper/tools/blocklist_all.py` — its companion, now a slower alias
    for `review --seen-all`.
  - `data/curated/blocklist.csv` — all 265 rows are in the store, so nothing
    needs the file to run. It is kept as the only surviving record of what had
    been reviewed before the migration, and it has already earned that once:
    the [WP8b](REFACTOR-PLAN.md#wp8b--readme-reconciliation) recovery used it as independent corroboration of which postings
    were unreviewed.

  Retiring any of these is a decision for the owner, not a maintenance finding.
  The audit reached the same conclusion independently (§7E) and left them.

- **Two sources are the same source when they are the same *board*, not the
  same host** (SP1). Half the supported ATS platforms are multi-tenant:
  `sources.yaml` today has six employers on `job-boards.greenhouse.io`, three
  on `jobs.ashbyhq.com` and two on `apply.workable.com`. A host match would
  report a brand-new Greenhouse employer as already present — and, in SP3 and
  SP7, as already tombstoned, which is the expensive direction to be wrong in.
  `urlutil.board_identity` is the normalised host (lowercased, `www.` and
  scheme removed via `normalize_http_url`) plus, on a known shared host, the
  first path segment that is not a locale. Single-tenant hosts are their own
  identity, so `careers.oatly.com/en-GB/jobs` and `careers.oatly.com/jobs/123`
  match. **Workday is treated as shared** even though each tenant has its own
  subdomain: a tenant can host another brand's board (`sources.yaml` reaches
  Busuu through Chegg's), so the path segment stays part of the identity. That
  errs towards "a different board", which is the safe error here. Adding a
  platform that puts every customer on one hostname means adding its host to
  `_SHARED_BOARD_HOSTS`; platforms that give each employer a subdomain
  (Teamtailor, Breezy, Personio, Recruitee) need no entry.

- **`data/curated/` is a git repository of its own** (owner's decision,
  2026-09-11, SP0 option 2; wired into the writer by SP1). `git init` inside
  that directory gives the curated files a real undo, and the outer repository
  cannot see it because `data/curated/*` is ignored deny-by-default — nothing
  there can reach the public remote. `tools/sources.py` commits each write to
  it automatically, staging **only** the YAML file it wrote, never the `.bak`
  copies. The commit is best-effort and reported: the file is already written
  by the time git runs, so a git failure warns rather than raises. Two things
  this does *not* replace: the timestamped `.bak` (it also covers an
  uncommitted hand-edit sitting in the working tree), and the mirror outside
  the working tree (`git clone --mirror data/curated ~/Documents/job_scraper_curated.git`),
  which is the only copy that survives the directory being deleted — plain
  `git clean -xfd` refuses to delete a directory holding a `.git`, but `-xff`
  overrides that.

- **A migration writes null, never a guess** (SP1). The old
  `excluded_sources.csv` had no `excluded_on` and the old
  `candidate_sources.xlsx` had no `last_checked`, `category`, `ats` or
  `source_of_record`. `scripts/migrate_curated_to_yaml.py` leaves every one of
  them null rather than stamping the migration date. `last_checked` exists
  precisely so nobody re-checks a source blind; a date invented during a
  migration says "checked" about work nobody did. The old `notes` column maps
  to `blocker` because it is the nearest field — in the owner's file it is
  empty in all twenty rows, so nothing was coerced in practice.

- **`sources check` exits 1 when it finds nothing** (SP1), the way `grep`
  does, so `sources check <url> || echo new` works in a shell. The refusals
  are exit 1 too, but they print to stderr and change no file, so a caller
  that cares can tell them apart by stream.

- **An E402 `noqa` goes only where ruff asks for one** (SP1, owner-approved
  scope extension). [WP2](REFACTOR-PLAN.md#wp2--test-net-and-tooling)'s record says every
  `sys.path`-amending import carries `# noqa: E402`. It no longer matches the
  tree, and it was never needed everywhere: ruff exempts an import that
  follows a `sys.path` call directly. `scripts/capture_fixtures.py` never had
  one, SP1's migration script and its test had four that suppressed nothing
  (removed), and `tests/fixture_cases.py` genuinely needs its three, because
  the `_PROJECT_ROOT` assignment before its `sys.path.insert` ends the
  exemption. Verify with `ruff check --extend-select RUF100 .` rather than by
  reading the code — that is how this was settled, after a first attempt
  removed the needed three on the strength of an argument.

- **A two-file write must be finishable by re-running it** (SP1). `promote`
  writes the tombstone first and removes the candidate second, so a crash in
  between leaves the board on both lists rather than on neither. That order
  was right but was not enough: the retry refused (already tombstoned),
  `exclude` refused (still a candidate) and pointed back at `promote`, and the
  only way out was a hand-edit of a curated file. A retried `promote` now
  completes the move when the tombstone holds that board under the *same*
  organisation, and refuses as a conflict under a different one. The general
  rule for any later command that touches more than one curated file: after a
  crash at any point, running the same command again must succeed or say
  exactly what conflicts. Choosing the safest write order is not enough.

- **A list in its old format is a refusal, not an empty list** (SP1, found in
  review). "Missing file reads as empty" is right for a fresh clone and wrong
  the moment the data exists in another shape: before migration, the real
  tombstone was a CSV the tool never looked at, so `check` cleared a banned
  employer and `candidate add` re-proposed it. `curated.require_migrated`
  refuses while `excluded_sources.csv` or `candidate_sources.xlsx` exists
  without its YAML file. Every CLI command runs it, and so do the two
  `load_*` functions any later package reads through. The same shape applies
  to any future format change: while the old file exists and the new one does
  not, refuse and name the migration.

- **A migration decides about every target before writing any, and re-running
  it must finish it** (SP1, found in review). Refusing file two after writing
  file one stranded a run whose retry then refused file one. Targets identical
  to what the migration would write count as done; any other existing content
  stops the run with nothing written. This is the migration-script case of the
  `promote` rule above. "Refuse to overwrite" is only safe when checked for all
  targets up front and when an identical target counts as finished.

- **A gitignored file deleted after a session read it is often recoverable
  from the transcript archive** (SP2, 2026-09-15). `skipped_sources.csv` was
  untracked, deleted in error in [CU1](REFACTOR-PLAN.md#cu1--final-cleanup-session-1-of-3), and recorded as permanently lost. It was
  not: `~/.claude/projects/` keeps every tool result verbatim, and
  `scripts/recover_skipped_sources.py` rebuilt thirteen rows. Their block is
  byte-for-byte the size an archived `ls` reported for the file, so they are
  the whole file as it stood from 2026-05-27 until its deletion — not proof
  that nothing was removed before then, when the archive barely begins. Check the archive before writing anything off.
  Three things the sweep has to get right, each of which a first attempt
  would miss: every project directory, because worktree sessions are archived
  apart from the main one; subagent transcripts one level down; and **tool
  results only**, because plans and prompts quote the header without holding
  a single row.
- **A transcript's date is when a row was read, not when the site was checked**
  (SP2). The prompt said to date recovered candidates by their session. The
  archive showed that session only read and pruned the file; its rows were
  last written on or before the file's modification time, two months
  earlier, and the dates of individual checks were never recorded. So the
  recovered candidates carry `last_checked: null` and the bound in
  `source_of_record`, via a new `candidate add --undated` — the default of
  today would have claimed a check nobody did, which is the "a migration
  writes null, never a guess" entry above applied to a recovery. Before
  dating anything from an archive, look at what the session actually did to
  the file.
- **`candidate record-check` is the one command that edits an existing entry,
  and it may only fill** (SP2, owner-approved exception). SP1's migration
  could carry no blocker, date, category or ATS for the candidates it moved,
  and `candidate add` refuses a listed candidate, so without it a migrated row
  could never say why it is not a source — the "checked blind" state the
  schema exists to prevent. The exception is kept as narrow as that need:
  a field is written only while empty, one already-set field refuses the whole
  command with the file unchanged, and `source_of_record` is appended to with
  `; `, never replaced, so the migration's provenance survives. There is still
  no general edit, delete or overwrite. The consequence to keep in mind:
  **a value written by `record-check` cannot be corrected by it**, which is
  why a half-remembered blocker should be left empty rather than recorded —
  empty can still be filled after a real check, and a wrong value cannot.
