# Final audit

An independent read of the code as it exists on `main` at commit `500837a`,
after all twenty-four work packages. Written for a reader who does not read
code.

**This audit changed nothing.** No file was edited, no command was run that
writes to `data/`, and the working tree is clean. This document is the only
thing added.

## How it was checked

| What | Result |
|---|---|
| Full test suite (`pytest`) | **583 passed**, 0 failed, 0 skipped, 13.5 seconds |
| Code style gate (`ruff check`) | clean |
| Formatting gate (`ruff format --check`) | 90 files, all clean |
| `python -m job_scraper.run --help` | works |
| A real scrape (`--dry-run`, writes nothing) | 49 of 50 sources, 7,746 postings |
| Filter quality (`python -m job_scraper.eval`) | precision 0.341, recall 0.868 |
| The live database | read-only, 856 postings, opened with a read-only handle |

The scrape was run with `--dry-run`, which fetches and filters exactly as a
real run does and then throws the results away rather than saving them. That
was deliberate: it gives a true picture of the funnel without touching the
store, which is what the brief asked for and what the WP8b incident argues
for. The database and spreadsheet still carry their pre-audit timestamps.

---

## Nothing is urgent

There is no data-loss risk, no security problem, and nothing that needs
attention today. Every protection the plan says it built is genuinely in
place and still working. The findings below are improvements, not repairs.

The single most useful thing in this document is finding **A**: three of your
fifty sources have never worked, in any run, ever.

---

## The short version

The refactor did what it set out to do. All twenty-four packages are real —
each one verified by looking at the code, not by trusting the plan's own
account of itself. The plan file is unusually honest: where it says something
was done, it was done; where it records a mistake, the mistake is real and
the fix is there too.

The codebase is in good shape. The list of things worth doing is short, and
none of it is repair work.

---

## 1. Plan versus reality

**Every package is genuinely done.** I checked each one against the code
rather than against the plan's status table.

| Package | Claim | Verified in the code |
|---|---|---|
| WP0 / WP0c | Fixture capture + sanitiser | Both present, including WP11's whole-walk capture |
| WP1 | Safe writes, empty-scrape guard | Guard present and working (see §3) |
| WP2 | Test net, linting, CI | CI runs lint, format and tests |
| WP3 | Employer name from config | Present; the extractor still wins |
| WP4 | Database schema | All tables present, plus every later column |
| WP5 | Database becomes the real store | Old CSV store genuinely deleted, not hidden |
| WP5b / WP5c | Review commands, ranges | Present and working |
| WP6 | Job descriptions saved | Present |
| WP7 | Optional AI scoring | Present, correctly switched off |
| WP8a | Drop log | Present, 79,315 rows held |
| WP8c | Evaluation harness | Present; I ran it |
| WP8d / 8e / 8f / 8g | Location fixes | All four present |
| WP8 | Two filters deleted, keywords pruned | Both filters gone; 102 keywords, as claimed |
| WP8h / WP8i | Layer renumbering + guard | Present; I tested the guard by hand |
| WP8b | README rewrite | Accurate against the current commands |
| WP9 | Browser reuse, page caching | Both present; the run showed 4 browser starts |
| WP10 | Politeness, robots.txt, safety doors | All present; I tested the safety doors |
| WP11 | Pagination guards | Present; the old bespoke module is deleted |
| WP12 | Formatter | Gate is in both the checklist and CI |

**Nothing the plan claims is done is missing.** That is a genuinely unusual
result for a refactor this long, and worth saying plainly.

### Your three by-hand edits all landed

The plan left you three things to do yourself, and asks a later reader to
check rather than assume. All three are done:

- The seniority list has 23 entries and no longer contains `Architect`.
- Your contact details are filled in, so the scraper now identifies itself
  honestly to the sites it visits.
- The one source that had to be switched to browser-rendering mode after
  WP11 moved it to a different address has been switched.

### Two very small discrepancies

Neither is a defect, and neither needs action.

