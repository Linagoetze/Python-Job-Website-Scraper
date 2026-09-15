"""The two curated source lists: the tombstone and the candidates.

Both live in `data/curated/` as YAML — hand-editable, and not something a
spreadsheet application claims. Neither is git-backed by the outer repository
(everything under `data/curated/` is ignored by design), so every write here
takes a timestamped backup first, goes through a temp file and `os.replace()`,
and optionally commits to a private repository *inside* that directory.

Two states, deliberately, not three:

* **excluded** — the tombstone. Ruled out permanently, with the reason.
* **candidates** — not done yet, carrying the blocker and the date it was last
  checked so the investigation is not repeated blind.

Entries are matched on **board identity**, never on host: half the supported
ATS platforms put every customer on one hostname. See
`job_scraper.urlutil.board_identity`.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from job_scraper.config_loader import load_sources
from job_scraper.urlutil import board_identity

log = logging.getLogger(__name__)

EXCLUDED_KEY = "excluded"
CANDIDATES_KEY = "candidates"

EXCLUDED_FIELDS: tuple[str, ...] = ("organisation", "url", "reason", "excluded_on")
CANDIDATE_FIELDS: tuple[str, ...] = (
    "organisation",
    "url",
    "category",
    "blocker",
    "last_checked",
    "ats",
    "source_of_record",
)

_REQUIRED_EXCLUDED: tuple[str, ...] = ("organisation", "url", "reason")
_REQUIRED_CANDIDATE: tuple[str, ...] = ("organisation", "url")

_HEADERS = {
    EXCLUDED_KEY: """\
# Permanently ruled-out sources — the tombstone. One entry per employer board.
#
# WRITTEN BY `python -m job_scraper.tools.sources`, NOT BY HAND. The tool is
# append-only, refuses a duplicate board, backs this file up before every write
# and replaces it atomically. Comments added here are not preserved.
#
# Fields: organisation, url, reason, excluded_on (YYYY-MM-DD).
""",
    CANDIDATES_KEY: """\
