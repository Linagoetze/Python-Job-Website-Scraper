# Sources plan

**In progress: SP0, SP0b, SP1, SP2, SP2b and SP3 are done** (as of 2026-09-17); the
Status table below is the live record, so check it rather than this sentence.
This file plans the next body of work after the refactor: getting the source
list — the employers this scraper watches, the ones it has ruled out, and the
ones still to check — onto a footing where adding a company is a routine rather
than a research project.

It is deliberately shaped like `docs/REFACTOR-PLAN.md`, because that shape
worked: one package per session, never two; a prompt written before the work; a
result section written after it; and a decisions log for anything a later
session would otherwise re-derive. The refactor plan's own verdict was that the
decisions log is the most valuable thing in the repository. This file starts one
of its own, already seeded with what was decided on 2026-09-10.

Lives at `docs/SOURCES-PLAN.md`. Read it alongside `CLAUDE.md`.

## The big picture

Four problems, and they are entangled — which is why this is a plan and not a
task list.

1. **The curated files are spreadsheet-shaped, and a spreadsheet application
   keeps eating them.** `data/curated/excluded_sources.csv` is seven rows of
   prose reasons with no arithmetic anywhere in it. Opening it by hand means
   opening Numbers, and Numbers has already converted it to a proprietary binary
   once (CU2) and left the surviving CSV semicolon-delimited — the fingerprint
   of a European-locale spreadsheet export. `candidate_sources.xlsx` is worse:
   it is a binary to begin with. Neither file is git-backed, by design, so
   neither has an undo.
2. **The middle tier was lost, and its absence is now shaping decisions.**
   `data/skipped_sources.csv` — employers checked, found not feasible *at the
   time*, and annotated with the blocker — was deleted in error during CU1. It
   was never tracked by git, so the refactor plan recorded its content as
   permanently lost. That is wrong, and this plan corrects it: **the file's rows
   survive verbatim in the local session transcript archive** (see SP2). Until
   they are back, the owner is carrying a list in their head, and every source
   audit re-derives the same blockers from scratch.
3. **Thirteen page readers have no saved page at all.** Named in the refactor
   plan's Future work: `asana`, `breezy`, `coefficient`, `jobsinlund`, `lever`,
   `mammut`, `norrsken`, `oatly`, `personio`, `sida`, `smartrecruiters`, `undp`,
   `workable`. No fixture means no golden test and no parse check, so if one
   breaks tomorrow the suite still passes. Five of the thirteen are *generic ATS
   readers*, and a bug in one of those is inherited by every employer ever added
   on that platform.
4. **There is no way to assess a candidate before committing to it.**
   `scripts/capture_fixtures.py` takes source names from `sources.yaml`, so
   answering "can we even scrape this site?" currently requires adding a
   registry line and a config entry to a company that may turn out to be
   unscrapeable. The feasibility ladder exists — CU2 worked it properly for
   `probably_good`, four rungs deep — but it lives in prose in a plan file
   rather than in a command.

**The entanglement:** problem 4 blocks adding companies; problem 3 means that
adding them on Breezy, Lever, Personio, SmartRecruiters or Workable would build
on an unverified reader; problem 2 means a company might be re-checked that was
already ruled out; and problem 1 means every one of those answers has to be
written into a file that a spreadsheet application is waiting to convert. Hence
the ordering below.

## Guiding decisions

- **Fix the format, not the editing volume.** "Edit these files less" leaves the
  landmine armed. Both curated lists move to YAML, which the owner already
  hand-edits comfortably in `sources.yaml` and which no spreadsheet application
  claims.
- **Two states, not three.** *Ruled out permanently* (the tombstone) and *not
  done yet* (candidates). The old `skipped_sources.csv` was a third tier whose
  "possibly feasible" annotations caused four organisations to be rediscovered
  and re-proposed repeatedly — which is why the tombstone had to be invented in
  the first place. The recovered rows go to **candidates**, per the owner's
  decision of 2026-09-10, carrying their prior blocker so the investigation is
  not repeated blind.
- **Recover before reconstructing.** A transcript archive is evidence; a memory
  is a reconstruction. SP2 goes to the archive first.
- **The probe reports; the owner decides.** Rungs 1 and 2 of the CU2 ladder
  (static HTML, rendered HTML) can be automated. Rungs 3 and 4 (a private API, a
  third-party index behind it) are judgement calls about fragility and about
  what a fixture would end up containing, and they stay judgement calls.
- **The probe prints; it does not edit.** A registry entry is one line. Code
  that generates one line of code has to be maintained forever to save a paste.
  Config over code cuts both ways.
- **No new filter layer.** Nothing here touches the five-layer ladder. The
  tombstone guard in SP7 is a source-level startup warning, not a sixth pass.

## Status

| SP | Title | Time | Model | Effort cue | Status | Branch |
|----|-------|------|-------|-----------|--------|--------|
| 0 | Back up `data/curated/` before anything writes to it | 0.5 hr | — (owner) | none | done | — |
| 0b | Split the refactor plan, retire the startup read | 0.5 hr | Sonnet 5 | none | done | `sp0b-split-plan` |
| 1 | Curated lists to YAML, and a writer CLI | 2.5 hr | Opus 5 | `think hard` | done | `sp1-curated-yaml` |
| 2 | Recover `skipped_sources` from the transcript archive | 3 hr | Opus 5 | `think` | done | `sp2-recover-skipped` |
| 2b | Candidate re-checks and activation | 1.5 hr | Opus 5 | `think` | done | `sp2b-candidate-lifecycle` |
| 3 | `sources probe` — the feasibility ladder as a command | 2.5 hr | Opus 5 | `think hard` | done | `sp3-source-probe` |
| 3b | Workday reads the whole board | 3 hr | Opus 5 | `think hard` | not started | `sp3b-workday-walk` |
| 4 | Fixtures for the five generic ATS readers | 3 hr | Sonnet 5 | `think` | not started | `sp4-fixtures-ats` |
| 5 | Add the new companies | 1.5 hr per batch | Sonnet 5 | `think` | not started | `sp5-add-sources` |
| 6 | Fixtures for the remaining eight readers | 2 hr per instalment | Sonnet 5 | `think` | not started | `sp6-fixtures-rest` |
| 7 | Tombstone guard at startup (optional) | 1 hr | Sonnet 5 | none | not started | `sp7-tombstone-guard` |

**Roughly 20 hours** for SP0–SP4 (SP2b and SP3b included) and SP7, plus SP5 and SP6 as recurring
instalments. Take the estimates the way the refactor's were taken: the refactor
estimated 30 hours and the packages that overran were the ones where a capture
revealed a bug. SP4 is that package here.

**Ordering.** SP0 first and non-negotiable — nothing writes to an unbacked
directory. SP0b next, because it makes every session after it cheaper, this
plan's own seven included. Then SP1, which unblocks everything that stores an
answer. SP2 is
cheap and independent after SP1. **SP2b before SP5**: SP5 re-checks candidates
and turns some of them into sources, and until SP2b lands neither answer can be
recorded. SP3 before SP5. **SP3b as soon as SP3 is merged**: it fixes data
loss in six live sources, and it must land before SP5 adds any Workday
employer. It is independent of SP4. **SP4 before SP5** if any new
company runs on Breezy, Lever, Personio, SmartRecruiters or Workable; if none
do, SP4 and SP5 are independent. SP6 is ongoing maintenance with no deadline and
SP7 is optional throughout.

### Model recommendations

`Opus 5` for SP1, SP2, SP2b, SP3 and SP3b: each is a design decision with a data-loss edge
(a schema people will live with, a one-shot recovery from an archive, a
judgement ladder that has to know when to stop, and — for SP3b — a choice
between two fragile routes where a changed detail URL would silently
rewrite review history). SP3b looks like SP4's capture-and-fix work, but the
route choice and the dedupe-key edge are why it is not a Sonnet package. `Sonnet 5` for SP0b, SP4, SP5, SP6
and SP7: capture, diagnose, fix, pin — mechanical work with a strong test net
under it, which is exactly the split the refactor settled on across its
twenty-six packages. Effort cues are the repo's usual `think` / `think hard`.

---

## Documentation each package must update

Added on 2026-09-10 after an audit of the prompts below found that only SP3
mentioned the README and **nothing mentioned the run summary at all**. The
refactor needed a whole package (WP8b) to reconcile a README that had drifted
from the CLI; that is the cost of leaving this to the end.

The surfaces, and what invalidates each:

| Surface | Invalidated by | Notes |
| --- | --- | --- |
| `README.md` — "Adding a source" | SP3 | Currently three steps that omit the tombstone check and the fixture capture. SP3 rewrites it as the real routine, not an appended command. |
| `README.md` — "Maintenance commands" | SP1, SP2, SP2b | The new CLI belongs beside `retrofilter` and `blocklist_all`, including the `--help`-exits-cleanly behaviour that section already warns about. |
| `README.md` — "Reading the run summary" | SP7 | A startup warning is user-visible output. The section prints a real rendered summary; if the `Sources` line or a preamble changes, the block changes with it. |
| `README.md` — test count, fixture count, "thirteen of the twenty-six extractors are uncovered" | SP1, SP2, SP2b, SP3, SP3b, SP4, SP5, SP6, SP7 | Every package that adds a test or pins a fixture moves these. The coverage sentence moves on **every SP4 and SP6 instalment** — that is the number the whole exercise is about. |
| `README.md` — "How this is built and maintained" table | SP0b | Says "Three documents, all public" and lists them. There are now five: `docs/SOURCES-PLAN.md` and `docs/DECISIONS.md` join it. The existing `docs/REFACTOR-PLAN.md` row is stale twice over — it was written while the file was still a working plan, and it credits it with holding the decisions log, which SP0b moves out. Rewrite it as an archive. |
| `README.md` — the `#future-work` anchor link | SP0b | Line 716 links into a section SP0b shrinks to a pointer. A dangling anchor in a public README. |
| `README.md` — Layout table | SP1 | The `data/curated/` row lists what lives there. |
| `job_scraper/config/sources.example.yaml` | SP3 | Its header explains how to add a source. That advice becomes "run `probe` first". |
| `CLAUDE.md` | SP0b, SP2b | Startup reads, the stale architecture block, the Definition of done. |
| `docs/DECISIONS.md` | all | Once SP0b creates it, every package appends anything a later session would otherwise re-derive. |
| This file | all | Status table and result section. |

**The systemic fix, which SP0b applies:** add a line to `CLAUDE.md`'s Definition
of done — *"README and the run summary reflect any user-visible change; anything
a later session would re-derive is in `docs/DECISIONS.md`."* A checklist item
outlives seven prompts.

## SP0 — Back up `data/curated/` before anything writes to it

