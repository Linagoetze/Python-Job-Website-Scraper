"""The drop log (WP8a): every exclusion recorded, naming the rule that fired.

Two things are being pinned here. The first is coverage — that no filter layer
silently drops a job without logging it, which is the blindness the package
exists to remove. The second is attribution — that the logged rule names the
*specific* keyword, term, language code or location case, because a layer name
alone is what made a false negative unfindable in the first place.

No network: the pipeline's extractor and both fetchers are replaced, and one
test counts the fetches to pin that logging costs no HTTP request at all.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from job_scraper import drops as drops_mod
from job_scraper import pipeline as pipeline_mod
from job_scraper.drops import (
    LAYER_DETAIL,
    LAYER_LANGUAGE,
    LAYER_REVIEW_STATUS,
    LAYER_RULES,
    LAYER_SENIORITY,
    LAYER_TITLE_KEYWORD,
    REFILTER_PREFIX,
    rule_counts,
)
from job_scraper.filtering import (
    RULE_LOC_CONDITIONAL_UNGATED,
    RULE_LOC_EMPTY,
    RULE_LOC_REMOTE_OVERRIDDEN,
    RULE_LOC_UNLISTED_CITY,
    apply_non_english_text_filter,
    build_hybrid_pattern,
    matches_rules,
)
from job_scraper.pipeline import run_pipeline
from job_scraper.storage.db import JobStore

_SOURCE = "acme"
_LISTING = "https://acme.example/jobs"

_SENIOR_BODY = "We are looking for someone with 8+ years of experience in the field."
_PHD_BODY = "A PhD is required for this role."
_OFFICE_BODY = "You will be in the office five days a week."
_PLAIN_BODY = "A great opportunity for someone early in their career."


def _job(title: str, *, location: str, slug: str, snippet: str = "") -> dict[str, str]:
    return {
        "source_name": _SOURCE,
        "title": title,
        "company": "Acme",
        "location": location,
        "listing_url": _LISTING,
        "detail_url": f"{_LISTING}/{slug}",
        "apply_url": "",
        "raw_snippet": snippet or f"{title} {location}",
    }


# One job per layer, plus the four location cases the owner needs told apart.
_EXTRACTED = [
    _job("Data Analyst", location="Berlin", slug="kept"),
    _job("Data Analyst", location="Lisbon", slug="unlisted-city"),
    _job("Data Analyst", location="", slug="no-location"),
    _job("Data Analyst", location="Remote | Nairobi", slug="remote-overridden"),
    _job("Marketing Analyst", location="Berlin", slug="keyword"),
    _job("Head of Data", location="Berlin", slug="seniority"),
    _job("Analyst (Dutch speaking)", location="Berlin", slug="lang"),
    _job("Data Analyst", location="Berlin", slug="blocked"),
    _job("Reporting Analyst", location="Berlin", slug="senior-detail"),
    _job("Research Analyst", location="Berlin", slug="phd"),
    _job("Insight Analyst", location="Munich", slug="not-hybrid"),
]

_RULES = {
    "locations": ["Berlin"],
    "conditional_locations": ["Munich"],
    "conditional_location_keywords": ["hybrid"],
    "remote_keywords": ["remote"],
    "seniority_filter_enabled": True,
    "seniority_exclude_titles": ["Head of"],
}


def _fake_fetch(url: str, *args: Any, **kwargs: Any) -> str:
    if url.endswith("senior-detail"):
        return _SENIOR_BODY
    if url.endswith("phd"):
        return _PHD_BODY
    if url.endswith("not-hybrid"):
        return _OFFICE_BODY
    return _PLAIN_BODY


def _seed_rejected_job(db_path: Path) -> None:
    with JobStore(db_path) as store:
        run_id = store.begin_run()
        store.upsert_jobs(
            [
                {
                    "dedupe_key": f"{_LISTING}/blocked",
                    "source_name": _SOURCE,
                    "title": "Data Analyst",
                    "location": "Berlin",
                    "detail_url": f"{_LISTING}/blocked",
                }
            ],
            run_id,
            now="2026-01-01T00:00:00+00:00",
        )
        store.set_status([f"{_LISTING}/blocked"], "rejected")
        store.finish_run(run_id)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "sources.yaml").write_text(
        yaml.dump({"sources": [{"name": _SOURCE, "url": _LISTING, "strategy": "static"}]}),
        encoding="utf-8",
    )
    (tmp_path / "rules.json").write_text(json.dumps(_RULES), encoding="utf-8")
    (tmp_path / "title_exclude_keywords.csv").write_text(
        "keyword,match\nmarketing,word\n", encoding="utf-8"
    )
    _seed_rejected_job(tmp_path / "jobs.sqlite3")
    monkeypatch.setattr(
        pipeline_mod, "get_extractor", lambda name: lambda url, fetch_fn: list(_EXTRACTED)
    )
    monkeypatch.setattr(pipeline_mod, "fetch_text", _fake_fetch)
    monkeypatch.setattr(pipeline_mod, "fetch_rendered", _fake_fetch)
    return tmp_path


def _run(tmp_path: Path, **kwargs: Any):
    return run_pipeline(
        sources_path=tmp_path / "sources.yaml",
        rules_path=tmp_path / "rules.json",
        out_db_path=tmp_path / "jobs.sqlite3",
        title_keywords_path=tmp_path / "title_exclude_keywords.csv",
        **kwargs,
    )


def _logged(tmp_path: Path) -> list[dict[str, Any]]:
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        run_id = store.latest_exclusion_run()
        assert run_id is not None
        return store.exclusions(run_id)


def _rules_by_slug(rows: list[dict[str, Any]]) -> dict[str, str]:
    return {str(r["dedupe_key"]).rsplit("/", 1)[-1]: str(r["rule"]) for r in rows}


# --- the location breakdown, unit-level -------------------------------------


class TestLocationDropRules:
    """7,378 of one real run's 8,154 exclusions happened here, under a single
    undifferentiated reason. These are the four cases worth telling apart."""

    _RULES_WITH_GATE = {
        "locations": ["Berlin"],
        "conditional_locations": ["Munich"],
        "conditional_location_keywords": ["hybrid"],
        "remote_keywords": ["remote"],
    }

    def _reason(self, location: str, rules: dict[str, Any], snippet: str = "") -> str:
        job = _job("Analyst", location=location, slug="x", snippet=snippet or "Analyst")
        ok, reasons = matches_rules(job, rules, build_hybrid_pattern(rules))
        assert not ok
        return reasons[0]

    def test_missing_location_field(self) -> None:
        assert self._reason("", self._RULES_WITH_GATE) == RULE_LOC_EMPTY

    def test_city_not_on_the_list(self) -> None:
        assert self._reason("Lisbon", self._RULES_WITH_GATE) == RULE_LOC_UNLISTED_CITY

    def test_remote_keyword_overridden_by_a_named_city(self) -> None:
        # The Impactpool shape: every posting tagged "Remote | <duty station>".
        assert (
            self._reason("Remote | Nairobi", self._RULES_WITH_GATE)
            == RULE_LOC_REMOTE_OVERRIDDEN
        )

    def test_conditional_city_with_no_hybrid_gate_configured(self) -> None:
        ungated = dict(self._RULES_WITH_GATE, conditional_location_keywords=[])
        assert self._reason("Munich", ungated) == RULE_LOC_CONDITIONAL_UNGATED

    def test_conditional_city_with_a_gate_is_not_a_layer_0_drop(self) -> None:
        # It passes provisionally and Layer 2 settles it — so the "conditional
        # city, not hybrid" verdict is logged there, not here.
        job = _job("Analyst", location="Munich", slug="x")
        ok, _ = matches_rules(
            job, self._RULES_WITH_GATE, build_hybrid_pattern(self._RULES_WITH_GATE)
        )
        assert ok


# --- every layer logs, and names its rule -----------------------------------


def test_every_layer_records_its_exclusions(env: Path) -> None:
    summary = _run(env)
    rows = _logged(env)

    by_layer: dict[str, int] = {}
    for row in rows:
        by_layer[str(row["layer"])] = by_layer.get(str(row["layer"]), 0) + 1

    assert by_layer[LAYER_RULES] == 3  # the three location cases
    assert by_layer[LAYER_TITLE_KEYWORD] == 1
    assert by_layer[LAYER_SENIORITY] == 1
    assert by_layer[LAYER_LANGUAGE] == 1
    assert by_layer[LAYER_REVIEW_STATUS] == 1
    assert by_layer[LAYER_DETAIL] == 3  # years, PhD, not-hybrid

    # Nothing is logged twice, and nothing kept is logged at all.
    assert summary.exclusions_logged == len(rows)
    assert "kept" not in _rules_by_slug(rows)


def test_each_rule_names_the_specific_thing_that_fired(env: Path) -> None:
    _run(env)
    rules = _rules_by_slug(_logged(env))

    assert rules["unlisted-city"] == RULE_LOC_UNLISTED_CITY
    assert rules["no-location"] == RULE_LOC_EMPTY
    assert rules["remote-overridden"] == RULE_LOC_REMOTE_OVERRIDDEN
    # The keyword and its match type, not just "a title keyword matched".
    assert rules["keyword"] == "title_keyword: 'marketing' (word)"
    assert rules["seniority"] == "seniority: 'Head of' (word)"
    assert rules["lang"] == "language_speaker: 'dutch'"
    assert rules["blocked"] == "review status: already rejected"
    assert rules["senior-detail"] == "experience: 8+ years required"
    assert rules["phd"].startswith("phd: required")
    assert rules["not-hybrid"].startswith("hybrid: conditional city")


def test_metadata_travels_with_the_exclusion(env: Path) -> None:
    """The log is answerable on its own: a row identifies the job it dropped
    without a join back to a jobs table it was never written to."""
    _run(env)
    row = next(r for r in _logged(env) if str(r["dedupe_key"]).endswith("unlisted-city"))

    assert row["title"] == "Data Analyst"
    assert row["company"] == "Acme"
    assert row["source_name"] == _SOURCE
    assert row["location"] == "Lisbon"
    assert row["excluded_at"]


def test_logging_costs_no_extra_http_request(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The package's hard constraint: titles and metadata only. Every fetch in
    a run must still be a Layer 2 detail page for a job that reached Layer 2,
    so the count matches the funnel's own intake."""
    fetched: list[str] = []

    def counting_fetch(url: str, *args: Any, **kwargs: Any) -> str:
        fetched.append(url)
        return _fake_fetch(url)

    monkeypatch.setattr(pipeline_mod, "fetch_text", counting_fetch)
    monkeypatch.setattr(pipeline_mod, "fetch_rendered", counting_fetch)

    summary = _run(env)

    assert len(fetched) == summary.jobs_new_checked + summary.jobs_stored_rechecked
    # None of the jobs dropped before Layer 2 was opened.
    dropped_early = {"unlisted-city", "no-location", "remote-overridden", "keyword",
                     "seniority", "lang", "blocked"}
    assert not any(url.rsplit("/", 1)[-1] in dropped_early for url in fetched)


