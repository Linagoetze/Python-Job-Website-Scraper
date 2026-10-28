# Optional plan

**The one live plan** since the sources plan closed on 2026-10-28. A session
reads it at the start, with `CLAUDE.md` and `docs/DECISIONS.md`.

OP1 to OP4 were moved out of `docs/SOURCES-PLAN.md` on 2026-10-26 (owner's
decision, recorded in `docs/DECISIONS.md`). None of them is needed to keep the
scraper's data safe or to add more companies: each has a workaround, or serves a
single employer. They keep their prompts so that a session can pick one up
without re-deriving it. OP5 onwards are the findings of `docs/AUDIT-SP.md`,
parked with the trigger that makes each worth doing. `docs/SOURCES-PLAN.md` is
now an archive; its "Shared rules for the new-extractor packages" still apply
to a reader package here, so grep for them when a prompt names them. This
file's own publishing rule is [below](#publishing-this-file): **no candidate's
name or URL goes in this file**.

## Status

| OP | Title | Time | Model | Effort cue | Status | Branch | Was |
|----|-------|------|-------|-----------|--------|--------|-----|
| 1 | The probe's strategy for Workable, and what its "no postings" means | 1.5 hr (2 with the exemption flag) | Sonnet 5 | `think` | not started | `op1-probe-corrections` | SP14 |
| 2 | `candidate activate` for a board hosted elsewhere | 1 hr | Sonnet 5 | `think` | not started; owner-approved 2026-10-07 | `op2-activate-board` | SP17 |
| 3 | A generic reader for Taleo career sites | 3 hr | Opus 5 | `think hard` | not started | `op3-taleo-reader` | SP16 |
| 4 | A per-source `enabled: false` | — | — | — | proposed, no prompt yet | — | SP12's proposal |
| 5 | The re-filter pass rejects rows `include_keywords` admitted | 1 hr | Sonnet 5 | `think` | parked | `op5-refilter-keywords` | [F1](AUDIT-SP.md#f1-the-end-of-run-re-filter-can-permanently-reject-a-job-the-run-just-kept) |
| 6 | `fjallraven` returns no rows | 1 hr | Sonnet 5 | `think` | parked | `op6-empty-sources` | [F2](AUDIT-SP.md#f2-fjallraven-has-returned-nothing-for-ten-runs-and-nobody-has-looked) |
| 7 | The goldens check row 1 only, and five tests skip on a missing fixture | 1.5 hr | Sonnet 5 | `think` | parked | `op7-every-row-checks` | [F3](AUDIT-SP.md#f3-the-golden-tests-look-at-each-saved-pages-first-posting-only) |
| 8 | SuccessFactors reads its URL from the registry | 2 hr | Opus 5 | `think` | parked | `op8-successfactors-config` | [F7](AUDIT-SP.md#f7-successfactors-sources-keep-their-configuration-in-the-registry) |
| 9 | A dry run can migrate the schema | 0.5 hr | Sonnet 5 | `think` | parked | `op9-dry-run-schema` | [F5](AUDIT-SP.md#f5-a-dry-run-can-still-change-the-stores-schema) |
| 10 | `jobsinlund` defaults the city | 0.25 hr | Sonnet 5 | none | parked | `op10-jobsinlund-city` | [F4](AUDIT-SP.md#f4-jobsinlund-invents-a-city-when-the-api-gives-none) |
| 11 | Candidates that overlap active sources | 0.25 hr | — (owner) | none | parked | — | [F12](AUDIT-SP.md#f12-the-candidates-list-overlaps-the-active-sources) |
| 12 | Dead code and the duplicated constant | 0.5 hr | Sonnet 5 | none | parked | — | [F13](AUDIT-SP.md#f13-dead-code-and-a-duplicated-constant) |
| 13 | Retire the two one-shot scripts and their machinery | 1 hr | Sonnet 5 | `think` | parked | `op13-retire-one-shots` | [Q5](AUDIT-SP.md#questions-for-the-owner) |
| 14 | Drop `data/jobs_sources.csv` | 0.5 hr | Sonnet 5 | none | parked | `op14-drop-jobs-sources` | [Q6](AUDIT-SP.md#questions-for-the-owner) |
| 15 | README split into docs guides; DECISIONS headings and an index | 3 hr | Opus 5 | `think` | parked | `op15-docs-structure` | [section 7](AUDIT-SP.md#7-readme-for-github) and [section 8](AUDIT-SP.md#8-docs-and-repo-hygiene) |
| 16 | Raise `requires-python` to 3.13 | 0.25 hr | Sonnet 5 | none | parked | — | close-out |
| 17 | `source_yield` still says "Layer 0" | 0.25 hr | Sonnet 5 | none | parked | — | [section 7, item 1](AUDIT-SP.md#7-readme-for-github) |
| 18 | A tracked `.example` twin for `labels.csv` | 0.25 hr | Sonnet 5 | none | parked | — | [section 7, item 3](AUDIT-SP.md#7-readme-for-github) |

### Why each is optional, and the workaround meanwhile

- **OP1.** The probe suggests `strategy: static` for a Workable board: set
  `strategy: dynamic` by hand when pasting the block (SP4d). Read a rung 1-2
  "no postings" as "no link of the shape the probe looks for", and look at the
  rendered page before recording a blocker. It edits `probe.py` as SP13 does,
  so whichever lands second rebases over the first.
- **OP2.** A candidate that became a source on a hosted board stays on the
  candidate list: record a `recheck` saying it is an active source, as SP5's
  follow-up did. Best before an SP5 batch that meets the case again.
- **OP3.** It serves one candidate employer, and its step 2 may end it if
  Impactpool already delivers the board.
- **OP4.** Remove a dormant board from `sources.yaml` and park it with
  `sources candidate add` and its blocker. Decide SP12's type-B sources first.

---

## OP1 — The probe's strategy for Workable, and what its "no postings" means

Found in SP5. Three corrections to `probe.py`, small, all about a verdict that reads more
certain than what the probe saw or could do.

1. **Workable's suggested strategy is wrong.** The probe's Platform entry for
   Workable says `static`, so a pasted block gives a source whose posting pages
   are a JS shell to Layer 5 (SP4d: it needs `strategy: dynamic`). The probe
   also uses that strategy to run the reader, so the entry cannot simply be
   edited: what is *suggested* and what is *run* have to be told apart.
2. **"The HTML shows no postings" means "no posting-shaped link".** On two old
   candidates the rendered page held its postings (a stated "Total: 4", and
   "1 - 25 of 69 Job Openings") as cards that are not links, and the verdict
   said rungs 1-2 showed no postings. The recorded blocker then said "no job
   content", which was false, and a second session would have renewed it. The
   probe should say what it saw: that the page states a count, or repeats a
   card structure, and that it found no link of the shape it looks for.

Sonnet 5, `think`, 1.5 hr (2 with the exemption flag). It touches only `probe.py` and its tests, and the
verdict text is a contract the tests pin, so every changed string is a
deliberate edit.

```
think

Read CLAUDE.md, docs/DECISIONS.md and docs/OPTIONAL-PLAN.md.
docs/SOURCES-PLAN.md is an archive: grep it for the SP sections named
below rather than reading it end to end. Work on OP1
only. SP3 must be merged, and SP8 if it has landed (it adds a check to the same
report: rebase over it rather than overwrite it).

1. WORKABLE. Give the Platform entry a way to say "suggest `dynamic`, run
   `static`" or the reverse, whichever the code needs: read how `probe.py`
   passes `strategy` to the reader and prints it in the sources.yaml block
   before choosing. The printed block for a Workable board must say
   `strategy: dynamic`, with one sentence of why (SP4d). Check the other nine
   platforms' suggested strategies against the listing page's behaviour, and
   report any that are wrong; do not change one without a measurement. Write
   the test against the old probe first and watch it fail.

2. THE VERDICT'S WORDS. In the rung 1-2 failure, report what the probe saw in
   the page text: a stated total or range ("N openings", "1 - 25 of N"), a
   repeated card structure, a pager; and say "no link of the shape the probe
   looks for" in place of "shows no postings" when any of those is present.
   When the page really holds nothing (a shell, a 29-character mount point),
   keep today's wording. The verdict category and exit code do not change: it
   is still `not feasible`, and rungs 3-4 are still the owner's. Add tests
   with small handwritten pages under tests/fixtures/probe/ (the README there
   says they are invented): one with a stated total and card divs, one that is a
   bare shell.

3. AN EXEMPTION THE OWNER HAS GIVEN. The probe passes no `ignore_robots` on
   purpose ("a probe asks may we?", docs/DECISIONS.md), so a SmartRecruiters
   board is always `not feasible, rung 2` and a session has had to capture
   instead of re-probing. ASK THE OWNER FIRST whether the probe should take an
   explicit `--ignore-robots HOST`, per invocation, never from sources.yaml and
   never as a default, so that the owner's yes can be run through the probe and
   the probe's report then says the exemption was applied. If the owner says
   no, leave it and say so in the result.

4. DOCS. README's "Adding a source" if it quotes either text, and the test
   count. docs/DECISIONS.md: that the probe reports what it saw, and that "no
   posting-shaped link" is not "no postings".

Branch op1-probe-corrections. Commit, do not push. Update this plan file.
```

## OP2 — `candidate activate` for a board hosted elsewhere

Found in SP5 (company 10). A candidate whose recorded URL is the employer's own
careers page became a source on a hosted board (a SmartRecruiters board found
from one posting's "Apply" link). `candidate activate` removes a candidate only
when its **recorded board** is in `sources.yaml`, and the two boards differ, so
the command refuses and the candidate stays on the list. SP5's follow-up
recorded a `recheck` saying so ("now an active source"), which is honest but
leaves an entry on a list whose meaning is "not a source yet". It will happen
again: the probe's own advice for a page that embeds a hosted board is to
probe the hosted board, and a candidate is often recorded under the employer's
own page.

Owner-approved on 2026-10-07 as the second exception's natural extension: it
removes an entry on evidence, and it needs two pieces of evidence where
`activate` today needs one. Sonnet 5, `think`, 1 hr.

```
think

Read CLAUDE.md, docs/DECISIONS.md and docs/OPTIONAL-PLAN.md.
docs/SOURCES-PLAN.md is an archive: grep it for the SP sections named
below rather than reading it end to end. Work on OP2
only. SP2b must be merged. Read SP2b's prompt and result, and the two
DECISIONS.md entries on `recheck` and `activate` ("the second exception, and
each needs its own evidence"), before changing anything: this is that
exception, one step wider, and the reasoning for how narrow it is has to
survive.

Add an optional `--board URL` to `sources candidate activate`, with the logic
in curated.py beside `activate`, not in the CLI.

  sources candidate activate <org> [--board <url>]

Without `--board`, nothing changes: the candidate's own recorded board must be
in sources.yaml, and every existing test passes unchanged.

With `--board`, the candidate is removed ONLY when ALL of these hold, and the
command refuses, changing nothing, naming the one that failed, when any does
not:
  1. sources.yaml exists and was read (a missing file is a refusal, never "not
     active": the existing rule).
  2. The `--board` URL's board identity (curated.find_board, never host) is in
     sources.yaml. A different slug on the same host is not a match (two
     Greenhouse employers; two SmartRecruiters ones).
  3. That sources.yaml entry's `company` is set and equals the candidate's
     organisation, case-insensitively and trimmed. An aggregator entry has no
     `company`, and a name that differs is a different employer: refuse in both
     cases. The owner's assertion that the two are the same company is not
     enough on its own, because it is the one thing the command cannot check
     and a typo would remove the wrong entry.
  4. The `--board` board is not on the tombstone, and is not the recorded board
     of a DIFFERENT candidate (that would be a conflict for the owner, as in
     `recheck`).
Print the whole entry being removed before writing, as `activate` does, and
add to the commit message and the terminal output which board it was matched
to. No `--force`, and no removal by name alone. Everything else follows the
writer: argparse front door and --help exits without acting, timestamped .bak
(a counter, never overwritten: see "A backup is never replaced by another
backup"), temp file plus os.replace(), commit to the curated repository, and
the unmigrated-list refusal.

Build nothing broader: not a way to change a candidate's URL, not an `--org`
rename, no bulk activation.

Tests, in tmp_path as in SP2b. Removed: a candidate recorded under its own
page with `--board` naming a hosted board in sources.yaml whose `company`
matches, and no other entry changes. Refused with the file byte-identical: a
board not in sources.yaml; a missing sources.yaml; the same host with a
different slug; a sources.yaml entry with no `company`; a `company` that
differs; a tombstoned board; a board that is another candidate's. No
`--board` and a board that does not match still refuses as before. Both:
--help writes nothing, the backup is the file as it was, an interrupted write
leaves the original intact. Run one removal as a real process.

THEN, WITH THE OWNER'S YES IN CHAT, USE IT ONCE: the candidate recorded in SP5
as company 10 is the first case. Show the command first; do not run it unasked.

DOCS. README "Maintenance commands": `--board`, and that it needs the source's
`company` to equal the candidate's name. CLAUDE.md's sentence naming the
curated-list exceptions: say that `activate` also removes a candidate named by
board, on the two pieces of evidence. docs/DECISIONS.md: the extension and
why `company` is required.

Branch op2-activate-board. Commit, do not push. Update this plan file.
```

### Your to-dos (OP2)

- [x] This widens an exception to CLAUDE.md's rule about `data/curated/`, so it
      is yours to grant: say yes or no to the `company`-must-match evidence
      before the session starts. **Granted by the owner on 2026-10-07.**

## OP3 — A generic reader for Taleo career sites

Found in SP5 (company 7, an existing candidate whose recorded blocker, login
required and "No jobs", does not hold: the rendered page, not signed in, states
"1 - 25 of 69 Job Openings"). The probe found no posting-shaped link and
fingerprints no Taleo. Taleo is a long-lived Oracle platform with many
employers, so this is a **generic ATS reader**. Taleo's classic career section is
a search form whose results are filled and paged by a script, with a session
and anti-forgery value the page issues, which is why the route is the hard part
and why this one is **Opus 5, `think hard`, 3 hr**, with the same stop-and-ask
as SP11.

```
think hard

Read CLAUDE.md, docs/DECISIONS.md and docs/OPTIONAL-PLAN.md.
docs/SOURCES-PLAN.md is an archive: grep it for the SP sections named
below rather than reading it end to end. Work on OP3
only, following "Shared rules for the new-extractor packages", and read SP3b's
result and docs/DECISIONS.md's Workday entries and SP11's prompt first: this is
the same kind of choice. SP3 and SP5's batch 1 must be merged, and OP1 if it
has landed. The owner gives you the board's URL in chat.

1. check, then probe. A changed verdict ends the package with a report.
2. IS IT ALREADY IN THE STORE? Before building anything, measure how many of
   the board's postings the aggregators already deliver: Impactpool lists UN-
   system and multilateral roles, and its rows carry the employer. Count the
   stored rows whose company is this employer, by source and status, and
   compare with the board's stated 69. If most of what Layer 0 would admit is
   already stored from Impactpool, the new source adds duplicates under a
   different dedupe key (a different URL), and that is a decision for the
   owner before it is a reader. Report it and wait.
3. STOP AND ASK BEFORE BUILDING, as SP11 does: what the page requests to fill
   and page its list, its method and body, what it needs (a session cookie, a
   token the page issues, a form value), how many postings a response holds,
   whether it states a total, and what the pager does. Put the options to the
   owner with their fragility and cost per run. A pager the fetcher cannot
   press and a token the page issues are rung 3-4 of the CU2 ladder: the
   owner's call, and no token goes into a fixture.
4. Build the chosen route as a generic reader, `job_scraper/extractors/
   taleo.py`, with the board as arguments. It walks the whole listing, checks
   the stated total, raises on a short read, uses the fetcher it is given
   (including `post_json` if it POSTs), supplies `description_text` if the
   response has it, reads a workplace field into `raw_snippet`, and builds the
   detail URL exactly as the board links it (it is a dedupe key: SP3b).
5. Capture the whole walk (`--pages all`), pin it, add the `Platform` entry to
   `probe.py`, then SP5's checks on this source. Say in the README's coverage
   sentence that the reader is covered by one employer's capture.

Branch op3-taleo-reader. Commit, do not push. Update this plan file.
```

### Your to-dos (OP3)

- [ ] OP3 stops for an owner decision about a private route, and may end at its
      step 2 if Impactpool already delivers the board.

## OP4 — A per-source `enabled: false`

Proposed in SP12's result (expectation 3, "Where a dormant board goes"). Today
a dormant board leaves the run by being removed from `sources.yaml` and, if
wanted, parked with `sources candidate add` and its blocker. A per-source
`enabled: false` key would keep the entry in place and skip it: a config change,
and its own package. No prompt is written yet; write one if the owner wants it,
after deciding SP12's type-B sources.

---

## OP5 — The re-filter pass rejects rows `include_keywords` admitted

- **Trigger:** before `include_keywords` is ever set.
- **Reference:** [AUDIT-SP](AUDIT-SP.md) [F1](AUDIT-SP.md#f1-the-end-of-run-re-filter-can-permanently-reject-a-job-the-run-just-kept).
- **Size and model:** 1 hr, Sonnet 5, `think`.

The store does not keep the department and snippet a run matched on, so the end-of-run pass and `retrofilter` mark such a row `rejected`, permanently. The owner chooses the direction first: skip the checks the pass cannot reproduce, or refuse to run while `include_keywords` is set. The README warns beside the key until this is done. The prompt is written when the trigger fires.

## OP6 — `fjallraven` returns no rows

- **Trigger:** the owner finds postings on the live board.
- **Reference:** [AUDIT-SP](AUDIT-SP.md) [F2](AUDIT-SP.md#f2-fjallraven-has-returned-nothing-for-ten-runs-and-nobody-has-looked).
- **Size and model:** 1 hr, Sonnet 5, `think`.

Empty in every run since 30 September. With the owner's approval, capture the page once and compare it with the saved fixture, then record the answer in `docs/DECISIONS.md` so the Empty sources block can be read as known. `giving_what_we_can`, empty since 27 October, goes in the same session. The prompt is written when the trigger fires.

## OP7 — The goldens check row 1 only, and five tests skip on a missing fixture

- **Trigger:** before the next new reader, or when a source looks thin.
- **Reference:** [AUDIT-SP](AUDIT-SP.md) [F3](AUDIT-SP.md#f3-the-golden-tests-look-at-each-saved-pages-first-posting-only).
- **Size and model:** 1.5 hr, Sonnet 5, `think`.

Add every-row checks to the parse test, or pin a digest of every row beside the first, so a reader that breaks rows two onwards fails. Make the five skip-on-missing tests fail like the golden test. The prompt is written when the trigger fires.

## OP8 — SuccessFactors reads its URL from the registry

- **Trigger:** before adding or editing a SuccessFactors board.
- **Reference:** [AUDIT-SP](AUDIT-SP.md) [F7](AUDIT-SP.md#f7-successfactors-sources-keep-their-configuration-in-the-registry).
- **Size and model:** 2 hr, Opus 5, `think`.

The `url` in `sources.yaml` is read for its country facet only, so editing anything else in it changes nothing, silently. The owner decides: derive the search URL from `sources.yaml`, or record that the registry owns it and add a loud check when the two disagree. A stored key must not move. The prompt is written when the trigger fires.

## OP9 — A dry run can migrate the schema

- **Trigger:** before the next schema change.
- **Reference:** [AUDIT-SP](AUDIT-SP.md) [F5](AUDIT-SP.md#f5-a-dry-run-can-still-change-the-stores-schema).
- **Size and model:** 0.5 hr, Sonnet 5, `think`.

Opening the store adds missing columns and tables outside the transaction a dry run rolls back. In a dry run, refuse a store that needs a migration, or a path that does not exist. The prompt is written when the trigger fires.

## OP10 — `jobsinlund` defaults the city

- **Trigger:** if that API starts returning postings without a city.
- **Reference:** [AUDIT-SP](AUDIT-SP.md) [F4](AUDIT-SP.md#f4-jobsinlund-invents-a-city-when-the-api-gives-none).
- **Size and model:** 0.25 hr, Sonnet 5.

Leave the location empty instead of writing the city the query asked for; Layer 5 settles it from the supplied description. The prompt is written when the trigger fires.

## OP11 — Candidates that overlap active sources

- **Trigger:** the next candidate-list session.
- **Reference:** [AUDIT-SP](AUDIT-SP.md) [F12](AUDIT-SP.md#f12-the-candidates-list-overlaps-the-active-sources).
- **Size and model:** 0.25 hr, — (owner).

Six candidates name an employer that is already a source. Run `candidate activate` on the one whose board is the active source's, and confirm the other five are other boards kept on purpose. The prompt is written when the trigger fires.

## OP12 — Dead code and the duplicated constant

- **Trigger:** as a passenger on any session in those files.
- **Reference:** [AUDIT-SP](AUDIT-SP.md) [F13](AUDIT-SP.md#f13-dead-code-and-a-duplicated-constant).
- **Size and model:** 0.5 hr, Sonnet 5.

Delete the two superseded title filters and `same_board` with their tests, use `curated._REQUIRED_EXCLUDED` where `tools/sources.py` spells it out, and drop the `langdetect` comment in `scrape_and_blocklist.sh`. The prompt is written when the trigger fires.

## OP13 — Retire the two one-shot scripts and their machinery

- **Trigger:** any time.
- **Reference:** [AUDIT-SP](AUDIT-SP.md) [Q5](AUDIT-SP.md#questions-for-the-owner).
- **Size and model:** 1 hr, Sonnet 5, `think`.

`recover_skipped_sources.py` and `migrate_curated_to_yaml.py` have done their work. Retiring the second takes `tests/fixtures/curated/` and `curated.py`'s unmigrated-file refusal with it. The owner says yes first; the migration script is theirs. The prompt is written when the trigger fires.

## OP14 — Drop `data/jobs_sources.csv`

- **Trigger:** any time.
- **Reference:** [AUDIT-SP](AUDIT-SP.md) [Q6](AUDIT-SP.md#questions-for-the-owner).
- **Size and model:** 0.5 hr, Sonnet 5.

Every real run writes it and nothing reads it; the Failed sources block does its old job. Stop writing it and take it out of the README and `docs/DECISIONS.md`'s report-writes entry. The existing file is the owner's to delete. The prompt is written when the trigger fires.

## OP15 — README split into docs guides; DECISIONS headings and an index

- **Trigger:** if the repository gets outside attention.
- **Reference:** [AUDIT-SP](AUDIT-SP.md) [section 7](AUDIT-SP.md#7-readme-for-github) and [section 8](AUDIT-SP.md#8-docs-and-repo-hygiene).
- **Size and model:** 3 hr, Opus 5, `think`.

Move the run-summary, sources and operations reference sections into guides under `docs/`, leaving a short summary and a link in the README; group `docs/DECISIONS.md`'s entries under topic headings, verbatim, with an index at the top. Bring along the sources plan's [Documentation each package must update](SOURCES-PLAN.md#documentation-each-package-must-update) table: it is live policy left in an archive. Re-key its surfaces to the new layout and give it a live home, `CLAUDE.md` or `docs/DECISIONS.md`, as the audit proposed; until then `CLAUDE.md`'s Definition of done covers most of it. The owner approves the shape first. The prompt is written when the trigger fires.

## OP16 — Raise `requires-python` to 3.13

- **Trigger:** any time, or as a passenger on a session in those files.
- **Reference:** close-out package (not an AUDIT-SP finding; follows its Q2).
- **Size and model:** 0.25 hr, Sonnet 5.

Python 3.13 is the supported version, but `pyproject.toml` still says `>=3.10`: raising it makes ruff's UP017 ask for `datetime.UTC` in `curated.py`, `extractors/jobsinlund.py`, `storage/db.py` and `scripts/refresh_label_locations.py`. Apply the four rewrites and the new floor together. The prompt is written when the trigger fires.

## OP17 — `source_yield` still says "Layer 0"

- **Trigger:** as a passenger on any session in `tools/source_yield.py`.
- **Reference:** [AUDIT-SP](AUDIT-SP.md) [section 7, item 1](AUDIT-SP.md#7-readme-for-github).
- **Size and model:** 0.25 hr, Sonnet 5.

The README now calls the location layer Layer 1, as the run summary does; the command's help, comments and output still say Layer 0. Change the words only; the classes and their tests do not move. The prompt is written when the trigger fires.

## OP18 — A tracked `.example` twin for `labels.csv`

- **Trigger:** any time.
- **Reference:** [AUDIT-SP](AUDIT-SP.md) [section 7, item 3](AUDIT-SP.md#7-readme-for-github).
- **Size and model:** 0.25 hr, Sonnet 5.

`CLAUDE.md` asks every private file for a tracked twin, and `labels.csv` has none (`tests/fixtures/eval_labels.csv` has fewer columns). The ignore rules now let a new `data/curated/*.example.*` through. Invented rows only, and the README's Layout row changes with it. The prompt is written when the trigger fires.

## Owner decisions pending

Not packages: questions only the owner can answer, carried over from the
sources plan and its audit.

- **The gold set** ([AUDIT-SP Q7](AUDIT-SP.md#questions-for-the-owner)): is
  `labels.csv` current enough to measure against? It was last written on 2
  October, and the sources added since have no labels.
- **SP12's type-B sources and SP14's three rows**
  ([AUDIT-SP Q8](AUDIT-SP.md#questions-for-the-owner)): keep, remove or park
  each type-B source (OP4 waits on this), and revive or leave the three
  `rejected` rows SP14's reading now keeps.
- **Time Machine** (SP0's to-do): whether to back up the machine as a whole.
  Unrelated to the scraper.

---

## Publishing this file

**Yes, publish it — with the curated lists kept out of it.** This is the rule
`docs/SOURCES-PLAN.md` set for itself, carried over now that that file is an
archive; its section of the same name has the full reasoning.

- **Employers followed are already public**, through the extractor registry
  and the fixture filenames, so a package here may name an active source.
- **Employers rejected are not.** A board on the tombstone, with the reason it
  could not be scraped, is a catalogue of other people's anti-bot postures:
  never name one here, and never quote a row of `excluded_sources.yaml`.
- **Candidates are not.** A candidate is a statement of intent about a named
  company: refer to one by its number in the session that found it ("SP5's
  company 10"), and give its name and URL in chat, never in this file.
- **Results name extractors and counts, not employers**, wherever an employer
  was rejected.
- **The exception that does not widen** (owner's decision, AUDIT-SP Q4,
  2026-10-28): two tombstoned boards are named in older tracked docs, because
  they were named while they were sources. Those mentions stay as written. It
  does not license naming any board that reaches the tombstone or the
  candidates list from now on.