- **The quality score has drifted by a fraction.** The plan records the
  finished state as precision 0.343 and F2 0.664. Today the harness reports
  0.341 and 0.663 — a difference of one posting being kept. The recall figure
  (0.868), which is the one that matters, is exactly as recorded. The likely
  cause is a small later edit to your labelled set. Not worth chasing.
- **One helper script has no package of its own.** The script that refreshes
  the locations in your labelled set was written mid-flight during WP8 and is
  mentioned twice in the plan, but never got its own section. It works. It is
  simply undocumented compared with everything else.

### Unplanned additions, all recorded

Three modules exist that were not in the original plan: the pagination policy
module, the politeness/robots module, and the test-suite safety file that stops
tests from touching your real page cache. All three were added mid-refactor,
all three are recorded in the plan where they happened, and all three earn
their place.

---

## 2. Leftovers and dead weight

You asked me to check specific items. Here they are, plus what else I found.

### The things you asked about

**The old CSV store's helpers — gone, properly.** WP5 said it deleted them and
it did. The entire old storage file is gone, along with the one-off migration
tool. I checked the file system and the git history: nothing was quietly kept
"just in case". **Nothing to do.**

**`scripts/scrape_and_blocklist.sh` — keep it.** It is clearly marked as
retired, explains what replaced it and why, and prints the same warning when
run. It still works and destroys nothing. The plan says it stays until you
confirm the new flow suits you. That is your call, not a maintenance problem.
The same applies to its companion tool. **Leave alone until you are ready.**

**`data/curated/blocklist.csv` — must stay.** All 265 rows are safely in the
database, so the file is no longer *needed* to run anything. But it is the only
surviving record of what you had reviewed before the migration, and the WP8b
recovery used it as independent corroboration when reconstructing which
postings were unreviewed. That is exactly the situation where you would want it
again. **Keep as an archive.**

**`.example` files with no real twin — none.** All four template files have
their real counterpart present on your machine. **Nothing to do.**

**Docs contradicting the code — essentially none.** The README is accurate:
every command it documents exists, the test count it quotes (583) is right, and
it correctly describes the old CSV file as a frozen archive rather than a live
input. I found one stale sentence, in a comment inside the label-refresh
script, which says your labelled set "lives under version control". It does
not — it is deliberately excluded, precisely because it holds real job titles.
The advice the comment goes on to give is still correct. **Cosmetic.**

### What else I found

**Five orphaned files in `data/`.** Nothing in the current code writes or reads
these any more:

| File | Last touched | What it was |
|---|---|---|
| `jobs.csv` | 10 Aug | the pre-database store — the README calls it frozen |
| `drops.csv` | 18 Aug | a one-off export you asked for |
| `survivors.csv` | 18 Aug | a one-off export |
| `skipped_sources.csv` | 30 Jul | predates the current run summary |
| `skipped_sources.xlsx` | 30 Jul | the same, as a spreadsheet |

Only `jobs_sources.csv` is still written each run. All of these are on your
machine only — none is published — so they cost nothing but clutter. **Safe to
delete whenever you like, but check `jobs.csv` is genuinely redundant first;
it is your only pre-migration snapshot.** No rush.

**One dead line of code.** The database file defines a named list of "statuses
a person set, which automated passes must never overwrite" — and then never
uses it. The rule it describes *is* correctly enforced, but by spelling the
statuses out again inside the database queries rather than by referring to the
named list. So the list is decoration. This is a small example of the thing
CLAUDE.md warns about: two ways of saying one thing, where only one is
load-bearing. **Safe to delete, or better, wire it up.**

**Fossil files from before the repo was published.** Your project folder holds
leftover compiled artefacts for four modules that no longer exist, including
one (`verdicts`) that never appears anywhere in the published history at all —
it predates publication. These are invisible to git and harmless. **Safe to
delete; they will not come back.**

---

## 3. Regressions and new bugs

You asked whether the guarantees each package introduced still hold. I checked
each one specifically. **Five of six hold completely. The sixth has one gap.**

### Are all writes still safe? — Yes, where it matters

The rule is: never write directly over a live file; write a temporary copy
first, then swap it in.

- **The spreadsheet: safe.** It writes to a hidden temporary file alongside the
  real one, swaps it in atomically, and cleans up if anything goes wrong.