# Sources still to check — candidates. Not "rejected": "not done yet".
#
# WRITTEN BY `python -m job_scraper.tools.sources`, NOT BY HAND. The tool is
# append-only, refuses a duplicate board, backs this file up before every write
# and replaces it atomically. Comments added here are not preserved.
#
# `blocker` and `last_checked` are the point of this file: a candidate that was
# checked and rejected must carry why and when, or the check gets repeated
# blind. Fields: organisation, url, category, blocker, last_checked
# (YYYY-MM-DD), ats, source_of_record.
""",
}


class CuratedError(Exception):
    """A write that was refused. The file on disk is unchanged."""


class DuplicateBoardError(CuratedError):
    """The board is already on a list. Nothing is ever silently overwritten."""


class NotMigratedError(CuratedError):
    """A list still exists only in its pre-YAML format. Refuse, never read it as empty."""


# --- reading ---------------------------------------------------------------


def excluded_path(curated_dir: Path) -> Path:
    return curated_dir / "excluded_sources.yaml"


def candidates_path(curated_dir: Path) -> Path:
    return curated_dir / "candidate_sources.yaml"


def _coerce_scalar(value: Any) -> Any:
    """Dates back to ISO strings, so the loaded list is plain data.

    PyYAML turns an unquoted `2026-05-11` into a `date`; leaving that in place
    would mean a value's type depended on how the previous writer quoted it.
    """
    if isinstance(value, date):
        return value.isoformat()
    return value


def load_list(path: Path, key: str, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    """Read one curated list. A missing file is an empty list, not an error.

    A file that exists but is malformed *is* an error: an unreadable tombstone
    must not look like an empty one, or a session re-proposes everything in it.
    """
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return []
    if not isinstance(data, dict) or key not in data:
        raise CuratedError(f"{path} is not a curated list: expected a top-level '{key}:' key")
    entries = data[key] or []
    if not isinstance(entries, list):
        raise CuratedError(f"'{key}' must be a list in {path}")
    out: list[dict[str, Any]] = []
    for i, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise CuratedError(f"{path}: entry {i} under '{key}' is not a mapping")
        unknown = sorted(set(entry) - set(fields))
        if unknown:
            raise CuratedError(f"{path}: entry {i} has unknown field(s): {', '.join(unknown)}")
        out.append({f: _coerce_scalar(entry.get(f)) for f in fields})
    return out


# The formats the two lists had before SP1. Only their presence matters here;
# reading them is the migration script's job.
LEGACY_FILES: dict[str, str] = {
    EXCLUDED_KEY: "excluded_sources.csv",
    CANDIDATES_KEY: "candidate_sources.xlsx",
}

MIGRATION_COMMAND = "python scripts/migrate_curated_to_yaml.py --dry-run"


def unmigrated_legacy_files(curated_dir: Path) -> list[Path]:
    """Old-format lists whose YAML replacement does not exist yet."""
    targets = {
        EXCLUDED_KEY: excluded_path(curated_dir),
        CANDIDATES_KEY: candidates_path(curated_dir),
    }
    return [
        curated_dir / name
        for key, name in LEGACY_FILES.items()
        if (curated_dir / name).is_file() and not targets[key].exists()
    ]


def require_migrated(curated_dir: Path, *, key: str | None = None) -> None:
    """Refuse while a list exists only in its old format.

    A missing YAML file otherwise reads as an empty list — and an empty
    tombstone is the one failure this file exists to prevent: `check` reports
    a permanently excluded employer as unknown, and `candidate add` records it
    as a fresh lead. Before SP1 the real tombstone was a CSV, so that is not
    hypothetical; a reviewer reproduced both. Pass *key* to check one list.
    """
    legacy = unmigrated_legacy_files(curated_dir)
    if key is not None:
        legacy = [p for p in legacy if p.name == LEGACY_FILES[key]]
    if legacy:
        names = ", ".join(p.name for p in legacy)
        raise NotMigratedError(
            f"{names} in {curated_dir} has not been migrated to YAML, so this tool cannot see "
            f"what it holds and will not pretend the list is empty. The owner migrates it: "
            f"`{MIGRATION_COMMAND}`, then again without --dry-run."
        )


def load_excluded(curated_dir: Path) -> list[dict[str, Any]]:
    require_migrated(curated_dir, key=EXCLUDED_KEY)
    return load_list(excluded_path(curated_dir), EXCLUDED_KEY, EXCLUDED_FIELDS)


def load_candidates(curated_dir: Path) -> list[dict[str, Any]]:
    require_migrated(curated_dir, key=CANDIDATES_KEY)
    return load_list(candidates_path(curated_dir), CANDIDATES_KEY, CANDIDATE_FIELDS)


def find_board(entries: list[dict[str, Any]], url: str) -> dict[str, Any] | None:
    """The entry naming the same employer board as *url*, or None."""
    try:
        wanted = board_identity(url)
    except ValueError:
        return None
    for entry in entries:
        candidate_url = str(entry.get("url") or "")
        try:
            if board_identity(candidate_url) == wanted:
                return entry
        except ValueError:
            continue
    return None


def find_organisation(entries: list[dict[str, Any]], organisation: str) -> dict[str, Any] | None:
    """The entry for *organisation*, compared case-insensitively."""
    wanted = organisation.strip().casefold()
    for entry in entries:
        if str(entry.get("organisation") or "").strip().casefold() == wanted:
            return entry
    return None


# --- writing ---------------------------------------------------------------


def _validate(entry: dict[str, Any], fields: tuple[str, ...], required: tuple[str, ...]) -> None:
    unknown = sorted(set(entry) - set(fields))
    if unknown:
        raise CuratedError(f"unknown field(s): {', '.join(unknown)}")
    for name in required:
        if not str(entry.get(name) or "").strip():
            raise CuratedError(f"{name} is required and must not be empty")
    for name in ("excluded_on", "last_checked"):
        value = entry.get(name)
        if value in (None, ""):
            continue
        try:
            date.fromisoformat(str(value))
        except ValueError as exc:
            raise CuratedError(f"{name} must be a YYYY-MM-DD date, got {value!r}") from exc
    board_identity(str(entry["url"]))  # ValueError here means "not a URL"


class _IndentedDumper(yaml.SafeDumper):
    """Indent list items under their key, the way `sources.yaml` is written."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        super().increase_indent(flow=flow, indentless=False)


