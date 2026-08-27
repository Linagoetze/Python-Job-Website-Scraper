"""The drop log (WP8a): every exclusion recorded, naming the rule that fired.

Two things are being pinned here. The first is coverage — that no filter layer
silently drops a job without logging it, which is the blindness the package
exists to remove. The second is attribution — that the logged rule names the
*specific* keyword, term or location case, because a layer name
alone is what made a false negative unfindable in the first place.

No network: the pipeline's extractor and both fetchers are replaced, and one
test counts the fetches to pin that logging costs no HTTP request at all.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from job_scraper import drops as drops_mod
from job_scraper import pipeline as pipeline_mod
from job_scraper.drops import (
    LAYER_DETAIL,
    LAYER_REVIEW_STATUS,
    LAYER_RULES,
    LAYER_SENIORITY,
    LAYER_TITLE_KEYWORD,
    REFILTER_PREFIX,
    rule_counts,
)
from job_scraper.filtering import (
    _LOCATION_EMPTY_ADMITTED_REASON,
    RULE_LOC_CONDITIONAL_UNGATED,
    RULE_LOC_REMOTE_OVERRIDDEN,
    RULE_LOC_UNLISTED_CITY,
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
    undifferentiated reason. These are the cases worth telling apart — three
    that still reject, and (WP8f) an empty field, which no longer does."""

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

    def test_missing_location_field_is_admitted_not_rejected(self) -> None:
        # WP8f: an empty field is not a place that failed to match — it is
        # settled outright at Layer 0, permanently, with its own reason.
        job = _job("Analyst", location="", slug="x", snippet="Analyst")
        hybrid_pattern = build_hybrid_pattern(self._RULES_WITH_GATE)
        ok, reasons = matches_rules(job, self._RULES_WITH_GATE, hybrid_pattern)
        assert ok
        assert reasons == [_LOCATION_EMPTY_ADMITTED_REASON]

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

    # Two location cases: 'no-location' (WP8f) is now admitted, not dropped.
    assert by_layer[LAYER_RULES] == 2
    assert by_layer[LAYER_TITLE_KEYWORD] == 1
    assert by_layer[LAYER_SENIORITY] == 1
    assert by_layer[LAYER_REVIEW_STATUS] == 1
    assert by_layer[LAYER_DETAIL] == 3  # years, PhD, not-hybrid

    # Nothing is logged twice, and nothing kept is logged at all.
    assert summary.exclusions_logged == len(rows)
    assert "kept" not in _rules_by_slug(rows)


def test_each_rule_names_the_specific_thing_that_fired(env: Path) -> None:
    _run(env)
    rules = _rules_by_slug(_logged(env))

    assert rules["unlisted-city"] == RULE_LOC_UNLISTED_CITY
    # 'no-location' is no longer in this table at all — WP8f admits it, so it
    # is never dropped and never logged.
    assert "no-location" not in rules
    assert rules["remote-overridden"] == RULE_LOC_REMOTE_OVERRIDDEN
    # The keyword and its match type, not just "a title keyword matched".
    assert rules["keyword"] == "title_keyword: 'marketing' (word)"
    assert rules["seniority"] == "seniority: 'Head of' (word)"
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
    # None of the jobs dropped before Layer 2 was opened. 'no-location' is not
    # in this set: WP8f admits it at Layer 0, so it does reach Layer 2, on the
    # same footing as any other newly-seen job with no prior verdict on it.
    dropped_early = {"unlisted-city", "remote-overridden", "keyword",
                     "seniority", "blocked"}
    assert not any(url.rsplit("/", 1)[-1] in dropped_early for url in fetched)


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

    # Two, since WP8f: 'no-location' is admitted, not dropped, so it no longer
    # contributes a "locations: ..." row here.
    assert len(located) == 2
    assert all("locations" in r["rule"] for r in located)
    assert len(detail) == 3
    assert elsewhere == []
    assert sum(n for _, _, n in rule_counts(located)) == len(located)


# --- the display ordinal (WP8h) ----------------------------------------------


def test_display_order_is_pinned() -> None:
    """The ladder's execution order, 1-5, with no gap and no layer 0.

    `LAYER_RULES`, historically prefixed '0', displays first — the numeric
    prefix on the stored id is not the display ordinal, which is the whole
    point of the renumbering. If a future package reorders the pipeline, this
    is the test that must be updated deliberately.
    """
    assert [(layer.id, layer.display, layer.name) for layer in drops_mod.LAYERS] == [
        (LAYER_RULES, 1, "Location and rules"),
        (LAYER_TITLE_KEYWORD, 2, "Title keywords"),
        (LAYER_SENIORITY, 3, "Seniority"),
        (LAYER_REVIEW_STATUS, 4, "Review status"),
        (LAYER_DETAIL, 5, "Detail page"),
    ]
    assert [layer.display for layer in drops_mod.LAYERS] == [1, 2, 3, 4, 5]


def test_a_retired_stored_id_renders_without_raising() -> None:
    """WP8 deleted 1c-non-english and 1b-language; ~49,000 old rows still name
    them. `layer_display` must show that instead of raising KeyError."""
    assert drops_mod.layer_display("1c-non-english") == "1c-non-english (retired)"
    assert drops_mod.layer_display("1b-language") == "1b-language (retired)"


def test_a_current_layer_renders_as_layer_n_name() -> None:
    assert drops_mod.layer_display(LAYER_RULES) == "Layer 1: Location and rules"
    assert drops_mod.layer_display(LAYER_DETAIL) == "Layer 5: Detail page"


def test_a_refiltered_layer_renders_as_its_base_layer_marked_re_filter() -> None:
    assert (
        drops_mod.layer_display(f"{REFILTER_PREFIX}{LAYER_SENIORITY}")
        == "Layer 3: Seniority (re-filter)"
    )
    # A retired layer behind the prefix still renders, not raises.
    assert (
        drops_mod.layer_display(f"{REFILTER_PREFIX}1c-non-english")
        == f"{REFILTER_PREFIX}1c-non-english (retired)"
    )


def test_layer_ordinal_raises_for_a_retired_id() -> None:
    """`layer_ordinal` is for current ids only; a retired one has no ordinal to
    give, and inventing one would be worse than refusing."""
    with pytest.raises(KeyError):
        drops_mod.layer_ordinal("1c-non-english")


def test_rule_counts_report_shows_the_display_label_not_the_stored_id(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(env)
    monkeypatch.setattr(sys, "argv", ["drops.py", "--db", str(env / "jobs.sqlite3")])
    drops_mod.main()

    out = capsys.readouterr().out
    assert "Layer 1: Location and rules" in out
    assert "0-rules" not in out


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
    # header + the two Layer 0 drops (WP8f: 'no-location' is admitted, not one)
    assert len(lines) == 3


def test_cli_without_a_run_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["drops.py", "--db", str(tmp_path / "empty.sqlite3")])
    with pytest.raises(SystemExit) as exc:
        drops_mod.main()
    assert "No exclusions recorded yet" in str(exc.value)
