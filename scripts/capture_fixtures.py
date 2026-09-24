"""Capture real career-page responses as test fixtures.

Saves the response the source's *extractor* actually consumes, not whatever
happens to live at the listing URL. Several extractors (Greenhouse, Lever,
SmartRecruiters) ignore the listing page entirely and call a JSON API instead,
and paginating extractors ask for `?page=1` or `?startrow=0` rather than the
bare URL. Rather than duplicating that URL-building here, the capture runs the
real extractor with a recording fetcher and keeps the first response it asks
for; see `capture_one`.

Captured HTML is sanitised before it is written: third-party inline scripts
(analytics, front-end config) are stripped, and only scripts carrying a job-data
payload are kept. See `sanitise_html` for why that distinction matters.

Most sources are captured as a single response, which is all their extractor
reads before the first page's jobs are in hand. A paginated source is different:
its extractor keeps asking until the listing runs out, and a walk replayed from
one saved page is a walk whose end has been faked. `--pages` records more than
the first response and saves them as `<name>.p1.<ext>`, `<name>.p2.<ext>`, … so
the fixture replays the real walk. J-PAL is captured this way; see
docs/REFACTOR-PLAN.md, WP11.

A POST made through the fetcher's `post_json` (Workday's JSON walk) is recorded
in the same sequence as the GETs, saved as the JSON it decoded to.

Every capture runs inside `http.polite_fetching`, unlike the rest of this
script's own network use (see docs/DECISIONS.md, "Politeness is run-scoped").
A test or a one-off fetch pays nothing by design, but a capture is neither: it
is a live request to somebody else's career site, and this script's whole job
is making five or six of them a session. `main()` builds the run's User-Agent
and its `ignore_robots` exemptions the same way `pipeline.run_pipeline` does —
`http.user_agent_from_rules` and `pipeline._robots_overrides`, reused rather
than copied — and opens one `polite_fetching` block around the whole batch, so
a capture identifies itself, honours robots.txt and waits its turn per host
exactly as a real run would. A robots.txt refusal surfaces as `RobotsDisallowed`
from inside the extractor and is reported as a failed capture like any other
exception; nothing here treats it specially.

Run this by hand whenever a fixture goes stale; see docs/REFACTOR-PLAN.md, WP0,
for the how-to.

Usage:
    python scripts/capture_fixtures.py <source_name> [<source_name> ...]
    python scripts/capture_fixtures.py --pages all jpal
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_scraper.config_loader import default_project_root, load_rules, load_sources
from job_scraper.extractors.registry import get_extractor
from job_scraper.http import fetch_rendered, fetch_text, polite_fetching, user_agent_from_rules
from job_scraper.pipeline import _robots_overrides

FIXTURES_DIR = default_project_root() / "tests" / "fixtures"
PAUSE_SECONDS = 3

logger = logging.getLogger(__name__)

# Script elements whose `type` is a data MIME type are payloads, never
# executable third-party code, so they are always safe to keep.
_KEEP_SCRIPT_TYPES = frozenset({"application/ld+json", "application/json"})

# Substrings that mark a script as carrying the page's own job data. Matched
# against both the script body and its `id`, because Next.js puts the marker in
# the attribute (`<script id="__NEXT_DATA__" type="application/json">`) while
# Ashby puts it in the body (`window.__appData = {…}`).
#
# This list is the reason the sanitiser is not simply "remove every script":
# ashby.py finds jobs by searching the raw HTML for `window.__appData` and
# decoding the JSON that follows, so stripping all scripts would silently
# reduce every Ashby fixture to zero jobs.
_DATA_MARKERS = (
    "window.__appData",
    "__NEXT_DATA__",
    "__NUXT__",
    "__INITIAL_STATE__",
    "__APOLLO_STATE__",
)


def _carries_job_data(tag: Any) -> bool:
    """True if this <script> holds a data payload rather than executable code."""
    if (tag.get("type") or "").strip().lower() in _KEEP_SCRIPT_TYPES:
        return True
    haystack = f"{tag.get('id') or ''}\n{tag.string or tag.get_text()}"
    return any(marker in haystack for marker in _DATA_MARKERS)


def sanitise_html(html: str) -> str:
    """Strip everything from a captured page that is not job data.

    Career pages embed third-party front-end config, and that config contains
    public API keys which secret scanners flag on sight. Nothing in this project
    parses those scripts, so they are dead weight in a fixture — but scripts
    holding the page's own job data are load-bearing and must survive.

    Hidden CSRF token inputs get the same treatment: they look exactly like
    credentials to a scanner, no extractor reads form inputs, and a token
    captured months ago is meaningless anyway.
    """
    soup = BeautifulSoup(html, "lxml")

    for tag in soup.find_all("script"):
        if not _carries_job_data(tag):
            tag.decompose()

    for tag in soup.find_all("input", attrs={"type": "hidden"}):
        identifier = f"{tag.get('name') or ''} {tag.get('id') or ''}".lower()
        if "token" in identifier and tag.has_attr("value"):
            tag["value"] = ""

    return str(soup)


def recorded_pages_fetch(texts: Sequence[str]) -> Callable[..., str]:
    """A fetcher that serves *texts* in order, then empty responses.

    Extractors ask for their pages in order, so replaying by position needs no
    URL bookkeeping and no fixture knows its own URL.

    Running past the end returns an empty body. For a single-page source that
    is how a real last page reads; for a paginated one it is a lie the walk is
    now entitled to reject — which is the point. A paginated fixture must hold
    every page its extractor asks for, and if it does not, the extractor raises
    here rather than in a quietly short run months later.
    """
    remaining = list(texts)

    def fetch(url: str, *args: Any, **kwargs: Any) -> str:
        return remaining.pop(0) if remaining else ""

    def post_json(url: str, payload: Any, *args: Any, **kwargs: Any) -> Any:
        # A POSTed page was saved as the JSON it decoded to, so it replays as
        # that; past the end, an empty object is the POST's empty body.
        return json.loads(remaining.pop(0)) if remaining else {}

    # Replay shares one queue with GET, because an extractor's requests are
    # replayed in the order it made them, whichever verb it used.
    fetch.post_json = post_json  # type: ignore[attr-defined]
    return fetch


def single_response_fetch(text: str) -> Callable[..., str]:
    """A fetcher that serves *text* once, then empty responses."""
    return recorded_pages_fetch([text])


class _CaptureComplete(Exception):
    """Raised to stop an extractor once enough requests have been recorded."""


def capture_one(source: dict[str, str], pages: int = 1) -> tuple[bool, str]:
    """Fetch and save the fixture for one source. Returns (ok, message).

    *pages* is how many responses to record before cutting the extractor short;
    0 means "however many it asks for", which is what a paginated source needs
    if its fixture is to replay the whole walk rather than a faked end.
    """
    name = source["name"]
    listing_url = source["url"]
    strategy = source.get("strategy", "static")
    fetch = fetch_rendered if strategy == "dynamic" else fetch_text
    extractor = get_extractor(name)

    recorded: list[tuple[str, str]] = []

    def recording_fetch(url: str, *args: Any, **kwargs: Any) -> str:
        text = fetch(url, *args, **kwargs)
        recorded.append((url, text))
        if pages and len(recorded) >= pages:
            raise _CaptureComplete
        # Let the extractor read what it just asked for and decide whether
        # there is another page; an unbounded capture ends when the walk does.
        return text

    # Carry the rendering mark through the wrapper, so extractors that add a
    # selector wait for JS-heavy pages still do so during capture.
    recording_fetch.renders = getattr(fetch, "renders", False)  # type: ignore[attr-defined]

    # And the POST, recorded like a GET. workday.py walks a JSON endpoint by
    # POST through the fetcher it is handed; without this the capture would
    # record nothing for it, as it still records nothing for workable.py, which
    # calls http.post_json directly. The decoded answer is saved re-encoded,
    # which is what replay decodes again.
    inner_post = getattr(fetch, "post_json", None)
    if inner_post is not None:

        def recording_post(url: str, payload: Any, *args: Any, **kwargs: Any) -> Any:
            data = inner_post(url, payload, *args, **kwargs)
            recorded.append((url, json.dumps(data, ensure_ascii=False, indent=1) + "\n"))
            if pages and len(recorded) >= pages:
                raise _CaptureComplete
            return data

        recording_fetch.post_json = recording_post  # type: ignore[attr-defined]

    try:
        if extractor is None:
            # No registry entry: fall back to the listing URL as-is.
            recorded.append((listing_url, fetch(listing_url)))
        else:
            try:
                extractor(listing_url, recording_fetch)
            except _CaptureComplete:
                pass
    except Exception as exc:  # noqa: BLE001 — any fetch failure is reported, not raised
        return False, f"{name}: FAILED ({exc})"

    if not recorded:
        return False, f"{name}: FAILED (extractor made no request)"

    ext = _guess_extension(recorded[0][1])
    texts = [sanitise_html(text) if ext == "html" else text for _, text in recorded]

    written: list[Path] = []
    for index, text in enumerate(texts):
        dest = FIXTURES_DIR / _page_filename(name, index, ext)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(dest)
        written.append(dest)

    stale = _remove_stale(name, ext, kept=len(texts))

    total_size = sum(dest.stat().st_size for dest in written)
    jobs = _verify(extractor, listing_url, texts)
    rel = written[0].relative_to(default_project_root())
    pages_note = f" +{len(written) - 1} more page(s)" if len(written) > 1 else ""
    fetched_from = recorded[0][0]
    message = (
        f"{name}: saved {rel}{pages_note} ({total_size:,} bytes, {jobs} jobs) from {fetched_from}"
    )
    if stale:
        message += f"; removed stale {', '.join(stale)}"
    return True, message


def _page_filename(name: str, index: int, ext: str) -> str:
    """`name.ext` for the first response, `name.pN.ext` for the ones after it.

    The first page keeps the plain name so that every single-page fixture, and
    every test that names one, is untouched by paginated capture existing.
    """
    return f"{name}.{ext}" if index == 0 else f"{name}.p{index}.{ext}"


def _remove_stale(name: str, ext: str, kept: int) -> list[str]:
    """Delete previous fixtures for *name* that this capture has superseded.

    Two kinds go: any other known extension entirely, and any page file past
    the *kept* pages just written.

    A source changes artefact type when its extractor is corrected — givewell
    went from listing HTML to the Greenhouse API's JSON. Leaving the old file
    behind is a trap: it looks like a valid fixture and nothing parses it. A
    left-behind page file is worse than a trap, because it *is* parsed: replay
    is positional, so yesterday's page 4 would be served as today's, and the
    fixture would hold postings the site no longer lists.

    Only ever removes files this script itself would have written.
    """
    removed: list[str] = []
    others = {e for e in _KNOWN_EXTENSIONS if e != ext}
    for candidate in sorted(FIXTURES_DIR.glob(f"{name}.*")):
        page = _page_of(candidate.name, name)
        if page is None:
            continue  # not a file this script writes for this source
        index, suffix = page
        if suffix in others or index >= kept:
            candidate.unlink()
            removed.append(candidate.name)
    return removed


def _page_of(filename: str, name: str) -> tuple[int, str] | None:
    """Read *filename* as one of this script's fixtures for *name*.

    Returns (page index, extension) for `name.ext` and `name.pN.ext`, or None
    for anything else in the directory.
    """
    parts = filename.split(".")
    if parts[0] != name:
        return None
    if len(parts) == 2:
        return 0, parts[1]
    if len(parts) == 3 and parts[1].startswith("p") and parts[1][1:].isdigit():
        return int(parts[1][1:]), parts[2]
    return None


_KNOWN_EXTENSIONS = ("html", "json", "xml")


def _guess_extension(text: str) -> str:
    """Decide whether *text* is JSON, XML or HTML.

    XML matters as its own case, not a subset of "html": Personio's feed is
    XML, and `sanitise_html` below runs an HTML parser (BeautifulSoup + lxml)
    over anything not recognised as JSON. Run over real XML that parser
    rewrites `<![CDATA[` into an HTML comment and closes tags it does not
    recognise, which is not a cosmetic difference — it broke `personio.py`'s
    `ET.fromstring` on every posting (SP4, found by capturing outdooractive:
    the sanitised fixture parsed to 0 jobs, the raw feed to 22). XML is
    detected by its declaration, which every feed here starts with; nothing
    downstream needs a more general sniff.
    """
    stripped = text.lstrip()
    if stripped[:1] in ("{", "["):
        try:
            json.loads(stripped)
            return "json"
        except json.JSONDecodeError:
            pass
    if stripped.startswith("<?xml"):
        return "xml"
    return "html"


def _verify(extractor: Any, listing_url: str, texts: Sequence[str]) -> str:
    """Re-parse what was just saved, so an empty or wrong capture is visible now.

    A cookie wall or an unrendered dynamic page is a successful HTTP response of
    a plausible size; only running the extractor over it shows it is useless.

    Replays every page captured, so a paginated capture that stopped short is
    reported here too: its extractor refuses a walk that ends on nothing.
    """
    if extractor is None:
        return "?"
    try:
        return str(len(extractor(listing_url, recorded_pages_fetch(texts))))
    except Exception as exc:  # noqa: BLE001 — a parse failure is a result, not a crash
        return f"UNPARSEABLE: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="+", help="source names from sources.yaml")
    parser.add_argument(
        "--pages",
        default="1",
        metavar="N",
        help=(
            "responses to record per source: 1 (default), a number, or 'all' to "
            "record the whole walk. Paginated sources need 'all' if their fixture "
            "is to replay the real walk rather than a faked end."
        ),
    )
    args = parser.parse_args()

    if args.pages == "all":
        pages = 0
    elif args.pages.isdigit() and int(args.pages) >= 1:
        pages = int(args.pages)
    else:
        parser.error("--pages takes a positive number or 'all'")

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    rules = load_rules()
    sources = load_sources()
    sources_by_name = {s["name"]: s for s in sources}
    unknown = [n for n in args.names if n not in sources_by_name]
    if unknown:
        parser.error(f"not in sources.yaml: {', '.join(unknown)}")

    exit_code = 0
    with polite_fetching(
        user_agent=user_agent_from_rules(rules),
        robots_overrides=_robots_overrides(sources),
    ):
        for i, name in enumerate(args.names):
            ok, message = capture_one(sources_by_name[name], pages=pages)
            print(message)
            if not ok:
                exit_code = 1
            if i < len(args.names) - 1:
                time.sleep(PAUSE_SECONDS)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