def test_language_filter_names_the_language_it_saw() -> None:
    """langdetect's verdict is the whole diagnosis for that layer, so the code
    it returned has to be in the rule rather than folded into 'non-English'."""
    fake = MagicMock()
    fake.detect.return_value = "sv"
    fake.LangDetectException = type("LangDetectException", (Exception,), {})
    with patch.dict(sys.modules, {"langdetect": fake}):
        _, excluded = apply_non_english_text_filter(
            [_job("Produktchef sokes till vart team i Malmo nu", location="Malmo", slug="x")]
        )

    assert excluded[0]["drop_rule"] == "non_english: langdetect 'sv'"


# --- the re-filter pass is logged, and kept separable -----------------------


def test_refilter_exclusions_are_logged_under_their_own_layer(env: Path) -> None:
    """A stored unreviewed job that a tightened rule now rejects is a real
    exclusion. It is logged, but not under the same layer name as this run's
    scrape — mixing the two populations would make the counts unreadable."""
    _run(env)

    # Tighten the rules so the one kept job no longer passes.
    (env / "rules.json").write_text(
        json.dumps(dict(_RULES, locations=["Hamburg"])), encoding="utf-8"
    )
    _run(env)

    layers = {str(r["layer"]) for r in _logged(env)}
    assert any(layer.startswith(REFILTER_PREFIX) for layer in layers)


