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
| — | Rules | Postings outside your locations, or missing your keywords |
| 1a | Title keywords | Role types you never want (`design`, `sales`, …) |
| 1 | Seniority | Titles containing `Senior`, `Lead`, `Director`, … |
| 1c | Non-English text | Postings written in another language |
| 1b | Language-speaker | `"Dutch-speaking Account Manager"` and similar |
| 1d | Blocklist | Postings you have already rejected by hand |
| 2 | Detail page | Roles requiring 3+ years of experience, or a PhD |

The table is in execution order. The layer *labels* are historical — they record
the order the filters were added, not the order they run — which is why 1c comes
before 1b.

Layers 1a through 1d are pure text matching on data already fetched, so they are
effectively free. **Layer 2 is the only one that costs an extra HTTP request per
job** — it opens each posting's detail page to look for an experience
requirement. Postings already stored in `jobs.csv` skip layer 2 entirely, since
they passed it on an earlier run. Layer 1d runs before layer 2 for the same
reason: blocklisted jobs should never cost a request.

Layer 2 fails open — if a detail page can't be fetched or parsed, the job is kept
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

`requirements.txt` includes `pytest`, so this also sets you up to run the tests.

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
its time in the Playwright-rendered sources and the layer 2 detail fetches.

### Options

| Flag | Default |
| --- | --- |
| `--sources` | `job_scraper/config/sources.yaml` |
| `--rules` | `job_scraper/config/rules.json` |
| `--title-keywords` | `job_scraper/config/title_exclude_keywords.csv` |
| `--output-db` | `data/jobs.sqlite3` |
| `--output-xlsx` | `data/jobs.xlsx` |
| `--keep-drop-runs` | `10` |
| `-v`, `--verbose` | off |

Every default resolves relative to the package, not your shell's working
directory, so the command works from anywhere. `--help` prints the resolved
absolute paths.

### Reading the run summary

Each run ends with a funnel printed to stderr. This is a real first run against
the example config, starting from an empty table:

```
Sources           9 / 9 processed  (0 skipped)

Jobs seen (all pages, dupes incl.)         435
  − off-criteria (location/keywords)      −418   →    17 match your criteria
  − title keyword                          −13
  − senior-level title                      −0
  − non-English text                        −0
  − language-speaker                        −0   →     4 passed title filters
  − blocklisted (rejected)                  −0   →     4 after blocklist
      already in table (skipped)             0
      new, detail-checked                    4
  − needs 3+ yrs / PhD (0 PhD)              −0   →     4 new jobs kept
────────────────────────────────────────────────
New rows written                             4
Marked delisted                              0
Still listed this run                        4
Unreviewed jobs in table                     4
Exclusions logged                          431
```

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
stored jobs are left alone rather than being wrongly treated as delisted.

**"Exclusions logged"** is how many postings the filters dropped this run, each
recorded with the specific rule that dropped it. See below.

### Why was something dropped?

Filtering 8,000 postings down to a handful is only trustworthy if you can check
what went in the bin. Every exclusion is recorded — the job, the layer, and the
exact rule that fired, down to which keyword, which seniority term, which
language code, or which of the location cases:

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
case-insensitive substring, so `--rule locations` and `--layer 2` both work.
`--drops-csv PATH` writes the matching rows out for a spreadsheet.

Reading the log costs nothing and neither does writing it: it is built entirely
from titles and metadata already fetched during the run, and never opens a
detail page. The log keeps the last `--keep-drop-runs` runs (default 10) so it
cannot grow forever.

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
| `seniority_filter_enabled` | Turns layer 1 on or off. |
| `seniority_exclude_titles` | Whole-word matches against the title. `"Lead"` will not match `"Leadership"`. |

