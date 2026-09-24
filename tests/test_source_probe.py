"""SP3: `sources probe`, the feasibility ladder as a command.

Every rung is driven from saved pages: the real captures in `tests/fixtures/`
where one fits, and the handwritten pages in `tests/fixtures/probe/` for the
cases no capture covers (a shell, an embedded board, a bespoke listing, a
robots.txt that says no). The fetcher is a stub that serves those files by URL
and raises for anything else, so **no test here reaches the network** — the one
exception is `fetch_page`'s own test, which talks to a server on localhost the
way `test_http_fetch.py` does.

The stub records every request alongside every printed line, in one log, so
"printed before fetching" and "stopped before fetching" are asserted on order,
not inferred.

As in SP1 and SP2b, the curated lists and `sources.yaml` live in `tmp_path`;
the autouse fixture makes the real ones unreachable from the CLI.
"""

from __future__ import annotations

import http.server
import json
import socketserver
import threading
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest
import yaml

from job_scraper import curated, probe
from job_scraper import http as http_mod
from job_scraper.extractors import workable, workday
from job_scraper.http import FetchedPage
from job_scraper.robots import RobotsDisallowed, RobotsPolicy
from job_scraper.tools import sources as sources_cli
from tests.fixture_cases import FIXTURES_DIR

PROBE_DIR = FIXTURES_DIR / "probe"
UA = "job-scraper/0.1 (+https://owner.example; contact=owner@example.org)"

KOGNITY = "https://jobs.ashbyhq.com/kognity"
STORYTEL = "https://jobs.storytel.com/jobs"
PATH_PROBED = "https://path.wd1.myworkdayjobs.com/en-US/External"
PATH_BOARD = "https://path.wd1.myworkdayjobs.com/External"
NOVO = "https://careers.novonordisk.com/search"
DSV = "https://jobs.dsv.com/search/"
GIVEWELL_BOARD = "https://job-boards.greenhouse.io/givewell"
GIVEWELL_API = "https://boards-api.greenhouse.io/v1/boards/givewell/jobs?per_page=500"
CONTOSO = "https://contoso.example/careers"
FABRIKAM = "https://fabrikam.example/careers"
LITWARE = "https://litware.example/about/vacancies"


def fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def probe_fixture(name: str) -> str:
    return (PROBE_DIR / name).read_text(encoding="utf-8")


class StubFetcher:
    """Serves saved pages by URL. Anything not on the menu raises, like a 404."""

    user_agent = UA

    def __init__(
        self,
        log: list[str],
        *,
        static: dict[str, str] | None = None,
        rendered: dict[str, str] | None = None,
        robots: dict[str, str] | None = None,
        redirects: dict[str, tuple[str, tuple[str, ...]]] | None = None,
        posted: dict[str, list[str]] | None = None,
    ) -> None:
        self.log = log
        self.static = static or {}
        self.rendered_pages = rendered or {}
        # POSTed JSON by URL, one saved response per page in walk order.
        self.posted = posted or {}
        self.redirects = redirects or {}
        robots = robots or {}

        def fetch_robots(url: str, agent: str, timeout: int) -> tuple[int, str]:
            self.log.append(f"FETCH robots.txt {url}")
            return (200, robots[url]) if url in robots else (404, "")

        # The real policy, so the rule quoted is the rule the run would obey.
        self.policy = RobotsPolicy(UA, fetch=fetch_robots)

    def robots(self, url: str) -> Any:
        return self.policy.explain(url)

    def page(self, url: str) -> FetchedPage:
        self.log.append(f"FETCH page {url}")
        final, hops = self.redirects.get(url, (url, ()))
        return FetchedPage(url=url, final_url=final, redirects=hops, text=self._serve(final))

    def text(self, url: str) -> str:
        self.log.append(f"FETCH text {url}")
        return self._serve(url)

    def rendered(self, url: str, **kwargs: Any) -> str:
        self.log.append(f"FETCH rendered {url}")
        if url not in self.rendered_pages:
            raise RuntimeError(f"no rendered fixture for {url}")
        return self.rendered_pages[url]

    def post_json(self, url: str, payload: dict[str, Any], **kwargs: Any) -> Any:
        offset, limit = int(payload.get("offset", 0)), int(payload.get("limit", 1))
        self.log.append(f"POST {url} offset={offset}")
        pages = self.posted.get(url)
        if pages is None:
            raise RuntimeError(f"404 Client Error: no POST fixture for {url}")
        index = offset // limit
        return json.loads(pages[index]) if index < len(pages) else {"jobPostings": []}

    def _serve(self, url: str) -> str:
        if url not in self.static:
            raise RuntimeError(f"404 Client Error: no fixture for {url}")
        return self.static[url]

    @property
    def fetches(self) -> list[str]:
        return [line for line in self.log if line.startswith("FETCH ")]


@pytest.fixture(autouse=True)
def no_real_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def boom() -> Path:
        raise AssertionError("a test reached for the real data/curated/")

    def no_live_fetcher(user_agent: str) -> Any:
        raise AssertionError("a test reached for the live fetcher")

    monkeypatch.setattr(sources_cli, "default_curated_dir", boom)
    monkeypatch.setattr(sources_cli, "_probe_fetcher", no_live_fetcher)
    monkeypatch.setattr(
        sources_cli, "default_sources_path", lambda: tmp_path / "config" / "sources.yaml"
    )
    monkeypatch.setattr(
        sources_cli, "default_rules_path", lambda: tmp_path / "config" / "rules.json"
    )


@pytest.fixture
def curated_dir(tmp_path: Path) -> Path:
    path = tmp_path / "curated"
    path.mkdir()
    return path


def write_sources(tmp_path: Path, *sources: dict[str, Any]) -> Path:
    path = tmp_path / "config" / "sources.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(yaml.safe_dump({"sources": list(sources)}), encoding="utf-8")
    return path


def tombstone(curated_dir: Path, organisation: str, url: str, reason: str) -> None:
    curated.save_list(
        curated.excluded_path(curated_dir),
        [{"organisation": organisation, "url": url, "reason": reason, "excluded_on": None}],
        curated.EXCLUDED_KEY,
        curated.EXCLUDED_FIELDS,
    )


def candidate(curated_dir: Path, **entry: Any) -> None:
    row = {field: entry.get(field) for field in curated.CANDIDATE_FIELDS}
    curated.save_list(
        curated.candidates_path(curated_dir),
        [row],
        curated.CANDIDATES_KEY,
        curated.CANDIDATE_FIELDS,
    )


def run_probe(
    url: str,
    fetcher: StubFetcher,
    curated_dir: Path,
    tmp_path: Path,
    **kwargs: Any,
) -> tuple[probe.ProbeResult, str]:
    result = probe.probe(
        url,
        fetcher,
        curated_dir=curated_dir,
        sources_path=tmp_path / "config" / "sources.yaml",
        emit=fetcher.log.append,
        **kwargs,
    )
    return result, "\n".join(line for line in fetcher.log if not line.startswith("FETCH "))


# --- ready-made fetchers for the captured pages ----------------------------


def kognity(log: list[str]) -> StubFetcher:
    return StubFetcher(log, static={KOGNITY: fixture("kognity.html")})


PATH_API = "https://path.wd1.myworkdayjobs.com/wday/cxs/path/External/jobs"
PATH_WALK = ("path.json", "path.p1.json", "path.p2.json", "path.p3.json")


def path_workday(log: list[str], posted: list[str] | None = None) -> StubFetcher:
    """path's rendered listing, and its JSON walk for the reader.

    The rendered page (2026-08-20) says "1 - 20 of 61 jobs"; the walk
    (2026-09-23) holds 64. They were captured a month apart, so the probe's
    comparison of the two reads 64 against 61 — more than stated, never short.
    """
    return StubFetcher(
        log,
        static={PATH_PROBED: probe_fixture("shell.html")},
        rendered={
            PATH_PROBED: fixture("path.rendered.html"),
            PATH_BOARD: fixture("path.rendered.html"),
        },
        posted={PATH_API: [fixture(name) for name in PATH_WALK] if posted is None else posted},
    )


def contoso_embed(log: list[str], api: str | None = None) -> StubFetcher:
    page = probe_fixture("greenhouse_embed.html")
    return StubFetcher(
        log,
        static={CONTOSO: page, GIVEWELL_API: fixture("givewell.json") if api is None else api},
        rendered={CONTOSO: page},
    )