def render_list(entries: list[dict[str, Any]], key: str, fields: tuple[str, ...]) -> str:
    """The whole file as text: fixed header, then the entries in field order."""
    body = [{f: entry.get(f) for f in fields} for entry in entries]
    dumped = yaml.dump(
        {key: body},
        Dumper=_IndentedDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=100,
    )
    return _HEADERS[key] + dumped


def backup_path_for(path: Path, *, now: str) -> Path:
    return path.with_name(f"{path.name}.{now}.bak")


def _write_atomically(path: Path, text: str) -> None:
    """Temp file in the same directory, then `os.replace()`.

    The same shape as `storage.xlsx_store._save_atomically`: an interrupted
    write must leave the previous file readable rather than a truncated one
    where a good one used to be.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=path.suffix)
    os.close(fd)
    try:
        Path(tmp).write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save_list(
    path: Path,
    entries: list[dict[str, Any]],
    key: str,
    fields: tuple[str, ...],
    *,
    now: str | None = None,
) -> Path | None:
    """Back the file up, then replace it atomically. Returns the backup path.

    None when there was nothing to back up because the file did not exist yet.
    """
    stamp = now or _utc_stamp()
    backup: Path | None = None
    if path.is_file():
        backup = backup_path_for(path, now=stamp)
        shutil.copy2(path, backup)
    _write_atomically(path, render_list(entries, key, fields))
    return backup


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def append_entry(
    path: Path,
    entry: dict[str, Any],
    key: str,
    fields: tuple[str, ...],
    required: tuple[str, ...],
) -> Path | None:
    """Append one entry. Refuses a duplicate board or organisation.

    Append-only and never in-place: an existing entry is a record of a decision
    someone made, and this tool does not get to edit it.
    """
    _validate(entry, fields, required)
    entries = load_list(path, key, fields)
    clash = find_board(entries, str(entry["url"]))
    if clash is not None:
        raise DuplicateBoardError(
            f"{path.name} already has this board under "
            f"{clash['organisation']!r} ({clash['url']}) — refusing to modify it"
        )
    clash = find_organisation(entries, str(entry["organisation"]))
    if clash is not None:
        raise DuplicateBoardError(
            f"{path.name} already has an entry for {clash['organisation']!r} "
            f"({clash['url']}) — refusing to modify it"
        )
    return save_list(path, [*entries, {f: entry.get(f) for f in fields}], key, fields)


# The fields `record_check` may fill. `organisation` and `url` identify the
# entry and are never written; `source_of_record` is extended, not filled.
FILLABLE_CANDIDATE_FIELDS: tuple[str, ...] = ("category", "blocker", "last_checked", "ats")


class FieldAlreadySetError(CuratedError):
    """A fill-only write met a field that already holds a value."""


class UnknownEntryError(CuratedError):
    """No entry on the list matches the organisation or board asked for."""


def _is_empty(value: Any) -> bool:
    return value is None or not str(value).strip()


def record_check(
    path: Path,
    organisation: str,
    fills: dict[str, str | None],
    *,
    source_of_record: str | None = None,
) -> tuple[dict[str, Any], Path | None]:
    """Fill empty fields on one existing candidate. Returns the entry and the backup.

    The one sanctioned exception to "never edit an existing entry" (owner's
    approval, SP2). SP1's migration could carry no blocker or date for the
    candidates it moved, and `candidate add` refuses a candidate already listed,
    so without this a migrated row could never record why it is not a source.

    It is **fill-only**, and all-or-nothing: if any field in *fills* already has
    a value, the whole call is refused and the file is not touched — a recorded
    blocker is a decision someone made, and a later check that disagrees with it
    is a conversation for the owner, not an overwrite. `source_of_record` is
    extended with `"; "`, never replaced, so the migration's own provenance
    survives. *organisation* is matched by name, or by board when it is a URL.
    Fields passed as None are simply not given.
    """
    given = {name: value for name, value in fills.items() if value is not None}
    unknown = sorted(set(given) - set(FILLABLE_CANDIDATE_FIELDS))
    if unknown:
        raise CuratedError(f"record-check cannot write field(s): {', '.join(unknown)}")
    blank = sorted(name for name, value in given.items() if not value.strip())
    if blank or (source_of_record is not None and not source_of_record.strip()):
        raise CuratedError(
            f"an empty value records nothing: {', '.join(blank) or 'source_of_record'}"
        )
    if not given and source_of_record is None:
        raise CuratedError("nothing to record: pass at least one field")

    entries = load_list(path, CANDIDATES_KEY, CANDIDATE_FIELDS)
    entry = find_organisation(entries, organisation) or find_board(entries, organisation)
    if entry is None:
        raise UnknownEntryError(f"{path.name} has no candidate matching {organisation!r}")

    already = {name: entry[name] for name in given if not _is_empty(entry.get(name))}
    if already:
        held = ", ".join(f"{name}={value!r}" for name, value in sorted(already.items()))
        raise FieldAlreadySetError(
            f"{entry['organisation']} already has {held} — record-check only fills empty "
            "fields, so nothing was changed"
        )

    updated = dict(entry)
    updated.update({name: value.strip() for name, value in given.items()})
    if source_of_record is not None:
        existing = entry.get("source_of_record")
        new = source_of_record.strip()
        updated["source_of_record"] = new if _is_empty(existing) else f"{existing}; {new}"
    _validate(updated, CANDIDATE_FIELDS, _REQUIRED_CANDIDATE)

    rewritten = [updated if e is entry else e for e in entries]
    backup = save_list(path, rewritten, CANDIDATES_KEY, CANDIDATE_FIELDS)
    return updated, backup


class BoardConflictError(CuratedError):
    """The board is somewhere that makes this command the wrong one for it."""


def _find_candidate(entries: list[dict[str, Any]], path: Path, organisation: str) -> dict[str, Any]:
    entry = find_organisation(entries, organisation) or find_board(entries, organisation)
    if entry is None:
        raise UnknownEntryError(f"{path.name} has no candidate matching {organisation!r}")
    return entry


def _history_value(value: Any) -> str:
    return "null" if _is_empty(value) else str(value)


def recheck(
    path: Path,
    organisation: str,
    *,
    blocker: str,
    last_checked: str,
    source_of_record: str,
    ats: str | None = None,
    excluded: list[dict[str, Any]],
    active: list[dict[str, Any]],
) -> tuple[dict[str, Any], Path | None]:
    """Replace a candidate's finding after a later check. Returns the entry and the backup.

    The second owner-approved exception to "never edit an existing entry"
    (SP2b). `record_check` only fills, so once a candidate carries a blocker a
    re-check had nowhere to go and the stale finding stood. This replaces
    blocker and last_checked (and ats, when given), but only after writing the
    old values into `source_of_record`, so a finding that changes is never
    simply lost: the file carries its own history, as do the `.bak` and the
    curated commit. organisation, url and category are never written.

    Refused with the file untouched when the date moves backwards, when
    nothing would change, and when the board is tombstoned (a conflict for the
    owner) or in *active*, the parsed `sources.yaml` (that is `activate`).
    """
    given = {"blocker": blocker, "last_checked": last_checked, "source_of_record": source_of_record}
    if ats is not None:
        given["ats"] = ats
    blank = sorted(name for name, value in given.items() if not value.strip())
    if blank:
        raise CuratedError(f"an empty value records nothing: {', '.join(blank)}")
    try:
        new_date = date.fromisoformat(last_checked.strip())
    except ValueError as exc:
        raise CuratedError(f"last_checked must be a YYYY-MM-DD date, got {last_checked!r}") from exc

    entries = load_list(path, CANDIDATES_KEY, CANDIDATE_FIELDS)
    entry = _find_candidate(entries, path, organisation)
    url = str(entry["url"])

    tombstoned = find_board(excluded, url)
    if tombstoned is not None:
        raise BoardConflictError(
            f"{entry['organisation']}'s board is tombstoned as {tombstoned['organisation']!r}. "
            "A board on both lists is a conflict for the owner to resolve, not a re-check — "
            "nothing was changed"
        )
    source = find_board(active, url)
    if source is not None:
        raise BoardConflictError(
            f"{entry['organisation']}'s board is an active source in sources.yaml "
            f"({source.get('name')}). A scraped board is not re-checked; use "
            f"`candidate activate` — nothing was changed"
        )

    recorded = entry.get("last_checked")
    if not _is_empty(recorded) and new_date < date.fromisoformat(str(recorded)):
        raise CuratedError(
            f"{entry['organisation']} was last checked on {recorded}, and a check does not move "
            f"backwards to {new_date.isoformat()} — nothing was changed"
        )

    new = {"blocker": blocker.strip(), "last_checked": new_date.isoformat()}
    ats_changes = ats is not None and ats.strip() != str(entry.get("ats") or "").strip()
    if ats_changes:
        new["ats"] = str(ats).strip()
    if all(str(entry.get(name) or "").strip() == value for name, value in new.items()):
        raise CuratedError(
            f"{entry['organisation']} already records this finding "
            f"(blocker={entry.get('blocker')!r}, last_checked={recorded}) — nothing to change"
        )

    was = [
        f"blocker={_history_value(entry.get('blocker'))}",
        f"last_checked={_history_value(recorded)}",
    ]
    if ats_changes:
        was.append(f"ats={_history_value(entry.get('ats'))}")
    history = f"rechecked {new['last_checked']}: was {', '.join(was)}; {source_of_record.strip()}"
    existing = entry.get("source_of_record")

    updated = dict(entry)
    updated.update(new)
    updated["source_of_record"] = history if _is_empty(existing) else f"{existing}; {history}"
    _validate(updated, CANDIDATE_FIELDS, _REQUIRED_CANDIDATE)

    rewritten = [updated if e is entry else e for e in entries]
    backup = save_list(path, rewritten, CANDIDATES_KEY, CANDIDATE_FIELDS)
    return updated, backup


def activate(
    path: Path,
    organisation: str,
    sources_path: Path,
    *,
    before_write: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], Path | None]:
    """Remove a candidate whose board is now scraped. Returns the removed entry and the backup.

    Part of SP2b's exception. Removal needs proof, not a name: the candidate's
    board identity must match an entry in *sources_path*. A missing
    `sources.yaml` proves nothing either way, so it is a refusal rather than
    "not active" — the one place in this module where a missing file is not
    read as empty. *before_write* is called with the entry and the matching
    source once every check has passed and before the file is touched, so the
    caller can put the whole entry on the terminal first.
    """
    if not sources_path.is_file():
        raise CuratedError(
            f"{sources_path} does not exist, so there is no proof that any board is scraped — "
            "refusing to remove a candidate"
        )
    active = load_sources(sources_path)

    entries = load_list(path, CANDIDATES_KEY, CANDIDATE_FIELDS)
    entry = _find_candidate(entries, path, organisation)
    source = find_board(active, str(entry["url"]))
    if source is None:
        raise BoardConflictError(
            f"{entry['organisation']}'s board {board_identity(str(entry['url']))!r} is not in "
            f"{sources_path.name}, so it is not active — nothing was removed"
        )
    if before_write is not None:
        before_write(entry, source)
    remaining = [e for e in entries if e is not entry]
    backup = save_list(path, remaining, CANDIDATES_KEY, CANDIDATE_FIELDS)
    return entry, backup


# --- the private repository inside data/curated/ ---------------------------


def commit_curated_change(curated_dir: Path, paths: list[Path], message: str) -> str | None:
    """Commit *paths* to the private git repository inside `data/curated/`.

    The owner's decision (SP0 option 2): `git init` in that directory gives
    every curated file a real undo, entirely locally, and the outer repository
    cannot see it because `data/curated/*` is ignored deny-by-default.

    Best-effort by design — the YAML write has already succeeded by the time
    this runs, so a git failure is reported and never raised. Returns a
    human-readable note, or None when there is no repository to commit to.
    Only the named files are ever staged; the `.bak` files stay untracked.
    """
    if not (curated_dir / ".git").exists():
        return None
    names = [str(p.relative_to(curated_dir)) for p in paths]
    try:
        subprocess.run(
            ["git", "-C", str(curated_dir), "add", "--", *names],
            check=True,
            capture_output=True,
            text=True,
        )
        done = subprocess.run(
            ["git", "-C", str(curated_dir), "commit", "-m", message, "--", *names],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return "git is not on PATH, so the curated repository was not committed to"
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip().splitlines()
        log.warning("committing to %s failed: %s", curated_dir / ".git", detail)
        return "the file was written, but committing it failed: " + (
            detail[-1] if detail else f"git exited {exc.returncode}"
        )
    first = done.stdout.strip().splitlines()
    return f"committed to the curated repository: {first[0] if first else 'ok'}"