**Conditional locations.** For a city that is too far to commute to daily but
workable a couple of days a week, put it in `conditional_locations` instead of
`locations`. Such a job is admitted only if a `conditional_location_keywords`
term appears in its title *or its description*. Since extractors never see the
description, the check runs in two stages: the location filter admits the job
provisionally, and layer 2 — which already fetches the detail page — confirms it
against the body text, so no extra HTTP requests are made. Unlike the rest of
layer 2 this **fails closed**: if the description can't be fetched, the job is
dropped, because a conditional location is out of range by default. Jobs from
these cities are re-checked on every run rather than served from the table cache
(the provisional marker isn't stored in `jobs.csv`).

**Unresolvable locations.** Plenty of listing pages never name the duty station:
the field says `2 Locations`, `Multiple locations`, `Home base - EMEA`, or just a
country. That is not a city that failed to match your list — there is nothing on
the page to match — so judging it against `locations` throws the job away
unread. Such a field is instead admitted provisionally and settled by layer 2
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

### `data/curated/blocklist.csv`

Postings you have permanently rejected. They are filtered out of every future run
even though they are still live on the employer's site.

```csv
dedupe_key,source_name,company,title,detail_url
```

Only `dedupe_key` is read by the filter; the other four columns are there so the
file can be reviewed by hand. **Delete a line to un-block that posting.**

The blocklist is personal by nature, so this repo ships only a template:

```bash
cp data/curated/blocklist.example.csv data/curated/blocklist.csv
```

That step is optional — with no blocklist present the filter is simply a no-op,
and the maintenance commands below create the file when they first need it.

## Output files

Everything under `data/` is generated and gitignored. Deleting it costs you the
run history and the dedupe state, nothing else.

### `data/jobs.csv`

The store. Rows accumulate across runs and are deduplicated by canonical URL.

```csv
source_name,title,company,location,detail_hyperlink,apply_hyperlink,run_id
```

`detail_hyperlink` and `apply_hyperlink` hold `=HYPERLINK("…")` formulas so the
file is clickable when opened directly in Excel. `run_id` increments by one each
run, so it doubles as a "when did this first show up" marker. The file is sorted
by `source_name`, newest first within each source.

### `data/jobs.xlsx`

The one you actually read. Two sheets:

- **Jobs** — the postings you have not reviewed yet, and nothing else. Six
  columns: `#`, `source_name`, `title`, `location`, `detail_url`, `apply_url`,
  with a frozen header row, real clickable links, and rows from the **two most
  recent runs filled light green** so new postings stand out. The `#` column is
  the row number the review commands take (see below); it is the same number
  Excel shows down the left-hand side, and unlike Excel's it stays with its
  posting if you sort the table.
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

```bash
python -m job_scraper.tools.retrofilter
```

Re-applies the current filters to the existing `jobs.csv` without scraping. Run
this after editing `rules.json` or `title_exclude_keywords.csv` to clear out rows
that no longer qualify.

```bash
python -m job_scraper.tools.blocklist_all
```

**Deprecated** — this is `python -m job_scraper.review --seen-all` under an
older name. Still works, kept until the new review flow is confirmed.

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
routes `dynamic` sources through Playwright.

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

81 tests, no network access required.

## Scraping responsibly

This tool sends requests to other people's servers. Before pointing it at a new
site:

- Check the site's terms of service and `robots.txt`.
- **Edit the User-Agent** in `job_scraper/http.py` — it ships as a placeholder
  (`contact=you@example.com`). Putting a real contact address there is what lets
  an administrator reach you instead of just blocking you.
- Note that layer 2 fetches detail pages with 10 parallel workers
  (`_DETAIL_WORKERS` in `job_scraper/experience_filter.py`). Lower it if you add a
  lot of sources or a host starts rate-limiting you.
- Run it on a schedule measured in hours, not minutes. Job postings do not change
  that fast.

## Layout

| Path | What's in it |
| --- | --- |
| `job_scraper/` | `run.py` (CLI), `pipeline.py` (the run), the filter modules, `http.py`, `urlutil.py` |
| `job_scraper/config/` | your `sources.yaml` and `rules.json` (both gitignored, created from the `.example` files), plus `title_exclude_keywords.csv` |
| `job_scraper/extractors/` | one module per site or ATS platform, wired up in `registry.py` |
| `job_scraper/storage/` | CSV store (dedupe, schema migration) and the xlsx writer |
| `job_scraper/tools/` | maintenance commands |
| `data/` | generated output — safe to delete |
| `data/curated/` | hand-maintained, not regenerable: your `blocklist.csv` (gitignored; `blocklist.example.csv` is the template) |
| `scripts/` | shell automation |
| `tests/` | pytest suite |

## License

MIT — see [LICENSE](LICENSE).
