"""SP2b: `candidate recheck` and `candidate activate`.

The second owner-approved exception to "never edit an existing entry". Both
commands change or remove a recorded finding, so every refusal here asserts the
file is byte-identical afterwards, not merely that the exit code was 1.

As in SP1 and SP2, everything lives in `tmp_path`. The autouse fixtures make the
real `data/curated/` and the real `sources.yaml` unreachable, rather than
trusting each test to point elsewhere.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from job_scraper import curated
from job_scraper.tools import sources as sources_cli

CONTOSO = "https://job-boards.greenhouse.io/contoso"
FABRIKAM = "https://careers.fabrikam.example/jobs"
MIGRATED = "migrated from candidate_sources.xlsx"


@pytest.fixture(autouse=True)
def no_real_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def boom() -> Path:
        raise AssertionError("a test reached for the real data/curated/")

    monkeypatch.setattr(sources_cli, "default_curated_dir", boom)
    monkeypatch.setattr(sources_cli, "_active_sources", list)
    # Nowhere by default: an activate test that forgets to write one is refused.
    monkeypatch.setattr(
        sources_cli, "default_sources_path", lambda: tmp_path / "config" / "sources.yaml"
    )


def cli(curated_dir: Path, *argv: str) -> int:
    return sources_cli.main(["--curated-dir", str(curated_dir), "--no-commit", *argv])


def snapshot(directory: Path) -> list[tuple[str, bytes]]:
    return sorted((p.name, p.read_bytes()) for p in directory.iterdir() if p.is_file())


def write_sources(tmp_path: Path, *sources: dict[str, Any]) -> Path:
    path = tmp_path / "config" / "sources.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(yaml.safe_dump({"sources": list(sources)}), encoding="utf-8")
    return path


@pytest.fixture
def listed(tmp_path: Path) -> Path:
    """Two candidates: one with a full finding, one as the migration left it."""
    curated_dir = tmp_path / "curated"
    curated_dir.mkdir()
    curated.save_list(
        curated.candidates_path(curated_dir),
        [
            {
                "organisation": "Contoso",
                "url": CONTOSO,
                "category": "For-profit",
                "blocker": "persistent 403",
                "last_checked": "2026-07-30",
                "ats": "greenhouse",
                "source_of_record": f"{MIGRATED}; recovered from session abc",
            },
            {
                "organisation": "Fabrikam",
                "url": FABRIKAM,
                "category": None,
                "blocker": None,
                "last_checked": None,
                "ats": None,
                "source_of_record": MIGRATED,
            },
        ],
        curated.CANDIDATES_KEY,
        curated.CANDIDATE_FIELDS,
    )
    return curated_dir


def entry(curated_dir: Path, org: str) -> dict[str, Any]:
    found = curated.find_organisation(curated.load_candidates(curated_dir), org)
    assert found is not None
    return found


RECHECK = [
    "candidate",
    "recheck",
    "Contoso",
    "--blocker",
    "listing is rendered client-side",
    "--last-checked",
    "2026-09-14",
    "--source-of-record",
    "rechecked by the owner in a browser",
]


class TestRecheck:
    def test_the_finding_is_replaced_and_the_old_one_kept(self, listed: Path) -> None:
        assert cli(listed, *RECHECK) == 0
        got = entry(listed, "Contoso")
        assert got["blocker"] == "listing is rendered client-side"
        assert got["last_checked"] == "2026-09-14"
        assert got["source_of_record"] == (
            f"{MIGRATED}; recovered from session abc; rechecked 2026-09-14: was "
            "blocker=persistent 403, last_checked=2026-07-30; "
            "rechecked by the owner in a browser"
        )

    def test_organisation_url_category_and_an_unpassed_ats_are_untouched(
        self, listed: Path
    ) -> None:
        before = entry(listed, "Contoso")
        assert cli(listed, *RECHECK) == 0
        after = entry(listed, "Contoso")
        for name in ("organisation", "url", "category", "ats"):
            assert after[name] == before[name]
        assert "ats=" not in after["source_of_record"], "ats did not change, so is not history"

    def test_empty_old_values_are_written_as_null(self, listed: Path) -> None:
        argv = ["candidate", "recheck", "Fabrikam", "--blocker", "login wall"]
        argv += ["--last-checked", "2026-09-14", "--source-of-record", "owner", "--ats", "workday"]
        assert cli(listed, *argv) == 0
        got = entry(listed, "Fabrikam")
        assert got["source_of_record"] == (
            f"{MIGRATED}; rechecked 2026-09-14: was blocker=null, last_checked=null, "
            "ats=null; owner"
        )
        assert (got["blocker"], got["ats"]) == ("login wall", "workday")

    def test_a_changed_ats_is_recorded_as_history(self, listed: Path) -> None:
        assert cli(listed, *RECHECK, "--ats", "ashby") == 0
        got = entry(listed, "Contoso")
        assert got["ats"] == "ashby"
        assert "last_checked=2026-07-30, ats=greenhouse; " in got["source_of_record"]

    def test_two_rechecks_keep_all_earlier_text(self, listed: Path) -> None:
        assert cli(listed, *RECHECK) == 0
        first = entry(listed, "Contoso")["source_of_record"]
        argv = ["candidate", "recheck", "Contoso", "--blocker", "robots.txt disallows"]
        argv += ["--last-checked", "2026-10-01", "--source-of-record", "second look"]
        assert cli(listed, *argv) == 0
        assert entry(listed, "Contoso")["source_of_record"] == (
            f"{first}; rechecked 2026-10-01: was blocker=listing is rendered client-side, "
            "last_checked=2026-09-14; second look"
        )

    def test_the_same_date_with_a_new_blocker_is_a_change(self, listed: Path) -> None:
        argv = ["candidate", "recheck", "Contoso", "--blocker", "a different reason"]
        argv += ["--last-checked", "2026-07-30", "--source-of-record", "same day"]
        assert cli(listed, *argv) == 0

    def test_other_entries_are_untouched(self, listed: Path) -> None:
        before = entry(listed, "Fabrikam")
        assert cli(listed, *RECHECK) == 0
        assert entry(listed, "Fabrikam") == before

    @pytest.mark.parametrize("missing", ["--blocker", "--last-checked", "--source-of-record"])
    def test_a_missing_required_argument_writes_nothing(self, listed: Path, missing: str) -> None:
        i = RECHECK.index(missing)
        argv = RECHECK[:i] + RECHECK[i + 2 :]
        before = snapshot(listed)
        with pytest.raises(SystemExit) as exit_info:
            cli(listed, *argv)
        assert exit_info.value.code == 2
        assert snapshot(listed) == before

    def refused(self, listed: Path, argv: list[str], capsys: pytest.CaptureFixture[str]) -> str:
        before = snapshot(listed)
        assert cli(listed, *argv) == 1
        assert snapshot(listed) == before, "a refusal changes nothing and takes no backup"
        return capsys.readouterr().err

    def test_an_earlier_date_is_refused(
        self, listed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        argv = [*RECHECK]
        argv[argv.index("2026-09-14")] = "2026-07-29"
        assert "does not move backwards" in self.refused(listed, argv, capsys)

    def test_an_identical_finding_is_refused(
        self, listed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        argv = ["candidate", "recheck", "contoso", "--blocker", "persistent 403"]
        argv += ["--last-checked", "2026-07-30", "--source-of-record", "again"]
        argv += ["--ats", "greenhouse"]
        assert "nothing to change" in self.refused(listed, argv, capsys)

    def test_an_unknown_organisation_is_refused(
        self, listed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        argv = [*RECHECK]
        argv[2] = "Northwind"
        assert "no candidate matching" in self.refused(listed, argv, capsys)

    def test_a_tombstoned_board_is_refused(
        self, listed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        curated.save_list(
            curated.excluded_path(listed),
            [{"organisation": "Contoso", "url": CONTOSO + "/", "reason": "x"}],
            curated.EXCLUDED_KEY,
            curated.EXCLUDED_FIELDS,
        )
        assert "tombstoned" in self.refused(listed, RECHECK, capsys)

    def test_an_active_board_is_refused_and_pointed_at_activate(
        self, listed: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(
            sources_cli,
            "_active_sources",
            lambda: [{"name": "contoso", "url": "http://www.job-boards.greenhouse.io/contoso"}],
        )
        assert "candidate activate" in self.refused(listed, RECHECK, capsys)

    def test_a_sibling_board_on_the_same_host_is_not_active(
        self, listed: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            sources_cli,
            "_active_sources",
            lambda: [{"name": "northwind", "url": "https://job-boards.greenhouse.io/northwind"}],
        )
        assert cli(listed, *RECHECK) == 0

    @pytest.mark.parametrize(
        ("flag", "value"),
        [("--blocker", "  "), ("--last-checked", "14/09/2026"), ("--source-of-record", "")],
    )
    def test_an_unusable_value_is_refused(self, listed: Path, flag: str, value: str) -> None:
        argv = [*RECHECK]
        argv[argv.index(flag) + 1] = value
        before = snapshot(listed)
        assert cli(listed, *argv) == 1
        assert snapshot(listed) == before


class TestActivate:
    ARGV = ("candidate", "activate", "Contoso")

    def test_a_candidate_whose_board_is_in_sources_yaml_is_removed(
        self, tmp_path: Path, listed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        write_sources(tmp_path, {"name": "contoso", "url": CONTOSO + "/"})
        fabrikam = entry(listed, "Fabrikam")

        assert cli(listed, *self.ARGV) == 0

        assert curated.load_candidates(listed) == [fabrikam], "no other entry changes"
        out = capsys.readouterr().out
        for line in (
            "organisation: Contoso",
            f"url: {CONTOSO}",
            "category: For-profit",
            "blocker: persistent 403",
            "last_checked: 2026-07-30",
            "ats: greenhouse",
            f"source_of_record: {MIGRATED}; recovered from session abc",
        ):
            assert line in out
        assert out.index("organisation: Contoso") < out.index("wrote"), "printed before writing"

    def test_the_whole_entry_is_printed_nulls_included(
        self, tmp_path: Path, listed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        write_sources(tmp_path, {"name": "fabrikam", "url": "https://careers.fabrikam.example/"})
        assert cli(listed, "candidate", "activate", FABRIKAM) == 0
        out = capsys.readouterr().out
        assert "blocker: null" in out and "last_checked: null" in out

    def refused(self, listed: Path, capsys: pytest.CaptureFixture[str]) -> str:
        before = snapshot(listed)
        assert cli(listed, *self.ARGV) == 1
        assert snapshot(listed) == before, "a refusal changes nothing and takes no backup"
        captured = capsys.readouterr()
        assert "organisation: Contoso" not in captured.out, "nothing announced as removed"
        return captured.err

    def test_a_board_not_in_sources_yaml_is_refused_naming_it(
        self, tmp_path: Path, listed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        write_sources(tmp_path, {"name": "fabrikam", "url": FABRIKAM})
        assert "job-boards.greenhouse.io/contoso" in self.refused(listed, capsys)

    def test_a_missing_sources_yaml_is_a_refusal_not_not_active(
        self, listed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert "does not exist" in self.refused(listed, capsys)

    def test_the_same_host_with_a_different_board_is_refused(
        self, tmp_path: Path, listed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Two Greenhouse employers share a hostname and nothing else."""
        write_sources(
            tmp_path, {"name": "northwind", "url": "https://job-boards.greenhouse.io/northwind"}
        )
        assert "is not in sources.yaml" in self.refused(listed, capsys)

    def test_a_matching_name_is_not_proof(
        self, tmp_path: Path, listed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        write_sources(
            tmp_path,
            {"name": "contoso", "company": "Contoso", "url": "https://contoso.example/careers"},
        )
        assert "is not in sources.yaml" in self.refused(listed, capsys)

    def test_an_unknown_organisation_is_refused(
        self, tmp_path: Path, listed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        write_sources(tmp_path, {"name": "contoso", "url": CONTOSO})
        before = snapshot(listed)
        assert cli(listed, "candidate", "activate", "Northwind") == 1
        assert snapshot(listed) == before


class TestBothWriters:
    @pytest.fixture
    def active(self, tmp_path: Path) -> None:
        write_sources(tmp_path, {"name": "contoso", "url": CONTOSO})

    @pytest.mark.parametrize(
        "argv",
        [[*RECHECK, "--help"], ["candidate", "activate", "Contoso", "--help"]],
        ids=["recheck", "activate"],
    )
    def test_help_writes_nothing(
        self, listed: Path, active: None, argv: list[str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        before = snapshot(listed)
        with pytest.raises(SystemExit) as exit_info:
            cli(listed, *argv)
        assert exit_info.value.code == 0
        assert "usage:" in capsys.readouterr().out
        assert snapshot(listed) == before

    @pytest.mark.parametrize(
        "argv", [RECHECK, ["candidate", "activate", "Contoso"]], ids=["recheck", "activate"]
    )
    def test_the_backup_is_the_file_as_it_was(
        self, listed: Path, active: None, argv: list[str]
    ) -> None:
        # recheck reads sources.yaml through `_active_sources`, which the autouse
        # fixture empties, so the same `active` file does not refuse it.
        before = curated.candidates_path(listed).read_bytes()
        assert cli(listed, *argv) == 0
        backups = list(listed.glob("candidate_sources.yaml.*.bak"))
        assert len(backups) == 1
        assert backups[0].read_bytes() == before

    @pytest.mark.parametrize(
        "argv", [RECHECK, ["candidate", "activate", "Contoso"]], ids=["recheck", "activate"]
    )
    def test_an_interrupted_write_leaves_the_original_intact(
        self, listed: Path, active: None, argv: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = curated.candidates_path(listed)
        before = path.read_bytes()

        def boom(src: object, dst: object, **kwargs: object) -> None:
            raise OSError("interrupted")

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError, match="interrupted"):
            cli(listed, *argv)
        assert path.read_bytes() == before
        assert list(listed.glob(".candidate_sources-*")) == []

    def test_both_refuse_before_the_migration(self, listed: Path, active: None) -> None:
        (listed / "candidate_sources.yaml").unlink()
        (listed / "candidate_sources.xlsx").write_bytes(b"not really a workbook")
        before = snapshot(listed)
        assert cli(listed, *RECHECK) == 1
        assert cli(listed, "candidate", "activate", "Contoso") == 1
        assert snapshot(listed) == before


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestAsARealProcess:
    """Only a real process proves the command runs, not just that the parser parses."""

    @staticmethod
    def python(cwd: Path, *argv: str) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
        return subprocess.run(
            [sys.executable, *argv], cwd=cwd, env=env, capture_output=True, text=True, timeout=60
        )

    def test_recheck(self, listed: Path) -> None:
        done = self.python(
            listed, "-m", "job_scraper.tools.sources", "--curated-dir", str(listed),
            "--no-commit", *RECHECK,
        )  # fmt: skip
        assert done.returncode == 0, done.stderr
        assert "rechecked 2026-09-14: was blocker=persistent 403" in str(
            entry(listed, "Contoso")["source_of_record"]
        )

    def test_activate(self, tmp_path: Path, listed: Path) -> None:
        """`-m` would read the owner's real sources.yaml, so the path is pointed here.

        Everything else — the import, the parser, `main()`, the write — is the
        command as it runs from a shell.
        """
        sources_path = write_sources(tmp_path, {"name": "contoso", "url": CONTOSO})
        script = (
            "import sys\n"
            "from pathlib import Path\n"
            "from job_scraper.tools import sources\n"
            f"sources.default_sources_path = lambda: Path({str(sources_path)!r})\n"
            "raise SystemExit(sources.main(sys.argv[1:]))\n"
        )
        done = self.python(
            listed, "-c", script, "--curated-dir", str(listed), "--no-commit",
            "candidate", "activate", "Contoso",
        )  # fmt: skip
        assert done.returncode == 0, done.stderr
        assert "organisation: Contoso" in done.stdout
        assert [e["organisation"] for e in curated.load_candidates(listed)] == ["Fabrikam"]
