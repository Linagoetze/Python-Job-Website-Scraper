"""SP12: the source-yield report, over a synthetic store where every class occurs.

Every place here is invented. Two sources mirror the real shapes SP5 met: a
board whose postings all name cities in one other country, a third of them
remote but city-bound (`elsewhere`), and a one-country board of 67 postings
that SP5 probed and did not add (`one_country`). The rest are built to trip one
signature each, so a classification that drifts says which one moved.
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
import yaml

from job_scraper.drops import (
    LAYER_DETAIL,
    LAYER_RULES,
    LAYER_TITLE_KEYWORD,
    REFILTER_PREFIX,
)
from job_scraper.experience_filter import RULE_LOCATION_EMPTY_NOT_LISTED
from job_scraper.filtering import RULE_LOC_REMOTE_OVERRIDDEN, RULE_LOC_UNLISTED_CITY
from job_scraper.storage.db import JobStore
from job_scraper.tools import source_yield
from job_scraper.tools.source_yield import (
    CLASS_ELSEWHERE,
    CLASS_STARVED,
    CLASS_TOO_NEW,
    CLASS_WORKING,
    VALUE_EMPTY,
    VALUE_GRADE,
    VALUE_LABEL,
    VALUE_PLACE,
    VALUE_PLACEHOLDER,
    VALUE_TITLE,
    classify,
    gather,
    reconcile,
    value_kind,
    window_runs,
)

_RUNS = 10
_VALDORIA = ["Port Elmsworth, Valdoria", "Northbridge, Valdoria", "Kessel Bay, Valdoria"]
_TARVIA = [f"{town}, TV" for town in ("Ambry", "Coldwater", "Feyn", "Hollin", "Marrow", "Tisk")]

_SOURCES = {
    "working": "static",
    "stored_barren": "static",
    "elsewhere": "static",
    "one_country": "dynamic",
    "empty_fields": "static",
    "placeholder": "static",
    "remote_rule": "static",
    "brand_new": "static",
    "deferred": "static",
}


class _Store:
    """Raw inserts into a store whose schema the real JobStore created."""

    def __init__(self, path: Path) -> None:
        with JobStore(path):
            pass
        self.conn = sqlite3.connect(path)
        for run_id in range(1, _RUNS + 1):
            self.conn.execute(
                "INSERT INTO runs (run_id, started_at, finished_at, kind) "
                "VALUES (?, ?, ?, 'scrape')",
                (run_id, f"2026-01-{run_id:02d}T08:00:00+00:00", f"2026-01-{run_id:02d}T09:00:00"),
            )

    def health(self, source: str, run_id: int, rows: int, ok: bool = True) -> None:
        self.conn.execute(
            "INSERT INTO source_health (source_name, run_id, rows_found, ok) VALUES (?, ?, ?, ?)",
            (source, run_id, rows, int(ok)),
        )

    def drop(
        self,
        source: str,
        run_id: int,
        n: int,
        location: str | list[str],
        *,
        layer: str = LAYER_RULES,
        rule: str = RULE_LOC_UNLISTED_CITY,
        title: str = "Field officer",
    ) -> None:
        locations = location if isinstance(location, list) else [location]
        for i in range(n):
            self.conn.execute(
                "INSERT INTO run_exclusions (run_id, dedupe_key, title, source_name, location, "
                "layer, rule, excluded_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'x')",
                (
                    run_id,
                    f"https://{source}.example/job/{i}",
                    f"{title} {i}" if title else "",
                    source,
                    locations[i % len(locations)],
                    layer,
                    rule,
                ),
            )

    def job(self, source: str, key: str, *, run_id: int, first_seen: str, status: str) -> None:
        self.conn.execute(
            "INSERT INTO jobs (dedupe_key, source_name, title, first_seen, last_seen, "
            "last_run_id, status) VALUES (?, ?, 'Analyst', ?, ?, ?, ?)",
            (key, source, first_seen, first_seen, run_id, status),
        )

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()


def _scrape(store: _Store, source: str, runs: range, rows: int, location, **kw) -> None:
    """A source whose every listed row is dropped at Layer 0, run after run."""
    for run_id in runs:
        store.health(source, run_id, rows)
        store.drop(source, run_id, rows, location, **kw)


@pytest.fixture
def paths(tmp_path: Path) -> tuple[Path, Path]:
    db = tmp_path / "jobs.sqlite3"
    s = _Store(db)
    every = range(1, _RUNS + 1)

    # A on passes: 5 rows a run, 3 dropped at Layer 0, 1 on the title, 1 stored.
    for run_id in every:
        s.health("working", run_id, 5)
        s.drop("working", run_id, 3, _VALDORIA)
        s.drop("working", run_id, 1, "", layer=LAYER_TITLE_KEYWORD, rule="intern")
    s.job(
        "working",
        "https://working.example/job/kept",
        run_id=10,
        first_seen="2026-01-10",
        status="new",
    )

    # A on stored rows alone: every row of the window dropped at Layer 0.
    _scrape(s, "stored_barren", every, 4, _VALDORIA)
    s.job(
        "stored_barren",
        "https://stored.example/old",
        run_id=1,
        first_seen="2025-11-02",
        status="seen",
    )

    # B: SP5's company 1 in invented clothes. 39 postings, all cities in one
    # country, 13 of them remote but bound to a city.
    for run_id in every:
        s.health("elsewhere", run_id, 39)
        s.drop("elsewhere", run_id, 26, _VALDORIA)
        s.drop("elsewhere", run_id, 13, _VALDORIA, rule=RULE_LOC_REMOTE_OVERRIDDEN)

    # B: the one-country board SP5 probed and did not add, 67 postings.
    _scrape(s, "one_country", every, 67, _TARVIA)

    # Looks like B (no pass, nothing stored, many drops), but the field is empty.
    _scrape(s, "empty_fields", every, 6, "")

    # One placeholder, repeated.
    _scrape(s, "placeholder", every, 8, "12 Locations")

    # Real places, but every drop is the remote-region rule: configuration.
    _scrape(s, "remote_rule", every, 5, _TARVIA, rule=RULE_LOC_REMOTE_OVERRIDDEN)

    # Two runs of history.
    _scrape(s, "brand_new", range(9, 11), 7, _TARVIA)

    # Deferred at Layer 0 (an empty field), then dropped at Layer 5: a pass.
    for run_id in every:
        s.health("deferred", run_id, 1)
    s.drop("deferred", 10, 1, "", layer=LAYER_DETAIL, rule=RULE_LOCATION_EMPTY_NOT_LISTED)
    # Read in full and found wanting, so the run stored it as rejected.
    s.job(
        "deferred",
        "https://deferred.example/job/0",
        run_id=10,
        first_seen="2026-01-10",
        status="rejected",
    )

    # Not configured any more: must be left out, not classified.
    _scrape(s, "retired", every, 2, _VALDORIA)
    s.close()

    sources = tmp_path / "sources.yaml"
    sources.write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {"name": n, "url": f"https://{n}.example/jobs", "strategy": st}
                    for n, st in _SOURCES.items()
                ]
            }
        ),
        encoding="utf-8",
    )
    return db, sources


def _yields(db: Path, sources: Path) -> dict[str, source_yield.SourceYield]:
    with closing(source_yield.open_readonly(db)) as conn:
        run_ids = [r["run_id"] for r in window_runs(conn)]
        return {y.name: y for y in gather(conn, source_yield.load_sources(sources), run_ids)}


def _verdicts(db: Path, sources: Path) -> dict[str, source_yield.Verdict]:
    return {name: classify(y) for name, y in _yields(db, sources).items()}


def test_each_class_lands_where_its_evidence_says(paths: tuple[Path, Path]) -> None:
    got = {name: v.klass for name, v in _verdicts(*paths).items()}
    assert got == {
        "working": CLASS_WORKING,
        "stored_barren": CLASS_WORKING,
        "elsewhere": CLASS_ELSEWHERE,
        "one_country": CLASS_ELSEWHERE,
        "empty_fields": CLASS_STARVED,
        "placeholder": CLASS_STARVED,
        "remote_rule": CLASS_STARVED,
        "brand_new": CLASS_TOO_NEW,
        "deferred": CLASS_WORKING,
    }


def test_a_stored_row_keeps_a_barren_source_out_of_b(paths: tuple[Path, Path]) -> None:
    y = _yields(*paths)["stored_barren"]
    assert (y.passes, y.stored, y.last_stored) == (0, 1, "2025-11-02")
    assert "no pass in the window" in classify(y).reasons


def test_a_deferred_row_dropped_at_layer_5_counts_as_a_layer_0_pass(
    paths: tuple[Path, Path],
) -> None:
    y = _yields(*paths)["deferred"]
    assert (y.seen, y.l0_drops, y.passes) == (10, 0, 10)
    assert (y.later_drops, y.deferred_settled) == (1, 1)
    assert "10 of 10 rows passed Layer 0 in the window" in classify(y).reasons


def test_each_starved_source_names_the_signature_that_fired(paths: tuple[Path, Path]) -> None:
    v = _verdicts(*paths)
    assert any("location empty" in r for r in v["empty_fields"].reasons)
    assert any("location placeholder" in r for r in v["placeholder"].reasons)
    assert any("one rule" in r and "remote keyword" in r for r in v["remote_rule"].reasons)


def test_b_is_never_given_to_a_mixed_rule_board_on_rule_grounds(paths: tuple[Path, Path]) -> None:
    """Company 1's shape: two rules, both about real places. That is B, not a config C."""
    y = _yields(*paths)["elsewhere"]
    assert set(y.drops_by_rule) == {RULE_LOC_UNLISTED_CITY, RULE_LOC_REMOTE_OVERRIDDEN}
    assert set(y.values) == set(_VALDORIA)


