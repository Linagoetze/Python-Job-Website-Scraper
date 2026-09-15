"""One-off: convert the two curated source lists to YAML.

    python scripts/migrate_curated_to_yaml.py --dry-run
    python scripts/migrate_curated_to_yaml.py

`data/curated/excluded_sources.csv` (semicolon-delimited, the fingerprint of a
European-locale spreadsheet export) becomes `excluded_sources.yaml`, and
`candidate_sources.xlsx` becomes `candidate_sources.yaml`. **The old files are
left exactly where they are** — deleting them is the owner's call, and they are
gitignored either way.

Run it yourself. A session must not: `CLAUDE.md` allows writes to
`data/curated/` only through `job_scraper.tools.sources`, and this is the one
step that has to write two whole files at once. It refuses to overwrite an
existing YAML file, so running it twice is safe.

Fields the old formats never held — `excluded_on`, and every candidate field
but the organisation and the URL — are written as null rather than guessed. A
date invented during a migration is worse than an absent one: `last_checked`
exists precisely so nobody re-checks a source blind.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

# scripts/ lives outside the package, so importing the project needs the root
# on the path — the same amendment `capture_fixtures.py` makes, for the same
# reason, immediately before the imports it enables.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_scraper import curated
from job_scraper.config_loader import default_curated_dir
from job_scraper.urlutil import board_identity

EXCLUDED_CSV_HEADER = ("organisation", "url", "reason")
CANDIDATES_XLSX_HEADER = ("organisation", "url", "notes")


class MigrationError(Exception):
    """The input is not what this script was told to expect. Nothing is written."""


def _cell(value: Any) -> str:
    return "" if value is None else str(value).strip()


def read_excluded_csv(path: Path) -> list[dict[str, Any]]:
    """The tombstone rows. Semicolon-delimited, but comma is accepted too."""
    text = path.read_text(encoding="utf-8-sig")
    first = text.splitlines()[0] if text.strip() else ""
    delimiter = ";" if ";" in first else ","
    rows = list(csv.reader(text.splitlines(), delimiter=delimiter))
    if not rows:
        raise MigrationError(f"{path} is empty")
    header = tuple(h.strip().lower() for h in rows[0])
    if header != EXCLUDED_CSV_HEADER:
        raise MigrationError(f"{path}: expected header {EXCLUDED_CSV_HEADER}, found {header}")
    out: list[dict[str, Any]] = []
    for line, row in enumerate(rows[1:], start=2):
        if not any(_cell(c) for c in row):
            continue
        if len(row) != 3:
            raise MigrationError(f"{path} line {line}: expected 3 fields, found {len(row)}")
        organisation, url, reason = (_cell(c) for c in row)
        if not (organisation and url and reason):
            raise MigrationError(f"{path} line {line}: organisation, url and reason are required")
        out.append(
            {
                "organisation": organisation,
                "url": url,
                "reason": reason,
                "excluded_on": None,  # the CSV never recorded one
            }
        )
    return out


def read_candidates_xlsx(path: Path) -> list[dict[str, Any]]:
    """The candidate rows. `notes` becomes `blocker` — it is the nearest field."""
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        rows = list(sheet.iter_rows(values_only=True))
    finally:
        workbook.close()
    if not rows:
        raise MigrationError(f"{path} is empty")
    header = tuple(_cell(c).lower() for c in rows[0][: len(CANDIDATES_XLSX_HEADER)])
    if header != CANDIDATES_XLSX_HEADER:
        raise MigrationError(f"{path}: expected header {CANDIDATES_XLSX_HEADER}, found {header}")
    out: list[dict[str, Any]] = []
    for line, row in enumerate(rows[1:], start=2):
        organisation, url, notes = (_cell(c) for c in (list(row) + [None, None, None])[:3])
        if not (organisation or url or notes):
            continue
        if not (organisation and url):
            raise MigrationError(f"{path} row {line}: organisation and url are required")
        out.append(
            {
                "organisation": organisation,
                "url": url,
                "category": None,
                "blocker": notes or None,
                "last_checked": None,  # the spreadsheet never recorded one
                "ats": None,
                "source_of_record": f"migrated from {path.name}",
            }
        )
    return out


def _reject_duplicate_boards(entries: list[dict[str, Any]], label: str) -> None:
    """Two rows for one employer board would be a duplicate the CLI could never fix."""
    seen: dict[str, str] = {}
    for entry in entries:
        try:
            identity = board_identity(str(entry["url"]))
        except ValueError as exc:
            raise MigrationError(f"{label}: {entry['organisation']}: {exc}") from exc
        if identity in seen:
            raise MigrationError(
                f"{label}: {entry['organisation']} and {seen[identity]} are the same board "
                f"({identity}). Resolve it in the old file first."
            )
        seen[identity] = str(entry["organisation"])


def migrate(
    *,
    excluded_csv: Path,
    candidates_xlsx: Path,
    out_dir: Path,
    dry_run: bool,
) -> int:
    plans: list[tuple[str, Path, list[dict[str, Any]], str, tuple[str, ...]]] = []
    if excluded_csv.is_file():
        entries = read_excluded_csv(excluded_csv)
        _reject_duplicate_boards(entries, excluded_csv.name)
        plans.append(
            (
                excluded_csv.name,
                curated.excluded_path(out_dir),
                entries,
                curated.EXCLUDED_KEY,
                curated.EXCLUDED_FIELDS,
            )
        )
    else:
        print(f"skipped: {excluded_csv} does not exist")
    if candidates_xlsx.is_file():
        entries = read_candidates_xlsx(candidates_xlsx)
        _reject_duplicate_boards(entries, candidates_xlsx.name)
        plans.append(
            (
                candidates_xlsx.name,
                curated.candidates_path(out_dir),
                entries,
                curated.CANDIDATES_KEY,
                curated.CANDIDATE_FIELDS,
            )
        )
    else:
        print(f"skipped: {candidates_xlsx} does not exist")

    # Decide about every target before writing any. Refusing the second file
    # after writing the first stranded the run: the next attempt then refused
    # the first. A target already holding exactly what this run would write is
    # a finished half of an interrupted run, so re-running completes it.
    pending: list[tuple[str, Path, list[dict[str, Any]], str, tuple[str, ...]]] = []
    conflicts: list[Path] = []
    for name, target, entries, key, fields in plans:
        text = curated.render_list(entries, key, fields)
        if target.exists():
            if target.read_text(encoding="utf-8") == text:
                print(f"already migrated: {target} matches {name}, left as it is")
                continue
            conflicts.append(target)
            continue
        pending.append((name, target, entries, key, fields))
    if conflicts:
        raise MigrationError(
            "nothing was written: "
            + ", ".join(str(p) for p in conflicts)
            + " already exists with content this migration would not produce. This script "
            "never overwrites a curated file. Compare it with the old file and move it aside "
            "yourself before migrating."
        )

    for name, target, entries, key, fields in pending:
        if dry_run:
            print(f"--- {name} -> {target} ({len(entries)} entries) ---")
            print(curated.render_list(entries, key, fields), end="")
            continue
        curated.save_list(target, entries, key, fields)
        print(f"{name} -> {target} ({len(entries)} entries)")

    if dry_run:
        print("dry run: nothing written")
    elif pending:
        print("The old files were left where they are. Delete them yourself once you are happy.")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """The front door. `--help` prints this docstring and migrates nothing."""
    parser = argparse.ArgumentParser(
        prog="python scripts/migrate_curated_to_yaml.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--curated-dir", type=Path, default=None, help="default: data/curated/")
    parser.add_argument("--excluded-csv", type=Path, default=None)
    parser.add_argument("--candidates-xlsx", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None, help="default: --curated-dir")
    parser.add_argument("--dry-run", action="store_true", help="print the YAML, write nothing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    curated_dir = args.curated_dir or default_curated_dir()
    try:
        return migrate(
            excluded_csv=args.excluded_csv or curated_dir / "excluded_sources.csv",
            candidates_xlsx=args.candidates_xlsx or curated_dir / "candidate_sources.xlsx",
            out_dir=args.out_dir or curated_dir,
            dry_run=args.dry_run,
        )
    except (MigrationError, curated.CuratedError) as exc:
        print(f"migration refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