def novo(log: list[str]) -> StubFetcher:
    pages = {
        0: "novo_nordisk.html",
        100: "novo_nordisk.p1.html",
        200: "novo_nordisk.p2.html",
        300: "novo_nordisk.p3.html",
    }
    static = {f"{NOVO}?startrow={row}": fixture(name) for row, name in pages.items()}
    static[NOVO] = fixture("novo_nordisk.html")
    return StubFetcher(log, static=static)


# --- rung 1: the lists -----------------------------------------------------


class TestLists:
    def test_a_tombstoned_board_stops_before_anything_is_fetched(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        tombstone(curated_dir, "Kognity", "https://jobs.ashbyhq.com/kognity/", "persistent 403")
        fetcher = kognity([])
        result, report = run_probe(KOGNITY + "?utm=x", fetcher, curated_dir, tmp_path)

        assert fetcher.fetches == []  # not even robots.txt
        assert result.kind == probe.NOT_FEASIBLE
        assert "rung 1: tombstoned (Kognity: persistent 403)" in result.line
        assert "TOMBSTONED — Kognity" in report
        assert "2. robots.txt" not in report

    def test_the_same_shared_host_with_another_board_is_not_the_tombstone(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        # Two employers on jobs.ashbyhq.com are two boards. A host match here
        # would refuse to probe every future Ashby employer.
        tombstone(curated_dir, "Contoso", "https://jobs.ashbyhq.com/contoso", "no")
        result, report = run_probe(KOGNITY, kognity([]), curated_dir, tmp_path)

        assert result.kind == probe.REUSE
        assert "TOMBSTONED" not in report

    def test_a_candidate_is_printed_before_the_first_fetch(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        candidate(
            curated_dir,
            organisation="Kognity",
            url=KOGNITY,
            blocker="Ashby board looked empty",
            last_checked="2026-07-30",
            source_of_record="SP2 recovery",
        )
        log: list[str] = []
        run_probe(KOGNITY, kognity(log), curated_dir, tmp_path)

        first_fetch = next(i for i, line in enumerate(log) if line.startswith("FETCH"))
        before = "\n".join(log[:first_fetch])
        assert "CANDIDATE — Kognity" in before
        assert "blocker: Ashby board looked empty" in before
        assert "last_checked: 2026-07-30" in before
        assert "source_of_record: SP2 recovery" in before

    def test_a_null_last_checked_is_called_unknown_not_never(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        candidate(curated_dir, organisation="Kognity", url=KOGNITY, blocker="403")
        _, report = run_probe(KOGNITY, kognity([]), curated_dir, tmp_path)

        assert "last_checked: null — the date is UNKNOWN" in report
        assert "not the same as never checked" in report
        assert "source_of_record: null" in report

    def test_a_candidate_names_the_printed_entry(self, curated_dir: Path, tmp_path: Path) -> None:
        candidate(curated_dir, organisation="Kognity AB", url=KOGNITY, blocker="403")
        _, report = run_probe(KOGNITY, kognity([]), curated_dir, tmp_path)

        assert "- name: kognity_ab" in report
        assert "company: Kognity AB" in report

    def test_an_active_source_is_said_and_the_probe_goes_on(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        write_sources(tmp_path, {"name": "kognity", "url": KOGNITY + "/", "strategy": "static"})
        result, report = run_probe(KOGNITY, kognity([]), curated_dir, tmp_path)

        assert "ACTIVE SOURCE — kognity" in report
        assert result.kind == probe.REUSE
        assert "Already active as 'kognity'" in report
        assert "not for pasting" in report

    def test_a_missing_sources_yaml_is_a_skipped_check_not_a_pass(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        _, report = run_probe(KOGNITY, kognity([]), curated_dir, tmp_path)
        assert "sources.yaml not found: the active-source check was SKIPPED" in report
        assert "or sources.yaml" not in report

    def test_an_unmigrated_list_refuses_before_fetching(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        (curated_dir / "excluded_sources.csv").write_text("organisation;url;reason\n")
        fetcher = kognity([])
        with pytest.raises(curated.NotMigratedError):
            run_probe(KOGNITY, fetcher, curated_dir, tmp_path)
        assert fetcher.fetches == []

    def test_a_board_found_on_the_page_is_checked_too(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        tombstone(curated_dir, "GiveWell", GIVEWELL_BOARD, "owner's call")
        fetcher = contoso_embed([])
        result, report = run_probe(CONTOSO, fetcher, curated_dir, tmp_path)

        assert "not read: that board is tombstoned" in report
        assert f"FETCH text {GIVEWELL_API}" not in fetcher.log
        assert result.kind == probe.NOT_FEASIBLE
        assert "rung 4" in result.line


# --- rung 2: robots.txt ----------------------------------------------------


class TestRobots:
    def test_a_disallowed_page_quotes_the_rule_and_is_not_fetched(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        fetcher = StubFetcher(
            [],
            static={CONTOSO: probe_fixture("greenhouse_embed.html")},
            robots={
                "https://contoso.example/robots.txt": probe_fixture("robots_disallow_careers.txt")
            },
        )
        result, report = run_probe(CONTOSO, fetcher, curated_dir, tmp_path)

        assert fetcher.fetches == ["FETCH robots.txt https://contoso.example/robots.txt"]
        assert f"evaluated for User-Agent: {UA}" in report
        assert "product token, 'job-scraper'" in report
        assert "group: User-agent: *" in report
        assert "rule:  Disallow: /careers" in report
        assert "DISALLOWED" in report
        assert result.kind == probe.NOT_FEASIBLE
        assert "rung 2: robots.txt forbids" in result.line
        assert "(Disallow: /careers)" in result.line

    def test_an_allowed_page_names_its_group_and_crawl_delay(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        fetcher = kognity([])
        fetcher.policy = RobotsPolicy(
            UA, fetch=lambda url, agent, timeout: (200, probe_fixture("robots_allow.txt"))
        )
        _, report = run_probe(KOGNITY, fetcher, curated_dir, tmp_path)

        assert "https://jobs.ashbyhq.com/robots.txt" in report
        assert "group: User-agent: *" in report
        assert "ALLOWED — no line in the matching group covers this path" in report
        assert "Crawl-delay: 2s" in report

    def test_no_robots_txt_says_so(self, curated_dir: Path, tmp_path: Path) -> None:
        _, report = run_probe(KOGNITY, kognity([]), curated_dir, tmp_path)
        assert "ALLOWED — the host has no robots.txt (4xx)" in report

    def test_a_board_host_that_forbids_us_is_not_read(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        fetcher = contoso_embed([])
        fetcher.policy = RobotsPolicy(
            UA,
            fetch=lambda url, agent, timeout: (
                (200, "User-agent: job-scraper\nDisallow: /\n")
                if "greenhouse" in url
                else (404, "")
            ),
        )
        _, report = run_probe(CONTOSO, fetcher, curated_dir, tmp_path)

        assert f"greenhouse on {GIVEWELL_BOARD}: not read" in report
        assert "group: User-agent: job-scraper" in report
        assert f"FETCH text {GIVEWELL_API}" not in fetcher.log


class TestRobotsExplain:
    """`RobotsPolicy.explain` is the policy's own answer, with the line behind it."""

    def test_the_first_group_naming_our_token_wins_over_star(self) -> None:
        text = "User-agent: *\nDisallow: /\n\nUser-agent: job-scraper\nDisallow: /private\n"
        policy = RobotsPolicy(UA, fetch=lambda *a: (200, text))
        verdict = policy.explain("https://a.example/jobs")
        assert verdict.allowed is True
        assert verdict.group == "User-agent: job-scraper"
        assert verdict.rule is None

        blocked = policy.explain("https://a.example/private/x?y=1")
        assert blocked.allowed is False
        assert blocked.rule == "Disallow: /private"

    def test_an_allow_line_is_quoted_when_it_decides(self) -> None:
        text = "User-agent: *\nAllow: /careers\nDisallow: /\n"
        verdict = RobotsPolicy(UA, fetch=lambda *a: (200, text)).explain(
            "https://a.example/careers"
        )
        assert verdict.allowed is True
        assert verdict.rule == "Allow: /careers"

    def test_an_unreadable_file_allows_and_says_why(self) -> None:
        verdict = RobotsPolicy(UA, fetch=lambda *a: (503, "")).explain("https://a.example/x")
        assert verdict.allowed is True
        assert "could not be read" in verdict.reason

    def test_an_override_is_reported_as_one(self) -> None:
        policy = RobotsPolicy(UA, overrides={"https://a.example"}, fetch=lambda *a: (200, ""))
        verdict = policy.explain("https://a.example/x")
        assert verdict.allowed is True
        assert "ignore_robots" in verdict.reason

    def test_explain_agrees_with_allows(self) -> None:
        text = probe_fixture("robots_disallow_careers.txt")
        policy = RobotsPolicy(UA, fetch=lambda *a: (200, text))
        for path in ("/", "/careers", "/careers/1", "/about", "/careersx"):
            url = "https://a.example" + path
            assert policy.explain(url).allowed == policy.allows(url)


# --- rung 3: the page ------------------------------------------------------


class TestPage:
    def test_static_job_data_needs_no_render(self, curated_dir: Path, tmp_path: Path) -> None:
        fetcher = kognity([])
        _, report = run_probe(KOGNITY, fetcher, curated_dir, tmp_path)

        assert not any(line.startswith("FETCH rendered") for line in fetcher.log)
        assert "12,566 bytes" in report
        assert "embedded data: window.__appData (holding a job list)" in report
        assert "=> the HTML carries job data" in report

    def test_a_shell_is_named_and_the_rendered_route_is_tried(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        fetcher = path_workday([])
        _, report = run_probe(PATH_PROBED, fetcher, curated_dir, tmp_path)

        assert "=> a client-rendered shell" in report
        assert "a <noscript> asking for JavaScript" in report
        assert f"FETCH rendered {PATH_PROBED}" in fetcher.log
        assert "20 link(s) shaped like a posting" in report

    def test_counts_postings_on_a_static_listing(self, curated_dir: Path, tmp_path: Path) -> None:
        fetcher = StubFetcher([], static={STORYTEL: fixture("storytel.html")})
        _, report = run_probe(STORYTEL, fetcher, curated_dir, tmp_path)
        assert "6 link(s) shaped like a posting" in report

    def test_both_routes_failing_is_rung_three(self, curated_dir: Path, tmp_path: Path) -> None:
        result, report = run_probe(FABRIKAM, StubFetcher([]), curated_dir, tmp_path)

        assert "static fetch FAILED — RuntimeError: 404" in report
        assert result.kind == probe.NOT_FEASIBLE
        assert "rung 3: neither route fetched the page" in result.line

    def test_a_static_failure_still_tries_the_rendered_route(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        fetcher = StubFetcher([], rendered={LITWARE: probe_fixture("bespoke.html")})
        result, report = run_probe(LITWARE, fetcher, curated_dir, tmp_path)

        assert "static fetch FAILED" in report
        assert result.kind == probe.NEW_EXTRACTOR
        assert "strategy: dynamic" in result.line

    def test_the_redirect_chain_is_printed(self, curated_dir: Path, tmp_path: Path) -> None:
        fetcher = StubFetcher(
            [],
            static={KOGNITY: fixture("kognity.html")},
            redirects={CONTOSO: (KOGNITY, (CONTOSO,))},
        )
        _, report = run_probe(CONTOSO, fetcher, curated_dir, tmp_path)
        assert f"static fetch: {KOGNITY}" in report
        assert f"redirected from {CONTOSO}" in report


# --- rung 4: fingerprints --------------------------------------------------


class TestFingerprint:
    def test_a_board_is_named_from_the_redirect_chain(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        fetcher = StubFetcher(
            [],
            static={KOGNITY: fixture("kognity.html")},
            redirects={CONTOSO: (KOGNITY, (CONTOSO,))},
        )
        result, report = run_probe(CONTOSO, fetcher, curated_dir, tmp_path)

        assert "Ashby: seen in the final URL" in report
        assert f"board: {KOGNITY}  (Ashby, from the final URL)" in report
        assert result.kind == probe.REUSE

    def test_a_board_is_named_from_an_embed_script(self, curated_dir: Path, tmp_path: Path) -> None:
        _, report = run_probe(CONTOSO, contoso_embed([]), curated_dir, tmp_path)
        assert f"board: {GIVEWELL_BOARD}  (Greenhouse, from a script src (static))" in report

    def test_nothing_recognised_says_so(self, curated_dir: Path, tmp_path: Path) -> None:
        fetcher = StubFetcher([], static={LITWARE: probe_fixture("bespoke.html")})
        _, report = run_probe(LITWARE, fetcher, curated_dir, tmp_path)
        assert "none of the ten supported platforms" in report
        assert "nothing to run: no board was found" in report

    def test_teamtailor_and_successfactors_are_the_page_itself(self) -> None:
        for url, html, key in (
            (STORYTEL, fixture("storytel.html"), "teamtailor"),
            (DSV, fixture("dsv.html"), "successfactors_html"),
        ):
            page = FetchedPage(url, url, (), html)
            seen, boards = probe.fingerprint(url, page, [probe.scan_page(html, url, "static")])
            assert key in seen
            assert [(b.platform.key, b.url) for b in boards] == [(key, url)]

    def test_a_workday_board_drops_the_locale(self) -> None:
        html = fixture("path.rendered.html")
        _, boards = probe.fingerprint(
            PATH_PROBED, None, [probe.scan_page(html, PATH_PROBED, "rendered")]
        )
        assert [(b.platform.key, b.url) for b in boards] == [("workday", PATH_BOARD)]

    @pytest.mark.parametrize(
        ("snippet", "key", "board"),
        [
            (
                '<script src="https://boards.greenhouse.io/embed/job_board/js?for=contoso">',
                "greenhouse",
                "https://job-boards.greenhouse.io/contoso",
            ),
            (
                '<a href="https://job-boards.eu.greenhouse.io/contoso/jobs/1">',
                "greenhouse",
                "https://job-boards.eu.greenhouse.io/contoso",
            ),
            (
                '<a href="https://jobs.lever.co/contoso/abc">',
                "lever",
                "https://jobs.lever.co/contoso",
            ),
            (
                '<iframe src="https://jobs.ashbyhq.com/contoso/embed"></iframe>',
                "ashby",
                "https://jobs.ashbyhq.com/contoso",
            ),
            (
                '<a href="https://apply.workable.com/contoso/j/ABC123/">',
                "workable",
                "https://apply.workable.com/contoso/",
            ),
            (
                '<a href="https://contoso.jobs.personio.de/job/1">',
                "personio",
                "https://contoso.jobs.personio.de/",
            ),
            (
                '<a href="https://careers.smartrecruiters.com/Contoso1/x">',
                "smartrecruiters",
                "https://careers.smartrecruiters.com/Contoso1",
            ),
            (
                '<a href="https://contoso.wd3.myworkdayjobs.com/en-US/Careers/job/x">',
                "workday",
                "https://contoso.wd3.myworkdayjobs.com/Careers",
            ),
            ('<a href="https://contoso.breezy.hr/p/abc">', "breezy", "https://contoso.breezy.hr"),
        ],
    )
    def test_each_hosted_platform_names_its_board(self, snippet: str, key: str, board: str) -> None:
        html = f"<html><body>{snippet}</body></html>"
        seen, boards = probe.fingerprint(
            FABRIKAM, None, [probe.scan_page(html, FABRIKAM, "static")]
        )
        assert key in seen
        assert [(b.platform.key, b.url) for b in boards] == [(key, board)]

    def test_all_ten_supported_platforms_are_known(self) -> None:
        assert {p.label for p in probe.PLATFORMS} == {
            "Greenhouse",
            "Lever",
            "Ashby",
            "Workable",
            "Teamtailor",
            "Personio",
            "SmartRecruiters",
            "Workday",
            "Breezy",
            "SuccessFactors",
        }

    def test_a_marketing_link_to_a_platform_is_not_a_board(self) -> None:
        html = '<a href="https://www.breezy.hr/">Breezy</a><a href="https://boards.greenhouse.io/embed/job_app">x</a>'
        seen, boards = probe.fingerprint(
            FABRIKAM, None, [probe.scan_page(html, FABRIKAM, "static")]
        )
        assert {"breezy", "greenhouse"} <= set(seen)
        assert boards == []

    def test_boards_per_platform_are_capped(self) -> None:
        links = "".join(
            f'<a href="https://jobs.lever.co/org{i}">x</a>'
            for i in range(probe.MAX_BOARDS_PER_PLATFORM + 2)
        )
        seen, boards = probe.fingerprint(
            FABRIKAM, None, [probe.scan_page(links, FABRIKAM, "static")]
        )
        assert len(boards) == probe.MAX_BOARDS_PER_PLATFORM
        assert "2 more board(s) not read" in seen["lever"]


# --- rung 5: readers -------------------------------------------------------


class TestReaders:
    def test_samples_show_title_location_and_detail_url(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        _, report = run_probe(KOGNITY, kognity([]), curated_dir, tmp_path)

        assert "ashby on https://jobs.ashbyhq.com/kognity (static)" in report
        assert "5 row(s)" in report
        assert report.count("- title:") == probe.SAMPLE_ROWS
        assert "title:      Delivery Manager - 12 months fixed-term contract" in report
        assert "location:   Sweden" in report
        assert (
            "detail_url: https://jobs.ashbyhq.com/kognity/bc514f8b-3ee3-4b5b-8917-2166fdf769fd"
            in report
        )

    def test_an_api_reader_goes_through_the_fetcher(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        fetcher = contoso_embed([])
        _, report = run_probe(CONTOSO, fetcher, curated_dir, tmp_path)

        assert f"FETCH text {GIVEWELL_API}" in fetcher.log
        assert "20 row(s)" in report
        assert "title:      Program Officer" in report

    def test_a_workday_reader_walks_its_json_through_the_fetcher(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        # SP3b: the reader POSTs through the probe's fetcher, page by page, and
        # the listing is still rendered for the page's own evidence.
        fetcher = path_workday([])
        result, report = run_probe(PATH_PROBED, fetcher, curated_dir, tmp_path)

        posts = [line for line in fetcher.log if line.startswith("POST ")]
        assert posts == [f"POST {PATH_API} offset={n}" for n in (0, 20, 40, 60)]
        assert f"workday on {PATH_BOARD} (dynamic)" in report
        assert "64 row(s)" in report
        assert result.kind == probe.REUSE
        assert "strategy: dynamic" in report
        assert "add --pages all" in report

    def test_a_reader_that_raises_is_reported_not_raised(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        fetcher = contoso_embed([], api="<html>not json</html>")
        result, report = run_probe(CONTOSO, fetcher, curated_dir, tmp_path)

        assert "FAILED — JSONDecodeError" in report
        assert result.kind == probe.NOT_FEASIBLE
        assert "rung 5: greenhouse is recognised but its reader failed (JSONDecodeError" in (
            result.line
        )
        assert "Rungs 3 and 4" not in report

    def test_an_empty_board_is_not_a_reuse(self, curated_dir: Path, tmp_path: Path) -> None:
        fetcher = contoso_embed([], api=json.dumps({"jobs": []}))
        result, report = run_probe(CONTOSO, fetcher, curated_dir, tmp_path)

        assert "0 row(s)" in report
        assert result.kind == probe.NOT_FEASIBLE
        assert "rung 5: greenhouse matched but read 0 postings" in result.line

    def test_a_suspicious_parse_is_flagged(self, curated_dir: Path, tmp_path: Path) -> None:
        _, report = run_probe(PATH_PROBED, path_workday([]), curated_dir, tmp_path)
        assert "! 21 of 64 row(s) have no location" in report

    def test_workable_reads_through_post_json(
        self, curated_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # workable.py POSTs through http.post_json rather than the fetcher it is
        # handed, so this is the one reader whose request is stubbed at the
        # module. The payload is a minimal invented board, not a capture: SP4
        # captures the real one.
        posted: list[str] = []

        def fake_post(url: str, payload: Any, **kwargs: Any) -> Any:
            posted.append(url)
            return {
                "results": [
                    {
                        "title": "Analyst",
                        "shortcode": "ABC123",
                        "department": ["Research"],
                        "location": {"city": "London", "country": "United Kingdom"},
                    }
                ]
            }

        monkeypatch.setattr(workable, "post_json", fake_post)
        board = "https://apply.workable.com/contoso/"
        shell = probe_fixture("shell.html")
        fetcher = StubFetcher([], static={board: shell}, rendered={board: shell})
        result, report = run_probe(board, fetcher, curated_dir, tmp_path)

        assert posted == ["https://apply.workable.com/api/v3/accounts/contoso/jobs"]
        assert result.kind == probe.REUSE
        assert "detail_url: https://apply.workable.com/contoso/j/ABC123/" in report
        assert "follows no next-page token" in report

    def test_lever_and_smartrecruiters_lines_carry_the_org_slug(self) -> None:
        for url, key in (
            ("https://jobs.lever.co/contoso", "lever"),
            ("https://careers.smartrecruiters.com/Contoso1", "smartrecruiters"),
        ):
            platform = next(p for p in probe.PLATFORMS if p.key == key)
            board = probe.Board(platform, url, url.rsplit("/", 1)[1], "test")
            _, call = probe._source_call(board, "contoso", None)
            slug = url.rsplit("/", 1)[1]
            assert call == f'partial({key}.extract, source_name="contoso", org_slug="{slug}")'


# --- rung 6: pagination ----------------------------------------------------


class TestPagination:
    def test_a_walk_that_stops_short_of_its_total_is_caught(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        # Until SP3b this test fed path's rendered first page to a reader that
        # read only that page, and expected SHORT (20 of 61). The reader now
        # walks the JSON, so the same fixture reads whole. What must still hold
        # is that a short read never passes: here the stub serves two of the
        # walk's four pages, so the reader holds 40 of a stated 64 and fails.
        fetcher = path_workday([], posted=[fixture("path.json"), fixture("path.p1.json")])
        result, report = run_probe(PATH_PROBED, fetcher, curated_dir, tmp_path)

        assert "FAILED — ShortWalkError" in report
        assert "holding 40 posting(s), but the listing says it has 64" in report
        assert result.kind == probe.NOT_FEASIBLE
        assert "workday is recognised but its reader failed (ShortWalkError" in result.line
        assert "registry.py (in REGISTRY)" not in report

    def test_a_walk_that_lost_its_total_is_checked_against_the_page(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        # The probe's own check, behind the reader's: with no total in the
        # response the reader cannot tell 40 from whole and accepts it, but the
        # rendered page still says 61, and the probe says SHORT.
        first = json.loads(fixture("path.json"))
        del first["total"]
        fetcher = path_workday([], posted=[json.dumps(first), fixture("path.p1.json")])
        result, report = run_probe(PATH_PROBED, fetcher, curated_dir, tmp_path)

        assert 'rendered page: "1 - 20 of 61 jobs"' in report
        assert "SHORT — read 40 posting(s) but the listing says 61" in report
        # Workday walks and checks a total itself, so this is its bug to fix,
        # not a missing walking reader — the verdict before SP3b's review said
        # "does not walk this listing", which was no longer true.
        assert result.kind == probe.NOT_FEASIBLE
        assert "rung 5: workday walks this listing and checks a total itself" in result.line
        assert "bug in workday.py" in result.line
        assert "does not walk this listing" not in result.line
        assert "registry.py (in REGISTRY)" not in report

    def test_a_first_page_reader_is_told_it_needs_a_walk(self) -> None:
        # The case the old path.html test covered, before Workday walked: a
        # reader that reads only its listing page, whose page states more.
        # No capture of such a board exists now, so decide() is driven
        # directly with a teamtailor run and a stated total.
        teamtailor = next(p for p in probe.PLATFORMS if p.key == "teamtailor")
        assert not teamtailor.guarded
        board = probe.Board(teamtailor, "https://jobs.contoso.example/jobs", None, "the page")
        run = probe.ReaderRun(
            board=board,
            strategy="static",
            rows=[{"title": f"Role {n}", "detail_url": f"https://x/{n}"} for n in range(20)],
            total=probe.DeclaredTotal(61, "1 - 20 of 61 jobs"),
        )
        result = probe.decide([run], [], None)

        assert result.kind == probe.NEW_EXTRACTOR
        assert "read 20 posting(s) but the listing says 61" in result.line
        assert "does not walk this listing" in result.line

    def test_more_rows_than_the_page_states_is_whole(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        # Not an exact match, and not meant to be one: path's rendered page
        # (61, 2026-08-20) and its JSON walk (64, 2026-09-23) are a month
        # apart, and a board that grew is not a short read. The exact case,
        # rows equal to the stated total, is test_a_guarded_walk_is_read_to_its_end
        # (novo_nordisk, 329 against 329), from one capture.
        _, report = run_probe(PATH_PROBED, path_workday([]), curated_dir, tmp_path)
        assert "64 row(s) against a stated 61: whole" in report

    def test_a_guarded_walk_is_read_to_its_end(self, curated_dir: Path, tmp_path: Path) -> None:
        fetcher = novo([])
        result, report = run_probe(NOVO, fetcher, curated_dir, tmp_path)

        assert "page size 100, from" in report
        assert "329 row(s) against a stated 329: whole" in report
        assert f"FETCH text {NOVO}?startrow=300" in fetcher.log
        assert result.kind == probe.REUSE
        assert "add --pages all" in report

    def test_a_walk_that_breaks_early_fails_loudly(self, curated_dir: Path, tmp_path: Path) -> None:
        # Only the first of DSV's 201 pages is saved, so page two comes back
        # 404 — and the reader's failure is shown, not swallowed.
        fetcher = StubFetcher(
            [], static={DSV: fixture("dsv.html"), f"{DSV}?startrow=0": fixture("dsv.html")}
        )
        result, report = run_probe(DSV, fetcher, curated_dir, tmp_path)

        assert "page size 10" in report
        assert "successfactors_html on https://jobs.dsv.com/search/ (static)" in report
        assert "FAILED — RuntimeError: 404" in report
        # A recognised platform whose reader broke needs that reader fixed, not
        # a second module beside it.
        assert result.kind == probe.NOT_FEASIBLE
        assert "a bug in successfactors_html.py" in result.line

    def test_a_reader_that_reads_nothing_from_a_full_page_is_its_bug(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        # Teamtailor markers, three bespoke postings the Teamtailor reader
        # cannot see: the reader is wrong for this layout.
        html = probe_fixture("bespoke.html").replace(
            "</body>", '<script src="https://scripts.teamtailor-cdn.com/x.js"></script></body>'
        )
        fetcher = StubFetcher([], static={LITWARE: html})
        result, _ = run_probe(LITWARE, fetcher, curated_dir, tmp_path)
        assert result.kind == probe.NOT_FEASIBLE
        assert "teamtailor read 0 postings, but the static page shows some" in result.line

    @pytest.mark.parametrize(
        ("text", "total", "page_size"),
        [
            ("Showing 1 to 20 of 62 Jobs", 62, 20),
            ("Results 1 – 100 of 329", 329, 100),
            ("1-6 of 74 results", 74, 6),
            ("61 JOBS FOUND", 61, None),
            ("Vacant positions: 2", 2, None),
        ],
    )
    def test_declared_totals_are_read(self, text: str, total: int, page_size: int | None) -> None:
        html = f"<html><body><p>{text}</p></body></html>"
        found = probe.scan_page(html, FABRIKAM, "static").total
        assert found is not None
        assert (found.total, found.page_size) == (total, page_size)

    def test_a_json_total_is_read(self) -> None:
        html = '<script>window.x = {"totalFound": 42, "content": []}</script>'
        found = probe.scan_page(html, FABRIKAM, "static").total
        assert found is not None and found.total == 42

    def test_a_year_is_not_a_total(self) -> None:
        assert probe.scan_page("<p>1 - 5 of 2026</p>", FABRIKAM, "static").total is None

    @pytest.mark.parametrize(
        ("html", "sign"),
        [
            ('<link rel="next" href="/jobs?page=2">', 'a rel="next" link'),
            ('<nav class="pagination"></nav>', "a pagination element"),
            ('<a href="/jobs?page=3">3</a>', "a link to a later page (page=3)"),
            ("<button>Load more</button>", "a 'Load more' control"),
        ],
    )
    def test_pager_signs(self, html: str, sign: str) -> None:
        signs = probe.scan_page(html, FABRIKAM, "static").pager
        assert any(s.startswith(sign) for s in signs)

    def test_a_filter_button_is_not_a_pager(self) -> None:
        html = "<button>Show More Options</button><a href='/jobs?page=1'>1</a>"
        assert probe.scan_page(html, FABRIKAM, "static").pager == []

    def test_a_typical_page_size_under_a_pager_is_suspicious(self) -> None:
        platform = next(p for p in probe.PLATFORMS if p.key == "teamtailor")
        board = probe.Board(platform, STORYTEL, None, "test")
        run = probe.ReaderRun(board, "static", rows=[{}] * 20, pager=["a 'Load more' control"])
        assert run.short is not None and "typical page size" in run.short
        run.pager = []
        assert run.short is None


# --- rung 7: the verdict ---------------------------------------------------


class TestWorkdayCapAndFacets:
    """SP3c: a capped board is told to narrow itself, and a narrowed one stays narrowed.

    The facet id is invented; the real one stays out of tracked files.
    """

    FACET_ID = "0a1b2c3d4e5f60718293a4b5c6d7e8f9"

    def capped_path(self) -> StubFetcher:
        first = json.loads(fixture("path.json"))
        first["total"] = 2000
        return path_workday([], posted=[json.dumps(first)])

    def test_a_capped_board_is_told_to_narrow_not_blamed(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        fetcher = self.capped_path()
        result, report = run_probe(PATH_PROBED, fetcher, curated_dir, tmp_path)

        assert "FAILED — CappedTotalError" in report
        assert result.kind == probe.NOT_FEASIBLE
        assert result.run is not None and result.run.capped == (2000, PATH_API)
        assert f"{PATH_API} states 2000 postings, Workday's cap" in result.line
        assert "past the cap" in result.line
        assert "facet query" in result.line
        assert "SP3c" in result.line
        assert "bug in workday.py" not in result.line
        assert "registry.py (in REGISTRY)" not in report
        # One request, then the refusal: the walk never starts.
        assert [line for line in fetcher.log if line.startswith("POST ")] == [
            f"POST {PATH_API} offset=0"
        ]

    def test_the_cap_is_read_from_the_exceptions_fields_not_its_words(
        self, curated_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The RobotsDisallowed rule: a reworded message must not change the verdict.
        def reworded(url: str, fetch: Any, source_name: str) -> Any:
            raise workday.CappedTotalError(
                "too many", total=2000, endpoint="https://x.wd1.myworkdayjobs.com/api"
            )

        monkeypatch.setattr(workday, "extract", reworded)
        result, _ = run_probe(PATH_PROBED, path_workday([]), curated_dir, tmp_path)

        assert "https://x.wd1.myworkdayjobs.com/api states 2000 postings" in result.line
        assert "past the cap" in result.line

    def test_the_capped_refusal_is_a_short_walk_with_fields(self) -> None:
        from job_scraper.extractors.pagination import ShortWalkError

        exc = workday.CappedTotalError("m", total=2000, endpoint="https://e")
        assert isinstance(exc, ShortWalkError)
        assert (exc.total, exc.endpoint) == (2000, "https://e")

    def test_a_facet_query_stays_in_the_board_and_the_paste_block(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        probed = f"{PATH_PROBED}?locationCountry={self.FACET_ID}"
        board = f"{PATH_BOARD}?locationCountry={self.FACET_ID}"
        first = json.loads(fixture("path.json"))
        first["facets"] = [
            {
                "facetParameter": "locationMainGroup",
                "values": [
                    {
                        "facetParameter": "locationCountry",
                        "values": [{"descriptor": "Ruritania", "id": self.FACET_ID, "count": 64}],
                    }
                ],
            }
        ]
        payloads: list[dict[str, Any]] = []

        class Recording(StubFetcher):
            def post_json(self, url: str, payload: dict[str, Any], **kwargs: Any) -> Any:
                payloads.append(payload)
                return super().post_json(url, payload, **kwargs)

        rendered = fixture("path.rendered.html")
        fetcher = Recording(
            [],
            static={probed: probe_fixture("shell.html")},
            rendered={probed: rendered, board: rendered},
            posted={PATH_API: [json.dumps(first)] + [fixture(n) for n in PATH_WALK[1:]]},
        )
        result, report = run_probe(probed, fetcher, curated_dir, tmp_path)

        assert result.kind == probe.REUSE
        assert f"board: {board}  (Workday" in report
        assert f"workday on {board} (dynamic)" in report
        assert f"url: {board}" in report
        assert {str(p["appliedFacets"]) for p in payloads} == {
            str({"locationCountry": [self.FACET_ID]})
        }
        # The detail URLs still carry no query.
        assert result.run is not None
        assert not any("?" in str(r["detail_url"]) for r in result.run.rows)

    def test_a_board_merely_linked_from_the_page_takes_no_query(self) -> None:
        # Only the URL the owner typed says which filter they meant.
        page = '<a href="https://contoso.wd3.myworkdayjobs.com/en-US/Careers?jobFamily=1">'
        scan = probe.scan_page(page, CONTOSO, "static")
        _, boards = probe.fingerprint(CONTOSO, None, [scan])
        assert [b.url for b in boards] == ["https://contoso.wd3.myworkdayjobs.com/Careers"]


class TestVerdict:
    def test_reuse_prints_both_blocks_and_writes_neither(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        result, report = run_probe(
            KOGNITY, kognity([]), curated_dir, tmp_path, name="acme_learning", company="Acme"
        )

        assert result.line.startswith("reuse ashby")
        assert "Printed, not written" in report
        block = report.split("sources.yaml (under `sources:`):")[1].split("registry.py")[0]
        entry = yaml.safe_load(block)
        assert entry == [
            {
                "name": "acme_learning",
                "url": KOGNITY,
                "strategy": "static",
                "company": "Acme",
            }
        ]
        assert '"acme_learning": partial(ashby.extract, source_name="acme_learning"),' in report
        assert "already a key in registry.py" not in report
        assert "python scripts/capture_fixtures.py acme_learning" in report

    def test_the_printed_registry_line_is_python_that_builds_the_reader(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        from functools import partial

        from job_scraper.extractors import successfactors_html

        _, report = run_probe(NOVO, novo([]), curated_dir, tmp_path, name="acme")
        line = next(ln for ln in report.splitlines() if ln.strip().startswith('"acme":'))
        registry = eval(  # noqa: S307 — our own printed line, in a test
            "{" + line.strip() + "}",
            {"partial": partial, "successfactors_html": successfactors_html},
        )
        reader = registry["acme"]
        assert reader.keywords == {
            "source_name": "acme",
            "page_step": 100,
            "base_search_url": NOVO,
        }

    def test_a_name_already_registered_is_flagged(self, curated_dir: Path, tmp_path: Path) -> None:
        _, report = run_probe(KOGNITY, kognity([]), curated_dir, tmp_path)
        assert "'kognity' is already a key in registry.py" in report

    def test_a_missing_company_is_a_comment_not_a_guess(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        _, report = run_probe(KOGNITY, kognity([]), curated_dir, tmp_path)
        assert "# company: <the employer's name>" in report

    def test_a_bespoke_listing_needs_a_new_extractor(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        fetcher = StubFetcher([], static={LITWARE: probe_fixture("bespoke.html")})
        result, report = run_probe(LITWARE, fetcher, curated_dir, tmp_path)

        assert "3 link(s) shaped like a posting" in report
        assert result.kind == probe.NEW_EXTRACTOR
        assert result.line == (
            "needs a new extractor — the static HTML carries job data, on no supported ATS; "
            "a bespoke module would use strategy: static."
        )
        assert "registry.py (in REGISTRY)" not in report

    def test_a_shell_both_ways_is_not_feasible_and_points_at_the_ladder(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        shell = probe_fixture("shell.html")
        fetcher = StubFetcher([], static={FABRIKAM: shell}, rendered={FABRIKAM: shell})
        result, report = run_probe(FABRIKAM, fetcher, curated_dir, tmp_path)

        assert result.kind == probe.NOT_FEASIBLE
        assert result.line.startswith("not feasible — rungs 1-2 of the CU2 ladder")
        assert "the static and rendered HTML shows no postings" in result.line
        assert "no supported ATS was recognised" in result.line
        assert "Rungs 3 and 4" in report
        assert "`probably_good` — genuinely unfixable within this design" in report

    def test_the_report_runs_in_the_documented_order(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        _, report = run_probe(KOGNITY, kognity([]), curated_dir, tmp_path)
        headings = [ln for ln in report.splitlines() if ln[:2] in {f"{i}." for i in range(1, 8)}]
        assert [h[0] for h in headings] == list("1234567")


# --- nothing is written ----------------------------------------------------


def _tree(path: Path) -> list[tuple[str, int, int]]:
    return sorted(
        (str(p.relative_to(path)), p.stat().st_size, p.stat().st_mtime_ns)
        for p in path.rglob("*")
        if p.is_file()
    )


def test_a_probe_writes_nothing_anywhere(curated_dir: Path, tmp_path: Path) -> None:
    registry = Path(probe.__file__).parent / "extractors" / "registry.py"
    sources = write_sources(tmp_path, {"name": "other", "url": FABRIKAM, "strategy": "static"})
    candidate(curated_dir, organisation="Kognity", url=KOGNITY, blocker="403")
    before = (
        _tree(FIXTURES_DIR),
        registry.read_bytes(),
        sources.read_bytes(),
        _tree(curated_dir),
        _tree(tmp_path),
    )

    result, _ = run_probe(KOGNITY, kognity([]), curated_dir, tmp_path)

    assert result.kind == probe.REUSE
    after = (
        _tree(FIXTURES_DIR),
        registry.read_bytes(),
        sources.read_bytes(),
        _tree(curated_dir),
        _tree(tmp_path),
    )
    assert after == before


def test_the_probe_module_never_opens_a_file_for_writing() -> None:
    source = Path(probe.__file__).read_text(encoding="utf-8")
    for needle in ("write_text", "write_bytes", '"w"', "'w'", "os.replace", "capture_fixtures"):
        if needle == "capture_fixtures":
            # Named in the report as advice, never imported.
            assert "import capture_fixtures" not in source
            continue
        assert needle not in source


# --- the CLI ---------------------------------------------------------------


def cli(curated_dir: Path, *argv: str) -> int:
    return sources_cli.main(["--curated-dir", str(curated_dir), *argv])


class TestCli:
    def test_help_fetches_nothing(
        self, curated_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exit_info:
            cli(curated_dir, "probe", "--help")
        assert exit_info.value.code == 0
        assert "never writes a fixture" in capsys.readouterr().out
        assert list(curated_dir.iterdir()) == []

    def test_no_url_is_a_usage_error(self, curated_dir: Path) -> None:
        with pytest.raises(SystemExit) as exit_info:
            cli(curated_dir, "probe")
        assert exit_info.value.code == 2

    def use(self, monkeypatch: pytest.MonkeyPatch, fetcher: StubFetcher) -> list[str]:
        agents: list[str] = []

        def factory(user_agent: str) -> Any:
            agents.append(user_agent)
            return nullcontext(fetcher)

        monkeypatch.setattr(sources_cli, "_probe_fetcher", factory)
        return agents

    def test_reuse_exits_zero(
        self,
        curated_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self.use(monkeypatch, kognity([]))
        assert cli(curated_dir, "probe", KOGNITY, "--name", "acme") == 0
        out = capsys.readouterr().out
        assert "7. Verdict" in out
        assert '"acme": partial(ashby.extract, source_name="acme"),' in out

    def test_anything_else_exits_one(
        self, curated_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.use(monkeypatch, StubFetcher([], static={LITWARE: probe_fixture("bespoke.html")}))
        assert cli(curated_dir, "probe", LITWARE) == 1

    def test_the_user_agent_comes_from_rules_json(
        self, curated_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agents = self.use(monkeypatch, kognity([]))
        rules = tmp_path / "config" / "rules.json"
        rules.parent.mkdir(exist_ok=True)
        rules.write_text(
            json.dumps({"contact_url": "https://owner.example", "contact_email": "o@example.org"})
        )
        cli(curated_dir, "probe", KOGNITY)
        assert agents == ["job-scraper/0.1 (+https://owner.example; contact=o@example.org)"]

    def test_without_rules_json_it_says_who_it_is_honestly(
        self, curated_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agents = self.use(monkeypatch, kognity([]))
        cli(curated_dir, "probe", KOGNITY)
        assert agents == [http_mod.DEFAULT_USER_AGENT]

    def test_not_a_url_is_refused(
        self,
        curated_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fetcher = StubFetcher([])
        self.use(monkeypatch, fetcher)
        assert cli(curated_dir, "probe", "not a url") == 1
        assert "refused" in capsys.readouterr().err
        assert fetcher.fetches == []

    def test_an_unmigrated_list_is_refused_before_the_fetcher_opens(
        self, curated_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (curated_dir / "candidate_sources.xlsx").write_bytes(b"")
        agents = self.use(monkeypatch, kognity([]))
        assert cli(curated_dir, "probe", KOGNITY) == 1
        assert agents == []

    def test_the_live_fetcher_is_the_polite_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No request: only that the block installs politeness and a cache at the path given."""
        with probe.live_fetcher(UA, tmp_path / "cache.sqlite3") as fetcher:
            assert http_mod.current_user_agent() == UA
            assert http_mod.current_robots_policy() is not None
            assert fetcher.user_agent == UA
        assert http_mod.current_robots_policy() is None


# --- http.fetch_page -------------------------------------------------------


@pytest.fixture
def redirecting_server() -> Iterator[str]:
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 - stdlib's spelling
            if self.path == "/old":
                self.send_response(301)
                self.send_header("Location", "/careers")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/careers":
                self.send_response(302)
                self.send_header("Location", "/jobs")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = b"<html><body>jobs</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def test_fetch_page_keeps_the_redirect_chain(redirecting_server: str, tmp_path: Path) -> None:
    origin = redirecting_server
    page = http_mod.fetch_page(f"{origin}/old")
    assert page.final_url == f"{origin}/jobs"
    assert page.redirects == (f"{origin}/old", f"{origin}/careers")
    assert page.text == "<html><body>jobs</body></html>"
    assert http_mod.fetch_text(f"{origin}/old") == page.text

    # And from the response cache, which is how a probe inside the TTL sees it.
    with http_mod.http_cache(tmp_path / "cache.sqlite3"):
        http_mod.fetch_page(f"{origin}/old")
        cached = http_mod.fetch_page(f"{origin}/old")
    assert cached.final_url == f"{origin}/jobs"
    assert cached.redirects == (f"{origin}/old", f"{origin}/careers")


# --- review fix 1: Lever EU boards keep their .eu --------------------------


class TestLeverEu:
    @pytest.mark.parametrize(
        "snippet",
        [
            '<a href="https://jobs.eu.lever.co/contoso/abc">Analyst</a>',
            '<script src="https://api.eu.lever.co/v0/postings/contoso?mode=json"></script>',
        ],
    )
    def test_an_eu_board_keeps_its_eu(self, snippet: str) -> None:
        html = f"<html><body>{snippet}</body></html>"
        _, boards = probe.fingerprint(FABRIKAM, None, [probe.scan_page(html, FABRIKAM, "static")])
        assert [(b.platform.key, b.url, b.eu) for b in boards] == [
            ("lever", "https://jobs.eu.lever.co/contoso", True)
        ]

    def test_a_non_eu_board_is_not_marked_eu(self) -> None:
        html = '<a href="https://api.lever.co/v0/postings/contoso">x</a>'
        _, boards = probe.fingerprint(FABRIKAM, None, [probe.scan_page(html, FABRIKAM, "static")])
        assert [(b.url, b.eu) for b in boards] == [("https://jobs.lever.co/contoso", False)]
        assert boards[0].eu_note is None

    def test_eu_and_non_eu_are_different_boards(self) -> None:
        from job_scraper.urlutil import board_identity

        eu = board_identity("https://jobs.eu.lever.co/contoso")
        assert eu != board_identity("https://jobs.lever.co/contoso")
        assert eu == board_identity("https://jobs.eu.lever.co/contoso/")

    def test_the_report_explains_the_reader_gap(self, curated_dir: Path, tmp_path: Path) -> None:
        # No fixture for api.lever.co, so the reader fails as it would live —
        # and the note beside it says why.
        html = probe_fixture("bespoke.html").replace(
            "</main>", '<a href="https://jobs.eu.lever.co/contoso/abc">Apply</a></main>'
        )
        fetcher = StubFetcher([], static={LITWARE: html})
        result, report = run_probe(LITWARE, fetcher, curated_dir, tmp_path)

        assert "board: https://jobs.eu.lever.co/contoso  (Lever" in report
        note = "EU board: lever.py only calls the non-EU API (api.lever.co)"
        section_4 = report.split("5. Generic readers")[0]
        section_5 = report.split("5. Generic readers")[1].split("6. Pagination")[0]
        assert note in section_4
        assert note in section_5
        assert "FETCH text https://api.lever.co/v0/postings/contoso" in fetcher.log
        assert result.kind == probe.NOT_FEASIBLE


# --- review fix 2: a robots refusal inside a reader is rung 2 --------------


def _http_refusal(url: str) -> RobotsDisallowed:
    """The exception http._check_robots raises, worded and fielded as it is."""
    return RobotsDisallowed(
        f"robots.txt forbids {url} for this user agent. If that rule is not meant for "
        "us, exempt https://boards-api.greenhouse.io by naming it in the source's "
        "`ignore_robots` list in sources.yaml.",
        url=url,
    )


class RefusingFetcher(StubFetcher):
    """Serves the page, then refuses the listed URLs the way the live fetcher would."""

    def __init__(self, log: list[str], refuse: set[str], **kwargs: Any) -> None:
        super().__init__(log, **kwargs)
        self.refuse = refuse

    def text(self, url: str) -> str:
        if url in self.refuse:
            self.log.append(f"FETCH text {url} (refused)")
            raise _http_refusal(url)
        return super().text(url)


class TestReaderRefusedByRobots:
    def test_the_verdict_is_rung_two_and_names_the_url(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        page = probe_fixture("greenhouse_embed.html")
        fetcher = RefusingFetcher(
            [], {GIVEWELL_API}, static={CONTOSO: page}, rendered={CONTOSO: page}
        )
        result, report = run_probe(CONTOSO, fetcher, curated_dir, tmp_path)

        assert result.kind == probe.NOT_FEASIBLE
        assert result.line.startswith(f"not feasible — rung 2: robots.txt forbids {GIVEWELL_API}")
        assert "greenhouse reader asked for" in result.line
        assert "`ignore_robots` exists for a rule not meant for us" in result.line
        assert "the owner's judgement" in result.line
        assert "bug in" not in result.line
        assert "rung 5" not in result.line
        assert f"REFUSED by robots.txt — the reader asked for {GIVEWELL_API}" in report
        assert "bug in" not in report

    def test_a_refusal_outside_the_fetcher_is_read_from_the_exception(
        self, curated_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # workable.py POSTs through http.post_json, not through the fetcher the
        # probe hands it, so the URL comes from the refusal's own message.
        api = "https://apply.workable.com/api/v3/accounts/contoso/jobs"

        def refuse(url: str, payload: Any, **kwargs: Any) -> Any:
            raise _http_refusal(url)

        monkeypatch.setattr(workable, "post_json", refuse)
        board = "https://apply.workable.com/contoso/"
        shell = probe_fixture("shell.html")
        fetcher = StubFetcher([], static={board: shell}, rendered={board: shell})
        result, _ = run_probe(board, fetcher, curated_dir, tmp_path)

        assert result.line.startswith(f"not feasible — rung 2: robots.txt forbids {api}")
        assert "bug in" not in result.line

    def test_an_ordinary_failure_is_still_rung_five(self) -> None:
        platform = next(p for p in probe.PLATFORMS if p.key == "greenhouse")
        board = probe.Board(platform, GIVEWELL_BOARD, "givewell", "test")
        run = probe.ReaderRun(board, "static", error="JSONDecodeError: x")
        result = probe.decide([run], [], None)
        assert "rung 5" in result.line
        assert "bug in greenhouse.py" in result.line


# --- review fix 3: a board assumed from the page says so -------------------


class TestAssumedBoard:
    def test_teamtailor_on_its_own_domain_says_the_page_is_assumed(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        fetcher = StubFetcher([], static={STORYTEL: fixture("storytel.html")})
        result, report = run_probe(STORYTEL, fetcher, curated_dir, tmp_path)

        assert result.kind == probe.REUSE
        section_4 = report.split("4. ATS fingerprint")[1].split("5. Generic readers")[0]
        assert f"board: {STORYTEL}  (Teamtailor, from the page itself)" in section_4
        assert "the board is taken to be the page you probed" in section_4
        assert "probe the listing page instead" in section_4

        verdict = report.split("7. Verdict")[1]
        before_blocks = verdict.split("sources.yaml (under `sources:`)")[0]
        assert "the board is taken to be the page you probed" in before_blocks

    def test_a_hosted_board_has_no_such_note(self, curated_dir: Path, tmp_path: Path) -> None:
        result, report = run_probe(KOGNITY, kognity([]), curated_dir, tmp_path)
        assert result.kind == probe.REUSE
        assert "taken to be the page you probed" not in report

    def test_successfactors_moves_to_search_and_needs_no_note(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        result, report = run_probe(NOVO, novo([]), curated_dir, tmp_path)
        assert result.kind == probe.REUSE
        assert "taken to be the page you probed" not in report


# --- Greenhouse EU boards get the same note --------------------------------


class TestGreenhouseEu:
    @pytest.mark.parametrize(
        "snippet",
        [
            '<a href="https://job-boards.eu.greenhouse.io/contoso/jobs/1">Analyst</a>',
            '<script src="https://boards.eu.greenhouse.io/embed/job_board/js?for=contoso"></script>',
            '<script src="https://boards-api.eu.greenhouse.io/v1/boards/contoso/jobs"></script>',
        ],
    )
    def test_an_eu_board_keeps_its_eu_and_is_marked(self, snippet: str) -> None:
        html = f"<html><body>{snippet}</body></html>"
        _, boards = probe.fingerprint(FABRIKAM, None, [probe.scan_page(html, FABRIKAM, "static")])
        assert [(b.platform.key, b.url, b.eu) for b in boards] == [
            ("greenhouse", "https://job-boards.eu.greenhouse.io/contoso", True)
        ]
        assert boards[0].eu_note is not None
        assert "boards-api.greenhouse.io" in boards[0].eu_note

    def test_the_report_explains_the_reader_gap(self, curated_dir: Path, tmp_path: Path) -> None:
        page = probe_fixture("greenhouse_embed.html").replace(
            "boards.greenhouse.io/embed/job_board/js?for=givewell",
            "boards.eu.greenhouse.io/embed/job_board/js?for=contoso",
        )
        fetcher = StubFetcher([], static={CONTOSO: page}, rendered={CONTOSO: page})
        result, report = run_probe(CONTOSO, fetcher, curated_dir, tmp_path)

        note = "EU board: greenhouse.py only calls the non-EU API"
        section_4 = report.split("5. Generic readers")[0]
        section_5 = report.split("5. Generic readers")[1].split("6. Pagination")[0]
        assert "board: https://job-boards.eu.greenhouse.io/contoso  (Greenhouse" in section_4
        assert note in section_4
        assert note in section_5
        assert "FETCH text https://boards-api.greenhouse.io/v1/boards/contoso/jobs" in "\n".join(
            fetcher.log
        )
        assert result.kind == probe.NOT_FEASIBLE

    def test_a_non_eu_board_has_no_note(self, curated_dir: Path, tmp_path: Path) -> None:
        _, report = run_probe(CONTOSO, contoso_embed([]), curated_dir, tmp_path)
        assert "EU board" not in report


# --- the refused URL travels as a field, not as words ----------------------


class TestRefusalCarriesItsUrl:
    @pytest.fixture
    def disallowing(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
        # The robots.txt fetch is replaced, and the refusal happens before any
        # request is made, so nothing here reaches the network.
        monkeypatch.setattr(
            http_mod,
            "_fetch_robots",
            lambda url, agent, timeout: (200, "User-agent: *\nDisallow: /\n"),
        )
        with http_mod.polite_fetching(user_agent=UA, delay=0):
            yield

    def test_fetch_text_sets_it(self, disallowing: None) -> None:
        url = "https://refusing.example/api/jobs?page=1"
        with pytest.raises(RobotsDisallowed) as info:
            http_mod.fetch_text(url)
        assert info.value.url == url

    def test_post_json_sets_it(self, disallowing: None) -> None:
        url = "https://refusing.example/api/v3/accounts/contoso/jobs"
        with pytest.raises(RobotsDisallowed) as info:
            http_mod.post_json(url, {})
        assert info.value.url == url

    def test_a_bare_refusal_still_works(self) -> None:
        assert RobotsDisallowed("robots.txt forbids it").url is None

    def test_the_probe_reads_the_field_not_the_wording(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        class Reworded(StubFetcher):
            def text(self, url: str) -> str:
                if url == GIVEWELL_API:
                    raise RobotsDisallowed("the site said no", url=url)
                return super().text(url)

        page = probe_fixture("greenhouse_embed.html")
        fetcher = Reworded([], static={CONTOSO: page}, rendered={CONTOSO: page})
        result, _ = run_probe(CONTOSO, fetcher, curated_dir, tmp_path)
        assert result.line.startswith(f"not feasible — rung 2: robots.txt forbids {GIVEWELL_API}")

    def test_a_refusal_without_a_url_says_so(self, curated_dir: Path, tmp_path: Path) -> None:
        class Unnamed(StubFetcher):
            def text(self, url: str) -> str:
                raise RobotsDisallowed("robots.txt forbids something")

        page = probe_fixture("greenhouse_embed.html")
        fetcher = Unnamed([], static={CONTOSO: page}, rendered={CONTOSO: page})
        result, _ = run_probe(CONTOSO, fetcher, curated_dir, tmp_path)
        assert "rung 2: robots.txt forbids (URL not reported)" in result.line
        assert "bug in" not in result.line


# --- an active source supplies the company ---------------------------------


class TestCompanyFromActiveSource:
    def test_the_active_entry_names_the_company(self, curated_dir: Path, tmp_path: Path) -> None:
        write_sources(
            tmp_path,
            {"name": "kognity", "url": KOGNITY, "strategy": "static", "company": "Kognity"},
        )
        _, report = run_probe(KOGNITY, kognity([]), curated_dir, tmp_path)

        block = report.split("sources.yaml (under `sources:`):")[1].split("registry.py")[0]
        assert yaml.safe_load(block)[0]["company"] == "Kognity"
        assert "# company: <the employer's name>" not in report

    def test_an_explicit_company_still_wins(self, curated_dir: Path, tmp_path: Path) -> None:
        write_sources(
            tmp_path,
            {"name": "kognity", "url": KOGNITY, "strategy": "static", "company": "Kognity"},
        )
        _, report = run_probe(KOGNITY, kognity([]), curated_dir, tmp_path, company="Kognity AB")
        assert "company: Kognity AB" in report

    def test_an_active_entry_without_a_company_leaves_the_comment(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        write_sources(tmp_path, {"name": "kognity", "url": KOGNITY, "strategy": "static"})
        _, report = run_probe(KOGNITY, kognity([]), curated_dir, tmp_path)
        assert "# company: <the employer's name>" in report


# --- an active source supplies the name ------------------------------------


class TestNameFromActiveSource:
    def test_the_active_entry_names_the_source(self, curated_dir: Path, tmp_path: Path) -> None:
        # A sources.yaml name that the board URL would never produce.
        write_sources(tmp_path, {"name": "Kognity_EdTech", "url": KOGNITY, "strategy": "static"})
        _, report = run_probe(KOGNITY, kognity([]), curated_dir, tmp_path)

        block = report.split("sources.yaml (under `sources:`):")[1].split("registry.py")[0]
        assert yaml.safe_load(block)[0]["name"] == "Kognity_EdTech"
        assert '"Kognity_EdTech": partial(ashby.extract, source_name="Kognity_EdTech"),' in report

    def test_its_own_registry_key_is_not_called_a_clash(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        write_sources(tmp_path, {"name": "kognity", "url": KOGNITY, "strategy": "static"})
        _, report = run_probe(KOGNITY, kognity([]), curated_dir, tmp_path)
        assert "- name: kognity" in report
        assert "already a key in registry.py" not in report

    def test_an_explicit_name_still_wins_and_is_checked(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        write_sources(tmp_path, {"name": "kognity_new", "url": KOGNITY, "strategy": "static"})
        _, report = run_probe(KOGNITY, kognity([]), curated_dir, tmp_path, name="givewell")
        assert "- name: givewell" in report
        assert "'givewell' is already a key in registry.py" in report

    def test_an_active_source_on_another_board_does_not_lend_its_name(
        self, curated_dir: Path, tmp_path: Path
    ) -> None:
        # Same shared host, different board: not this board's name.
        write_sources(
            tmp_path,
            {
                "name": "other_ashby",
                "url": "https://jobs.ashbyhq.com/contoso",
                "strategy": "static",
            },
        )
        _, report = run_probe(KOGNITY, kognity([]), curated_dir, tmp_path)
        assert "- name: kognity" in report
        assert "other_ashby" not in report


# --- explain follows whichever robots parser this Python has ---------------


class TestExplainAcrossParserVersions:
    """Python 3.13.15 moved urllib.robotparser to RFC 9309 in a patch release.

    Before it, the first matching line decided; after it, the longest one does.
    CI and a laptop can run either, so these assert what holds on both: the
    quoted line always agrees with the policy's own answer.
    """

    def test_the_quoted_line_agrees_where_the_versions_differ(self) -> None:
        text = "User-agent: *\nDisallow: /\nAllow: /careers\n"
        policy = RobotsPolicy(UA, fetch=lambda *a: (200, text))
        verdict = policy.explain("https://a.example/careers/1")
        assert verdict.allowed == policy.allows("https://a.example/careers/1")
        assert verdict.group == "User-agent: *"
        assert verdict.rule == ("Allow: /careers" if verdict.allowed else "Disallow: /")

    def test_a_star_group_is_found(self) -> None:
        text = "User-agent: somebot\nDisallow: /\n\nUser-agent: *\nDisallow: /private\n"
        verdict = RobotsPolicy(UA, fetch=lambda *a: (200, text)).explain(
            "https://a.example/private/x"
        )
        assert verdict.allowed is False
        assert verdict.group == "User-agent: *"
        assert verdict.rule == "Disallow: /private"