**This one is yours, not a session's.** `data/curated/` holds files that are
gitignored by design, not regenerable, and have no undo. Two have already been
lost. Every package below writes there.

You said you have no Time Machine. Three options, in ascending order of effort:

1. **A second local copy, now.** Thirty seconds, covers today:
   `cp -R data/curated ~/Documents/job_scraper_curated_backup_2026-09-10`.
   Note the destination is **outside the repository**, and that is the point —
   see the hazard below. Fine as a stopgap, useless as a habit, because you will
   forget.
2. **A private git repository *inside* `data/curated/`** (recommended).
   `git init` in that directory gives every file a full history and a real undo,
   entirely locally. The outer repository cannot see it: `data/curated/*` is
   already ignored deny-by-default, so the nested `.git` is ignored with
   everything else and **nothing there can ever reach the public remote**. It
   costs one `git -C data/curated commit -am "..."` after a change, which SP1's
   tool can do for you. **Mirror it outside the working tree as well**
   (`git clone --mirror data/curated ~/Documents/job_scraper_curated.git`,
   re-pulled occasionally): a backup that lives inside the directory it is
   backing up survives an accidental edit but not an accidental removal of the
   directory.
3. **Time Machine.** Worth having for the machine as a whole, but it needs an
   external disk or a network volume, and it is a slow, coarse instrument for
   seven small text files. It does not slot into any package; buy the disk on
   its own schedule. Do not let it block SP1.

**Recommendation: option 2, plus option 1 today.** Option 2 gives per-change
undo, which is the failure mode this project actually has; the two files it lost
were lost by a person, one edit at a time, not by a disk failure.

### The hazard that makes SP0 non-negotiable

**`git clean -xfd` in this repository deletes every curated file and the entire
job store.** Verified by dry run on 2026-09-10: it would remove
`data/curated/blocklist.csv`, `candidate_sources.xlsx`, `excluded_sources.csv`,
`labels.csv`, `data/jobs.sqlite3` and the HTTP cache. That is not a bug in the
ignore rules — `-x` means "ignored files too", and everything irreplaceable here
is ignored **by design**, which is exactly what makes the command dangerous in
this repository specifically. One tidy-up command, and the review history of
nineteen runs is gone with no git undo behind it.

Two things follow. `git clean -xfd` belongs in CLAUDE.md's never-run list, and
SP0b should put it there. And a nested repository (option 2) is only partial
cover: plain `git clean -xfd` refuses to delete a directory containing a `.git`,
but `-xff` overrides that. The mirror outside the tree is the part that actually
saves you.

### Your to-dos

- [x] Run the option-1 copy now, before any package starts.
- [x] **Decided 2026-09-11: option 2, a private git repository inside
      `data/curated/`.** SP1 wired the commit into the writer — every write
      stages just the YAML file it wrote and commits it, and says so. Still
      yours to do: `git init` in `data/curated/`, an initial commit, and the
      mirror outside the working tree
      (`git clone --mirror data/curated ~/Documents/job_scraper_curated.git`).
      Until that `git init` happens the tool prints that there is no undo
      beyond the `.bak`, and carries on.
- [ ] Decide separately whether you want Time Machine for the machine. Unrelated
      to this plan.

---

## SP0b — Split the refactor plan, retire the startup read

`CLAUDE.md` tells every session to read `docs/REFACTOR-PLAN.md` before doing
anything. That file is **6,089 lines and 53,421 words — roughly 70k tokens** —
and 87% of it is the archived prompts and results of twenty-six finished
packages. `CLAUDE.md` itself is 116 lines. The instruction was correct when it
was written, because the file was then the queue of work; the refactor closed on
2026-09-02 and the instruction did not move with it. The file's own header
already says it is no longer a queue.

What would genuinely be lost by not reading it is the **decisions log**, and
only that — the part the audit called the most valuable thing in the repository,
and the part that stops a session re-deriving a settled question. This session
alone it supplied the `probably_good` ladder, the "missing fixture must fail,
do not re-propose" note, and the place-names policy. So the answer is not to
stop reading it: it is that roughly 800 live lines are trapped inside a
6,000-line archive.

```
Read CLAUDE.md and docs/SOURCES-PLAN.md, then work on SP0b only.

Documentation only. No code, no config, no tests change.

1. Extract the decisions log (docs/REFACTOR-PLAN.md lines 118-573) into
   docs/DECISIONS.md as the living file every session reads. Keep the entries
   verbatim — they are a record, not prose to improve. Add a short header
   saying what the file is and that every package appends to it.
2. Move the standing POLICY out of the archive and into CLAUDE.md, where the
   other rules live: "Keeping the public repo clean" and "Place names in this
   file and in the tests" (lines 6030-6089). These are rules, not records.
   Compress to a few lines each in CLAUDE.md with a pointer back to the
   reasoning in the archive. Do not paraphrase the place-names decision into
   something stricter than it is — it deliberately permits real place names.
3. Shrink "Future work" (lines 5951-6029) to a pointer at SP4, SP5 and SP6 of
   docs/SOURCES-PLAN.md, which supersede it. Do not delete the reasoning about
   why uncovered readers matter; move what is still useful into SP4. NOTE that
   README.md line ~716 links to docs/REFACTOR-PLAN.md#future-work — fix that
   anchor or it dangles in a public README.
4. Retitle docs/REFACTOR-PLAN.md as an archive in its opening lines: consult on
   demand, grep rather than read, never at session start.
5. Update CLAUDE.md's opening instruction to: this file, docs/DECISIONS.md, and
   docs/SOURCES-PLAN.md. Roughly 1,200 lines instead of 7,000.
6. Add to CLAUDE.md's Definition of done: "README and the run summary reflect
   any user-visible change; anything a later session would re-derive is in
   docs/DECISIONS.md." Seven prompts can forget; a checklist item does not.
7. Add `git clean -xfd` to CLAUDE.md's never-run list, with one line of why:
   everything irreplaceable in this repo is gitignored by design and -x targets
   exactly that. Verified 2026-09-10 — it would remove the whole store and all
   of data/curated/.
8. README.md's "How this is built and maintained" section says "Three
   documents, all public" and tables them. There are five now: add
   docs/SOURCES-PLAN.md and docs/DECISIONS.md. The existing
   docs/REFACTOR-PLAN.md row still describes a live plan and still credits it
   with the decisions log — both untrue once this package lands. Rewrite it as
   the archive it is, and point the decisions-log half of the sentence at
   docs/DECISIONS.md. The paragraph under that table ("The plan file is the one
   to read if you want the reasoning rather than the result") needs the same
   treatment: that reasoning now lives in docs/DECISIONS.md.

CROSS-REFERENCES ARE THE RISK. Decisions-log entries cite the packages that
produced them ("WP8g's lesson", "closed by CU1", "see the result section").
Once the log lives in its own file those become dangling references. Rewrite
each as a link into docs/REFACTOR-PLAN.md with its section anchor, and verify
every one resolves. Grep the whole repository — README.md, CLAUDE.md, docs/,
comments in .py files — for references to the moved sections and fix them too.

WHILE YOU ARE IN CLAUDE.md, fix what has gone stale since the refactor. Verify
each against the code rather than trusting this list:
  - The architecture block still lists `storage/` as "CSV store (internal) and
    xlsx store (presentation)". storage/csv_store.py was DELETED in WP5. The
    directory holds db.py (SQLite) and xlsx_store.py.
  - "Redesign, do not patch" names `storage/csv_store.py` as one of three drift
    areas. It does not exist. Replace it with an area that does, or drop it to
    two.
  - The Definition of done says `ruff check .` passes "(once WP2 has added it)".
    WP2 landed on 2026-08-07 and CI has run ruff ever since.
  - The data-flow line ends "-> store -> data/jobs.xlsx". The store is SQLite
    and the xlsx is an export of it.

Branch sp0b-split-plan. Commit, do not push. Update this plan file.
```

### Result — done 2026-09-11, branch `sp0b-split-plan`

- **`docs/DECISIONS.md` created**, holding the refactor plan's decisions log
  (its former lines 118-573) verbatim. Every in-line citation of a package
  (`WP8g`, `CU3`, ...) is now a markdown link into `docs/REFACTOR-PLAN.md`'s
  matching section, computed against GitHub's own heading-slug algorithm and
  checked for collisions — no citation was left as inert text.
- **`docs/REFACTOR-PLAN.md` retitled as an archive** in its opening lines
  (consult on demand, grep rather than read) and its decisions-log section
  replaced with a one-paragraph pointer to `docs/DECISIONS.md`, leaving the
  per-package prompts and results untouched below it.
- **The standing POLICY moved, not copied.** "Keeping the public repo clean"
  and "Place names in this file and in the tests" stay physically in
  `docs/REFACTOR-PLAN.md` (they are the reasoning an archive holds), and
  `CLAUDE.md`'s Working rules gained a compressed rule for each with a link
  back to the full section. The place-names compression was checked against
  the instruction not to overstate it: it names `docs/REFACTOR-PLAN.md` and
  the tests specifically, and calls out that `docs/SOURCES-PLAN.md` made the
  opposite call for itself (company names stay out of that file).
- **"Future work" shrunk to a pointer** at SP4, SP5 and SP6, heading text kept
  unchanged so the two `#future-work` links in `README.md` keep resolving
  without themselves needing a fix. The one piece of its reasoning not already
  duplicated in this file — that the reader count, not the source count, is
  the honest measure of fixture coverage — was moved into SP4 above rather
  than deleted.
- **CLAUDE.md's opening instruction, architecture block and Definition of
  done updated**, each verified against the code rather than trusted from the
  prompt: `storage/csv_store.py` is gone (deleted in WP5, confirmed by
  `ls job_scraper/storage/`), the "Redesign, do not patch" list dropped to the
  two areas that still exist, the data-flow line now says SQLite store with
  the xlsx as its export, and the `ruff check` parenthetical was removed (CI
  has run it since WP2, 2026-08-07, confirmed against `.github/workflows/ci.yml`).
  `git clean -xfd` added to the never-run list.
- **`README.md`'s "How this is built and maintained" table now lists all
  five documents**, the `REFACTOR-PLAN.md` row rewritten as the archive it is,
  the paragraph beneath it split between "reasoning" (`docs/DECISIONS.md`) and
  "full incident narrative" (`docs/REFACTOR-PLAN.md`), and the Layout table's
  `docs/` row updated to match. The "thirteen of the twenty-six extractors are
  uncovered" paragraph now points at SP4/SP6 of this file instead of the old
  Future work anchor — that number itself is untouched here, since moving it
  is SP4's and SP6's job, not this one's.
- **Grepped the whole repository** for `REFACTOR-PLAN.md` references
  (`scripts/capture_fixtures.py`, `scripts/refresh_label_locations.py`,
  `job_scraper/filtering.py`, `tests/test_extractors_golden.py`): all of them
  cite package sections that are still physically in `docs/REFACTOR-PLAN.md`
  unchanged, so none needed a fix.