- **The database: safe.** Everything a run does is one single transaction. If
  the run crashes halfway, the database rolls back to exactly how it was
  before. A half-written run cannot exist.

Two files are still written the old, unprotected way: the per-run source list,
and the drop-log export you get when you ask for one by name. Neither holds
anything that cannot be regenerated by running the tool again, and neither is a
store. CLAUDE.md's rule is about live data files, and these are not. **Correct
as they stand.**

### Can a broken scrape still delete stored jobs? — No

This is the protection that matters most, and it is intact and layered:

1. A source that **errors** is never recorded as scraped, so its stored jobs
   accrue nothing and can never drift towards being marked gone.
2. A source that **returns nothing** logs a loud error naming it, and is also
   excluded — because an empty result is indistinguishable from a broken page
   reader.
3. Even a healthy scrape only marks a posting as gone after it has been
   **missing from two consecutive successful runs**.
4. Postings you have shortlisted or rejected keep their status regardless.

I confirmed this is live, not theoretical: the dry run hit three sources that
returned nothing, and all three produced the loud error and were correctly
excluded from delisting.

### Is anything ever permanently deleted? — No job, ever

I searched the whole codebase for deletion. There are exactly two, and neither
touches a posting:

- The drop log is trimmed to the last 10 runs. That is the intended retention
  window, and I confirmed it works: exactly 10 runs are held, oldest first out.
- The spreadsheet row-number map is replaced wholesale on each export. That is
  intended — it only ever describes the most recent spreadsheet.

**Postings are never deleted.** Rejected and gone-from-site postings both keep
their row and their full history. I verified this against your real database.

### Are your 265 original blocklist rows still there? — Yes, all of them

This was the most important single check, and the answer is good:

- **All 265 are present.** None is missing.
- **None has become 'rejected'.** The distinction WP5 went to trouble to
  preserve — that your old blocklist meant "already shown me", not "I said
  no" — is intact.
- 54 are still marked `seen`. The other 211 are marked `delisted`, meaning the
  posting has since come off the employer's careers page.

That last number looks alarming and is not. `delisted` is about the *job advert
disappearing from the website*, not about your opinion of it, and after several
months most of a 265-row batch of adverts has naturally expired. The rows are
all still there with their history. Nothing has been rewritten as a rejection.

### Are patterns built once rather than rebuilt for every job? — Yes

Every search pattern is either built once when the program starts, or built
once at the start of a batch and handed down. Nothing is rebuilt inside a loop.
The one exception is deliberate, measured and documented: the title-keyword
pattern is rebuilt twice per run at a cost of 0.275 milliseconds, and WP8
explicitly decided that threading it through was not worth saving a quarter of
a millisecond. That reasoning still holds.

### Does the hybrid-locations feature still work? — Yes, fully intact

Your deliberate feature — cities too far to commute to daily, allowed through
only when the role turns out to be hybrid — is complete and untouched. I traced
it end to end:

- The location check still recognises a hybrid-eligible city and holds the
  posting open rather than rejecting it.
- If the listing text already proves the role is hybrid, it is confirmed
  immediately with no extra page fetch.
- Otherwise it is settled later against the actual job description.
- Once confirmed, the answer is **saved to the database**, so the job is never
  re-checked on later runs.
- If the description cannot be read at all (a network failure), the posting is
  held back for that run but **deliberately not saved as rejected** — so a
  single timeout can never permanently lose it.

That last point is a genuinely careful piece of design and it is still there.
Your five hybrid-eligible cities and the keyword that gates them are all
configured and live. **No action, and nothing here should be removed.**

### The one gap: a maintenance tool applies stricter rules than the scraper

This is the only real code defect I found.

The scraper understands four kinds of location: a real city, a hybrid-eligible
city, a *placeholder* that names no real place, and a genuinely empty field.
The third kind — things like a bare region name — was added in WP8d, and a
posting with one is deliberately **held open** rather than rejected, so it can
be judged on its actual description.

The one-off `retrofilter` tool re-applies your filters to stored postings. It
was never updated to know about that third kind. So it judges a placeholder
location as "a city not on your list" and marks the posting **rejected** —
which is permanent by design.

