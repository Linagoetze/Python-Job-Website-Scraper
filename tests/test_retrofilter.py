"""§7B: `retrofilter` must judge placeholder locations exactly as the scraper
does, by building `non_place_pattern` from the same rules and passing it down.

Before the fix, `retrofilter` called `refilter_stored_jobs` without
`non_place_pattern`, so a stored job whose location is a bare region name
("EMEA", "Worldwide") was judged "a city not on the list" and permanently
marked rejected — a verdict the scraper itself would never reach, since
`pipeline.py` always builds and passes that pattern.
"""

from __future__ import annotations

from typing import Any

import pytest

from job_scraper.tools import retrofilter


class _DummyStore:
    def __enter__(self) -> _DummyStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def test_main_passes_a_non_place_pattern_built_from_the_same_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_refilter_stored_jobs(
        store: object, rules: dict[str, Any], title_keywords: object, *args: object
    ) -> tuple[dict[str, int], list[Any]]:
        # non_place_pattern is the last positional argument the fixed call
        # site passes, after hybrid_pattern.
        captured["non_place_pattern"] = args[-1] if args else None
        return {"rules": 0, "title": 0, "title_keywords": 0}, []

    monkeypatch.setattr(retrofilter, "load_rules", lambda: {"non_place_locations": ["Worldwide"]})
    monkeypatch.setattr(retrofilter, "default_jobs_db_path", lambda: "unused.sqlite3")
    monkeypatch.setattr(retrofilter, "default_jobs_xlsx_path", lambda: "unused.xlsx")
    monkeypatch.setattr(retrofilter, "JobStore", lambda db_path: _DummyStore())
    monkeypatch.setattr(retrofilter, "refilter_stored_jobs", fake_refilter_stored_jobs)
    monkeypatch.setattr(retrofilter, "write_xlsx", lambda db_path, xlsx_path: 0)
    monkeypatch.setattr("sys.argv", ["retrofilter"])

    retrofilter.main()

    pattern = captured["non_place_pattern"]
    assert pattern is not None
    assert pattern.search("Worldwide") is not None