- Code, config and tests: none touched, as scoped.

### Your to-dos

- [ ] Confirm you want the decisions log at `docs/DECISIONS.md` rather than
      somewhere else. Every future session will read it, so the name matters
      more than it looks.
- [ ] After it lands, watch one session start and check it is not still pulling
      the archive in out of habit.

---

## SP1 — Curated lists to YAML, and a writer CLI

The package that removes the spreadsheet from the loop. After it, you never open
these files by hand again: you say what you want recorded, a session runs one
command, and the file is appended to atomically.

```
think hard

Read CLAUDE.md and docs/SOURCES-PLAN.md, then work on SP1 only.

Build job_scraper/tools/sources.py, a small CLI over the two curated source
lists, and migrate both lists to YAML.

FORMAT MIGRATION.
- data/curated/excluded_sources.csv (semicolon-delimited, header
  organisation;url;reason, 7 rows) becomes data/curated/excluded_sources.yaml.
- data/curated/candidate_sources.xlsx becomes
  data/curated/candidate_sources.yaml.
Write the migration as scripts/migrate_curated_to_yaml.py, which reads the old
file, writes the new one, and leaves the old one exactly where it is. DO NOT RUN
IT against the real files — CLAUDE.md forbids writing there outside the tool,
and the owner runs the migration themselves. Prove it works on fixtures under
tests/fixtures/ instead.

SCHEMAS. Excluded entries: organisation, url, reason, excluded_on (date).
Candidate entries: organisation, url, category, blocker, last_checked (date),
ats (nullable), source_of_record (free text — where the finding came from).
`blocker` and `last_checked` are the point: a candidate that was checked and
rejected must carry why and when, or the check gets repeated blind.

COMMANDS.
  sources list [excluded|candidates]
  sources check <url-or-name>     — searches both lists AND sources.yaml
  sources exclude <org> <url> <reason>
  sources candidate add <org> <url> --blocker ... [--ats ...] [--category ...]
  sources candidate promote <org>  — moves a candidate to the tombstone
MATCHING IS NOT BY HOST. Half the supported ATS platforms put every customer
on one shared hostname: sources.yaml today has six sources on
job-boards.greenhouse.io, three on jobs.ashbyhq.com and two on
apply.workable.com. A host match would report a brand-new Greenhouse employer
as already present, and — worse, in SP3 and SP7 — as already tombstoned.

Match on BOARD IDENTITY: the normalised host plus the path segment that
identifies the employer on that host (the board slug), via
job_scraper.urlutil.normalize_http_url for the normalisation. Where a host is
single-tenant the host alone is the identity. Write the shared-host cases into
the tests explicitly — two different Greenhouse boards must not match, the same
board with and without a trailing slash must.

Every writing command: append-only, refuses a duplicate board identity, refuses to modify
an existing entry, writes via tempfile + os.replace() into the same directory,
and leaves a timestamped .bak beside the target first. Reuse the atomic-write
shape from storage/xlsx_store.py:126 rather than inventing a second one, and
take paths from config_loader.default_curated_dir().

Argparse front door on every subcommand, and `--help` must exit without doing
anything — README.md's maintenance-commands section records what it cost when a
command with no argument parser ran regardless of its arguments.

Tests: build the lists in tmp_path, never against data/curated/. Cover the
duplicate refusal, the host-normalisation match (same company, http vs https,
trailing slash, www), the backup file, and that a write interrupted mid-way
leaves the original intact.

No new dependencies: pyyaml is already in requirements.txt.

DOCS. Add the CLI to README.md's "Maintenance commands" section, beside
retrofilter and blocklist_all, including that --help exits without acting —
that section already records what it cost when a command ignored its arguments.
Update the data/curated/ row of README.md's Layout table for the new YAML
files, and update the test count in README.md's Tests section.

Branch sp1-curated-yaml. Commit, do not push. Update this plan file.
```


### Result — done 2026-09-11, branch `sp1-curated-yaml`

- **`job_scraper/tools/sources.py`** is the CLI: `list`, `check`, `exclude`,
  `candidate add`, `candidate promote`. **`job_scraper/curated.py`** is the
  data layer under it (load, validate, append, atomic write, backup, the
  commit into the curated repository), so SP2, SP3 and SP7 can read and write
  the lists without going through argv.
- **Matching is by board identity**, as specified: `urlutil.board_identity`
  is the normalised host plus, on a known multi-tenant host, the path segment
  naming the board. The shared-host cases are written into
  `tests/test_board_identity.py` explicitly — two Greenhouse boards do not
  match, the same board does across `http`/`https`, a trailing slash, `www.`,
  case, whitespace and a deep link into a posting. Workday is treated as
  multi-tenant too, because a Workday tenant can host another brand's board
  (`sources.yaml` reaches Busuu through Chegg's). Reasoning in
  `docs/DECISIONS.md`.
- **Every writing command is append-only**, refuses a duplicate board *or* a
  duplicate organisation, refuses to edit an existing entry, takes a
  timestamped `.bak` first, and writes through a temp file in the same
  directory with `os.replace()` — the shape lifted from
  `storage/xlsx_store.py`, not a second one. `promote` is the single command
  that removes anything, and it backs up both files and writes the tombstone
  before removing the candidate, so an interruption leaves the board on both
  lists rather than on neither.
- **Three refusals that were not in the prompt but fall out of having both
  lists in one place**: a tombstoned board cannot be re-added as a candidate,
  a candidate cannot be excluded behind its own back (`promote` is the route),
  and a board already in `sources.yaml` is neither. Each prints what to do
  instead and changes no file.
- **The curated repository is wired in** per the owner's SP0 option-2
  decision: each write is committed to `data/curated/.git` if that repository
  exists, staging only the YAML file and never the `.bak` copies. It is
  best-effort — the file is already written when git runs, so a failure warns
  rather than raises — and when there is no repository the tool says so and
  names `git init` as the fix. `--no-commit` skips it.
- **`scripts/migrate_curated_to_yaml.py`** reads the semicolon-delimited CSV
  (quoted fields and all — one real reason contains a semicolon) and the
  workbook, and writes the two YAML files. **It has not been run against the
  real files**, as instructed; it is proved against
  `tests/fixtures/curated/`, whose organisations are invented because the
  rejected list is the one thing this plan decided stays unpublished. It
  leaves the old files untouched, refuses to overwrite an existing YAML file,
  refuses two rows that are the same board, and has a `--dry-run` that prints
  the YAML it would write.
- **Fields the old formats never held are null, not guessed** — `excluded_on`
  for all seven tombstone rows, and everything but organisation and URL for
  the twenty candidates. The old `notes` column maps to `blocker`; it is empty
  in all twenty rows, so nothing was actually coerced.
- **123 new tests** (602 to 725), all against `tmp_path`. An autouse fixture
  makes the real `data/curated/` unreachable from the suite rather than
  trusting each test to pass `--curated-dir`: a test suite that writes to the
  file it is protecting is the same mistake it is testing for. The interrupted
  write is covered twice — failing at `os.replace` and failing while writing
  the temp file — and both assert the previous list is byte-identical
  afterwards with no temp file left behind.
- **`--help` exits without acting** on every subcommand, including the bare
  command and the `candidate` group, and is tested for each. An unusable
  command line writes nothing at all: the tests assert the directory is still
  empty afterwards.
- **`.gitignore`**: the `data/curated/` negation was a per-extension list
  (`*.example.csv`, `*.example.xlsx`) — the enumeration `CLAUDE.md` warns
  about, and it would not have covered a `.yaml` example. It is now
  `!data/curated/*.example.*` with an explicit re-deny of `data/curated/*.bak`,
  so the backups stay private whatever they are named. Both new files have a
  tracked `.example` twin.
- **README**: the new CLI and the migration script are in "Maintenance
  commands" (including that `--help` exits without acting, and that matching
  is by board rather than host), the `data/curated/` and `scripts/` rows of
  the Layout table are updated, and the test count moved 602 → 725.

- **Follow-up in the same package, three gaps closed** after the result above
  was first written. (1) A crash between `promote`'s two writes was a trap:
  the retry refused because the board was tombstoned, `exclude` refused
  because it was still a candidate and pointed back at `promote`, and the only
  way out was hand-editing a curated file. Writing the test exposed it. A
  retried `promote` now finishes the move when the tombstone holds that board
  under the same organisation, and refuses with "conflict" under a different
  one. (2) A failed commit to the curated repository is now tested with a
  real git that has no identity, and with git missing from `PATH`: the file
  is written and the failure is reported. (3) The CLI and the migration
  script are run as real processes, because the migration script's import
  failure earlier in this package was invisible to every in-process test.
  Also in the follow-up, approved by the owner as slightly out of scope: the
  four `# noqa: E402` comments this package added suppressed nothing and were
  removed, and the `pyproject.toml` comment now says which module does need
  one (`tests/fixture_cases.py`) and why.

- **Two bugs found by a reviewer session before push, both fixed.**
  (1) *Before migration, the tool read the tombstone as empty.* It reads only
  the YAML files, and the real tombstone was still the CSV, so `check` said
  "no match" for a banned employer and `candidate add` accepted one. Now every
  command except `--help` refuses while an old-format list exists without its
  YAML replacement, and so do `curated.load_excluded` and `load_candidates`,
  so SP3 and SP7 get the refusal too. (2) *The migration could stop halfway
  and then refuse to finish.* It wrote the tombstone, refused the candidates
  because that YAML already existed, then on the retry refused the tombstone.
  It now checks both targets before writing either. A target identical to
  what it would write counts as done, so a re-run, including one after a
  crash between the two writes, completes the migration. A target with any
  other content stops the run with nothing written. Both bugs were reproduced
  on fixture copies first, and the new tests fail against the previous code.

**One thing to know before running the migration:** `candidate_sources.xlsx`
holds twenty rows and its `notes` column is empty in every one of them, so the
migrated candidates carry no blocker at all. That is honest but not useful —
`candidate promote` will ask for a `--reason` for each of them until a blocker
is recorded. SP2 is the natural place to fill them in, since it is recovering
exactly that kind of annotation from the transcript archive.

### Your to-dos

- [x] After the branch is reviewed, run
      `python scripts/migrate_curated_to_yaml.py --dry-run`, read it, then run
      it without the flag. This is the one step a session must not do for you.
- [x] Confirm both YAML files look right, then delete the old `.csv` / `.xlsx`
      **yourself** — or keep them; they are ignored either way.
- [x] The SP0 option-2 question is answered: yes, and the writer commits to it.
      The `git init` in `data/curated/` and the off-tree mirror are still
      yours — see SP0.