I confirmed this by running both versions of the check side by side against
your real configuration: a posting whose location is a bare region name is
*kept* by the scraper and *permanently rejected* by the tool.

**The good news is that it is not currently biting.** The tool only touches
unreviewed postings, and I checked all 10 you have: none would be affected
today. It is a trap waiting rather than damage done. The fix is to pass the
tool one extra piece of configuration that already exists.

---

## 4. Consistency

The codebase is more consistent than a project of this history has any right to
be. Specifically:

- **No unused configuration.** Every setting in your rules file is read by the
  code, and every setting the code reads exists in your file. Same for your
  source list: five kinds of entry, all five used, none orphaned.
- **No leftover duplicate logic from the database migration.** Spreadsheet
  formulas are generated in exactly one place, as the rule requires.
- **No sources fetching pages behind the scraper's back.** WP8g and WP10 both
  fixed cases where a page reader bypassed the politeness rules. I confirmed
  the invariant holds today: no page reader reaches for the network on its own.
- **The status vocabulary is enforced, not just documented.** Every path that
  changes a posting's status checks it against the approved list first, and the
  database itself refuses an unapproved value as a backstop.

Three small inconsistencies, all cosmetic:

1. **The named "statuses a person set" list is unused** — described under §2.
   The rule is enforced, just not through the name meant to carry it.
2. **The run summary prints to the error channel, not the normal one.** If you
   ever redirect a run's output to a file the ordinary way, you get three
   trailing lines and not the funnel. Everything still appears on screen, so
   this only bites if you try to save the output.
3. **One comment describes a file as version-controlled when it is
   deliberately excluded** — described under §2.

---

## 5. Tests

**583 tests, all passing, none skipped, in 13.5 seconds.** No test is disabled,
marked as expected-to-fail, or left without assertions.

### No test was weakened to make a package pass

I looked for this specifically, because it is the failure mode a long refactor
invites. I did not find it. Where a test changed, the plan records why, and the
recorded reason matches the code:

- WP5 deleted 21 tests — but only together with the code they tested, and the
  properties worth keeping were moved to a new file first, not dropped.
- WP8 re-pinned the evaluation baseline after deleting two filters, and
  recorded the exact before-and-after numbers rather than just accepting the
  new ones.
- WP8f replaced a test that pinned the old rejecting behaviour with one that
  pins the new admitting behaviour — a deliberate change, recorded as such.
- WP10 added a test that *proves* the safety doors work by making every route
  to the database raise an error if reached.

**The fixture non-zero assertion is intact**, as you asked me to confirm. Every
saved page is still checked to parse to more than zero jobs, and every job is
checked to have a title. Alongside it, a second test refuses to let a newly
saved page be added without pinning its exact expected output. Both are
present and both pass.

### Where a real breakage would pass silently

This is the honest weak spot, and it is about *coverage*, not about test
quality.

**Fifteen of your twenty-seven page readers have no saved example page at
all.** They have no golden test and no parse check. If one of those breaks
tomorrow, the whole test suite still passes.

Counting by source rather than by reader, 21 of your 50 sources have a saved
page. The gap is smaller than that sounds, because many sources share one
reader — six of your sources use the same recruitment platform, and one saved
page covers the shared logic for all six. But fifteen readers with nothing at
all is still the real number.

This matters because of a lesson the plan itself learned the hard way. In WP8g,
four sources got a saved page for the first time, and **three of the four
turned out to be carrying a bug nobody had noticed** — one silently dropping a
quarter of its postings. The plan's own conclusion was: treat "this source has
no saved page" as an open question about correctness, not a chore.

**And it has happened again.** All three of the permanently-dead sources in
finding A are in the uncovered fifteen.

### A second, quieter gap

The golden tests are written to **skip** rather than fail if a saved page is
missing. That is sensible while pages are being captured one at a time, but it
means that deleting a saved page silently removes its coverage instead of
breaking the build. Today no test skips — I confirmed that — so this is a
latent risk rather than a live one.

---

## 6. Public repository safety