def test_too_new_says_where_its_evidence_points(paths: tuple[Path, Path]) -> None:
    v = _verdicts(*paths)["brand_new"]
    assert (v.klass, v.provisional) == (CLASS_TOO_NEW, CLASS_ELSEWHERE)


def test_a_single_constant_on_many_postings_is_starved_not_elsewhere() -> None:
    y = source_yield.SourceYield("acme", "static", window_runs=10, seen=60, l0_drops=60)
    y.values["Ambry, TV"] = 60
    y.value_kinds[VALUE_PLACE] = 60
    y.drops_by_rule[RULE_LOC_UNLISTED_CITY] = 60
    y.constant_postings = 6
    assert classify(y).klass == CLASS_STARVED
    y.constant_postings = 1  # one posting seen ten times is just one posting
    assert classify(y).klass == CLASS_ELSEWHERE


def test_a_board_that_listed_nothing_is_starved_not_elsewhere() -> None:
    y = source_yield.SourceYield("acme", "static", window_runs=10)
    v = classify(y)
    assert v.klass == CLASS_STARVED and "nothing listed" in v.reasons[0]


@pytest.mark.parametrize(
    ("location", "title", "kind"),
    [
        ("", "Analyst", VALUE_EMPTY),
        ("   ", "Analyst", VALUE_EMPTY),
        ("3 Locations", "Analyst", VALUE_PLACEHOLDER),
        ("Multiple locations", "Analyst", VALUE_PLACEHOLDER),
        ("Title", "Analyst", VALUE_LABEL),
        ("P-3", "Analyst", VALUE_GRADE),
        ("NOB", "Analyst", VALUE_PLACE),  # a bare grade family with no number is not shaped
        ("Grade 7", "Analyst", VALUE_GRADE),
        ("Programme Analyst", "Programme  analyst", VALUE_TITLE),
        ("Port Elmsworth, Valdoria", "Analyst", VALUE_PLACE),
        ("Feyn, TV - Remote", "Analyst", VALUE_PLACE),
    ],
)
def test_value_kind(location: str, title: str, kind: str) -> None:
    assert value_kind(location, title) == kind