- [x] Sanity-check one refusal by hand after the migration, e.g.
      `python -m job_scraper.tools.sources check <a URL already in the list>`.
      It should print `EXCLUDED` and exit 0.

---

## SP2 — Recover `skipped_sources` from the transcript archive

`docs/REFACTOR-PLAN.md` records this content as permanently lost. It is not.
Verified on 2026-09-10: the file's rows appear verbatim in the local session
transcript archive at `~/.claude/projects/`, in sessions dated 2026-06-12,
2026-07-30 and 2026-08-02, under the header `organisation,url,category,reason` —
each row carrying the ATS finding that was the reason for skipping. Four rows
were sighted directly during that check; the archive holds more.

Recovery beats recollection, so this package goes to the archive first and to
your memory only for whatever the archive does not have.

**It also fills in the blockers the migration could not carry.** SP1's
migration turned `candidate_sources.xlsx` into YAML, but that spreadsheet's
`notes` column was empty in all twenty rows, so every migrated candidate has
`blocker: null` and `last_checked: null`. A candidate with no blocker cannot be
promoted without a hand-typed `--reason`, and it gives the next audit nothing
to go on, which is the "checked blind" problem the schema exists to prevent.
The transcript archive this package sweeps is also the likeliest place those
blockers were ever written down, so both jobs go in one package.

This needs one exception to SP1's rules, and it is yours to grant. SP1's tool
never changes an existing entry, and `candidate add` refuses a candidate that is
already listed, so no command can currently record a blocker on a migrated
row. The prompt below adds exactly one narrow command for that. It is
**fill-only**: it writes a field only while that field is empty, and it never
replaces a value.

```
think

Read CLAUDE.md and docs/SOURCES-PLAN.md, then work on SP2 only. SP1 must be
merged first — this package writes through its CLI. The owner must also have run
scripts/migrate_curated_to_yaml.py: until then the sources tool refuses every
command, by design. If it refuses, stop and say so; do not run the migration.

Recover the content of the deleted data/skipped_sources.csv from the local
session transcript archive and load it into the candidates list.

WHERE. ~/.claude/projects/ holds MORE THAN ONE directory for this project — the
main one plus a worktree directory (…-project--claude-worktrees-…). Glob for
*job*scraper* and sweep every .jsonl in every match; a sweep of the main
directory alone silently misses sessions.
Sessions dated 2026-06-12, 2026-07-30 and 2026-08-02 are known to contain the
file; sweep all of them rather than only those. The rows appear inside tool results as
escaped text under the header `organisation,url,category,reason`.

HOW. Write scripts/recover_skipped_sources.py: scan the archive, extract every
block matching that header, JSON-unescape it, parse the rows, and merge across
sessions — later sessions supersede earlier ones for the same organisation.
Output a YAML file to the SCRATCHPAD, not to data/curated/, and print a summary:
how many distinct organisations, which sessions each came from, and any row that
appears with conflicting content across sessions.

THEN. Cross-check the recovered set against data/curated/excluded_sources.yaml,
data/curated/candidate_sources.yaml and sources.yaml, by BOARD IDENTITY
(job_scraper.urlutil.board_identity, via curated.find_board), NEVER by host —
see docs/DECISIONS.md: a host match calls every Greenhouse employer the same
one. Match organisation names too, case-insensitively, and report a row that
matches by name but not by board, or the reverse, as a question for the owner
rather than guessing. Report four groups:
  1. already tombstoned — drop.
  2. already an active source — drop, and say so: the blocker was solved.
  3. already a candidate — do not add; these feed BLOCKERS below.
  4. genuinely new — add, as below.

Then add the genuinely-new ones as CANDIDATES — not exclusions — via
`sources candidate add`, with last_checked set to the date of the transcript the
row came from, not today's date, and source_of_record naming the session id.
The owner's decision of 2026-09-10: these were "not feasible at the time" with a
note that they might become feasible, so they belong in candidates.

BLOCKERS FOR THE MIGRATED CANDIDATES. List every entry in
candidate_sources.yaml whose blocker is null, and split it in two:
  a. covered by a recovered row (group 3 above): propose blocker = that row's
     reason, category = its category, and last_checked = the date of the
     session it came from.
  b. not covered by the archive: list the organisation names IN CHAT ONLY.
     The owner dictates a blocker for each one they remember, or says to leave
     it empty. For a dictated blocker, set last_checked only if the owner gives
     a date. Otherwise leave it null and record "dictated by the owner on
     <today>" in source_of_record. A null date is honest; today's date would
     claim a check that did not happen today (docs/DECISIONS.md, "a migration
     writes null, never a guess").

To write them, add ONE command to job_scraper/tools/sources.py:
  sources candidate record-check <org> [--blocker ...] [--category ...]
      [--last-checked YYYY-MM-DD] [--ats ...] [--source-of-record ...]
It is FILL-ONLY. blocker, category, last_checked and ats are written only while
null. If any field passed already has a value, refuse the whole command and
change nothing. source_of_record is EXTENDED, never replaced: append
"; <new text>" to whatever is there, because the migration already set it to
"migrated from candidate_sources.xlsx" and that provenance must survive.
Everything else follows SP1's writer: argparse front door and --help exits
without acting, board and organisation lookup through curated.py, timestamped
.bak, temp file plus os.replace(), commit to the curated repository, and the
unmigrated-list refusal. Put the logic in curated.py beside append_entry, not
in the CLI. This is the owner-approved exception to "never edit an existing
entry" — see "Your to-dos". Build nothing broader: no general edit, no
delete, no overwrite.
Tests, in tmp_path as in SP1: a null field is filled; a non-null field is
refused with the file byte-identical afterwards; one filled field among the
arguments refuses the whole command; source_of_record keeps its old text; an
unknown organisation changes nothing; --help writes nothing; the backup
exists; an interrupted write leaves the original intact.

SHOW BEFORE WRITING. Before any write, print one table in chat: group 4 rows to
be added, and the blockers from a. and b. to be recorded, each with the exact
values and, for a., the session it came from.
Wait for the owner's yes. Then write one command per row, so every row gets
its own backup and its own commit in the curated repository.

The transcripts contain other personal data — blocklist rows, real job titles,
absolute paths. Read only what this task needs, extract only the
skipped_sources blocks, and do not quote anything else into the plan file, the
commit message or the summary.

README.md: add `candidate record-check` to the curated-lists part of
"Maintenance commands", saying plainly that it only fills empty fields, and
update the test count. docs/DECISIONS.md: the recovery lesson (a gitignored
file deleted after a session that read it is often recoverable from the
transcript archive), and the fill-only exception with its reason.

Branch sp2-recover-skipped. Commit, do not push. Update this plan file with the
count recovered, how many migrated candidates now have a blocker, and how many
are still empty, as counts only, no names. Correct the "permanently lost" claim in
docs/REFACTOR-PLAN.md with a one-line pointer to this package.
```

### Result — done 2026-09-15, branch `sp2-recover-skipped`

- **13 organisations recovered: the whole file as it stood from 2026-05-27
  until its deletion.**
  `scripts/recover_skipped_sources.py` swept 84 transcript files across both
  project directories, subagent transcripts included, and found rows in three
  sessions. The full 13-row block is exactly 2,600 bytes, the size an archived
  `ls` gave for the file, and the 7-row version left after the 2026-07-30
  edits is exactly the 1,390 bytes listed afterwards. The sizes cannot rule
  out rows removed before 2026-05-27; the archive starts on 2026-05-26.
- **One claim in this file was wrong: the 2026-06-12 session holds no rows.**
  It only lists the file. The rows are in the 2026-07-30 and 2026-08-02
  sessions.
- **Cross-check, by board identity and by name, with no row matching one way
  but not the other:** 6 already tombstoned (the six rows removed from the
  file on 2026-07-30, so the history is consistent), 0 already active
  sources, 1 already a candidate, 6 new. One organisation appeared with two
  URLs for the same board; the later one, a deliberate correction made in
  that session, was used.
