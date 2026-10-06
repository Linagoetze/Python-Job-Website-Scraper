"""SP4g: the re-filter pass re-judges Layer 5's years and PhD from the stored
description, and does nothing else with it.

What is pinned, in the order the owner asked for it:

- a stored 'new' row is judged by the same reading and the same verdict as a
  run (`judge_experience` is shared), with no HTTP;
- a row with nothing to judge is left alone, and "nothing to judge" comes from
  the level and the description, never from the description's length (SP4d);
- 'seen', 'shortlisted' and 'rejected' rows are never touched (WP5);
- the pass never rejects on a deferred location or hybrid state it cannot
  reproduce, because the store has no raw_snippet (SP4g, F13);
- a level is rewritten when the status stays; a dry run changes nothing;
- the drops reach `run_exclusions` under the `refilter/` prefix, in a run of
  their own.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from job_scraper import drops as drops_mod
from job_scraper.drops import LAYER_DETAIL, REFILTER_PREFIX
from job_scraper.experience_filter import (
    _MAX_DESCRIPTION_CHARS,
    EXPERIENCE_UNREADABLE,
    RULE_PHD_REQUIRED,
    judge_experience,
    rejudge_stored_description,
)
from job_scraper.filtering import (
    build_hybrid_pattern,
    build_non_place_pattern,
    build_remote_region_pattern,
)
from job_scraper.pipeline import refilter_stored_jobs
from job_scraper.storage.db import RUN_KIND_REFILTER, JobStore
from job_scraper.tools import retrofilter

_RULES: dict[str, Any] = {
    "locations": ["Berlin"],
    "conditional_locations": ["Munich"],
    "conditional_location_keywords": ["hybrid"],
    "remote_keywords": ["remote"],
    "remote_regions": ["Wingtip Region"],
    "non_place_locations": ["Home based - Wingtip Region"],
}

_SENIOR = "You will bring 6 years of experience in analytics."
_JUNIOR = "You will bring at least 2 years of experience in analytics."
_PHD = "A PhD is required for this role."
_PLAIN = "A great opportunity for someone early in their career."


def _row(slug: str, *, text: str, level: str, **more: Any) -> dict[str, Any]:
    return {
        "dedupe_key": f"https://acme.example/jobs/{slug}",
        "source_name": "acme",
        "company": "Acme",
        "title": f"Analyst {slug}",
        "location": "Berlin",
        "detail_url": f"https://acme.example/jobs/{slug}",
        "description_text": text,
        "experience_level": level,
        **more,
    }


def _seed(db: Path, rows: list[dict[str, Any]], statuses: dict[str, str] | None = None) -> None:
    with JobStore(db) as store:
        run_id = store.begin_run()
        store.upsert_jobs(rows, run_id, now="2026-10-01T00:00:00+00:00")
        for key, status in (statuses or {}).items():
            store.set_status([key], status)
        store.finish_run(run_id)


def _pass(store: JobStore, rules: dict[str, Any] | None = None):
    rules = rules or _RULES
    return refilter_stored_jobs(
        store,
        rules,
        [],
        build_hybrid_pattern(rules),
        build_non_place_pattern(rules),
        remote_region_pattern=build_remote_region_pattern(rules),
    )


def _state(db: Path) -> dict[str, tuple[str, str]]:
    with JobStore(db) as store:
        return {
            r["dedupe_key"].rsplit("/", 1)[-1]: (r["status"], r["experience_level"])
            for r in store.all_jobs()
        }


# --- the verdict is shared with a run ----------------------------------------


@pytest.mark.parametrize(
    ("years", "phd", "expected"),
    [
        (None, False, ("unspecified", None)),
        (2, False, ("junior (<=2yr)", None)),
        (3, False, ("senior (3+yr)", "experience: 3+ years required")),
        (1, True, ("phd_required", RULE_PHD_REQUIRED)),
        (None, True, ("phd_required", RULE_PHD_REQUIRED)),
    ],
)
def test_judge_experience_verdicts(years: int | None, phd: bool, expected: tuple) -> None:
    assert judge_experience(years, phd) == expected


# --- what there is to judge ---------------------------------------------------


def test_a_row_with_no_description_has_nothing_to_judge() -> None:
    assert rejudge_stored_description({"description_text": "", "experience_level": ""}) is None


def test_an_unreadable_row_has_nothing_to_judge() -> None:
    row = {"description_text": _SENIOR, "experience_level": EXPERIENCE_UNREADABLE}
    assert rejudge_stored_description(row) is None


def test_a_short_stored_description_is_judged_not_taken_for_a_shell() -> None:
    """jobsinlund's supplied text runs from 397 characters: length decides nothing."""
    assert len(_SENIOR) < 100
    verdict = rejudge_stored_description({"description_text": _SENIOR, "experience_level": ""})
    assert verdict == ("senior (6+yr)", "experience: 6+ years required")