**This is the cleanest area of the audit. Nothing private has ever been
published.**

### Nothing private is tracked, and nothing ever was

I checked the entire history, not just today's files. Every one of these has
**zero commits, ever**:

your source list · your rules file · your profile · the database · the old
`jobs.csv` · the spreadsheet · your blocklist · your labelled set · your
candidate-source notes · your excluded-source list · the page cache · every
one-off export

The only similarly-named files in the history are small synthetic test files
built for the evaluation harness, which is correct.

### No credentials anywhere

I ran the project's own secret scan over all 43 saved pages: clean. I then ran
a considerably wider scan of my own — cloud provider keys, chat platform
tokens, code-hosting tokens, AI provider keys, payment keys, private key
blocks, web tokens, embedded passwords — across the saved pages **and** every
published file. **Zero hits.** Every mention of an API key in the code is the
*name* of the environment variable, never a value.

The only email addresses in published files are a third party's own public
company contact address inside a captured careers page, and a literal
placeholder. Your own address appears nowhere in anything published.

### Your `.gitignore` covers everything that now exists

I checked it against the actual contents of your machine. Every private file
present is matched. One small latent gap worth knowing about: the rules cover
databases ending `.sqlite3` and `.db`, but not a plain `.sqlite`. WP9
accidentally created exactly such a file once, and it holds the full text of
every page fetched. It was caught and renamed at the time. The gap that let it
through is still open. Adding one line would close it permanently.

### The plan file does name real places and employers

You asked specifically, and the plan file's own instructions tell you to check
this before pushing. It is not being met — but the true picture is more
nuanced than it first appears, and the conclusion is probably "leave it".

**Employers: not a meaningful disclosure.** The plan names about half of the
employers you follow. But your published code *already has to* name all fifty
of them — the file that maps a source to the right page reader lists every one,
and many are named in the filenames themselves. This is unavoidable given how
the project is built. The plan adds nothing that the code does not already
say.

**Locations: a real, if mild, disclosure — and wider than the plan file.** Your
target cities are configured in your private rules file, so nothing *requires*
them to be public. Six of them appear in the plan file. But eleven of your
twenty-seven location settings appear across published files in total, because
the **test suite uses your real cities as test data** — which is natural, since
the tests were written from real examples.

So cleaning the plan file alone would not close this. It would need a sweep of
the tests too, and the tests legitimately need place names in them.

What it actually reveals: that someone is job-hunting in a particular region of
Europe. Combined with the repository being under your own name, that is a
little personal — but it is not an address, not a credential, and not anything
that could be used against you. My honest advice is in §7.

### This document

`docs/AUDIT.md` is published like everything else, so it has been written to
name no city, no employer and no personal detail. Where a finding required
naming something, I have described it instead. The three broken sources are
named, because those names are already in the published code and you cannot act
on the finding without them.

---

## 7. What you should actually do

**Nothing must be fixed.** There is no data-loss risk, nothing is broken in a
way that costs you postings today, and no protection has been undone. If you
did nothing at all, the scraper would keep working correctly.

That said, one finding is worth your attention soon, because you are quietly
losing coverage you think you have.

### A. Three sources have never worked — not once, in nineteen runs

**Worth fixing · ~2 hours · Sonnet 5, `think`**

`gfi_europe`, `probably_good` and `lifesum` return zero postings every single
run, and always have. I checked every run in your database: they have **never**
returned a single row, and they have zero stored postings between them.

The safety net is doing its job — each one logs a loud error and correctly
refuses to delist anything. But the error is three lines among fifty, and it
has scrolled past you nineteen times. Six per cent of your sources are
decorative.

There is also a **blind spot in the health warnings** that explains why nothing
escalated. The warning fires when a source's count collapses *against its own
last successful run*. A source that has never worked has nothing to collapse
from, so it can never trigger the warning. A source that dies loudly on day one
stays quiet forever after.

Two pieces of work, and the order matters:

1. Save a copy of each of the three pages first, using the capture script. The
   plan's hardest-won lesson is that reasoning identifies the page layout
   correctly and gets the *data* wrong — do not skip this.
