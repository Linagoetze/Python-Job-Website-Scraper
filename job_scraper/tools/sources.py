"""Read and append to the two curated source lists, without opening a file.

    python -m job_scraper.tools.sources list [excluded|candidates]
    python -m job_scraper.tools.sources check <url-or-name>
    python -m job_scraper.tools.sources exclude <org> <url> <reason>
    python -m job_scraper.tools.sources candidate add <org> <url> --blocker ...
    python -m job_scraper.tools.sources candidate promote <org>

`check` searches both curated lists *and* `sources.yaml`, and exits 1 when it
finds nothing, so `sources check <url> || echo new` works.

Every writing command is append-only: it refuses a board that is already on a
list rather than modifying the entry, backs the file up with a timestamped
`.bak` beside it, and replaces it through a temp file in the same directory.
`promote` is the single exception — moving a candidate to the tombstone means
removing it from the candidates file — and it backs up both files.

Matching is by **board identity**, not by host: `job-boards.greenhouse.io`
carries six different employers in `sources.yaml` today, and a host match would
call a seventh one already tombstoned.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any

from job_scraper import curated
from job_scraper.config_loader import default_curated_dir, default_sources_path, load_sources
from job_scraper.urlutil import board_identity

_EXCLUDED = curated.EXCLUDED_KEY
_CANDIDATES = curated.CANDIDATES_KEY


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """The front door. Nothing below it runs until argv has been read.

    `blocklist_all --help` once ran the command it was asking about, because it
    had no parser at all and never looked at its arguments (WP10). Every
    subcommand here has one, `--help` prints and exits, and an unrecognised
    argument exits non-zero having changed nothing.
    """
    parser = argparse.ArgumentParser(
        prog="python -m job_scraper.tools.sources",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--curated-dir",
        type=Path,
        default=None,
        help="override data/curated/ (tests and dry runs only)",
    )
    parser.add_argument(
        "--no-commit",
        action="store_true",
        help="skip the commit to the private git repository inside data/curated/",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="print one or both curated lists")
    listing.add_argument("which", nargs="?", choices=[_EXCLUDED, _CANDIDATES], default=None)

    check = sub.add_parser("check", help="is this board already known? searches sources.yaml too")
    check.add_argument("query", help="a URL, or part of an organisation name")

    exclude = sub.add_parser("exclude", help="tombstone a board permanently")
    exclude.add_argument("organisation")
    exclude.add_argument("url")
    exclude.add_argument("reason")
    exclude.add_argument("--on", dest="excluded_on", default=None, help="date (default: today)")

    candidate = sub.add_parser("candidate", help="the not-done-yet list")
    candidate_sub = candidate.add_subparsers(dest="candidate_command", required=True)

    add = candidate_sub.add_parser("add", help="record a board still to be checked")
    add.add_argument("organisation")
    add.add_argument("url")
    add.add_argument("--blocker", required=True, help="why it is not a source yet")
    add.add_argument("--ats", default=None)
    add.add_argument("--category", default=None)
    add.add_argument("--last-checked", dest="last_checked", default=None, help="default: today")
    add.add_argument("--source-of-record", dest="source_of_record", default=None)

    promote = candidate_sub.add_parser("promote", help="move a candidate to the tombstone")
    promote.add_argument("organisation")
    promote.add_argument("--reason", default=None, help="default: the candidate's blocker")
    promote.add_argument("--on", dest="excluded_on", default=None, help="date (default: today)")

    return parser.parse_args(argv)


# --- rendering -------------------------------------------------------------


def _render_entry(entry: dict[str, Any], fields: tuple[str, ...]) -> str:
    head = f"{entry.get('organisation')}  {entry.get('url')}"
    rest = [
        f"    {name}: {entry[name]}"
        for name in fields
        if name not in ("organisation", "url") and entry.get(name) not in (None, "")
    ]
    return "\n".join([head, *rest])


def _print_list(title: str, entries: list[dict[str, Any]], fields: tuple[str, ...]) -> None:
    print(f"{title} ({len(entries)})")
    if not entries:
        print("    (empty)")
    for entry in entries:
        print(_render_entry(entry, fields))


# --- commands --------------------------------------------------------------


def _cmd_list(args: argparse.Namespace, curated_dir: Path) -> int:
    if args.which in (None, _EXCLUDED):
        _print_list("excluded", curated.load_excluded(curated_dir), curated.EXCLUDED_FIELDS)
    if args.which is None:
        print()
    if args.which in (None, _CANDIDATES):
        _print_list("candidates", curated.load_candidates(curated_dir), curated.CANDIDATE_FIELDS)
    return 0


def _active_sources() -> list[dict[str, Any]]:
    """`sources.yaml` if it exists — a fresh clone has only the example."""
    if not default_sources_path().is_file():
        return []
    return load_sources()


def _matching_sources(query: str, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        wanted: str | None = board_identity(query)
    except ValueError:
        wanted = None
    needle = query.strip().casefold()
    out = []
    for source in sources:
        url = str(source.get("url") or "")
        identity = None
        try:
            identity = board_identity(url)
        except ValueError:
            pass
        names = (str(source.get("company") or ""), str(source.get("name") or ""))
        if (wanted is not None and identity == wanted) or any(
            needle and needle in name.casefold() for name in names
        ):
            out.append(source)
    return out


def _matching_entries(query: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    board = curated.find_board(entries, query)
    needle = query.strip().casefold()
    out = [e for e in entries if board is not None and e is board]
    out += [
        e
        for e in entries
        if e is not board and needle and needle in str(e.get("organisation") or "").casefold()
    ]
    return out


def _cmd_check(args: argparse.Namespace, curated_dir: Path) -> int:
    query = args.query
    excluded = _matching_entries(query, curated.load_excluded(curated_dir))
    candidates = _matching_entries(query, curated.load_candidates(curated_dir))
    active = _matching_sources(query, _active_sources())

    for entry in excluded:
        print("EXCLUDED — tombstoned, do not re-propose")
        print(_render_entry(entry, curated.EXCLUDED_FIELDS))
    for entry in candidates:
        print("CANDIDATE — on the list, not checked off")
        print(_render_entry(entry, curated.CANDIDATE_FIELDS))
    for source in active:
        print("ACTIVE SOURCE — already scraped, in sources.yaml")
        print(f"{source.get('company') or source.get('name')}  {source.get('url')}")
        print(f"    name: {source.get('name')}")

    if not (excluded or candidates or active):
        print(f"no match for {query!r} in the tombstone, the candidates or sources.yaml")
        return 1
    return 0


def _today() -> str:
    return date.today().isoformat()


def _report_write(curated_dir: Path, written: list[Path], backups: list[Path], note: str) -> None:
    for backup in backups:
        print(f"backed up  {backup.name}")
    for path in written:
        print(f"wrote      {path}")
    print(note)


def _commit(curated_dir: Path, paths: list[Path], message: str, *, skip: bool) -> None:
    if skip:
        print("not committed (--no-commit)")
        return
    note = curated.commit_curated_change(curated_dir, paths, message)
    print(
        note
        if note is not None
        else (
            f"{curated_dir} is not a git repository, so there is no undo beyond the .bak — "
            "`git init` there to get one (SOURCES-PLAN.md SP0)"
        )
    )


def _cmd_exclude(args: argparse.Namespace, curated_dir: Path) -> int:
    path = curated.excluded_path(curated_dir)
    clash = curated.find_board(curated.load_candidates(curated_dir), args.url)
    if clash is not None:
        print(
            f"{args.url} is a candidate ({clash['organisation']}). Move it with "
            f"`candidate promote {clash['organisation']!r}` so it leaves that list too.",
            file=sys.stderr,
        )
        return 1
    active = _matching_sources(args.url, _active_sources())
    if active:
        print(
            f"{args.url} is an active source in sources.yaml ({active[0].get('name')}). "
            "Remove it there first — a tombstoned source that is still being scraped is a "
            "contradiction the next session has to resolve.",
            file=sys.stderr,
        )
        return 1
    entry = {
        "organisation": args.organisation,
        "url": args.url,
        "reason": args.reason,
        "excluded_on": args.excluded_on or _today(),
    }
    backup = curated.append_entry(
        path, entry, _EXCLUDED, curated.EXCLUDED_FIELDS, ("organisation", "url", "reason")
    )
    _report_write(curated_dir, [path], [b for b in [backup] if b], "excluded.")
    _commit(curated_dir, [path], f"exclude {args.organisation}", skip=args.no_commit)
    return 0


def _cmd_candidate_add(args: argparse.Namespace, curated_dir: Path) -> int:
    path = curated.candidates_path(curated_dir)
    tombstoned = curated.find_board(curated.load_excluded(curated_dir), args.url)
    if tombstoned is not None:
        print(
            f"{args.url} is already tombstoned ({tombstoned['organisation']}: "
            f"{tombstoned['reason']}). That is what the tombstone is for — not re-proposing it.",
            file=sys.stderr,
        )
        return 1
    active = _matching_sources(args.url, _active_sources())
    if active:
        print(
            f"{args.url} is already an active source in sources.yaml "
            f"({active[0].get('name')}) — nothing to check.",
            file=sys.stderr,
        )
        return 1
    entry = {
        "organisation": args.organisation,
        "url": args.url,
        "category": args.category,
        "blocker": args.blocker,
        "last_checked": args.last_checked or _today(),
        "ats": args.ats,
        "source_of_record": args.source_of_record,
    }
    backup = curated.append_entry(
        path, entry, _CANDIDATES, curated.CANDIDATE_FIELDS, ("organisation", "url")
    )
    _report_write(curated_dir, [path], [b for b in [backup] if b], "added as a candidate.")
    _commit(curated_dir, [path], f"candidate {args.organisation}", skip=args.no_commit)
    return 0


def _cmd_candidate_promote(args: argparse.Namespace, curated_dir: Path) -> int:
    candidates_file = curated.candidates_path(curated_dir)
    excluded_file = curated.excluded_path(curated_dir)
    candidates = curated.load_candidates(curated_dir)
    entry = curated.find_organisation(candidates, args.organisation)
    if entry is None:
        print(f"no candidate named {args.organisation!r}", file=sys.stderr)
        return 1
    reason = args.reason or entry.get("blocker")
    if not str(reason or "").strip():
        print(
            f"{entry['organisation']} has no blocker recorded, so there is nothing to write as "
            "the reason. Pass --reason.",
            file=sys.stderr,
        )
        return 1

    tombstone = {
        "organisation": entry["organisation"],
        "url": entry["url"],
        "reason": reason,
        "excluded_on": args.excluded_on or _today(),
    }
    # The tombstone is written first: a crash between the two writes leaves the
    # board on both lists, which `check` reports plainly. The other order would
    # lose it from both.
    excluded_backup = curated.append_entry(
        excluded_file,
        tombstone,
        _EXCLUDED,
        curated.EXCLUDED_FIELDS,
        ("organisation", "url", "reason"),
    )
    remaining = [e for e in candidates if e is not entry]
    candidates_backup = curated.save_list(
        candidates_file, remaining, _CANDIDATES, curated.CANDIDATE_FIELDS
    )
    _report_write(
        curated_dir,
        [excluded_file, candidates_file],
        [b for b in (excluded_backup, candidates_backup) if b],
        f"promoted {entry['organisation']} to the tombstone.",
    )
    _commit(
        curated_dir,
        [excluded_file, candidates_file],
        f"promote {entry['organisation']} to the tombstone",
        skip=args.no_commit,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    curated_dir = args.curated_dir or default_curated_dir()
    try:
        if args.command == "list":
            return _cmd_list(args, curated_dir)
        if args.command == "check":
            return _cmd_check(args, curated_dir)
        if args.command == "exclude":
            return _cmd_exclude(args, curated_dir)
        if args.candidate_command == "add":
            return _cmd_candidate_add(args, curated_dir)
        return _cmd_candidate_promote(args, curated_dir)
    except (curated.CuratedError, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
