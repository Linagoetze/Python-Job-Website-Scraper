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
| `--output` | `data/jobs.csv` |
| `--output-xlsx` | `data/jobs.xlsx` |
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
Delisted removed                             0
Jobs now in table                            4
```

**"New rows written" can exceed "Jobs now in table".** That is not an error. A few
sources re-advertise the same posting under different URLs, so after the new rows
are written a content-based pass collapses duplicates that share an employer and
title, keeping the most recent. The written count is tallied before that pass.

**"Delisted removed"** counts rows dropped because the posting no longer appears
on its source's career page — it was filled or withdrawn. This only happens for
sources that were scraped successfully on that run; if a source errors out, its
stored rows are left alone rather than being wrongly treated as delisted.

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
  "remote_keywords": ["remote", "anywhere"],
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
| `remote_keywords` | Words that mark a job as location-independent — see the caveat below. |
| `match_in` | `title_and_description` (title, snippet, department and location) or `title_only`. |
| `seniority_filter_enabled` | Turns layer 1 on or off. |
| `seniority_exclude_titles` | Whole-word matches against the title. `"Lead"` will not match `"Leadership"`. |

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

The one you actually read. Five columns — `source_name`, `title`, `location`,
`detail_hyperlink`, `apply_hyperlink` — with a frozen header row, real clickable
links, and rows from the **two most recent runs filled light green** so new
postings stand out.

Regenerated from scratch on every run.

### `data/jobs_sources.csv`

```csv
source_name,listing_url
```

Which sources were scraped successfully this run. Useful for spotting a source
that has quietly started failing — compare it against `sources.yaml`.

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

Moves **every** job currently in `jobs.csv` into the blocklist and empties the
table. This is the "I've reviewed all of these and I'm not interested in any of
them" button.

```bash
bash scripts/scrape_and_blocklist.sh
```

Scrapes, then immediately blocklists everything the scrape found. **This leaves
you with an empty table** — it is for establishing a baseline on first setup, so
that subsequent runs only ever show genuinely new postings. Don't run it
expecting to see results.

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
