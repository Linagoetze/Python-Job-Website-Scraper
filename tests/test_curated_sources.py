"""SP1: the curated lists and the CLI over them.

Every test builds its lists in `tmp_path`. Nothing here may touch
`data/curated/` — those files are hand-maintained, not regenerable and have no
undo, which is the whole reason the tool exists. The autouse fixture below
makes the real directory unreachable rather than trusting each test to pass
`--curated-dir`: a test suite that writes to the file it is protecting is the
same mistake it is testing for.
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

GREENHOUSE = "https://job-boards.greenhouse.io/northwind"


@pytest.fixture(autouse=True)
def no_real_curated_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> Path:
        raise AssertionError("a test reached for the real data/curated/")

    monkeypatch.setattr(sources_cli, "default_curated_dir", boom)


@pytest.fixture(autouse=True)
def no_sources_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    """`check` and the writers consult sources.yaml; give them an empty one by default."""
    monkeypatch.setattr(sources_cli, "_active_sources", list)


def run(*argv: str) -> int:
    return sources_cli.main(list(argv))


def cli(tmp_path: Path, *argv: str) -> int:
    return run("--curated-dir", str(tmp_path), "--no-commit", *argv)


def read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def add_excluded(tmp_path: Path, org: str = "Northwind Health", url: str = GREENHOUSE) -> int:
    return cli(tmp_path, "exclude", org, url, "requires login")


def add_candidate(tmp_path: Path, org: str = "Contoso", url: str = GREENHOUSE) -> int:
    return cli(tmp_path, "candidate", "add", org, url, "--blocker", "persistent 403")


class TestFrontDoor:
    """WP10's lesson, applied to a tool that writes to files with no undo."""

    @pytest.mark.parametrize(
        "argv",
        [
            ["--help"],
            ["list", "--help"],
            ["check", "--help"],
            ["exclude", "--help"],
            ["candidate", "--help"],
            ["candidate", "add", "--help"],
            ["candidate", "promote", "--help"],
            ["candidate", "record-check", "--help"],
        ],
        ids=lambda a: " ".join(a),
    )
    def test_help_exits_zero_having_written_nothing(
        self, argv: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exit_info:
            run("--curated-dir", str(tmp_path), *argv)
        assert exit_info.value.code == 0
        assert "usage:" in capsys.readouterr().out
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.parametrize(
        "argv",
        [
            [],
            ["candidate"],
            ["exclude", "Northwind Health"],
            ["candidate", "add", "Northwind Health", GREENHOUSE],
            ["list", "everything"],
            ["destroy", "--all"],
        ],
        ids=[
            "no-subcommand",
            "no-candidate-subcommand",
            "too-few",
            "no-blocker",
            "bad-choice",
            "unknown",
        ],
    )
    def test_an_unusable_command_exits_non_zero_having_written_nothing(
        self, argv: list[str], tmp_path: Path
    ) -> None:
        with pytest.raises(SystemExit) as exit_info:
            run("--curated-dir", str(tmp_path), *argv)
        assert exit_info.value.code != 0
        assert list(tmp_path.iterdir()) == []


class TestAppending:
    def test_exclude_writes_the_schema(self, tmp_path: Path) -> None:
        assert cli(tmp_path, "exclude", "Northwind Health", GREENHOUSE, "requires login") == 0
        data = read_yaml(curated.excluded_path(tmp_path))
        assert list(data) == ["excluded"]
        assert data["excluded"] == [
            {
                "organisation": "Northwind Health",
                "url": GREENHOUSE,
                "reason": "requires login",
                "excluded_on": curated.date.today().isoformat(),
            }
        ]

    def test_candidate_add_writes_the_schema(self, tmp_path: Path) -> None:
        assert (
            cli(
                tmp_path,
                "candidate",
                "add",
                "Contoso Rail",
                "https://jobs.ashbyhq.com/contoso",
                "--blocker",
                "no job content in the DOM",
                "--ats",
                "ashby",
                "--category",
                "logistics",
                "--last-checked",
                "2026-06-12",
                "--source-of-record",
                "session 2026-06-12",
            )
            == 0
        )
        entry = read_yaml(curated.candidates_path(tmp_path))["candidates"][0]
        assert entry == {
            "organisation": "Contoso Rail",
            "url": "https://jobs.ashbyhq.com/contoso",
            "category": "logistics",
            "blocker": "no job content in the DOM",
            "last_checked": "2026-06-12",
            "ats": "ashby",
            "source_of_record": "session 2026-06-12",
        }

    def test_a_second_entry_is_appended_not_replaced(self, tmp_path: Path) -> None:
        add_excluded(tmp_path)
        cli(tmp_path, "exclude", "Contoso Rail", "https://job-boards.greenhouse.io/contoso", "403")
        entries = read_yaml(curated.excluded_path(tmp_path))["excluded"]
        assert [e["organisation"] for e in entries] == ["Northwind Health", "Contoso Rail"]

    def test_last_checked_defaults_to_today_but_can_be_backdated(self, tmp_path: Path) -> None:
        """SP2 loads rows recovered from old transcripts; today's date would be a lie."""
        add_candidate(tmp_path)
        entry = read_yaml(curated.candidates_path(tmp_path))["candidates"][0]
        assert entry["last_checked"] == curated.date.today().isoformat()

    def test_a_malformed_date_is_refused(self, tmp_path: Path) -> None:
        assert cli(tmp_path, "exclude", "X", GREENHOUSE, "r", "--on", "11/05/2026") == 1
        assert not curated.excluded_path(tmp_path).exists()

    def test_something_that_is_not_a_url_is_refused(self, tmp_path: Path) -> None:
        assert cli(tmp_path, "exclude", "X", "not a url", "r") == 1
        assert not curated.excluded_path(tmp_path).exists()


class TestDuplicateRefusal:
    @pytest.mark.parametrize(
        "variant",
        [
            GREENHOUSE,
            "http://job-boards.greenhouse.io/northwind",
            "https://job-boards.greenhouse.io/northwind/",
            "https://www.job-boards.greenhouse.io/northwind",
            "https://job-boards.greenhouse.io/northwind/jobs/123",
        ],
        ids=["identical", "http", "trailing-slash", "www", "deep-link"],
    )
    def test_the_same_board_written_differently_is_refused(
        self, variant: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        add_excluded(tmp_path)
        before = curated.excluded_path(tmp_path).read_bytes()
        capsys.readouterr()

        assert cli(tmp_path, "exclude", "Someone Else", variant, "another reason") == 1
        assert "refusing to modify" in capsys.readouterr().err
        assert curated.excluded_path(tmp_path).read_bytes() == before

    def test_a_different_board_on_the_same_host_is_accepted(self, tmp_path: Path) -> None:
        add_excluded(tmp_path)
        assert (
            cli(
                tmp_path,
                "exclude",
                "Adventure Works",
                "https://job-boards.greenhouse.io/adventureworks",
                "403",
            )
            == 0
        )
        assert len(read_yaml(curated.excluded_path(tmp_path))["excluded"]) == 2

    def test_the_same_organisation_twice_is_refused(self, tmp_path: Path) -> None:
        add_excluded(tmp_path)
        assert cli(tmp_path, "exclude", "northwind health", "https://elsewhere.example", "403") == 1
        assert len(read_yaml(curated.excluded_path(tmp_path))["excluded"]) == 1

    def test_a_tombstoned_board_cannot_be_added_as_a_candidate(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        add_excluded(tmp_path)
        capsys.readouterr()
        assert (
            cli(
                tmp_path,
                "candidate",
                "add",
                "Northwind",
                "https://job-boards.greenhouse.io/northwind/",
                "--blocker",
                "worth another look",
            )
            == 1
        )
        assert "already tombstoned" in capsys.readouterr().err
        assert not curated.candidates_path(tmp_path).exists()

    def test_a_candidate_cannot_be_excluded_behind_its_own_back(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        add_candidate(tmp_path)
        capsys.readouterr()
        assert cli(tmp_path, "exclude", "Contoso", GREENHOUSE, "gave up") == 1
        assert "candidate promote" in capsys.readouterr().err
        assert not curated.excluded_path(tmp_path).exists()

    def test_an_active_source_is_not_a_candidate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(
            sources_cli,
            "_active_sources",
            lambda: [{"name": "canonical", "url": "https://job-boards.greenhouse.io/canonical"}],
        )
        assert (
            cli(
                tmp_path,
                "candidate",
                "add",
                "Canonical",
                "http://www.job-boards.greenhouse.io/canonical/",
                "--blocker",
                "none",
            )
            == 1
        )
        assert "already an active source" in capsys.readouterr().err


class TestBackups:
    def test_the_first_write_has_nothing_to_back_up(self, tmp_path: Path) -> None:
        add_excluded(tmp_path)
        assert list(tmp_path.glob("*.bak")) == []

    def test_every_later_write_leaves_a_timestamped_backup(self, tmp_path: Path) -> None:
        add_excluded(tmp_path)
        first = curated.excluded_path(tmp_path).read_bytes()
        cli(tmp_path, "exclude", "Contoso Rail", "https://job-boards.greenhouse.io/contoso", "403")

        backups = list(tmp_path.glob("excluded_sources.yaml.*.bak"))
        assert len(backups) == 1
        assert backups[0].read_bytes() == first, "the backup is the file as it was before"
        assert backups[0].name.endswith("Z.bak")

    def test_promote_backs_up_both_files(self, tmp_path: Path) -> None:
        add_excluded(tmp_path)
        add_candidate(tmp_path, "Contoso Rail", "https://job-boards.greenhouse.io/contoso")
        assert cli(tmp_path, "candidate", "promote", "Contoso Rail") == 0
        assert len(list(tmp_path.glob("excluded_sources.yaml.*.bak"))) == 1
        assert len(list(tmp_path.glob("candidate_sources.yaml.*.bak"))) == 1


class TestAnInterruptedWrite:
    """The file on disk must never be the casualty of a crash mid-write."""

    def test_a_failure_before_the_replace_leaves_the_original_intact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_excluded(tmp_path)
        path = curated.excluded_path(tmp_path)
        before = path.read_bytes()

        def boom(src: object, dst: object, **kwargs: object) -> None:
            raise OSError("interrupted")

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError, match="interrupted"):
            cli(tmp_path, "exclude", "Contoso", "https://job-boards.greenhouse.io/contoso", "403")

        assert path.read_bytes() == before, "the readable list survived"
        assert list(tmp_path.glob(".excluded_sources-*")) == [], "no temp file left behind"

    def test_a_failure_while_writing_the_temp_file_leaves_the_original_intact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_excluded(tmp_path)
        path = curated.excluded_path(tmp_path)
        before = path.read_bytes()

        def boom(self: Path, *args: object, **kwargs: object) -> int:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", boom)
        with pytest.raises(OSError, match="disk full"):
            cli(tmp_path, "exclude", "Contoso", "https://job-boards.greenhouse.io/contoso", "403")

        assert path.read_bytes() == before
        assert list(tmp_path.glob(".excluded_sources-*")) == []

    def test_the_backup_is_written_before_the_file_is_touched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A crash mid-write is survivable twice over: the .bak, then the temp file."""
        add_excluded(tmp_path)
        before = curated.excluded_path(tmp_path).read_bytes()

        def boom(src: object, dst: object, **kwargs: object) -> None:
            raise OSError("interrupted")

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError):
            cli(tmp_path, "exclude", "Contoso", "https://job-boards.greenhouse.io/contoso", "403")

        backups = list(tmp_path.glob("excluded_sources.yaml.*.bak"))
        assert [b.read_bytes() for b in backups] == [before]


class TestPromote:
    def test_it_moves_the_entry_and_carries_the_blocker_as_the_reason(self, tmp_path: Path) -> None:
        add_candidate(tmp_path, "Contoso Rail", "https://jobs.ashbyhq.com/contoso")
        assert cli(tmp_path, "candidate", "promote", "contoso rail") == 0

        assert read_yaml(curated.candidates_path(tmp_path))["candidates"] == []
        tombstone = read_yaml(curated.excluded_path(tmp_path))["excluded"]
        assert tombstone == [
            {
                "organisation": "Contoso Rail",
                "url": "https://jobs.ashbyhq.com/contoso",
                "reason": "persistent 403",
                "excluded_on": curated.date.today().isoformat(),
            }
        ]

    def test_an_explicit_reason_wins_over_the_blocker(self, tmp_path: Path) -> None:
        add_candidate(tmp_path)
        cli(tmp_path, "candidate", "promote", "Contoso", "--reason", "the site is gone")
        assert read_yaml(curated.excluded_path(tmp_path))["excluded"][0]["reason"] == (
            "the site is gone"
        )

    def test_an_unknown_organisation_changes_nothing(self, tmp_path: Path) -> None:
        add_candidate(tmp_path)
        before = curated.candidates_path(tmp_path).read_bytes()
        assert cli(tmp_path, "candidate", "promote", "Someone Else") == 1
        assert curated.candidates_path(tmp_path).read_bytes() == before
        assert not curated.excluded_path(tmp_path).exists()

    def test_a_candidate_with_no_blocker_needs_a_reason(self, tmp_path: Path) -> None:
        """Migrated rows carry no blocker, and the tombstone may not hold a blank reason."""
        path = curated.candidates_path(tmp_path)
        curated.save_list(
            path,
            [{"organisation": "Litware", "url": "https://litware.example/careers"}],
            curated.CANDIDATES_KEY,
            curated.CANDIDATE_FIELDS,
        )
        assert cli(tmp_path, "candidate", "promote", "Litware") == 1
        assert not curated.excluded_path(tmp_path).exists()
        assert cli(tmp_path, "candidate", "promote", "Litware", "--reason", "no DOM content") == 0


class TestListAndCheck:
    def test_list_prints_both_lists_when_asked_for_neither(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        add_excluded(tmp_path)
        add_candidate(tmp_path, "Contoso Rail", "https://jobs.ashbyhq.com/contoso")
        capsys.readouterr()
        assert cli(tmp_path, "list") == 0
        out = capsys.readouterr().out
        assert "excluded (1)" in out and "candidates (1)" in out
        assert "Northwind Health" in out and "Contoso Rail" in out

    def test_list_can_be_narrowed_to_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        add_excluded(tmp_path)
        capsys.readouterr()
        cli(tmp_path, "list", "candidates")
        out = capsys.readouterr().out
        assert "candidates (0)" in out and "Northwind" not in out

    def test_check_finds_a_tombstoned_board_from_a_deep_link(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        add_excluded(tmp_path)
        capsys.readouterr()
        assert cli(tmp_path, "check", "https://job-boards.greenhouse.io/northwind/jobs/99") == 0
        assert "EXCLUDED" in capsys.readouterr().out

    def test_check_finds_an_organisation_by_name(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        add_candidate(tmp_path, "Wide World Importers", "https://jobs.ashbyhq.com/wideworld")
        capsys.readouterr()
        assert cli(tmp_path, "check", "wide world") == 0
        assert "CANDIDATE" in capsys.readouterr().out

    def test_check_searches_sources_yaml_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(
            sources_cli,
            "_active_sources",
            lambda: [
                {
                    "name": "canonical",
                    "company": "Canonical",
                    "url": "https://job-boards.greenhouse.io/canonical",
                }
            ],
        )
        assert cli(tmp_path, "check", "https://job-boards.greenhouse.io/canonical/") == 0
        assert "ACTIVE SOURCE" in capsys.readouterr().out

    def test_a_sibling_board_on_a_shared_host_is_not_a_match(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The failure this whole design exists to prevent."""
        monkeypatch.setattr(
            sources_cli,
            "_active_sources",
            lambda: [{"name": "canonical", "url": "https://job-boards.greenhouse.io/canonical"}],
        )
        add_excluded(tmp_path)
        capsys.readouterr()
        assert cli(tmp_path, "check", "https://job-boards.greenhouse.io/adventureworks") == 1
        assert "no match" in capsys.readouterr().out

    def test_check_exits_non_zero_when_nothing_is_known(self, tmp_path: Path) -> None:
        assert cli(tmp_path, "check", "https://nobody.example") == 1


class TestReadingTheFiles:
    def test_a_missing_file_reads_as_empty(self, tmp_path: Path) -> None:
        assert curated.load_excluded(tmp_path) == []
        assert curated.load_candidates(tmp_path) == []

    def test_a_malformed_file_is_an_error_not_an_empty_list(self, tmp_path: Path) -> None:
        """An unreadable tombstone must not look like an empty one."""
        curated.excluded_path(tmp_path).write_text("- just: a list\n", encoding="utf-8")
        with pytest.raises(curated.CuratedError):
            curated.load_excluded(tmp_path)

    def test_an_unknown_field_is_an_error(self, tmp_path: Path) -> None:
        curated.excluded_path(tmp_path).write_text(
            "excluded:\n  - organisation: X\n    url: https://x.example\n    note: hmm\n",
            encoding="utf-8",
        )
        with pytest.raises(curated.CuratedError, match="unknown field"):
            curated.load_excluded(tmp_path)

    def test_a_hand_written_unquoted_date_still_reads_as_a_string(self, tmp_path: Path) -> None:
        """YAML turns a bare 2026-05-11 into a date; the list stays plain data."""
        curated.excluded_path(tmp_path).write_text(
            "excluded:\n  - organisation: X\n    url: https://x.example\n"
            "    reason: r\n    excluded_on: 2026-05-11\n",
            encoding="utf-8",
        )
        assert curated.load_excluded(tmp_path)[0]["excluded_on"] == "2026-05-11"

    def test_what_the_tool_writes_it_can_read_back(self, tmp_path: Path) -> None:
        add_excluded(tmp_path)
        add_candidate(tmp_path, "Contoso Rail", "https://jobs.ashbyhq.com/contoso")
        assert [e["organisation"] for e in curated.load_excluded(tmp_path)] == ["Northwind Health"]
        assert [e["organisation"] for e in curated.load_candidates(tmp_path)] == ["Contoso Rail"]


class TestTheCuratedRepository:
    """SP0 option 2: a private git repository inside data/curated/, for a real undo."""

    def test_no_repository_is_reported_not_an_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert run("--curated-dir", str(tmp_path), "exclude", "X", GREENHOUSE, "r") == 0
        assert "not a git repository" in capsys.readouterr().out

    def test_a_write_is_committed_when_the_repository_exists(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import subprocess

        for command in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "test@example.com"],
            ["git", "config", "user.name", "Test"],
        ):
            subprocess.run([*command[:1], "-C", str(tmp_path), *command[1:]], check=True)

        assert run("--curated-dir", str(tmp_path), "exclude", "Northwind", GREENHOUSE, "r") == 0
        assert "committed to the curated repository" in capsys.readouterr().out

        tracked = subprocess.run(
            ["git", "-C", str(tmp_path), "ls-files"], capture_output=True, text=True, check=True
        )
        assert tracked.stdout.split() == ["excluded_sources.yaml"]

    def test_the_backup_files_are_never_staged(self, tmp_path: Path) -> None:
        """Once the repository exists the .bak files are noise; git holds the history."""
        import subprocess

        subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
        subprocess.run(
            ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True
        )
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
        run("--curated-dir", str(tmp_path), "exclude", "Northwind", GREENHOUSE, "r")
        run(
            "--curated-dir",
            str(tmp_path),
            "exclude",
            "Contoso",
            "https://job-boards.greenhouse.io/contoso",
            "r",
        )

        tracked = subprocess.run(
            ["git", "-C", str(tmp_path), "ls-files"], capture_output=True, text=True, check=True
        )
        assert ".bak" not in tracked.stdout


MIGRATED = "migrated from candidate_sources.xlsx"


class TestRecordCheck:
    """SP2's fill-only exception to "never edit an existing entry".

    The candidates here look the way SP1's migration left the real ones: every
    field but the organisation and URL null, and `source_of_record` naming the
    migration.
    """

    @pytest.fixture
    def migrated(self, tmp_path: Path) -> Path:
        blank = {"category": None, "blocker": None, "last_checked": None, "ats": None}
        curated.save_list(
            curated.candidates_path(tmp_path),
            [
                {
                    "organisation": "Contoso",
                    "url": GREENHOUSE,
                    **blank,
                    "source_of_record": MIGRATED,
                },
                {
                    "organisation": "Fabrikam",
                    "url": "https://careers.fabrikam.example/jobs",
                    **blank,
                    "blocker": "persistent 403",
                    "source_of_record": MIGRATED,
                },
            ],
            curated.CANDIDATES_KEY,
            curated.CANDIDATE_FIELDS,
        )
        return tmp_path

    def entry(self, curated_dir: Path, org: str) -> dict[str, Any]:
        found = curated.find_organisation(curated.load_candidates(curated_dir), org)
        assert found is not None
        return found

    def test_null_fields_are_filled(self, migrated: Path) -> None:
        argv = ["candidate", "record-check", "Contoso", "--blocker", "no job content in the DOM"]
        argv += ["--category", "For-profit", "--last-checked", "2026-07-30", "--ats", "hibob"]
        assert cli(migrated, *argv) == 0
        entry = self.entry(migrated, "Contoso")
        assert entry["blocker"] == "no job content in the DOM"
        assert entry["category"] == "For-profit"
        assert entry["last_checked"] == "2026-07-30"
        assert entry["ats"] == "hibob"

    def test_fields_not_passed_stay_null_and_no_date_is_invented(self, migrated: Path) -> None:
        assert cli(migrated, "candidate", "record-check", "Contoso", "--blocker", "403") == 0
        entry = self.entry(migrated, "Contoso")
        assert entry["last_checked"] is None, "a dictated blocker is not a check done today"
        assert entry["category"] is None and entry["ats"] is None

    def test_other_entries_are_untouched(self, migrated: Path) -> None:
        before = self.entry(migrated, "Fabrikam")
        assert cli(migrated, "candidate", "record-check", "Contoso", "--blocker", "403") == 0
        assert self.entry(migrated, "Fabrikam") == before

    def test_a_field_with_a_value_is_refused_and_the_file_is_byte_identical(
        self, migrated: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = curated.candidates_path(migrated)
        before = path.read_bytes()
        assert cli(migrated, "candidate", "record-check", "Fabrikam", "--blocker", "login") == 1
        assert path.read_bytes() == before
        assert list(migrated.glob("*.bak")) == [], "a refusal takes no backup either"
        assert "persistent 403" in capsys.readouterr().err

    def test_one_filled_field_among_the_arguments_refuses_the_whole_command(
        self, migrated: Path
    ) -> None:
        path = curated.candidates_path(migrated)
        before = path.read_bytes()
        argv = ["candidate", "record-check", "Fabrikam", "--category", "For-profit"]
        argv += ["--last-checked", "2026-07-30", "--blocker", "login", "--source-of-record", "x"]
        assert cli(migrated, *argv) == 1
        assert path.read_bytes() == before
        assert self.entry(migrated, "Fabrikam")["category"] is None

    def test_source_of_record_is_extended_not_replaced(self, migrated: Path) -> None:
        argv = ["candidate", "record-check", "Contoso", "--blocker", "403"]
        argv += ["--source-of-record", "recovered from session abc"]
        assert cli(migrated, *argv) == 0
        assert (
            self.entry(migrated, "Contoso")["source_of_record"]
            == f"{MIGRATED}; recovered from session abc"
        )

    def test_source_of_record_alone_may_be_extended_on_a_filled_entry(self, migrated: Path) -> None:
        argv = ["candidate", "record-check", "Fabrikam", "--source-of-record", "confirmed"]
        assert cli(migrated, *argv) == 0
        entry = self.entry(migrated, "Fabrikam")
        assert entry["source_of_record"] == f"{MIGRATED}; confirmed"
        assert entry["blocker"] == "persistent 403"

    def test_an_unknown_organisation_changes_nothing(self, migrated: Path) -> None:
        before = sorted((p.name, p.read_bytes()) for p in migrated.iterdir())
        assert cli(migrated, "candidate", "record-check", "Northwind", "--blocker", "403") == 1
        assert sorted((p.name, p.read_bytes()) for p in migrated.iterdir()) == before

    def test_the_organisation_is_matched_case_insensitively_or_by_board(
        self, migrated: Path
    ) -> None:
        assert cli(migrated, "candidate", "record-check", "contoso", "--ats", "greenhouse") == 0
        board = "http://www.job-boards.greenhouse.io/northwind/"
        assert cli(migrated, "candidate", "record-check", board, "--blocker", "403") == 0
        entry = self.entry(migrated, "Contoso")
        assert (entry["ats"], entry["blocker"]) == ("greenhouse", "403")

    @pytest.mark.parametrize(
        "extra",
        [[], ["--blocker", "  "], ["--last-checked", "30/07/2026"]],
        ids=["nothing-to-record", "blank-value", "bad-date"],
    )
    def test_an_unusable_fill_changes_nothing(self, migrated: Path, extra: list[str]) -> None:
        path = curated.candidates_path(migrated)
        before = path.read_bytes()
        assert cli(migrated, "candidate", "record-check", "Contoso", *extra) == 1
        assert path.read_bytes() == before
        assert list(migrated.glob("*.bak")) == []

    def test_help_writes_nothing(self, migrated: Path) -> None:
        before = sorted((p.name, p.read_bytes()) for p in migrated.iterdir())
        with pytest.raises(SystemExit) as exit_info:
            cli(migrated, "candidate", "record-check", "Contoso", "--blocker", "403", "--help")
        assert exit_info.value.code == 0
        assert sorted((p.name, p.read_bytes()) for p in migrated.iterdir()) == before

    def test_the_backup_is_the_file_as_it_was(self, migrated: Path) -> None:
        before = curated.candidates_path(migrated).read_bytes()
        assert cli(migrated, "candidate", "record-check", "Contoso", "--blocker", "403") == 0
        backups = list(migrated.glob("candidate_sources.yaml.*.bak"))
        assert len(backups) == 1
        assert backups[0].read_bytes() == before

    def test_an_interrupted_write_leaves_the_original_intact(
        self, migrated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = curated.candidates_path(migrated)
        before = path.read_bytes()

        def boom(src: object, dst: object, **kwargs: object) -> None:
            raise OSError("interrupted")

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError, match="interrupted"):
            cli(migrated, "candidate", "record-check", "Contoso", "--blocker", "403")
        assert path.read_bytes() == before
        assert list(migrated.glob(".candidate_sources-*")) == []

    def test_the_library_refuses_a_field_it_may_not_fill(self, migrated: Path) -> None:
        with pytest.raises(curated.CuratedError, match="cannot write"):
            curated.record_check(
                curated.candidates_path(migrated), "Contoso", {"url": "https://x.example"}
            )


class TestAnInterruptedPromote:
    """`promote` writes two files. A crash between them must not strand the board."""

    @staticmethod
    def crash_on_the_candidates_write(monkeypatch: pytest.MonkeyPatch) -> Any:
        """Returns the real writer, so a test can restore just this one patch."""
        real = curated.save_list

        def boom(path: Path, *args: Any, **kwargs: Any) -> Path | None:
            if path.name == "candidate_sources.yaml":
                raise OSError("crashed between the two writes")
            return real(path, *args, **kwargs)

        monkeypatch.setattr(curated, "save_list", boom)
        return real

    def test_the_crash_leaves_the_board_on_both_lists_not_on_neither(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_candidate(tmp_path, "Contoso Rail", "https://jobs.ashbyhq.com/contoso")
        candidates_before = curated.candidates_path(tmp_path).read_bytes()
        self.crash_on_the_candidates_write(monkeypatch)

        with pytest.raises(OSError, match="between the two writes"):
            cli(tmp_path, "candidate", "promote", "Contoso Rail")

        assert [e["organisation"] for e in curated.load_excluded(tmp_path)] == ["Contoso Rail"]
        assert curated.candidates_path(tmp_path).read_bytes() == candidates_before

    def test_running_promote_again_finishes_the_move(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Before this, the retry refused and `exclude` pointed back at `promote`: a trap."""
        add_candidate(tmp_path, "Contoso Rail", "https://jobs.ashbyhq.com/contoso")
        real_save_list = self.crash_on_the_candidates_write(monkeypatch)
        with pytest.raises(OSError):
            cli(tmp_path, "candidate", "promote", "Contoso Rail")
        tombstone_after_crash = curated.excluded_path(tmp_path).read_bytes()
        monkeypatch.setattr(curated, "save_list", real_save_list)
        capsys.readouterr()

        assert cli(tmp_path, "candidate", "promote", "contoso rail") == 0

        assert "earlier promote was interrupted" in capsys.readouterr().out
        assert curated.load_candidates(tmp_path) == []
        assert curated.excluded_path(tmp_path).read_bytes() == tombstone_after_crash, (
            "the tombstone entry is left exactly as the first attempt wrote it"
        )

    def test_the_retry_needs_no_reason_even_without_a_blocker(self, tmp_path: Path) -> None:
        """The tombstone already holds the reason; asking again would be noise."""
        curated.save_list(
            curated.candidates_path(tmp_path),
            [{"organisation": "Litware", "url": "https://litware.example/careers"}],
            curated.CANDIDATES_KEY,
            curated.CANDIDATE_FIELDS,
        )
        curated.save_list(
            curated.excluded_path(tmp_path),
            [{"organisation": "Litware", "url": "https://litware.example/", "reason": "403"}],
            curated.EXCLUDED_KEY,
            curated.EXCLUDED_FIELDS,
        )
        assert cli(tmp_path, "candidate", "promote", "Litware") == 0
        assert curated.load_candidates(tmp_path) == []

    def test_a_different_organisation_on_that_board_is_a_conflict_not_a_retry(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        curated.save_list(
            curated.candidates_path(tmp_path),
            [{"organisation": "Contoso Rail", "url": "https://jobs.ashbyhq.com/contoso"}],
            curated.CANDIDATES_KEY,
            curated.CANDIDATE_FIELDS,
        )
        curated.save_list(
            curated.excluded_path(tmp_path),
            [{"organisation": "Contoso", "url": "https://jobs.ashbyhq.com/contoso", "reason": "x"}],
            curated.EXCLUDED_KEY,
            curated.EXCLUDED_FIELDS,
        )
        before = {p.name: p.read_bytes() for p in tmp_path.glob("*.yaml")}

        assert cli(tmp_path, "candidate", "promote", "Contoso Rail") == 1

        assert "conflict" in capsys.readouterr().err
        assert {p.name: p.read_bytes() for p in tmp_path.glob("*.yaml")} == before


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


class TestWhenTheCommitFails:
    """The YAML write has already happened; a git failure must say so, not undo it."""

    @pytest.fixture
    def repo_without_an_identity(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """No user.name anywhere git looks — the likeliest failure on a fresh machine.

        The global and system config are shut out so the owner's own identity
        cannot rescue the commit, and `useConfigOnly` stops git inventing one
        from the hostname.
        """
        empty = tmp_path.parent / f"{tmp_path.name}-gitconfig"
        empty.write_text("", encoding="utf-8")
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        for name in (
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
            "EMAIL",
        ):
            monkeypatch.delenv(name, raising=False)
        _git(tmp_path, "init", "-q")
        _git(tmp_path, "config", "user.useConfigOnly", "true")
        return tmp_path

    def test_the_file_is_written_and_the_failure_is_reported(
        self, repo_without_an_identity: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = repo_without_an_identity
        assert run("--curated-dir", str(repo), "exclude", "Northwind", GREENHOUSE, "r") == 0

        out = capsys.readouterr().out
        assert "committing it failed" in out
        assert [e["organisation"] for e in curated.load_excluded(repo)] == ["Northwind"]
        log = subprocess.run(["git", "-C", str(repo), "log"], capture_output=True, text=True)
        assert log.returncode != 0, "no commit was made"

    def test_git_missing_from_path_is_reported_not_raised(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _git(tmp_path, "init", "-q")
        monkeypatch.setenv("PATH", str(tmp_path / "no-binaries-here"))

        assert run("--curated-dir", str(tmp_path), "exclude", "Northwind", GREENHOUSE, "r") == 0

        assert "git is not on PATH" in capsys.readouterr().out
        assert curated.excluded_path(tmp_path).is_file()


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestAsARealProcess:
    """Calling `main()` proves the parser; only a real process proves the command runs.

    `python scripts/migrate_curated_to_yaml.py` could not import the project
    when first written, and every in-process test passed regardless.
    """

    @staticmethod
    def sources(cwd: Path, *argv: str) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
        return subprocess.run(
            [sys.executable, "-m", "job_scraper.tools.sources", *argv],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_help_exits_zero_having_written_nothing(self, tmp_path: Path) -> None:
        done = self.sources(tmp_path, "--curated-dir", str(tmp_path), "candidate", "add", "--help")
        assert done.returncode == 0, done.stderr
        assert "usage:" in done.stdout
        assert list(tmp_path.iterdir()) == []

    def test_an_unknown_command_exits_non_zero_having_written_nothing(self, tmp_path: Path) -> None:
        done = self.sources(tmp_path, "--curated-dir", str(tmp_path), "destroy")
        assert done.returncode == 2
        assert list(tmp_path.iterdir()) == []

    def test_a_write_and_a_check_work_end_to_end(self, tmp_path: Path) -> None:
        url = "https://job-boards.greenhouse.io/subprocess-test-board"
        written = self.sources(
            tmp_path, "--curated-dir", str(tmp_path), "--no-commit", "exclude", "Nobody", url, "r"
        )
        assert written.returncode == 0, written.stderr
        found = self.sources(tmp_path, "--curated-dir", str(tmp_path), "check", url + "/")
        assert found.returncode == 0 and "EXCLUDED" in found.stdout


LEGACY_FIXTURES = Path(__file__).parent / "fixtures" / "curated"


class TestBeforeTheMigration:
    """An unmigrated tombstone must refuse, not read as empty.

    Found in review: with the real tombstone still a CSV, `check` answered
    "no match" for a permanently excluded employer and `candidate add` recorded
    it as a new lead. Both reproduced here first, against the fixture copies.
    """

    @pytest.fixture
    def unmigrated(self, tmp_path: Path) -> Path:
        for name in ("excluded_sources.csv", "candidate_sources.xlsx"):
            (tmp_path / name).write_bytes((LEGACY_FIXTURES / name).read_bytes())
        return tmp_path

    def test_check_refuses_rather_than_reporting_no_match(
        self, unmigrated: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli(unmigrated, "check", "https://job-boards.greenhouse.io/northwind") == 1
        captured = capsys.readouterr()
        assert "no match" not in captured.out
        assert "has not been migrated" in captured.err
        assert "migrate_curated_to_yaml.py" in captured.err

    def test_a_banned_board_cannot_be_added_as_a_candidate(self, unmigrated: Path) -> None:
        before = sorted(p.name for p in unmigrated.iterdir())
        assert (
            cli(
                unmigrated,
                "candidate",
                "add",
                "Northwind Health",
                "https://job-boards.greenhouse.io/northwind",
                "--blocker",
                "worth another look",
            )
            == 1
        )
        assert sorted(p.name for p in unmigrated.iterdir()) == before

    @pytest.mark.parametrize(
        "argv",
        [
            ["list"],
            ["list", "candidates"],
            ["exclude", "Someone", "https://someone.example", "r"],
            ["candidate", "promote", "Adventure Works"],
            ["candidate", "record-check", "Adventure Works", "--blocker", "b"],
        ],
        ids=lambda a: " ".join(a[:2]),
    )
    def test_every_command_refuses_and_writes_nothing(
        self, unmigrated: Path, argv: list[str]
    ) -> None:
        before = sorted(p.name for p in unmigrated.iterdir())
        assert cli(unmigrated, *argv) == 1
        assert sorted(p.name for p in unmigrated.iterdir()) == before

    def test_help_still_works(self, unmigrated: Path) -> None:
        with pytest.raises(SystemExit) as exit_info:
            run("--curated-dir", str(unmigrated), "check", "--help")
        assert exit_info.value.code == 0

    def test_one_unmigrated_list_is_enough_to_refuse(self, tmp_path: Path) -> None:
        (tmp_path / "excluded_sources.csv").write_bytes(
            (LEGACY_FIXTURES / "excluded_sources.csv").read_bytes()
        )
        assert cli(tmp_path, "list", "candidates") == 1

    def test_the_library_refuses_too(self, unmigrated: Path) -> None:
        """SP3 and SP7 will read the tombstone without going through argv."""
        with pytest.raises(curated.NotMigratedError):
            curated.load_excluded(unmigrated)
        with pytest.raises(curated.NotMigratedError):
            curated.load_candidates(unmigrated)

    def test_after_the_migration_the_old_files_may_stay(
        self, unmigrated: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Keeping the CSV and XLSX is the owner's choice and must not block the tool."""
        import importlib.util

        script = PROJECT_ROOT / "scripts" / "migrate_curated_to_yaml.py"
        spec = importlib.util.spec_from_file_location("_migrate_for_test", script)
        assert spec is not None and spec.loader is not None
        migrate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migrate)
        assert migrate.main(["--curated-dir", str(unmigrated)]) == 0
        capsys.readouterr()

        assert cli(unmigrated, "check", "https://job-boards.greenhouse.io/northwind/") == 0
        assert "EXCLUDED" in capsys.readouterr().out
        assert (unmigrated / "excluded_sources.csv").is_file()