# --- retention, transactionality --------------------------------------------


def test_only_the_last_n_runs_are_kept(env: Path) -> None:
    """Every run logs thousands of rows in real use, so the table has to have a
    ceiling — but the recent runs, which are the ones a rule change is judged
    against, must survive."""
    run_ids: list[int] = []
    for _ in range(4):
        _run(env, keep_drop_runs=2)
        with JobStore(env / "jobs.sqlite3") as store:
            latest = store.latest_exclusion_run()
            assert latest is not None
            run_ids.append(latest)

    assert len(set(run_ids)) == 4, "each run logs under its own run id"
    with JobStore(env / "jobs.sqlite3") as store:
        kept = [rid for rid in run_ids if store.exclusions(rid)]
    assert kept == run_ids[-2:]


def test_exclusions_roll_back_with_the_run(tmp_path: Path) -> None:
    """They are written inside the run's transaction, so a crash mid-run leaves
    no orphan drop log describing a run that never committed."""
    db_path = tmp_path / "jobs.sqlite3"
    with JobStore(db_path) as store:
        run_id = store.begin_run()
        store.finish_run(run_id)

    with pytest.raises(RuntimeError):
        with JobStore(db_path) as store:
            crashed = store.begin_run()
            store.record_exclusions(
                crashed,
                [{"dedupe_key": "k", "title": "t", "layer": "0-rules", "rule": "r"}],
            )
            raise RuntimeError("boom")

    with JobStore(db_path) as store:
        assert store.latest_exclusion_run() is None


