"""SP1: the one-off migration of the curated lists to YAML.

The real `data/curated/excluded_sources.csv` and `candidate_sources.xlsx` are
never read here — the owner runs the migration against those. The fixtures in
`tests/fixtures/curated/` carry the same *shapes*: a semicolon-delimited,
CRLF, quoted-where-needed CSV of the kind a European-locale spreadsheet export
produces, and a three-column `organisation / url / notes` workbook. The
organisations in them are invented, because a list of employers that could not
be scraped is the one thing `docs/SOURCES-PLAN.md` decided stays unpublished.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
import yaml

from job_scraper import curated

# The script lives in `scripts/`, outside the package. One import follows the
# amendment, so there is nothing for an import-sorter to hoist above it —
# the same shape `tests/fixture_cases.py` uses for `capture_fixtures`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import migrate_curated_to_yaml as migrate

FIXTURES = Path(__file__).parent / "fixtures" / "curated"


@pytest.fixture
def old_files(tmp_path: Path) -> Path:
    for name in ("excluded_sources.csv", "candidate_sources.xlsx"):
        shutil.copy2(FIXTURES / name, tmp_path / name)
    return tmp_path


def read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class TestExcludedCsv:
    def test_every_row_survives_with_its_reason(self, old_files: Path) -> None:
        assert migrate.main(["--curated-dir", str(old_files)]) == 0
        entries = read_yaml(curated.excluded_path(old_files))["excluded"]
        assert [e["organisation"] for e in entries] == [
            "Northwind Health",
            "Contoso Rail",
            "Fabrikam Nutrition",
            "Tailspin Aid",
        ]
        assert entries[0]["reason"] == "Requires a login before any posting is visible"

    def test_a_reason_containing_the_delimiter_survives(self, old_files: Path) -> None:
        """The quoted field is why this reads with csv rather than str.split(';')."""
        migrate.main(["--curated-dir", str(old_files)])
        entries = read_yaml(curated.excluded_path(old_files))["excluded"]
        assert entries[2]["reason"] == "No job content in the DOM; rendered from a private API"

    def test_the_date_it_never_held_is_null_not_today(self, old_files: Path) -> None:
        """`excluded_on` is unknown for every migrated row. Guessing it is worse."""
        migrate.main(["--curated-dir", str(old_files)])
        entries = read_yaml(curated.excluded_path(old_files))["excluded"]
        assert {e["excluded_on"] for e in entries} == {None}

    def test_the_result_loads_through_the_normal_reader(self, old_files: Path) -> None:
        migrate.main(["--curated-dir", str(old_files)])
        assert len(curated.load_excluded(old_files)) == 4

    def test_a_comma_delimited_export_is_read_too(self, tmp_path: Path) -> None:
        (tmp_path / "excluded_sources.csv").write_text(
            "organisation,url,reason\nNorthwind,https://northwind.example,403\n", encoding="utf-8"
        )
        assert migrate.main(["--curated-dir", str(tmp_path)]) == 0
        assert len(curated.load_excluded(tmp_path)) == 1

    def test_an_unexpected_header_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (tmp_path / "excluded_sources.csv").write_text(
            "company;link;why\nNorthwind;https://northwind.example;403\n", encoding="utf-8"
        )
        assert migrate.main(["--curated-dir", str(tmp_path)]) == 1
        assert "expected header" in capsys.readouterr().err
        assert not curated.excluded_path(tmp_path).exists()

    def test_a_short_row_is_refused_rather_than_padded(self, tmp_path: Path) -> None:
        (tmp_path / "excluded_sources.csv").write_text(
            "organisation;url;reason\nNorthwind;https://northwind.example\n", encoding="utf-8"
        )
        assert migrate.main(["--curated-dir", str(tmp_path)]) == 1
        assert not curated.excluded_path(tmp_path).exists()


class TestCandidatesXlsx:
    def test_every_row_survives(self, old_files: Path) -> None:
        assert migrate.main(["--curated-dir", str(old_files)]) == 0
        entries = read_yaml(curated.candidates_path(old_files))["candidates"]
        assert [e["organisation"] for e in entries] == [
            "Adventure Works",
            "Wide World Importers",
            "Litware Institute",
        ]

    def test_the_new_fields_are_null_and_the_provenance_is_recorded(self, old_files: Path) -> None:
        migrate.main(["--curated-dir", str(old_files)])
        entry = read_yaml(curated.candidates_path(old_files))["candidates"][0]
        assert entry == {
            "organisation": "Adventure Works",
            "url": "https://job-boards.greenhouse.io/adventureworks",
            "category": None,
            "blocker": None,
            "last_checked": None,
            "ats": None,
            "source_of_record": "migrated from candidate_sources.xlsx",
        }

    def test_a_note_becomes_the_blocker(self, old_files: Path) -> None:
        migrate.main(["--curated-dir", str(old_files)])
        entries = read_yaml(curated.candidates_path(old_files))["candidates"]
        assert entries[2]["blocker"] == "Teamtailor, looks straightforward"

    def test_the_result_loads_through_the_normal_reader(self, old_files: Path) -> None:
        migrate.main(["--curated-dir", str(old_files)])
        assert len(curated.load_candidates(old_files)) == 3


class TestSafety:
    def test_the_old_files_are_left_exactly_where_they_are(self, old_files: Path) -> None:
        before = {
            name: (old_files / name).read_bytes()
            for name in ("excluded_sources.csv", "candidate_sources.xlsx")
        }
        migrate.main(["--curated-dir", str(old_files)])
        for name, content in before.items():
            assert (old_files / name).read_bytes() == content

    def test_it_refuses_to_overwrite_a_yaml_file_that_already_exists(
        self, old_files: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        migrate.main(["--curated-dir", str(old_files)])
        after = curated.excluded_path(old_files).read_bytes()
        capsys.readouterr()

        assert migrate.main(["--curated-dir", str(old_files)]) == 1
        assert "already exists" in capsys.readouterr().err
        assert curated.excluded_path(old_files).read_bytes() == after

    def test_two_rows_for_one_board_are_refused(self, tmp_path: Path) -> None:
        """A duplicate the append-only CLI could never repair afterwards."""
        (tmp_path / "excluded_sources.csv").write_text(
            "organisation;url;reason\n"
            "Northwind;https://job-boards.greenhouse.io/northwind;403\n"
            "Northwind Health;http://www.job-boards.greenhouse.io/northwind/;login\n",
            encoding="utf-8",
        )
        assert migrate.main(["--curated-dir", str(tmp_path)]) == 1
        assert not curated.excluded_path(tmp_path).exists()

    def test_two_boards_on_one_shared_host_are_not_a_duplicate(self, tmp_path: Path) -> None:
        (tmp_path / "excluded_sources.csv").write_text(
            "organisation;url;reason\n"
            "Northwind;https://job-boards.greenhouse.io/northwind;403\n"
            "Adventure Works;https://job-boards.greenhouse.io/adventureworks;login\n",
            encoding="utf-8",
        )
        assert migrate.main(["--curated-dir", str(tmp_path)]) == 0
        assert len(curated.load_excluded(tmp_path)) == 2

    def test_a_missing_old_file_is_skipped_not_an_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert migrate.main(["--curated-dir", str(tmp_path)]) == 0
        assert "skipped" in capsys.readouterr().out
        assert list(tmp_path.iterdir()) == []

    def test_dry_run_prints_the_yaml_and_writes_nothing(
        self, old_files: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert migrate.main(["--curated-dir", str(old_files), "--dry-run"]) == 0
        out = capsys.readouterr().out
        assert "Northwind Health" in out and "Adventure Works" in out
        assert not curated.excluded_path(old_files).exists()
        assert not curated.candidates_path(old_files).exists()

    def test_help_exits_zero_having_migrated_nothing(
        self, old_files: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exit_info:
            migrate.main(["--help"])
        assert exit_info.value.code == 0
        assert "usage:" in capsys.readouterr().out
        assert not curated.excluded_path(old_files).exists()

    def test_an_unrecognised_argument_exits_non_zero(self, old_files: Path) -> None:
        with pytest.raises(SystemExit) as exit_info:
            migrate.main(["--force"])
        assert exit_info.value.code != 0
        assert not curated.excluded_path(old_files).exists()

    def test_the_output_directory_can_differ_from_the_input(
        self, old_files: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        out = tmp_path_factory.mktemp("out")
        assert migrate.main(["--curated-dir", str(old_files), "--out-dir", str(out)]) == 0
        assert curated.excluded_path(out).is_file()
        assert not curated.excluded_path(old_files).exists()
