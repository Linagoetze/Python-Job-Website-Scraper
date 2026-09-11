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
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

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


def load_excluded(curated_dir: Path) -> list[dict[str, Any]]:
    return load_list(excluded_path(curated_dir), EXCLUDED_KEY, EXCLUDED_FIELDS)


def load_candidates(curated_dir: Path) -> list[dict[str, Any]]:
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