def test_the_drop_log_share_and_the_refilter_rows(paths: tuple[Path, Path]) -> None:
    db, sources = paths
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO run_exclusions (run_id, source_name, location, layer, rule, excluded_at) "
        "VALUES (10, 'elsewhere', 'x', ?, 'intern', 'x')",
        (REFILTER_PREFIX + LAYER_TITLE_KEYWORD,),
    )
    conn.commit()
    conn.close()
    y = _yields(db, sources)["elsewhere"]
    # A re-filter row judges a stored job, not this run's scrape: never counted.
    assert (y.drop_log_rows, y.l0_drops, y.later_drops) == (390, 390, 0)


def test_reconciliation_accounts_for_every_pass_on_the_latest_run(
    paths: tuple[Path, Path],
) -> None:
    db, _ = paths
    with closing(source_yield.open_readonly(db)) as conn:
        checks = reconcile(conn, [r["run_id"] for r in window_runs(conn)])
    latest = checks[-1]
    assert not any(c.gaps for c in checks)
    # Run 10: working passes 2 (1 title drop, 1 stored) and deferred passes 1
    # (dropped at Layer 5 and stored rejected, so sighted all the same).
    assert latest.passes == 3
    assert latest.accounted == (
        "3 passes = 1 dropped on the title + 0 held back unverified + 2 sighted in the store"
    )


def test_a_drop_under_a_source_that_did_not_scrape_is_a_gap(
    paths: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    db, sources = paths
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO run_exclusions (run_id, source_name, location, layer, rule, excluded_at) "
        "VALUES (4, 'ghost', 'Feyn, TV', ?, ?, 'x')",
        (LAYER_RULES, RULE_LOC_UNLISTED_CITY),
    )
    conn.commit()
    conn.close()
    assert source_yield.main(["--db", str(db), "--sources", str(sources)]) == 1
    out = capsys.readouterr().out
    assert "GAP: 1 drops under 'ghost', not scraped this run" in out
    assert "do not reconcile" in out


def test_the_report_writes_nothing(
    paths: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    db, sources = paths
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    assert source_yield.main(["--db", str(db), "--sources", str(sources), "--places", "0"]) == 0
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    out = capsys.readouterr().out
    assert "Classes: A working 3, B truthfully elsewhere 2, C starved, not dead 3, D too new" in out
    assert "Not in sources.yaml, so left out: retired" in out
    assert "(empty)  <- empty" in out


def test_a_missing_store_is_refused_not_created(tmp_path: Path) -> None:
    missing = tmp_path / "nope.sqlite3"
    with pytest.raises(SystemExit, match="No store"):
        source_yield.open_readonly(missing)
    assert not missing.exists()


def test_help_exits_having_opened_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("the tool ran instead of parsing its arguments")

    monkeypatch.setattr(source_yield, "open_readonly", boom)
    monkeypatch.setattr(source_yield, "load_sources", boom)
    with pytest.raises(SystemExit) as exit_info:
        source_yield.main(["--help"])
    assert exit_info.value.code == 0
    assert "--min-runs" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exit_info:
        source_yield.main(["--remove"])
    assert exit_info.value.code != 0


def test_it_never_reads_the_rules_file() -> None:
    """The owner's locations are private: the report has no route to them."""
    assert "load_rules" not in Path(source_yield.__file__).read_text(encoding="utf-8")
