# Python-Job-Website-Scraper

Scrapes jobs from company career pages you specify — not commercial job boards —
filters the postings against your own keyword and location rules, and maintains a
spreadsheet of the ones worth looking at.

The workflow it's built for: you tell it which employers to watch and what you're
after, it checks them all on every run, and it only ever shows you postings you
haven't already seen and rejected. New postings are highlighted in green.

## How it works

Each run fetches every source in `sources.yaml`, hands the HTML or JSON to that
source's extractor, and pushes the resulting postings through a series of
filters:

| Layer | Filter | Drops |
| --- | --- | --- |
| 1 | Location and rules | Postings outside your locations, or missing your keywords |
| 2 | Title keywords | Role types you never want (`design`, `sales`, …) |
| 3 | Seniority | Titles containing `Senior`, `Lead`, `Director`, … |
| 4 | Review status | Postings you have already rejected |
| 5 | Detail page | Roles requiring 3+ years of experience, or a PhD |

The numbers are each layer's position in execution order, and they are what the
run summary and the drop log print. The log *stores* an older set of layer ids
recording the order the filters were added, which is why `--layer` takes a name
rather than one of these numbers — see
[Why was something dropped?](#why-was-something-dropped).

Layers 1 to 4 are pure text matching on data already fetched, so they are
effectively free. **Layer 5 is the only one that costs an extra HTTP request per
job** — it opens each posting's detail page to look for an experience
requirement. Postings already in the store skip layer 5 entirely, since they
passed it on an earlier run. Layer 4 runs before layer 5 for the same reason: a
posting you have already rejected should never cost a request.

Layer 5 fails open — if a detail page can't be fetched or parsed, the job is kept
rather than silently dropped.

## Requirements

- Python 3.10 or newer (developed on 3.13)
- macOS or Linux
- ~400 MB of disk for the headless Chromium that Playwright downloads

Playwright is only needed for sources marked `strategy: dynamic` (one of the nine
in the example config). Drop those and you can skip the browser download.

## Setup

From the project root:

```bash
python3 -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

```bash
playwright install chromium
```

Then create your own config from the shipped examples — the scraper will not run
until you do, and will tell you so:

```bash
cp job_scraper/config/sources.example.yaml job_scraper/config/sources.yaml
```

```bash
cp job_scraper/config/rules.example.json job_scraper/config/rules.json
```

Both are gitignored, so your personal search never ends up in a commit. Edit them
freely — see [Input files](#input-files).

`requirements.txt` includes `pytest` and `ruff`, so this also sets you up to run
the tests and the linter.

## Running

With the virtualenv activated:

```bash
python -m job_scraper.run
```

Or without activating it:

```bash
.venv/bin/python -m job_scraper.run
```

Add `-v` for per-source debug logging, including how many jobs each filter layer
dropped.

The nine example sources take well under a minute. A larger config spends most of
its time in the Playwright-rendered sources and the layer 5 detail fetches. A
second run inside the cache TTL is much faster, because the plain-HTTP pages come
off disk rather than the network — on a 50-source config, 365s cold against 132s
warm. Rendered pages are never cached, so they cost the same every time.

A run holds up to four headless Chromiums in memory from the first `dynamic`
source until it finishes, rather than starting one per page — about 600 MB. That
is what makes the rendered sources faster; if it is too much for your machine,
`_RENDER_WORKERS` in `job_scraper/http.py` is the number to lower.

### Options

| Flag | Default |
| --- | --- |
| `--sources` | `job_scraper/config/sources.yaml` |
| `--rules` | `job_scraper/config/rules.json` |
| `--title-keywords` | `job_scraper/config/title_exclude_keywords.csv` |
| `--output-db` | `data/jobs.sqlite3` |
| `--output-xlsx` | `data/jobs.xlsx` |
| `--delist-after` | `2` |
| `--allow-empty-delist` | off |
| `--keep-drop-runs` | `10` |
| `--no-cache` | off |
| `--cache-ttl` | `1800` (30 minutes) |
| `--score` | off |
| `--dry-run` | off |
| `--show-all` | off |
| `-v`, `--verbose` | off |

Every default resolves relative to the package, not your shell's working
directory, so the command works from anywhere. `--help` prints the resolved
absolute paths.

The seven that are not paths:

- `--delist-after` — how many consecutive successful runs a stored posting must
  go unseen before it is marked delisted. See below.
- `--allow-empty-delist` — a source that returns zero rows is treated as a
  broken selector by default, so its stored postings are left alone. This flag
  delists them instead. Off for a reason: a bad selector would otherwise erase
  real history.
- `--no-cache` — refetch every page instead of reading any of it from the
  response cache. Every plain-HTTP page is cached for half an hour by default —
  listing pages and the job detail pages layer 5 reads — so two runs in quick
  succession mostly read from disk; pass this when a run has to see the sites as
  they are this second. Pages rendered through Playwright are never cached.
- `--cache-ttl` — how many seconds a cached page counts as fresh. After that the
  page is not thrown away: the next run asks the site "has this changed since?"
  and only downloads it again if the answer is yes. `--cache-ttl 0` is not the
  same as `--no-cache` — it means nothing is ever fresh, so every page is
  checked with the site on every run, but an unchanged one still costs a reply
  rather than a download. Use `--no-cache` to switch the cache off.
- `--score` — force the optional LLM scoring stage on for this run, overriding
  `scoring_enabled` in `rules.json`. It scores stored descriptions against your
  own rubric in `job_scraper/config/profile.md` (copy `profile.example.md`),
  reads the API key from `ANTHROPIC_API_KEY`, and costs API credits — the run
  summary prints the estimate.
- `--dry-run` — fetch and filter exactly as usual, then write nothing: the store
  transaction is rolled back, and no spreadsheet, sources csv or drop-log row is
  produced. The run summary it prints is the one a real run would have printed,
  which is what makes it useful for seeing what a `rules.json` change would do
  before it touches the table. Scoring is skipped too, since its verdicts would
  be discarded and its tokens would not.
- `--show-all` — put every posting on the xlsx review sheet, not just the
  unreviewed ones.

### Reading the run summary

Each run ends with a funnel printed to stderr. The layout below is the real
thing — it was rendered by the summary code itself — but the counts are
illustrative rather than from any one run, since a real store's numbers are
personal. Yours will differ; the shape will not:

```
Run summary
────────────────────────────────────────────────────
Sources           30 / 30 processed  (0 skipped)

Jobs seen (all pages, dupes incl.)             8,000
  L1  − off-criteria (location/keywords)      −2,057   → 5,943 match your criteria
  L2  − title keyword                         −1,450
  L3  − senior-level title                      −520   → 3,973 passed title filters
  L4  − blocklisted (rejected)                −3,800   →   173 after blocklist
        already in table (skipped)               140
        stored, hybrid recheck                     3
        new, detail-checked                       30
  L5  − needs 3+ yrs / PhD (1 PhD)                −6
  L5  − non-hybrid (distant city)                 −2
  L5  − location unresolvable in the text         −5   →    20 new jobs kept
────────────────────────────────────────────────────
New rows written                                  20
Marked delisted                                    4
Still listed this run                            620
Unreviewed jobs in table                          20
Exclusions logged                              7,840
```

The `L1`–`L5` gutter is the filter ladder from [How it works](#how-it-works);
the three `L5` lines are the detail-page layer's three ways of dropping a job.
Each `→` carries the running total forward, so you can read down the right-hand
side to see what survived each stage. The three indented lines are not drops —
they split the postings that reached layer 5 into the ones it could skip and the
ones that cost a fetch.

**"Still listed this run" and "Unreviewed jobs in table" are different questions,
and they diverge.** The first counts every stored job this run found still on its
source's career page, whatever you have already decided about it; the second counts
only the ones you have not looked at yet. A run that reports 102 still listed and 2
unreviewed is a completely normal run — 100 of those are postings you reviewed
weeks ago that simply have not been filled yet.

**"Marked delisted"** counts jobs whose posting no longer appears on its source's
career page — filled or withdrawn — after it has been missing for
`--delist-after` consecutive successful scrapes. Nothing is deleted: the row
keeps its history and moves to the archive sheet. This only happens for sources
that were scraped successfully; if a source errors out, or returns zero rows, its
stored jobs are left alone rather than being wrongly treated as delisted — a
zero-row source is usually a broken selector, and `--allow-empty-delist` is what
overrides that judgement.

**"Exclusions logged"** is how many postings the filters dropped this run, each
recorded with the specific rule that dropped it. See below.

**Source health warnings** appear under the funnel, in a block of their own, and
only when there is something to say:

```
────────────────────────────────────────────────────
!  Source health: 1 source returned far fewer rows than last time
!  impactpool: 4 rows this run, was 120 (-97%)
```

A source that breaks loudly already fails and is counted as skipped. This is the
quieter failure: a selector that still matches *something* returns a short list,
nothing errors, and the missing postings are simply never seen. Each source's
row count is compared against its own last **successful** scrape — not against
the last run, so recovering from an outage is not reported as a collapse — and
anything that lost more than half is named here. The `!` marker is deliberately
not an `L` gutter: a shrinking source is not a filter that fired, and it should
never be read as one.

### Why was something dropped?

Filtering 8,000 postings down to a handful is only trustworthy if you can check
what went in the bin. Every exclusion is recorded — the job, the layer, and the
exact rule that fired, down to which keyword, which seniority term, or which of
the location cases:

```bash
python -m job_scraper.drops
```

That prints the last run's exclusions grouped by rule, most frequent first — the
quickest way to see whether one over-eager keyword is costing you more than it
saves, and to tell whether a rule change helped. To see the individual postings:

```bash
python -m job_scraper.drops --show-drops
```

`--layer`, `--rule` and `--source` narrow either view; they match on a
case-insensitive substring, so `--rule locations` and `--layer seniority` both
work. `--drops-csv PATH` writes the matching rows out for a spreadsheet.

`--layer` matches the layer ids the log *stores*, never the display numbers the
reports print, so a bare number is refused rather than answered:

```
$ python -m job_scraper.drops --layer 3
--layer 3 is a display number, not a stored layer name. Did you mean --layer seniority? (layer 3 is stored as '1-seniority')
```

`python -m job_scraper.drops --help` lists all five pairs. The location cases
are named by their rule rather than their layer, so narrowing to those is
`--rule locations`, not `--layer locations`.

Reading the log costs nothing and neither does writing it: it is built entirely
from titles and metadata already fetched during the run, and never opens a
detail page. The log keeps the last `--keep-drop-runs` runs (default 10) so it
cannot grow forever.

### Would loosening that rule help?

The drop log says what a rule *did*. Whether changing it would be an improvement
is a different question, and guessing at it is how a filter ladder quietly stops
matching what you want. Label a set of postings by hand and replay the ladder
over them:

```bash
python -m job_scraper.eval
```

Read-only and entirely offline — it opens no detail page and makes no HTTP
request of any kind. It reads a gold set from `data/curated/labels.csv` (columns
`dedupe_key, title, company, source_name, location, label`, where `label` is
`review` or `discard`; the file is yours to build and is gitignored), replays the
same filter functions the pipeline uses, and
reports precision, recall and F-beta (weighted towards recall by default), a
confusion matrix, a per-layer table, and **every posting you wanted that a rule
dropped**, named, with the rule that killed it.

```bash
python -m job_scraper.eval --compare job_scraper/config /path/to/edited/config
```

Scores two config directories and diffs them: which postings change side, which
rule used to fire, and the precision/recall delta. The workflow is to copy
`job_scraper/config/`, edit the copy, and compare — never to edit the live rules
and hope you remember the old numbers.

**The "rules that cost wanted jobs" table reports attribution, not marginal
cost.** A rule is credited with a drop when it is the *first* one to match, so
removing it changes nothing at all if something further down the ladder catches
the same posting anyway. Measured properly — remove one keyword, re-run, diff —
only 38 of the 112 keywords on the pre-prune list changed any verdict, and three
of the ones the table named as expensive changed none. Two rules can also mask
each other exactly, so that removing either one alone measures zero and removing
both measures the real cost. **Never prune straight from the printed table.**
Remove the rule, re-run, and diff.

Layers 4 and 5 are not replayed — one is review history rather than a rule, the
other needs a detail page the harness will not fetch — so the recall it reports
is an upper bound.

## Input files

### `job_scraper/config/sources.yaml`

The list of career pages to scrape. Gitignored — copy
`sources.example.yaml` to create it.

```yaml
sources:
  - name: canonical
    url: https://job-boards.greenhouse.io/canonical
    strategy: static
  - name: slack
    url: https://salesforce.wd12.myworkdayjobs.com/Slack
    strategy: dynamic
```

| Field | Meaning |
| --- | --- |
| `name` | Identifier for the source. **Must match a key in `job_scraper/extractors/registry.py`** — a source with no registered extractor is skipped with a log line. |
| `url` | The career page or job board to fetch. |
| `strategy` | `static` for plain HTTP, `dynamic` to render the page in headless Chromium first. Use `dynamic` when the jobs only appear after JavaScript runs. |

The example ships nine entries chosen to exercise a different extractor each, so
there's one working example per supported ATS.

### `job_scraper/config/rules.json`

What counts as a job you want. Gitignored — copy `rules.example.json` to create
it.

```json
{
  "include_keywords": [],
  "exclude_keywords": [],
  "locations": ["Berlin", "Amsterdam", "London"],
  "conditional_locations": ["Munich", "Paris"],
  "conditional_location_keywords": ["hybrid"],
  "remote_keywords": ["remote", "anywhere"],
  "non_place_locations": ["EMEA", "Worldwide", "home base", "Sweden"],
  "match_in": "title_and_description",
  "seniority_filter_enabled": true,
  "seniority_exclude_titles": ["Senior", "Lead", "Director"]
}
```

| Key | Meaning |
| --- | --- |
| `include_keywords` | A job must contain at least one of these. Empty list = no keyword requirement. |
| `exclude_keywords` | A job containing any of these is rejected outright. |
| `locations` | A job's `location` field must contain one of these. Empty list = no location requirement. |
| `conditional_locations` | Cities admitted **only** for hybrid roles — see below. Empty list = feature off. |
| `conditional_location_keywords` | What makes a `conditional_locations` job qualify. Matched as word prefixes, so `hybrid` also covers `hybridarbete`. Empty list makes `conditional_locations` inert. |
| `remote_keywords` | Words that mark a job as location-independent — see the caveat below. |
| `non_place_locations` | Regions and bare country names that name no specific place — see below. Empty or absent = only the shapes recognised in code. |
| `match_in` | `title_and_description` (title, snippet, department and location) or `title_only`. |
| `seniority_filter_enabled` | Turns layer 3 on or off. |
| `seniority_exclude_titles` | Whole-word matches against the title. `"Lead"` will not match `"Leadership"`. |

**Conditional locations.** For a city that is too far to commute to daily but
workable a couple of days a week, put it in `conditional_locations` instead of
`locations`. Such a job is admitted only if a `conditional_location_keywords`
term appears in its title *or its description*. Since extractors never see the
description, the check runs in two stages: the location filter admits the job
provisionally, and layer 5 — which already fetches the detail page — confirms it
against the body text, so no extra HTTP requests are made. Unlike the rest of
layer 5 this **fails closed**: if the description can't be fetched, the job is
dropped, because a conditional location is out of range by default. A
confirmation earned from a detail page is stored on the posting's own row, so a
confirmed posting is skipped on later runs like any other stored one; only a
conditional-city posting that has never been confirmed is re-checked.

**Unresolvable locations.** Plenty of listing pages never name the duty station:
the field says `2 Locations`, `Multiple locations`, `Home base - EMEA`, or just a
country. That is not a city that failed to match your list — there is nothing on
the page to match — so judging it against `locations` throws the job away
unread. Such a field is instead admitted provisionally and settled by layer 5
against the fetched description, exactly as a conditional location is, and it
**fails closed** in the same way: if the description names none of your
`locations`, the job is dropped. The `"N locations"` and `"Multiple locations"`
shapes and the home-base wording (`Home base`, `Home based`, `home-based`) are
recognised without configuration; `non_place_locations` is where you add the
regions and countries no code list could guess. A value that combines the two,
like `Home base - EMEA`, needs `EMEA` in the list — the wording alone is not
enough, because what is left over is still a name this filter has to judge. Terms are matched whole-word and case-insensitively against each
segment of the field, and a segment still counts as a place if anything is left
once they are struck out — `Barcelona, Spain` is Barcelona, not a placeholder.

The price is a detail fetch for jobs that used to cost nothing, mostly on the
first run after switching it on: a job dropped this way is stored as rejected
and skipped thereafter, so the load falls back to newly posted jobs.

**The remote caveat.** A `remote_keyword` only admits a job when its location
field names no specific city. Some job boards tag every single posting
`Remote | <duty station>`, so treating "remote" as "location doesn't matter" would
let the entire board through. `Remote` and `Remote | Home Based` pass;
`Remote | Nairobi` is rejected, because Nairobi is a real duty station and it
isn't in your `locations`.

### `job_scraper/config/title_exclude_keywords.csv`

Role types to drop on sight, matched against the title only.

```csv
keyword,match
design,prefix
tax,word
```

| `match` | Behaviour |
| --- | --- |
| `word` | Whole word. `sales` drops "Sales Manager" but not "Salesforce Admin". |
| `prefix` | Word start. `design` drops "Designer" and "Design Lead". |

Anything other than `prefix` is treated as `word`.

### `data/curated/blocklist.csv` — legacy, not read

**Nothing reads this file any more.** A posting you have rejected is recorded on
its own row in the store, and layer 4 keeps it out of every future run from
there — see [Reviewing what it found](#reviewing-what-it-found). This CSV is how
that was recorded before the store existed, and it is kept only so an existing
one can be carried across, once:

```bash
python -m job_scraper.tools.import_blocklist
```

The routine that wrote it blocklisted *every* posting it surfaced, so a row here
means "already seen" rather than "rejected", and that is the status each row is
imported as. The file is only ever read, never modified, and re-running the
import changes nothing once the rows are in. With no such file, skip this
entirely — a fresh setup has nothing to import.

## Output files

Everything under `data/` is generated and gitignored, apart from `data/curated/`,
which is hand-maintained. Deleting `data/curated/` costs you nothing the scraper
needs; deleting the rest costs you the run history, the dedupe state and every
review decision you have recorded.

### `data/jobs.sqlite3`

The store, and the one file here that nothing can regenerate. It holds one row
per posting, keyed on a canonical form of its URL, plus the run history, each
source's health per run, the drop log, and the row numbers of the last export.

Nothing is ever deleted from it. A posting you reject keeps its row and its
history, and a posting that has disappeared from its career page is marked
`delisted` rather than removed — that is what lets the archive sheet below show
you everything the scraper has ever seen.

### `data/jobs.csv`

Frozen. This is the pre-SQLite store, left behind by the cutover; nothing reads
or writes it any more. If you have one, it is an archive of what the scraper
knew before the migration, and it is safe to delete once you are satisfied the
migration carried everything across. A fresh setup never creates it.

### `data/jobs.xlsx`

The one you actually read. Two sheets:

- **Jobs** — the postings you have not reviewed yet, and nothing else:
  `#`, `source_name`, `title`, `location`, `score`, `score_reasoning`,
  `score_flags`, `detail_url` and `apply_url`, with a frozen header row, real
  clickable links, and rows from the **two most recent runs filled light green**
  so new postings stand out. The three `score` columns stay empty unless you
  run the optional scoring stage; when they are filled, the sheet is sorted
  best score first. The `#` column is the row number the review commands take
  (see below); it is the same number Excel shows down the left-hand side, and
  unlike Excel's it stays with its posting if you sort the table.
- **Archive** — every posting ever stored, whatever its status, with
  `first_seen` and `last_seen`. Nothing is hidden from you: a posting that has
  left the Jobs sheet is here.

`python -m job_scraper.run --show-all` puts every posting on the Jobs sheet too,
with a `status` column, when you want to review something you have already dealt
with.

Regenerated from scratch on every run.

### `data/jobs_sources.csv`

```csv
source_name,listing_url
```

Which sources were scraped successfully this run. Useful for spotting a source
that has quietly started failing — compare it against `sources.yaml`.

## Reviewing what it found

Open `data/jobs.xlsx`, read the Jobs sheet, then record what you decided. Every
command below takes the row numbers from the sheet's `#` column, and none of
them deletes anything — a reviewed posting keeps its row and its history in the
store, it just stops being offered to you.

```bash
python -m job_scraper.review --seen-all
```

"I have read all of these." The next run's Jobs sheet then shows only postings
stored after this point. This is the command that replaced
`scripts/scrape_and_blocklist.sh`.

```bash
python -m job_scraper.review --shortlist 4 7 --reject 5 9-12
```

Record a decision on individual rows. Ranges like `9-12` are inclusive and mix
freely with single numbers. Each row acted on is echoed back with its title, so
a mistyped number is visible immediately; if any number is not on the current
sheet, nothing at all is applied.

```bash
python -m job_scraper.review --shortlist 4 --reject-all
```

The two-decision workflow: shortlist what you want, reject everything else on
the sheet in one go. `--reject-all` only ever touches unreviewed postings, so
decisions recorded earlier are safe. Note that rejection is permanent by design
— nothing automatic brings a rejected posting back — so if a posting might
interest you later, `--seen-all` is the gentler sweep.

All of these regenerate `jobs.xlsx` afterwards, which **renumbers the rows** — reopen the
file before using row numbers again, or pass `--no-export` to keep the numbers
you are reading valid across several commands. `--show-all` regenerates it with
every posting on the Jobs sheet.

## Maintenance commands

**These two take no flags, but they now read their arguments.** Until WP10 they
had no argument parser, so anything typed after the module name — `--help`
included — was ignored and the command ran immediately against
`data/jobs.sqlite3`; that is how one `blocklist_all --help` lost the record of
which postings were unreviewed. Both now have a front door with no options in
it: `--help` prints what the command does and exits, and anything else it does
not recognise exits non-zero having changed nothing. Neither ever deletes a row,
but both change review state, so read before running.

```bash
python -m job_scraper.tools.retrofilter
```

Re-applies the current filters to the unreviewed postings already in the store,
without scraping. Run it after editing `rules.json` or
`title_exclude_keywords.csv` to clear out rows that no longer qualify. Failing
rows are marked `rejected`, never deleted, and postings you have already
reviewed are left alone — a rule change must not silently rewrite a decision you
made. `jobs.xlsx` is regenerated afterwards.

```bash
python -m job_scraper.tools.blocklist_all
```

**Deprecated** — this is `python -m job_scraper.review --seen-all` under an
older name: it marks every unreviewed posting as seen and regenerates
`jobs.xlsx`, which clears the review sheet. Still works, kept until the new
review flow is confirmed. Prefer `review --seen-all`.

```bash
bash scripts/scrape_and_blocklist.sh
```

**Deprecated.** Scrapes, then immediately marks everything the scrape found as
seen — before you have looked at it, so a run you never opened is
indistinguishable from one you reviewed. Use `python -m job_scraper.run`
followed by `python -m job_scraper.review --seen-all` once you have actually
read the sheet.

## Adding a source

Three steps.

**1. Write an extractor** in `job_scraper/extractors/`. The contract is:

```python
def extract(
    listing_url: str,
    fetch_text: Callable[[str], str],
    source_name: str,
) -> list[dict[str, Any]]:
```

Return one dict per posting with these keys: `source_name`, `title`, `location`,
`department`, `listing_url`, `detail_url`, `apply_url`, `raw_snippet`. Use the
`fetch_text` you were handed rather than calling `requests` yourself — it is what
routes `dynamic` sources through Playwright, and what puts plain-HTTP fetches
through the response cache.

`greenhouse.py` is the shortest example at 46 lines (it hits the public Greenhouse
API instead of parsing HTML). `teamtailor.py` is a representative HTML-parsing
one.

**2. Register it** in `job_scraper/extractors/registry.py`:

```python
"my_employer": partial(greenhouse.extract, source_name="my_employer"),
```

Many employers share an ATS — Greenhouse, Lever, Ashby, Workable, Teamtailor,
Personio, SmartRecruiters, Workday, Breezy and SuccessFactors all have generic
extractors already, so a new employer on one of those needs no new code, just a
registry line.

**3. Add it to `sources.yaml`** with a matching `name`.

## Tests

```bash
python -m pytest -q
```

491 tests, no network access required.

## Scraping responsibly

This tool sends requests to other people's servers. Before pointing it at a new
site:

- **Fill in `contact_url` and `contact_email` in `rules.json`.** They become the
  User-Agent every request carries, which is what lets an administrator reach
  you instead of just blocking you. They live in `rules.json` rather than in the
  code because `rules.json` is gitignored, so your address stays out of a public
  repo. Left empty, every run warns and identifies itself as
  `job-scraper/0.1 (no contact configured)`.
- Check the site's terms of service. `robots.txt` is checked for you: each run
  reads it once per host and skips a source the site disallows, saying so. Where
  that answer is wrong for us — a blanket rule aimed at search engines, on a
  careers page the employer publishes and links to — set `ignore_robots` on that
  source in `sources.yaml`: `true` for the source's own host, or a list of hosts
  where the extractor reads from more than one (SmartRecruiters fetches its
  postings from `api.smartrecruiters.com`, not from the careers host in your
  config). Either way it exempts whole hosts, so it is a judgement about a site,
  not about one page. The refusal message names the host you need to list.
- Requests are capped at **two at a time per host, a second apart**
  (`DEFAULT_PER_HOST_REQUESTS` and `DEFAULT_HOST_DELAY` in
  `job_scraper/http.py`), and a site that states its own longer `Crawl-delay`
  gets it. Layer 5's ten detail workers (`_DETAIL_WORKERS` in
  `job_scraper/experience_filter.py`) are a cap on this tool, not on any one
  site: they mean ten different employers in parallel, not ten requests landing
  on one.