- **The 6 new rows were added as candidates**, not exclusions, one command
  and one curated commit each. All six carry the recovered blocker and
  category, four carry the ATS their blocker names, and `last_checked` is
  **null**. The archive showed that neither session checked any site — they
  read and pruned the file, whose rows were last written on or before
  2026-05-27 — so dating them by session would have claimed a check that did
  not happen. The bound is recorded in `source_of_record` instead, and
  `candidate add` gained `--undated` to write it (owner's decision).
- **Migrated candidates: 1 of 20 now has a blocker, 19 are still empty.** The
  one was covered by a recovered row. For the other nineteen the owner chose
  to leave the blocker empty rather than record anything from memory, because
  empty can still be filled after a real check and a wrong value cannot.
- **`candidate record-check`** is the owner-approved fill-only exception: it
  writes blocker, category, `last_checked` and ats only while empty, refuses
  the whole command if any passed field holds a value, and appends to
  `source_of_record`. Reasoning in `docs/DECISIONS.md`.
- **32 new tests** (725 to 757): record-check's fill, refusal with the file
  byte-identical, all-or-nothing refusal, provenance kept, unknown
  organisation, `--help`, backup and interrupted write; `--undated`; and the
  recovery script against synthetic transcripts only, including one full
  sweep run as a real process from an unrelated directory.
- **Docs:** README's curated-lists section and test count, `docs/DECISIONS.md`
  (the recovery lesson, dating from an archive, the fill-only exception), and
  a one-line correction in `docs/REFACTOR-PLAN.md`'s CU1 result.

### Your to-dos

**Before the session starts:**

- [x] Run the SP1 migration (SP1's to-dos). The tool refuses to work until
      you do, and the session is told to stop rather than run it for you.
- [x] **Approve the fill-only exception**, or strike it from the prompt.
      `CLAUDE.md` reserves "editing an existing entry" for you. This is a
      narrow form of it: an empty blocker, category, date or ATS gets filled,
      `source_of_record` gets text appended, and nothing is ever replaced. If
      you would rather not have it, delete the "BLOCKERS" part of the prompt;
      the migrated candidates then stay blank until you decide otherwise.

**During the session.** Only you can do these, because they are judgements
about your own history that no file records:

- [x] Review the table the session shows before anything is written. It is the
      one chance to catch a row that should be a tombstone rather than a
      candidate, or an old blocker that is no longer true.
- [x] For each candidate the archive does not cover, dictate the blocker if you
      remember it, with a date if you know one, or say "leave it". A blank
      blocker is honest; a guessed one misleads every later audit.
- [x] Tell the session about any organisation you remember that the archive did
      not turn up. Dictate it in chat; do not open a file.

**What the session does for you:** it sweeps the archive, matches every row
against the three lists by board, proposes each blocker with its evidence,
builds and tests the fill-only command, writes only the rows you approve
(one command each, with a backup and a commit), and reports counts in this
file.

---

## SP2b — Candidate re-checks and activation

Added 2026-09-15, after SP2, on the owner's decision. SP2 left the candidates
list with two gaps that SP5 will hit in its first batch:

1. **A re-check has nowhere to go.** `candidate record-check` fills empty
   fields only, by design. Once a candidate carries a blocker, a later check
   that finds a different reason — or the same reason on a later date — cannot
   be recorded, and the stale blocker is the "checked blind" problem again.
2. **A candidate that becomes a source cannot leave the list.** `promote` is
   the only command that removes a candidate, and it moves it to the tombstone.
   A board SP5 adds to `sources.yaml` would stay a candidate indefinitely,
   and `check` would report it as both.

Both need a second exception to "never edit an existing entry", and it is
yours to grant (see "Your to-dos"). It is kept as narrow as the two cases: a
re-check replaces a finding but writes the old one into the entry first, and
activation removes an entry only when the board is provably in `sources.yaml`.

```
think

Read CLAUDE.md, docs/DECISIONS.md and docs/SOURCES-PLAN.md, then work on SP2b
only. SP2 must be merged first.

Add two commands to job_scraper/tools/sources.py, with the logic in curated.py
beside record_check, not in the CLI.

1. sources candidate recheck <org> --blocker ... --last-checked YYYY-MM-DD
       --source-of-record ... [--ats ...]
   For a candidate that was checked again and is still not a source.
   - --blocker, --last-checked and --source-of-record are REQUIRED. There is
     no default date: a re-check is dated by whoever did it, and today is only
     right when it is typed.
   - Replaces blocker and last_checked, and ats when it is passed. Never
     touches organisation, url or category.
   - BEFORE replacing anything, extend source_of_record — never replace it —
     with "; rechecked <new date>: was blocker=<old>, last_checked=<old>" (and
     ats=<old> when ats changes), writing null for an empty old value, then
     "; <the new --source-of-record text>". The file carries its own history;
     the .bak and the curated commit carry it too.
   - Refuses, changing nothing, when: the organisation is unknown; the new
     date is earlier than the recorded one (a check does not move backwards);
     nothing would change; the board is tombstoned (that is a conflict for the
     owner, not a re-check); or the board is an active source in sources.yaml
     (that is `activate`).

2. sources candidate activate <org>
   For a candidate that is now scraped.
   - Removes the entry ONLY when its board identity matches an entry in
     sources.yaml. Otherwise refuse, naming the board it looked for. A missing
     sources.yaml is a refusal, never "not active".
   - Before writing, print the whole entry being removed, so the terminal
     holds it as well as the .bak and the curated commit.
   - No --force, and no removal by name alone.

Match organisations and boards through curated.find_organisation and
curated.find_board — board identity, never host. Everything else follows
SP1's writer: argparse front door and --help exits without acting,
timestamped .bak, temp file plus os.replace(), commit to the curated
repository, and the unmigrated-list refusal.

Build nothing broader: no general edit, no delete by name, no overwrite
without history. record-check stays fill-only and is not changed.

Tests, in tmp_path as in SP1 and SP2. recheck: the finding is replaced and
the old values appear in source_of_record, with null for empty ones;
source_of_record keeps all of its earlier text; a missing required argument
writes nothing; an earlier date, an identical finding, an unknown organisation,
a tombstoned board and an active board are each refused with the file
byte-identical. activate: a candidate whose board is in sources.yaml is
removed and no other entry changes; a board NOT in sources.yaml, a missing
sources.yaml, and the same host with a different board slug (two Greenhouse
employers) are each refused with the file byte-identical. Both: --help writes
nothing, the backup is the file as it was, and an interrupted write leaves the
original intact. Run one of each as a real process.

DOCS. README.md "Maintenance commands": both commands, saying plainly that
recheck keeps the old finding in source_of_record and that activate refuses
unless the board is in sources.yaml; update the test count. CLAUDE.md: the
data/curated/ rule says the tool "appends one record at a time", which has not
been the whole truth since SP1's promote — name the exceptions (promote,
record-check, recheck, activate) in one sentence, with a pointer to
docs/DECISIONS.md. docs/DECISIONS.md: the second exception and its reason.

Branch sp2b-candidate-lifecycle. Commit, do not push. Update this plan file.
```

### Result — done 2026-09-15, branch `sp2b-candidate-lifecycle`

- **`candidate recheck` and `candidate activate`** are in
  `job_scraper/tools/sources.py`, with the logic in `curated.recheck` and
  `curated.activate` beside `record_check`. `record-check` is unchanged.
- **`recheck`** requires `--blocker`, `--last-checked` and
  `--source-of-record` (argparse refuses a missing one before anything runs),
  replaces blocker and `last_checked` and, when passed, ats, and first appends
  `rechecked <date>: was blocker=<old>, last_checked=<old>` (with `ats=<old>`
  only when ats actually changes, `null` for an empty old value) and then the
  new text to `source_of_record`. It refuses, with the file byte-identical and
  no backup taken, an unknown organisation, an earlier date, an identical
  finding, a tombstoned board and a board in `sources.yaml`. Two choices the
  prompt left open: the same date with a different blocker counts as a change
  (only an *earlier* date is refused), and a new `--source-of-record` text
  alone does not — "nothing would change" means the finding, not the note.
- **`activate`** removes a candidate only when its board identity matches a
  `sources.yaml` entry, refuses and names the board otherwise, and refuses a
  missing `sources.yaml`. `curated.activate` takes the path and reads the
  file itself, so no caller can hand it an empty list and get a removal. The
  whole entry, nulls included, is printed through a callback that runs after
  every check has passed and before the file is touched.
- Both take the timestamped `.bak`, write through a temp file and
  `os.replace()`, commit to the curated repository, and refuse while a list is
  unmigrated.
- **37 new tests** (757 to 794), all in `tmp_path`, with autouse fixtures that
  make both the real `data/curated/` and the real `sources.yaml` unreachable.
  Everything the prompt listed is covered, plus a second recheck keeping the
  first's history, a changed ats, a name-only match in `sources.yaml` refused
  by `activate`, and the unmigrated refusal. The real-process `recheck` runs
  via `-m`. The real-process `activate` runs `main()` in a fresh interpreter
  via `-c`, with only `default_sources_path` pointed at a temp file. Without
  that it would read the owner's real `sources.yaml`, and a `--sources` flag
  would have given `activate` the `--force` the prompt rules out.
- **Docs:** README "Maintenance commands" and test count, `CLAUDE.md`'s
  `data/curated/` rule (the four exceptions in one sentence), and
  `docs/DECISIONS.md` (the second exception, and why `activate` reads
  `sources.yaml` itself).
- **Follow-up after review, two fixes.** (1) `recheck` read a missing
  `sources.yaml` as "nothing is active", so it could have re-checked a board
  already being scraped instead of pointing at `activate`. It now refuses,
  through the same `curated.load_active_sources` as `activate`, and a test
  covers it (795 tests). Both missing-file tests were confirmed to fail when
  the refusal is replaced by an empty list. (2) The real-process `recheck`
  test ran plain `-m`, so it read the owner's real `sources.yaml`. It now runs
  the same way as the `activate` one, with `sources.yaml` pointed at a temp
  file. Since (1), a plain `-m` run would refuse on any machine without that
  file anyway, so neither real-process test is a literal `-m` run.

### Your to-dos

- [x] **Approve the second exception**, or strike this package. `recheck`
      replaces a recorded finding (keeping the old one in the entry) and
      `activate` removes a candidate. If you strike it, SP5 falls back to
      reporting these cases in chat and writing nothing.
- [x] Nothing during the session: it writes no curated file, only tests in
      `tmp_path`.

---

## SP3 — `sources probe` — the feasibility ladder as a command

CU2's investigation of `probably_good` is the model: four rungs, worked in
order, with the answer written down. This package automates the first two rungs
and makes the other two a structured report rather than a fresh piece of
detective work each time.

```
think hard

Read CLAUDE.md and docs/SOURCES-PLAN.md, then work on SP3 only. SP1 must be
merged first.

Add `probe` to job_scraper/tools/sources.py:

    python -m job_scraper.tools.sources probe <url>

It answers "can this career page be scraped within this design?" WITHOUT
touching sources.yaml, registry.py or tests/fixtures/. It prints a report and
exits. It never writes config.

The report, in this order:
1. Tombstone / candidate / active check first, by BOARD IDENTITY, not by host —
   see SP1. Six existing sources share one Greenhouse hostname, so a host match
   would refuse to probe every future Greenhouse employer. If the board is
   already tombstoned, say so and STOP — do not fetch. Re-investigating a
   permanent exclusion is the exact failure the tombstone exists to prevent.
   If it is a candidate, print its blocker, last_checked and source_of_record
   before fetching, and say explicitly when last_checked is null: that means
   the date is unknown, not that it was never checked (see SP2's result).
2. robots.txt for the host, via the existing policy in http.py, quoting the rule
   and the User-Agent it was evaluated against.
3. Static fetch: does the HTML contain job data, or is it a client-rendered
   shell? Report bytes, and count anchors/cards that look like postings.
4. ATS fingerprint: look for the markers of the ten supported platforms
   (Greenhouse, Lever, Ashby, Workable, Teamtailor, Personio, SmartRecruiters,
   Workday, Breezy, SuccessFactors) in the HTML, the script sources and the
   redirect chain. Name the board URL if one is discoverable.
5. For each plausible match, run that generic extractor against the page through
   the normal fetcher and report the row count plus three sample postings —
   title, location, detail_url — so a plausible-but-wrong reader is visible
   rather than merely counted.
6. Pagination: does the listing publish a total or a pager, and does the row
   count look like a first page? WP11 exists because a silently short walk
   looks like success.
7. A verdict line: `reuse <extractor>`, `needs a new extractor`, or
   `not feasible — <which rung failed>`. On the first, print the paste-ready
   sources.yaml block and the one-line registry entry. PRINT them. Do not edit
   either file.

Rungs 3 and 4 of the CU2 ladder — a private API, a third-party index behind it —
are NOT automated. Where the static and rendered routes both fail, say so and
point the reader at the probably_good section of docs/REFACTOR-PLAN.md, which is
what a proper investigation of those rungs looks like.

This fetches a live third-party site, so it goes through http.py's normal
fetcher: honest User-Agent, robots honoured, per-host rate limit, response
cache. It must never write to tests/fixtures/ — capturing a fixture stays
scripts/capture_fixtures.py's job, per CLAUDE.md.

Tests: drive every rung from saved fixtures under tests/fixtures/, with a stub
fetcher. No test may touch the network.

DOCS. README.md's "Adding a source" section is three steps that omit both the
tombstone check and the fixture capture, so REWRITE it as the routine actually
is — check, probe, then reuse (and capture), exclude, or record the finding on
the candidates list — rather than appending the command to the end of what is
there. Recording covers all three candidate commands: `candidate add` for a new
board, `candidate record-check` to fill empty fields, and `candidate recheck`
(SP2b) to replace a finding while keeping the old one. Name `recheck` only if
SP2b has merged; a README must not describe a command that does not exist. Update the header comment in
job_scraper/config/sources.example.yaml the same way: its advice on how to add
a source now starts with `probe`. Update the test count in README.md.

Branch sp3-source-probe. Commit, do not push. Update this plan file.
```

### Result — done 2026-09-17, branch `sp3-source-probe`

- **`python -m job_scraper.tools.sources probe <url> [--name ...] [--company ...]`**
  prints the seven sections in the order the prompt gives and exits 0 only on
  `reuse`. The logic is `job_scraper/probe.py`, one function per rung; the CLI
  is a thin door in `tools/sources.py`, and `--help` fetches nothing.
- **Rung 1 is by board identity**, through `curated.find_board` and
  `urlutil.board_identity`. A tombstoned board stops the probe before any
  request, robots.txt included, and the test asserts the fetch log is empty.
  A candidate's blocker, `last_checked` and `source_of_record` are printed
  before the first request, and a null date reads "UNKNOWN, which is not the
  same as never checked". A missing `sources.yaml` is reported as a skipped
  check, not a pass. **Not in the prompt:** every board the page *points at*
  is checked too, because an employer's own careers page can embed a
  tombstoned hosted board; such a board is named and not read.
- **Rung 2** quotes the deciding line, its `User-agent` group, the full
  User-Agent and the product token robots.txt actually matches on, and any
  Crawl-delay. That needed `RobotsPolicy.explain` in `robots.py`, which reports
  `allows()`'s own answer and refuses to quote a line its walk cannot confirm.
- **Rung 3** reports bytes, visible text, posting-shaped links, job-card
  elements, JSON-LD postings and embedded data payloads, and names a shell by
  its signs (a `<noscript>` asking for JavaScript, an empty mount point). The
  rendered route runs only when the static page shows no postings, or failed.
- **Rung 4** reads the redirect chain as well as the page. That needed
  `http.fetch_page`, which is `fetch_text` keeping `final_url` and the hops;
  `fetch_text` is now that function trimmed to its body, so there is still one
  request path. Boards are named for the seven hosted platforms; Teamtailor
  and SuccessFactors live on the employer's host, so the page is the board.
  At most three boards per platform are read.
- **Rung 5** runs the generic reader bound as `registry.py` would bind it
  (org slug for Lever and SmartRecruiters; `page_step` and `base_search_url`
  for SuccessFactors, the page size read off "1 to N of T" or counted), with
  the rendering fetcher for Workday and for a page that only showed postings
  rendered. A board on another host gets its own robots check first. Samples
  are three rows of title, location and `detail_url`, plus flags for empty
  locations, rows linking back to the listing, and duplicate URLs. A reader
  that raises is reported, never raised.
- **Rung 6** reads a stated total ("1 - 20 of 61 jobs", "61 JOBS FOUND",
  "Vacant positions: 2", `totalFound`) and pager signs, and marks a read
  `SHORT` when it holds fewer rows than the total, or a typical page size
  under a pager from a reader that checks no total.
- **Rung 7, and a choice the prompt left open.** `needs a new extractor` is
  for postings on no supported platform and for a reader that reads only a
  first page. A *recognised* platform whose reader fails or reads nothing is
  `not feasible — rung 5`, naming that reader's module as the thing to fix:
  a second module beside a broken generic one is the wrong answer. Only a
  whole read is `reuse`, and only then are the paste blocks printed (a YAML
  entry the tests parse back, and a registry line they `eval` into the same
  partial). When both routes show nothing and no platform is recognised, the
  report names rungs 3–4 and points at `probably_good` in
  `docs/REFACTOR-PLAN.md`. Reasoning in `docs/DECISIONS.md`.
- **Writes nothing.** A test snapshots `tests/fixtures/`, `registry.py`, a
  temp `sources.yaml` and the curated directory around a `reuse` probe and
  finds them unchanged; another asserts `probe.py` contains no file-writing
  call.
- **92 new tests** (795 to 887): 86 in `tests/test_source_probe.py`, and six
  more from the existing secret scan over the new fixture files. Every rung is
  driven through a stub fetcher from saved pages: real captures
  (`kognity`, `storytel`, `busuu`, `path`, `novo_nordisk` as its whole walk,
  `dsv`, `givewell`) and five handwritten ones under `tests/fixtures/probe/`
  for the cases no capture covers. The one localhost test is `fetch_page`'s
  redirect chain, live and from the cache. Four mutations of the probe's key
  guards (tombstone stop, board-not-host, short-read check, render fallback)
  each fail the suite.
- **Docs:** README "Adding a source" rewritten as check, probe, then reuse and
  capture, write a module, or record the finding (with a table of which
  candidate command fits where, `recheck` included since SP2b has merged);
  `probe` in "Maintenance commands", `probe.py` in the Layout table, the test
  count; the header of `sources.example.yaml` now starts with `check` and
  `probe`; `probe.py` in `CLAUDE.md`'s architecture block; three entries in
  `docs/DECISIONS.md`.

- **Follow-up after review, three fixes in `probe.py`, one commit each.**
  (1) Lever boards on `jobs.eu.lever.co` or `api.eu.lever.co` kept losing the
  `.eu`; the board URL now keeps it (the two are different boards by board
  identity), and the report says beside the board and beside the reader run
  that `lever.py` only calls the non-EU API. (2) A robots.txt refusal raised
  inside a reader — whose API host is often not the board's — was reported as
  "rung 5 … a bug in <reader>.py". It is now "rung 2: robots.txt forbids
  <the URL the reader asked for>", with the `ignore_robots` wording, and no
  module is blamed; the probe still does not pre-check guessed API hosts.
  (3) A Teamtailor board taken from the page itself now says, in rung 4 and
  again above the paste blocks, that the page probed is assumed to be the
  listing. SuccessFactors does not need it: it moves to `/search/`, and its
  walk fails a short read. **11 new tests (887 to 898)**; no existing test
  changed. Then, at the owner's request, Greenhouse EU boards got the same
  note as Lever's (their `.eu` was already kept): **5 more tests (898 to
  903)**.
  Last, also at the owner's request: `RobotsDisallowed` now carries the
  refused URL as a field, set in `http.py`, and the probe reads it instead of
  parsing the message — fix (2) had taken it from the wording, which a
  rewording would have broken silently. The stand-in refusal in fix (2)'s
  tests gained that field, since the real one now has it; their assertions
  are unchanged. **5 more tests (903 to 908).**
  After the owner's live probe of `canonical`, the printed entry takes its
  company from an active source too. **3 more tests (908 to 911).** Then,
  at the owner's request, the name as well: a board already in `sources.yaml`
  is printed under the name it has there (exactly as written, since it is a
  registry key), and that name is not reported as a registry clash.
  **4 more tests (911 to 915).**

**Found while testing, not fixed here (scope).** The probe's first run against
the saved `path` page called it short, and it is right: `path.html` states
"1 - 20 of 61 jobs" and `workday.py` reads the 20 on the first rendered page.
The golden test pins 20. Every Workday source in `sources.yaml` has the same
reader, so any of them with more than one page of postings is being read short
today, silently — the WP11 failure in a reader WP11 did not cover. Separately,
`workable.py` reads one API response and follows no next-page token; whether
that loses anything is for SP4's capture to show.
`lever.py` always calls `api.lever.co`, and `greenhouse.py` always calls
`boards-api.greenhouse.io`, so neither can read a board hosted in the EU
(`jobs.eu.lever.co` / `api.eu.lever.co`, `job-boards.eu.greenhouse.io` /
`boards-api.eu.greenhouse.io`). The probe keeps the `.eu` in the board it
names and says beside it that the reader only calls the non-EU API, so the
failure that follows is not mistaken for an unexplained reader bug. Teaching
the readers the EU APIs belongs to SP4.

### Your to-dos

- [x] **Decide what to do about the Workday reader** (above). Planned as
      SP3b, below: its own package, before SP5 adds any Workday employer.
- [x] Nothing during the package. Afterwards, run `probe` against one site you
      already know the answer for — an existing Personio or Greenhouse source —
      and check the verdict matches reality before trusting it on a new one.
      **Done 2026-09-17** against the `canonical` Greenhouse source: found as
      active by board identity, robots.txt allowed, the static page carries
      job data, Greenhouse recognised from the URL, the reader read 304 rows
      with real samples, verdict `reuse greenhouse` with `strategy: static` —
      matching `sources.yaml`. The board page has a pager (52 posting links),
      which is expected: the reader takes the whole board from one API
      response. One weakness it showed, fixed in the same branch: the printed
      entry said the company was unknown although the active entry names it;
      the probe now takes the company from an active source (after `--company`,
      before a candidate's organisation). A Personio source has not been
      probed; its reader has no saved page until SP4.

---

## SP3b — Workday reads the whole board

Added 2026-09-17, from SP3's result. The probe's first run against a saved page
found a data-loss bug in a live reader: `tests/fixtures/path.html` states
"1 - 20 of 61 jobs", and `extractors/workday.py` returns the 20 on the first
rendered page. Nothing fails, and `tests/test_extractors_golden.py` pins 20 as
correct. Six sources use this reader — `slack`, `busuu`, `airbus`, `path`,
`irc` and `axis_comms` in `registry.py` — so any of them with more than one
page of postings is read short on every run today. It is WP11's failure in a
reader WP11 never treated as paginated.

**Why it is its own package, and why before SP4.** It is live data loss, not
a coverage gap, and SP4's five readers do not include Workday. It also blocks
SP5 for any Workday employer: until it lands, `probe` calls every multi-page
Workday board `needs a new extractor`, correctly.

**Two parts, in this order.** A guard first, so a short read fails loudly the
day it lands; then a walk, so it stops failing. Landing only the guard turns
every multi-page Workday source into a failing source until the walk exists —
the store keeps their jobs and nothing is delisted, which is priority 2
working as intended, but the run summary will name them on every run. So the
package lands both, guard commit first.

**The data-loss edge is the dedupe key, not the walk.** A stored job is
recognised by its `detail_url` (`storage/db.py`, `dedupe_key_for_job`). If a
new route builds that URL even slightly differently — a missing `/en-US`, a
different board segment — every stored Workday job looks new, and the old
rows are delisted after two runs. That is a silent rewrite of review history,
which is worse than the bug being fixed.

```
think hard

Read CLAUDE.md, docs/DECISIONS.md and docs/SOURCES-PLAN.md, then work on SP3b
only. SP3 must be merged first.

extractors/workday.py reads only the first rendered page of a Workday board.
tests/fixtures/path.html says "1 - 20 of 61 jobs" (and "61 JOBS FOUND" in
data-automation-id="jobFoundText"); the reader returns 20. Six sources use this
reader: slack, busuu, airbus, path, irc, axis_comms. Fix it so every page is
read, and so a short read fails loudly.

1. GUARD FIRST, as its own commit. Read the board's total from the page
   (jobFoundText, or the "1 - 20 of 61" range) and call
   extractors/pagination.reconcile once the read has ended, as
   successfactors_html.py does. A page with no readable total goes through
   pagination.unverifiable_end, not a silent pass. busuu.html states 6 and
   yields 6, so its golden is unchanged. path.html will now RAISE: that is the
   bug made visible. Do not edit path's golden to make the suite pass: stop,
   and replace its single-page case with a whole-walk capture in step 3.

2. CHOOSE THE WALK, and put the choice to the owner before building it.
   A `fetch(url) -> str` fetcher cannot click Workday's pager, which is why
   probably_good's "Load more" was a dead end (docs/REFACTOR-PLAN.md,
   "`probably_good` — genuinely unfixable within this design"). Investigate,
   with ONE live request each at most, through http.py's polite fetchers:
   a. whether the rendered listing accepts a page or offset in its URL;
   b. the JSON endpoint the page itself calls,
      POST https://<tenant>.<dc>.myworkdayjobs.com/wday/cxs/<tenant>/<board>/jobs
      with {"limit": 20, "offset": N, "searchText": "", "appliedFacets": {}}.
   FOR EACH, check that host's robots.txt first (RobotsPolicy.explain) and
   quote the rule. A route robots.txt forbids is not a route. For (b), check
   whether the response holds a total, and whether its fields give the same
   title, location and detail URL the rendered page does.
   Report both in chat with a recommendation, and wait for the owner's choice.

3. BUILD THE CHOSEN WALK behind the same extract() signature, so registry.py
   does not change. Rules:
   - DETAIL URLS MUST NOT CHANGE. dedupe_key_for_job keys stored jobs on
     detail_url; a different URL shape makes every stored Workday job look
     new and delists the old rows two runs later. Before and after, for
     busuu and path, compare every detail_url the old reader produced from
     the saved page with what the new one produces for the same postings.
     They must be identical. If they cannot be, STOP and put it to the owner.
   - Compare location too, posting by posting. A route that says
     "2 Locations" where the page named a city moves jobs at Layer 0 (see
     DECISIONS.md on WP8d). Report every difference; do not paper over one.
   - The walk ends when it holds the stated total, and fails through
     pagination.reconcile when it does not. Posts go through http.post_json,
     never requests directly.
   - The rendered route is still needed if (a) was chosen; if (b) was, say
     whether sources.yaml's `strategy: dynamic` is still needed for these six
     and PRINT the change for the owner — sources.yaml is not yours to edit.

4. CAPTURE, then pin. Re-capture path as its whole walk
   (scripts/capture_fixtures.py --pages all path) and busuu. NOTE that
   capture_fixtures.py records only what goes through the fetcher it hands
   the extractor; http.post_json bypasses it (workable.py has the same gap).
   If (b) was chosen, fix the capture path for POSTed pages first, with its
   own test, rather than hand-writing a fixture. Pin the new goldens, with a
   comment on path's saying why its count moved from 20.

5. THE PROBE. job_scraper/probe.py describes Workday's walk as "reads the first
   rendered page only" and does not mark it guarded; update both. Its test
   test_a_first_page_read_as_the_whole_board_is_short drives path.html through
   the old reader and expects SHORT. After this package that expectation is
   wrong. Do not quietly edit it: say in chat what it now shows and why, then
   change it so it still proves a short read is caught (a stub that serves
   fewer pages than the stated total will do).

No test may touch the network. The two investigation requests in step 2 are
the only live traffic besides the captures in step 4.

DOCS. README.md: test and fixture counts. docs/DECISIONS.md: the route chosen
and why, the robots.txt findings, and the detail-URL rule. This plan file: the
result, with before/after row counts for path and busuu.

Branch sp3b-workday-walk. One commit per step. Do not push.
```

### Your to-dos

- [ ] **Before the session:** decide whether you are happy for the guard to
      land first, which means any multi-page Workday source shows as failing
      in the run summary until the walk is built. Its stored jobs are kept.
- [ ] **During the session:** choose the route in step 2. The session will
      recommend one; the choice is about fragility, and it is yours.
- [ ] If the detail URLs cannot be kept identical, the session stops. Then the
      decision is whether to migrate stored keys, which is a bigger package.
- [ ] Apply any `sources.yaml` change the session prints.
- [ ] Run it at a civilised hour: it makes live requests to six employers'
      Workday tenants, and `rules.json` should carry your contact details.

---

## SP4 — Fixtures for the five generic ATS readers

`breezy`, `lever`, `personio`, `smartrecruiters`, `workable`. These five come
first because a bug in a generic reader is inherited by every employer added on
that platform afterwards — which is precisely what SP5 is about to do.

**Count by reader, not by source, and expect the gap to look smaller than it
is.** Moved here from the refactor plan's old Future work section (SP0b,
2026-09-11): 22 of 49 sources have a saved page today, which reads as
reasonable coverage, but the reader count is the honest one — thirteen readers
have no saved page at all, so no golden test and no parse check exists for the
code that actually does the parsing, and a bug in one is inherited by every
source that shares it. That is also why these five come before SP6's other
eight: a shared reader multiplies its bugs across employers, and a single-use
one does not.

**Expect bugs, and budget for fixing rather than for capturing.** WP8g captured
four SuccessFactors sources and three of the four were broken. CU2 captured
three more and found one. The estimate below assumes roughly three of these five
have something wrong with them.

```
think

Read CLAUDE.md and docs/SOURCES-PLAN.md, then work on SP4 only.

Capture fixtures for the five uncovered GENERIC ATS readers and fix whatever
that reveals: breezy, lever, personio, smartrecruiters, workable.

Method, and it is not negotiable — WP8g and CU2 both learned it the hard way:
CAPTURE FIRST, THEN READ THE EXTRACTOR. Reasoning about a page layout identifies
it correctly and gets the data wrong.

FIRST, RESOLVE THE NAMES. These five are READER names. capture_fixtures.py
takes SOURCE names from sources.yaml, and for these five no source is named
after its reader — `capture_fixtures.py lever` fails. Read
extractors/registry.py to find which source names map to each of the five
readers, and capture those. (Of the thirteen uncovered readers, seven happen to
share a name with their single source and six do not: breezy, coefficient,
lever, personio, smartrecruiters, workable.)

Then, for each, in turn:
  python scripts/capture_fixtures.py <source_name>
  (--pages all if the source paginates)
Then read the captured page, then read the extractor, then compare what it
produces against what is actually on the page — title, location, department,
detail_url, per posting. Pin the golden output.

workable serves two sources; the others serve one each.

If a reader is wrong, fix it in this package — that is the point of the package,
not scope creep. If a fix turns out to be large, stop, pin the fixture with the
CURRENT (wrong) output clearly marked in the plan file as a characterisation
test, and raise it as its own package rather than half-doing it.

Do not capture the other eight readers here. That is SP6.

DOCS. README.md's Tests section says "thirteen of the twenty-six extractors are
uncovered" and gives a test count and a fixture count. All three move here.
That sentence is the headline number for this whole exercise, so update it on
every instalment rather than at the end.

Branch sp4-fixtures-ats. Commit each source's capture and fix as its own commit.
Do not push. Update this plan file with what each capture revealed — including
the ones that turned out to be correct, because "checked and fine" is a result
worth not repeating.
```

### Your to-dos

- [ ] This package fetches five live career sites. Run it at a civilised hour
      and not alongside a scheduled scrape.
- [ ] Confirm `rules.json` still has your contact details filled in — WP10 put
      them in the User-Agent, and a capture run is exactly when an administrator
      might want to reach you.
- [ ] Expect this one to need a second session. That is the plan working, not
      slipping.

---

## SP5 — Add the new companies

The routine this whole plan exists to make possible. One session per batch of
two or three; a company that turns out to need a bespoke extractor gets its own
package instead.

```
think

Read CLAUDE.md and docs/SOURCES-PLAN.md, then work on SP5 only. SP1, SP2b and
SP3 must be merged; SP4 too if any of these companies runs on Breezy, Lever, Personio,
SmartRecruiters or Workable; SP3b too if any runs on Workday.

Add the following companies to the scraper: <OWNER FILLS IN NAMES AND URLS>

For each, in order, and stop at the first rung that fails:
1. `sources check <url>` — if it is tombstoned, stop and report. If it is an
   existing candidate, note the recorded blocker and last_checked date before
   doing anything else: it may have been checked recently and the answer may not
   have changed. `check` does not print empty fields, so no date line means
   last_checked is null — unknown, not "never checked". Read source_of_record:
   the candidates recovered in SP2 carry their date bound there.
2. `sources probe <url>`.
3. If the verdict is `reuse <extractor>`: add the sources.yaml entry and the
   registry line the probe printed. Then capture the fixture IN THIS SESSION
   (scripts/capture_fixtures.py) and pin the golden. A new source with no saved
   page joins the uncovered population and undoes SP4's work. If the company
   was a candidate, propose `sources candidate activate <org>` once the source
   and its fixture are committed, and ask the owner before running it.
4. If the verdict is `needs a new extractor`: do NOT write one here. Report what
   the probe found, and propose it as its own package. CLAUDE.md's "config over
   code" means a bespoke module has to earn itself.
5. If the verdict is `not feasible`: propose the exact command, with the reason
   and the date, and ask the owner before running it. Not feasible *for now* is
   a candidate with a blocker; not feasible *by design* is a tombstone. Say
   which you think it is and why. The command depends on where the board is:
   - Not on either list: `sources candidate add` (for now) or `sources exclude`
     (by design).
   - Already a candidate, blocker and date empty: `sources candidate
     record-check` (for now) or `sources candidate promote` (by design).
   - Already a candidate with a recorded finding: `sources candidate recheck`
     (for now) or `sources candidate promote` (by design). Never try
     `candidate add` or `exclude` on an existing candidate: both refuse, and
     neither refusal is a reason to edit the file by hand.
   A re-check is dated with the day the probe actually ran.

Then run the pipeline against the new sources only and confirm the postings that
come back look like real postings, not like a plausible-looking parse of the
wrong element.

DOCS. Update the test and fixture counts in README.md. Do NOT add the company
names to any tracked file — see "Publishing this file".

Branch sp5-add-sources. Commit, do not push. Update this plan file with one line
per company: verdict, extractor reused, rows captured.
```

### Your to-dos

- [ ] **Give the session the company names and URLs.** They are not in this
      file on purpose — see "Publishing this file" below.
- [ ] Answer the open question in "What is still unknown": do any of them run on
      the five uncovered ATS platforms? If you do not know, that is fine — SP3's
      probe will tell you, and the answer decides whether SP4 has to come first.
- [ ] Approve or reject each proposed `exclude`, `candidate add`, `record-check`,
      `recheck`, `promote` or `activate` in chat.
- [ ] After the session, refresh the mirror: SP5 writes to the curated lists
      and nothing refreshes it automatically —
      `git --git-dir="$HOME/Documents/job_scraper_curated.git" remote update --prune`.

---

## SP6 — Fixtures for the remaining eight readers

`asana`, `coefficient`, `jobsinlund`, `mammut`, `norrsken`, `oatly`, `sida`,
`undp`. Same method as SP4, lower stakes: each serves one source, so a bug is
contained rather than inherited.

**This is ongoing maintenance, not a package with an end date.** Two or three
per session, in whatever order suits. The refactor plan makes the same point
about the same work and it is worth restating: treat "this source has no saved
page" as an open question about correctness, not as a gap in paperwork.

```
think

Read CLAUDE.md and docs/SOURCES-PLAN.md, then work on SP6 only.

Capture fixtures for these uncovered readers and fix what that reveals:
<PICK TWO OR THREE: asana, coefficient, jobsinlund, mammut, norrsken, oatly,
sida, undp>

Same method as SP4: capture first, then read the extractor, then compare against
the page, then pin the golden. Do not reason about the layout before capturing.

Resolve reader names to source names via registry.py before capturing — seven of
these eight share a name with their source, but `coefficient` does not.

DOCS. Update README.md's uncovered-reader sentence, test count and fixture
count on EVERY instalment — the number is the point of the exercise, and a
sentence that is right only at the end is wrong for most of the time it is read.

Branch sp6-fixtures-rest. Commit, do not push. Update the SP6 table in this plan
file with what each capture revealed, and leave the rest of the list alone for a
later instalment.
```

### Your to-dos

- [ ] Pick which two or three each time. No need to plan the order in advance.
- [ ] Keep the running tally in this file honest — including readers that turned
      out to be correct.

---

## SP7 — Tombstone guard at startup (optional)

Today nothing in the code reads `excluded_sources`. The tombstone is enforced by
a session remembering to look, which is a rule of the kind CU3 replaced with an
honest note precisely because nobody keeps them.

```
Read CLAUDE.md and docs/SOURCES-PLAN.md, then work on SP7 only. SP1 must be
merged.

At startup, run.py should warn — once, naming the organisation and the recorded
reason — if any sources.yaml entry matches an entry in excluded_sources.yaml by
BOARD IDENTITY (SP1's matcher, host plus board slug — never host alone; six
sources share one Greenhouse hostname and a host match would warn on all of
them).

It WARNS. It does not skip the source and it does not exit. The owner may have
re-added something deliberately, and this project does not silently drop a
source (see the delisting guard in WP1 and the zero-row check in CU2 for the
same principle applied to data).

This is a source-level startup check, not a filter layer. Do not touch the
five-layer ladder, and do not add anything to the filtering modules.

DOCS — THE RUN SUMMARY. This is user-visible output, so README.md's "Reading
the run summary" section has to show it. That section prints a real rendered
summary block with illustrative counts; add the warning exactly as it will
appear, and say where it lands relative to the funnel. Keep the counts in that
block illustrative — a real store's numbers are personal, which is why they are
fake there. Update the test count too.

Print the warning through run.py's user-facing summary path, not through
logging, so it appears for a normal run rather than only under -v.

Branch sp7-tombstone-guard. Commit, do not push. Update this plan file.
```

### Your to-dos

- [ ] Decide whether you want this at all. It is the smallest package here and
      the easiest to skip.

---

## What is still unknown

Three open questions, all of which need you rather than a session:

1. **Do the new companies run on the five uncovered ATS platforms?** Decides
   whether SP4 blocks SP5. SP3's probe answers it definitively; your recollection
   answers it sooner.
2. **How complete is the transcript recovery?** Answered by SP2: complete as
   of 2026-05-27. The recovered rows match the file's archived size byte for
   byte from then until its deletion; anything removed before then, when the
   archive barely begins, cannot be known. What the
   archive cannot give is when each site was checked, so those dates stay null.
3. **Nested git repository in `data/curated/`, yes or no?** SP0. It changes what
   SP1 builds.

## Decisions log

Record any decision a future session would otherwise re-derive. Seeded with
2026-09-10.

- **The curated lists move to YAML rather than being edited less.** The problem
  was never edit volume; it was that a `.csv` double-clicks into Numbers, which
  writes sidecars and once replaced the file with a `.numbers` binary (CU2). The
  surviving CSV's semicolon delimiter is itself evidence of spreadsheet
  round-tripping. Neither file has a numeric column.
- **The recovered `skipped_sources` rows become candidates, not exclusions.**
  Owner's decision, 2026-09-10: they were checked, found not feasible at the
  time, and annotated as possibly feasible later. They therefore carry a
  `blocker` and a `last_checked` date, so the next audit can see what was tried
  and when rather than re-deriving it.
- **`skipped_sources.csv` is NOT permanently lost.** `docs/REFACTOR-PLAN.md`
  says it is, at the CU1 result section. Verified false on 2026-09-10: the rows
  survive in the local transcript archive under the header
  `organisation,url,category,reason`, in sessions of 2026-06-12, 2026-07-30 and
  2026-08-02. *(SP2 correction: the 2026-06-12 session only lists the file;
  the rows are in the other two.)* **The general lesson is worth more than the file: a gitignored
  file deleted after a session that read it is often recoverable from
  `~/.claude/projects/`.** Check there before writing anything off.
- **`CLAUDE.md`'s blanket ban on `data/curated/` was re-scoped, not lifted**
  (2026-09-10, owner's explicit sign-off). It now forbids hand-editing,
  wholesale rewriting and spreadsheet-opening, and permits appends through
  `tools/sources.py` only. Reasoning: both files this project has lost were lost
  by a person with the file open — a deletion in CU1 and a Numbers conversion in
  CU2 — and zero were lost by a program. An append-only writer with an atomic
  write and a backup is the safer path, not the looser one.
- **The probe automates two rungs of the CU2 ladder and stops.** Static and
  rendered HTML are mechanical. A persisted-query API and a third-party search
  index behind it are judgements about fragility and about what a fixture would
  end up containing — a session-issued API key that the HTML sanitiser cannot
  strip, in the `probably_good` case. Encoding that as a verdict would make a
  hard call look like a computed one.
- **The probe prints the registry line rather than editing `registry.py`.**
  Generating one line of code costs more to maintain than it saves to paste.
- **Company names stay out of this file.** See below.
- **Every package names the docs it invalidates, in its own prompt.** An audit
  on 2026-09-10 found only SP3 mentioned the README and none of the seven
  mentioned the run summary — the same drift that cost the refactor a whole
  package (WP8b) to reconcile. Fixed in two places at once: a table above of
  which surface each package touches, and a line in CLAUDE.md's Definition of
  done, because a checklist item outlives a prompt.

- **Match sources by board identity, never by hostname.** Half the supported ATS
  platforms are multi-tenant: six current sources share
  `job-boards.greenhouse.io`, three share `jobs.ashbyhq.com`, two share
  `apply.workable.com`. Every "is this already known?" check in SP1, SP3 and SP7
  therefore keys on host **plus board slug**. A host-only matcher would have
  reported every future Greenhouse employer as already tombstoned — a check
  designed to prevent re-proposals, silently blocking legitimate additions
  instead.
- **Reader names are not source names.** `capture_fixtures.py` takes source
  names from `sources.yaml`, while the uncovered-coverage list is by reader.
  Seven of the thirteen coincide and six do not, including four of the five
  generic ATS readers, so any capture instruction has to resolve reader → source
  through `registry.py` first.
- **`git clean -xfd` is a data-loss command in this repository.** Everything
  irreplaceable is gitignored by design, and `-x` targets exactly that. Recorded
  under SP0.
- **Backups go outside the working tree.** A copy inside `data/` shares the fate
  of the thing it is backing up.
- **The refactor's decisions log lives at `docs/DECISIONS.md`, not in
  `docs/REFACTOR-PLAN.md` (SP0b, 2026-09-11).** `docs/REFACTOR-PLAN.md` is now
  an archive, consulted on demand rather than read every session; its
  decisions log — the ~800 lines a session actually needs — moved out
  verbatim, with every in-line package citation rewritten as a link back into
  the archive. This file's own decisions log, above, stays exactly where it
  is: it is already the size a session needs, and splitting it further would
  just be more files to open.

## Publishing this file

**Yes, publish it — as written, with the lists kept out of it.**

`docs/REFACTOR-PLAN.md` settled the general question already, in its
"Place names in this file and in the tests" section: employers are not a
meaningful disclosure, because the published registry and fixture filenames
already name every source this scraper watches. That reasoning carries here, but
it does not carry all the way, and the difference is worth being explicit about:

- **Employers followed are already public. Employers *rejected* are not.** A
  list of career sites that could not be scraped, each annotated with *why* —
  "requires login", "persistent 403", "anti-fingerprinting", "no job content in
  the DOM" — is a catalogue of other people's anti-bot postures. It says nothing
  discreditable, and it is a more pointed thing to publish about a third party
  than "this person watches your job board". It also has no value to a reader.
- **The candidates list is a statement of intent about named companies**, which
  is a different kind of disclosure again from a list of sources already being
  watched.
- **The transcript archive must not be quoted into this repository.** It
  contains absolute paths, blocklist rows and real job titles. SP2's prompt says
  so explicitly.

So the split, which is the `.example` pattern this repository already uses
everywhere else: **the method is public, the lists are not.** This file carries
the packages, the prompts, the schemas, the decisions and the reasoning — all of
which is the genuinely useful part for anyone reading the repo — and refers to
`excluded_sources.yaml` and `candidate_sources.yaml` by name without ever
quoting a row. Both files stay gitignored under the existing deny-by-default
rule for `data/curated/`.

Two consequences to keep to:

- **SP5's prompt has `<OWNER FILLS IN NAMES AND URLS>` in it deliberately.** Fill
  it in in the chat, not in this file.
- **Result sections in this file name extractors and counts, not employers**, in
  the cases where an employer was rejected. "`smartrecruiters` returned a label
  as a location" is publishable. "Company X blocks scrapers" stays local.

What that leaves published is a plan whose most valuable content — capture
before you reason, recover before you reconstruct, re-scope a rule rather than
break it, automate two rungs and stop — is exactly the content that carries no
personal data at all.