# --- reading it back --------------------------------------------------------


def test_filters_narrow_both_the_listing_and_the_counts(env: Path) -> None:
    _run(env)
    with JobStore(env / "jobs.sqlite3") as store:
        run_id = store.latest_exclusion_run()
        assert run_id is not None
        # Substring, case-insensitive: nobody should have to type a rule verbatim.
        located = store.exclusions(run_id, rule="LOCATIONS")
        detail = store.exclusions(run_id, layer="2-detail")
        elsewhere = store.exclusions(run_id, source="nothing-like-this")

    assert len(located) == 3
    assert all("locations" in r["rule"] for r in located)
    assert len(detail) == 3
    assert elsewhere == []
    assert sum(n for _, _, n in rule_counts(located)) == len(located)


def test_cli_shows_the_last_run(env: Path, monkeypatch: pytest.MonkeyPatch,
                                capsys: pytest.CaptureFixture[str]) -> None:
    _run(env)
    monkeypatch.setattr(
        sys, "argv", ["drops.py", "--db", str(env / "jobs.sqlite3"), "--show-drops"]
    )
    drops_mod.main()

    out = capsys.readouterr().out
    assert "title_keyword: 'marketing' (word)" in out
    assert "Marketing Analyst" in out


def test_cli_summary_counts_the_rules(env: Path, monkeypatch: pytest.MonkeyPatch,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    _run(env)
    monkeypatch.setattr(sys, "argv", ["drops.py", "--db", str(env / "jobs.sqlite3")])
    drops_mod.main()

    out = capsys.readouterr().out
    assert "Exclusions in run" in out
    assert RULE_LOC_UNLISTED_CITY in out


def test_cli_exports_csv(env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                         capsys: pytest.CaptureFixture[str]) -> None:
    _run(env)
    out_path = tmp_path / "exported" / "drops.csv"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "drops.py",
            "--db",
            str(env / "jobs.sqlite3"),
            "--layer",
            "0-rules",
            "--drops-csv",
            str(out_path),
        ],
    )
    drops_mod.main()
    capsys.readouterr()

    lines = out_path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("run_id,dedupe_key,title,company,source_name,location,layer,rule")
    assert len(lines) == 4  # header + the three Layer 0 drops


def test_cli_without_a_run_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["drops.py", "--db", str(tmp_path / "empty.sqlite3")])
    with pytest.raises(SystemExit) as exc:
        drops_mod.main()
    assert "No exclusions recorded yet" in str(exc.value)