- Run it on a schedule measured in hours, not minutes. Job postings do not change
  that fast.

## Layout

| Path | What's in it |
| --- | --- |
| `job_scraper/` | `run.py` (CLI), `pipeline.py` (the run), the filter modules, `http.py`, `urlutil.py`, plus the commands you run between scrapes: `review.py`, `drops.py`, `eval.py`, and `scoring.py` for the optional LLM stage |
| `job_scraper/config/` | your `sources.yaml`, `rules.json` and (for scoring) `profile.md` — all gitignored, created from the `.example` files — plus `title_exclude_keywords.csv` |
| `job_scraper/extractors/` | one module per site or ATS platform, wired up in `registry.py` |
| `job_scraper/storage/` | the SQLite job store (`db.py`) and the xlsx writer |
| `job_scraper/tools/` | maintenance commands |
| `data/` | generated output: `jobs.sqlite3` (the store), `jobs.xlsx`, `jobs_sources.csv`. All regenerable from a scrape except the store's own review history |
| `data/curated/` | hand-maintained, not regenerable and all gitignored: the `labels.csv` gold set the eval harness reads, and the legacy `blocklist.csv` (`blocklist.example.csv` is the tracked template) |
| `scripts/` | the deprecated scrape-and-blocklist wrapper, and the fixture-capture helper the tests are built from |
| `tests/` | pytest suite |

## License

MIT — see [LICENSE](LICENSE).
