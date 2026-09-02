"""WP10: `retrofilter` and `blocklist_all` now read their argv.

Until now neither parsed `sys.argv` at all, so an argument was not something
they rejected — it was text they never looked at, and the command ran. That is
how WP8b lost the record of which postings were unreviewed, by typing `--help`
at `blocklist_all` to find out what it did.

Every dangerous call in both modules is monkeypatched to raise here. If a
front door ever regresses, this file fails with that exception rather than
running the real thing against the real store — which is the same mistake, and
a test suite is not the place to repeat it.
"""

from __future__ import annotations

import pytest

from job_scraper.tools import blocklist_all, retrofilter

_TOOLS = pytest.mark.parametrize("module", [blocklist_all, retrofilter], ids=lambda m: m.__name__)


@pytest.fixture(autouse=True)
def defused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every route to the store explode, in both modules."""

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("the tool ran instead of parsing its arguments")

    for module in (blocklist_all, retrofilter):
        for name in ("write_xlsx", "mark_all_new_seen", "JobStore", "load_rules"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, boom)
        monkeypatch.setattr(module, "default_jobs_db_path", boom)


@_TOOLS
def test_help_prints_the_docstring_and_exits_zero(
    module: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", [module.__name__, "--help"])  # type: ignore[attr-defined]
    with pytest.raises(SystemExit) as exit_info:
        module.main()  # type: ignore[attr-defined]
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert module.__doc__ is not None
    assert module.__doc__.splitlines()[0][:40] in out


@_TOOLS
@pytest.mark.parametrize("argument", ["--seen-all", "-x", "everything", "--dry-run"])
def test_an_unrecognised_argument_exits_non_zero_having_done_nothing(
    module: object, argument: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.argv", [module.__name__, argument])  # type: ignore[attr-defined]
    with pytest.raises(SystemExit) as exit_info:
        module.main()  # type: ignore[attr-defined]
    assert exit_info.value.code != 0


@_TOOLS
def test_no_arguments_still_runs_the_tool(module: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """The front door must not become a lock: the bare command still works."""
    monkeypatch.setattr("sys.argv", [module.__name__])  # type: ignore[attr-defined]
    with pytest.raises(AssertionError, match="the tool ran"):
        module.main()  # type: ignore[attr-defined]