2. Add a warning for a source that returned zero rows *on this run*, regardless
   of history. That is a different question from "did it collapse?", and it is
   the one that would have caught all three.

Note that `lifesum` uses a page reader that already has six saved pages and
passing tests — so its page must differ from all six. That is worth knowing
before you start.

### B. The `retrofilter` tool can permanently reject postings the scraper keeps

**Worth fixing · ~15 minutes · Sonnet 5, no effort cue**

Described in §3. The tool is missing one piece of configuration that the
scraper passes, so it judges placeholder locations more harshly and marks them
rejected — which is permanent. Not currently affecting any of your 10
unreviewed postings, but it is a trap.

This is a one-line change plus a test. It is small enough to fold into whoever
touches that area next, but it is also small enough to just do.

### C. Close the fixture gap for the fifteen uncovered page readers

**Worth fixing · ~3 hours, or spread over several sittings · Sonnet 5, `think`**

Described in §5. Fifteen of twenty-seven readers have no saved page, so a
breakage in any of them passes the whole test suite. The plan's own track
record says roughly three in four uncovered readers are carrying an unnoticed
bug.

This does not need doing in one go, and it should not be. Capture a few pages,
pin them, fix what surfaces, repeat. Finding A is the first instalment.

While you are there: make the golden tests **fail** rather than skip when a
saved page is missing, so deleting one cannot quietly remove its coverage. That
part is fifteen minutes.

### D. Small tidying, all optional

**Worth fixing · ~30 minutes for all of it · Haiku 4.5 or Sonnet 5**

- Add one line to `.gitignore` covering databases ending `.sqlite`, closing the
  gap that once let a cache of fetched pages slip past the rules.
- Either use the unused "statuses a person set" list, or delete it.
- Fix the comment that calls your labelled set version-controlled.
- Delete the five orphaned files in `data/` — but check `jobs.csv` really is
  redundant first, since it is your only pre-migration snapshot.
- Delete the fossil compiled files from before the repo was published.

Do these as passengers on other work, not as a package of their own.

### E. Leave these alone

- **The retired shell script and its companion tool.** They work, they are
  clearly marked, they destroy nothing, and the plan says they stay until you
  say otherwise. Retiring them is a decision, not a fix.
- **Your old blocklist file.** Keep it. It is your only pre-migration record
  and it has already proved useful once during a recovery.
- **The two unprotected file writes.** Both are regenerable reports, not
  stores. The rule they appear to break is about live data files, and neither
  is one.
- **The hybrid-locations feature.** Working exactly as designed. Nothing here
  suggests touching it, and this audit does not propose removing it.
- **The `SEA` keyword.** The evaluation report now credits it with costing you
  two wanted jobs, which looks like a clear case for removing it. **It is
  not.** I measured it properly — removed the keyword, re-ran, compared — and
  the result is that *no posting is treated differently either way*: something
  further down catches both. This is exactly the attribution trap the plan
  warns about, and it caught my eye first too. Leave it, and do not re-propose
  removing it without a marginal measurement.
- **The summary printing to the error channel.** Only matters if you redirect
  output to a file, which you have no reason to do.
- **Place names in the plan file and tests.** This one is a genuine judgement
  call. Cleaning the plan file would be an afternoon and would not actually
  close the disclosure, because the tests carry the same names for good
  reasons. The information revealed is that someone is job-hunting in a
  particular region — mild, and not exploitable. **My advice is to accept it
  and update the plan file's own instruction to say so**, rather than leave a
  standing rule you are not meeting. An honest note is worth more than a rule
  everybody skips.

---

## A closing observation

The most valuable thing in this repository is not the scraper. It is the
decisions log in the plan file — particularly the entries recording things that
were tried and *rejected*, and the ones recording mistakes in full rather than
quietly correcting them.

Three times during this audit I found something that looked like a defect,
went looking for the reasoning, and found it already written down with the
measurement attached: the deliberately unprotected report files, the filter
ladder's ordering, and the `SEA` keyword. Each would otherwise have become a
finding in this document, and each would have been wrong.

That is the log doing precisely what it was built for. Keep writing it.