def test_a_description_at_the_storage_cap_is_not_judged() -> None:
    """It may be a prefix of what a run read, so a figure past the cut is unseen."""
    cap = _MAX_DESCRIPTION_CHARS
    at_cap = (_SENIOR + " ").ljust(cap, "x")
    just_under = (_SENIOR + " ").ljust(cap - 1, "x")
    assert len(at_cap) == cap
    assert rejudge_stored_description({"description_text": at_cap, "experience_level": ""}) is None
    verdict = rejudge_stored_description({"description_text": just_under, "experience_level": ""})
    assert verdict == ("senior (6+yr)", "experience: 6+ years required")


# --- the pass over a store ----------------------------------------------------


def test_a_senior_description_flips_a_new_row_and_writes_its_level(tmp_path: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    _seed(db, [_row("a", text=_SENIOR, level="unspecified")])
    with JobStore(db) as store:
        result = _pass(store)
    assert result.counts["experience"] == 1
    assert _state(db) == {"a": ("rejected", "senior (6+yr)")}
    assert [(d["layer"], d["rule"]) for d in result.drops] == [
        (f"{REFILTER_PREFIX}{LAYER_DETAIL}", "experience: 6+ years required")
    ]


def test_a_phd_description_flips_a_new_row(tmp_path: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    _seed(db, [_row("a", text=_PHD, level="unspecified")])
    with JobStore(db) as store:
        result = _pass(store)
    assert _state(db) == {"a": ("rejected", "phd_required")}
    assert result.drops[0]["rule"] == RULE_PHD_REQUIRED


def test_a_level_is_rewritten_when_the_status_stays(tmp_path: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    _seed(db, [_row("a", text=_JUNIOR, level="unspecified")])
    with JobStore(db) as store:
        result = _pass(store)
    assert _state(db) == {"a": ("new", "junior (<=2yr)")}
    assert result.drops == []
    (change,) = result.level_changes
    assert (change.was, change.now, change.rejected) == ("unspecified", "junior (<=2yr)", False)


def test_an_unchanged_level_is_not_reported(tmp_path: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    _seed(db, [_row("a", text=_PLAIN, level="unspecified")])
    with JobStore(db) as store:
        result = _pass(store)
    assert result.level_changes == ()
    assert _state(db) == {"a": ("new", "unspecified")}


@pytest.mark.parametrize("status", ["seen", "shortlisted", "rejected", "delisted"])
def test_only_new_rows_are_re_judged(tmp_path: Path, status: str) -> None:
    db = tmp_path / "jobs.sqlite3"
    key = "https://acme.example/jobs/a"
    _seed(db, [_row("a", text=_SENIOR, level="unspecified")], {key: status})
    with JobStore(db) as store:
        result = _pass(store)
    assert _state(db) == {"a": (status, "unspecified")}
    assert result.drops == [] and result.level_changes == ()


def test_unreadable_and_empty_rows_are_left_as_they_are(tmp_path: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    _seed(
        db,
        [
            _row("shell", text=_SENIOR, level=EXPERIENCE_UNREADABLE),
            _row("empty", text="", level=""),
        ],
    )
    with JobStore(db) as store:
        result = _pass(store)
    assert _state(db) == {
        "shell": ("new", EXPERIENCE_UNREADABLE),
        "empty": ("new", ""),
    }
    assert result.drops == [] and result.level_changes == ()


# --- the hazard: no verdict on a state the store cannot reproduce ------------


def test_a_hybrid_confirmed_conditional_city_is_not_rejected_for_its_text(
    tmp_path: Path,
) -> None:
    """Hybrid by the platform's own field, never by the description (kognity's shape)."""
    db = tmp_path / "jobs.sqlite3"
    _seed(
        db,
        [_row("a", text=_PLAIN, level="unspecified", location="Munich", hybrid_confirmed=1)],
    )
    with JobStore(db) as store:
        result = _pass(store)
    assert _state(db) == {"a": ("new", "unspecified")}
    assert result.drops == []


def test_a_deferred_location_is_never_rejected_by_this_pass(tmp_path: Path) -> None:
    """An empty field, a placeholder and a conditional city all stay, though the
    description names no listed place: a run settled them from what it read, and
    the store has no raw_snippet to read that again from."""
    db = tmp_path / "jobs.sqlite3"
    _seed(
        db,
        [
            _row("empty", text=_PLAIN, level="unspecified", location=""),
            _row(
                "placeholder",
                text=_PLAIN,
                level="unspecified",
                location="Home based - Wingtip Region",
            ),
            _row("munich", text=_PLAIN, level="unspecified", location="Munich"),
        ],
    )
    with JobStore(db) as store:
        result = _pass(store)
    assert {k: v[0] for k, v in _state(db).items()} == {
        "empty": "new",
        "placeholder": "new",
        "munich": "new",
    }
    assert result.counts == {"rules": 0, "title": 0, "title_keywords": 0, "experience": 0}


def test_a_deferred_row_is_still_judged_on_its_years(tmp_path: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    _seed(db, [_row("a", text=_SENIOR, level="unspecified", location="")])
    with JobStore(db) as store:
        _pass(store)
    assert _state(db) == {"a": ("rejected", "senior (6+yr)")}


# --- the dry run and the drop log ---------------------------------------------


def _fake_env(monkeypatch: pytest.MonkeyPatch, db: Path, tmp_path: Path) -> None:
    monkeypatch.setattr(retrofilter, "default_jobs_db_path", lambda: db)
    monkeypatch.setattr(retrofilter, "default_jobs_xlsx_path", lambda: tmp_path / "jobs.xlsx")
    monkeypatch.setattr(retrofilter, "load_rules", lambda: dict(_RULES))
    monkeypatch.setattr(retrofilter, "default_title_keywords_path", lambda: tmp_path / "none.csv")
    monkeypatch.setattr(retrofilter, "load_title_exclude_keywords", lambda path: [])


def _bytes_of_tables(db: Path) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return [
            tuple(r)
            for t in ("jobs", "runs", "run_exclusions")
            for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")
        ]
    finally:
        conn.close()


def test_a_dry_run_prints_the_changes_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "jobs.sqlite3"
    _seed(
        db,
        [
            _row("senior", text=_SENIOR, level="unspecified"),
            _row("lower", text=_JUNIOR, level="unspecified"),
        ],
    )
    before = _bytes_of_tables(db)
    _fake_env(monkeypatch, db, tmp_path)
    monkeypatch.setattr("sys.argv", ["retrofilter", "--dry-run"])

    retrofilter.main()

    out = capsys.readouterr().out
    assert "nothing was written" in out
    assert "Status changes (new -> rejected): 1" in out
    assert "Analyst senior" in out
    assert "Level-only changes (status stays new): 1" in out
    assert "unspecified -> junior (<=2yr)" in out
    assert _bytes_of_tables(db) == before
    assert not (tmp_path / "jobs.xlsx").exists()


def test_a_real_pass_logs_its_drops_in_a_run_of_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "jobs.sqlite3"
    _seed(db, [_row("senior", text=_SENIOR, level="unspecified")])
    _fake_env(monkeypatch, db, tmp_path)
    monkeypatch.setattr(retrofilter, "write_xlsx", lambda *a, **k: 0)
    monkeypatch.setattr("sys.argv", ["retrofilter"])

    retrofilter.main()

    with JobStore(db) as store:
        latest = store.latest_exclusion_run(RUN_KIND_REFILTER)
        assert latest is not None
        logged = store.exclusions(latest)
        runs = store._c().execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    assert runs == 2, "the seeding run and the pass's own"
    assert [(r["layer"], r["rule"]) for r in logged] == [
        (f"{REFILTER_PREFIX}{LAYER_DETAIL}", "experience: 6+ years required")
    ]
    assert drops_mod.layer_display(logged[0]["layer"]).endswith("(re-filter)")
    assert "Rewrote the experience level of 1 rows" in capsys.readouterr().out


def test_a_pass_with_nothing_to_log_opens_no_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "jobs.sqlite3"
    _seed(db, [_row("a", text=_PLAIN, level="unspecified")])
    _fake_env(monkeypatch, db, tmp_path)
    monkeypatch.setattr(retrofilter, "write_xlsx", lambda *a, **k: 0)
    monkeypatch.setattr("sys.argv", ["retrofilter"])

    retrofilter.main()

    with JobStore(db) as store:
        assert store._c().execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


# --- the refilter run does not disturb the scrape's drop log ------------------


def _log(store: JobStore, kind: str, rule: str = "r") -> int:
    run_id = store.begin_run(kind=kind)
    store.record_exclusions(
        run_id, [{"dedupe_key": f"k{run_id}", "layer": "0-rules", "rule": rule}]
    )
    store.finish_run(run_id)
    return run_id


def test_a_refilter_run_does_not_hide_the_last_scrape(tmp_path: Path) -> None:
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        scrape = _log(store, "scrape")
        refilter = _log(store, RUN_KIND_REFILTER)
        assert store.latest_exclusion_run() == scrape
        assert store.latest_exclusion_run(RUN_KIND_REFILTER) == refilter


def test_a_refilter_run_uses_no_retention_slot(tmp_path: Path) -> None:
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        first = _log(store, "scrape")
        second = _log(store, "scrape")
        for _ in range(3):
            _log(store, RUN_KIND_REFILTER)
        store.prune_exclusions(2)
        assert store.exclusions(first) and store.exclusions(second)


def test_a_refilter_run_ages_out_with_the_scrapes(tmp_path: Path) -> None:
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        old_pass = _log(store, RUN_KIND_REFILTER)
        _log(store, "scrape")
        last = _log(store, "scrape")
        recent_pass = _log(store, RUN_KIND_REFILTER)
        store.prune_exclusions(1)
        assert not store.exclusions(old_pass)
        assert store.exclusions(recent_pass) and store.exclusions(last)


def test_a_store_without_the_run_kind_column_is_migrated(tmp_path: Path) -> None:
    db = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE runs (run_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " started_at TEXT NOT NULL, finished_at TEXT)"
    )
    conn.execute("INSERT INTO runs (started_at) VALUES ('2026-01-01')")
    conn.commit()
    conn.close()
    with JobStore(db) as store:
        kinds = [r[0] for r in store._c().execute("SELECT kind FROM runs")]
    assert kinds == ["scrape"]
