# Optional plan

Packages moved out of `docs/SOURCES-PLAN.md` on 2026-10-26 (owner's decision,
recorded in `docs/DECISIONS.md`). None of them is needed to keep the scraper's
data safe or to add more companies: each has a workaround, or serves a single
employer. They keep their prompts so that a session can pick one up without
re-deriving it. Read this file alongside `CLAUDE.md` and `docs/SOURCES-PLAN.md`,
whose shared rules, decisions log and "Publishing this file" section apply here
unchanged: **no candidate's name or URL goes in this file**.

## Status

| OP | Title | Time | Model | Effort cue | Status | Branch | Was |
|----|-------|------|-------|-----------|--------|--------|-----|
| 1 | The probe's strategy for Workable, and what its "no postings" means | 1.5 hr (2 with the exemption flag) | Sonnet 5 | `think` | not started | `op1-probe-corrections` | SP14 |
| 2 | `candidate activate` for a board hosted elsewhere | 1 hr | Sonnet 5 | `think` | not started; owner-approved 2026-10-07 | `op2-activate-board` | SP17 |
| 3 | A generic reader for Taleo career sites | 3 hr | Opus 5 | `think hard` | not started | `op3-taleo-reader` | SP16 |
| 4 | A per-source `enabled: false` | — | — | — | proposed, no prompt yet | — | SP12's proposal |

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

Read CLAUDE.md, docs/DECISIONS.md, docs/SOURCES-PLAN.md and
docs/OPTIONAL-PLAN.md, then work on OP1
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

Read CLAUDE.md, docs/DECISIONS.md, docs/SOURCES-PLAN.md and
docs/OPTIONAL-PLAN.md, then work on OP2
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

Read CLAUDE.md, docs/DECISIONS.md, docs/SOURCES-PLAN.md and
docs/OPTIONAL-PLAN.md, then work on OP3
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
