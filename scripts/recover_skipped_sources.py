"""One-off: recover the deleted `data/skipped_sources.csv` from session transcripts.

    python scripts/recover_skipped_sources.py --out <scratchpad>/skipped.yaml

The file was gitignored and deleted in error during CU1, so no git history
holds it. Sessions that read it did, though: the local Claude Code transcript
archive under `~/.claude/projects/` keeps every tool result verbatim, as JSON.
This script sweeps that archive, pulls out every block of rows under the
header `organisation,url,category,reason`, and merges them.

**Every directory for this project, not just the main one.** Worktree sessions
are archived under a directory of their own (`...-project--claude-worktrees-...`),
and a sweep of the main directory alone silently misses them. Subagent
transcripts live one level further down, so the sweep is recursive.

Only tool results are read — not prompts, not the assistant's own commands —
because a plan that *quotes* the header is not the file. A block counts only
when at least one CSV row follows the header, which also rules out the plan
file's prose mentioning it.

Merge rule: the later sighting of an organisation supersedes the earlier one.
A row whose content differs between sightings is reported, because the later
one winning is a rule, not evidence that it is right.

The output goes wherever `--out` says, and is never `data/curated/`: loading
the rows into the candidates list is `job_scraper.tools.sources`'s job.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

HEADER = "organisation,url,category,reason"
COLUMNS = ("organisation", "url", "category", "reason")
DEFAULT_ARCHIVE = Path.home() / ".claude" / "projects"
DEFAULT_PROJECT_GLOB = "*job*scraper*"


@dataclass(frozen=True)
class Sighting:
    """One row, as one tool result showed it."""

    row: tuple[str, str, str, str]
    session_id: str
    date: str  # YYYY-MM-DD, local time, of the record the row appeared in
    timestamp: str  # full ISO timestamp, for ordering only


@dataclass
class Recovered:
    organisation: str
    latest: Sighting
    sightings: list[Sighting] = field(default_factory=list)

    @property
    def sessions(self) -> list[str]:
        return sorted({s.session_id for s in self.sightings})

    @property
    def conflicting(self) -> bool:
        return len({s.row for s in self.sightings}) > 1


# --- finding the transcripts ----------------------------------------------


def transcript_files(archive: Path, project_glob: str) -> list[Path]:
    """Every `.jsonl` under every project directory matching *project_glob*."""
    return sorted(
        path
        for directory in sorted(archive.glob(project_glob))
        if directory.is_dir()
        for path in directory.rglob("*.jsonl")
    )


def _session_id(path: Path, record: dict[str, Any]) -> str:
    # A subagent's transcript records its parent session's id; falling back to
    # the file name covers records that carry none.
    return str(record.get("sessionId") or path.stem)


def _local_date(timestamp: str) -> str:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone().date().isoformat()


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def tool_result_texts(record: dict[str, Any]) -> Iterator[str]:
    """The text of every tool result in one transcript record, and nothing else."""
    message = record.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        for item in message["content"]:
            if isinstance(item, dict) and item.get("type") == "tool_result":
                yield from _strings(item.get("content"))
    if "toolUseResult" in record:
        yield from _strings(record["toolUseResult"])


# --- reading a block -------------------------------------------------------


def _strip_line_number(line: str) -> str:
    """Drop the `   12\\t` (or `12→`) prefix a file-reading tool puts on each line."""
    head, sep, rest = line.partition("\t")
    if sep and head.strip().isdigit():
        return rest
    head, sep, rest = line.partition("→")
    if sep and head.strip().isdigit():
        return rest
    return line


def _unescape_if_needed(text: str) -> str:
    """JSON-unescape a string that still holds literal `\\n` sequences.

    Loading the transcript line already undoes one level of escaping. A tool
    result that itself printed JSON, or a repr, carries a second level, and its
    rows then sit on one physical line joined by a backslash and an `n`.
    """
    if "\n" in text or "\\n" not in text:
        return text
    try:
        return json.loads('"' + text.replace('"', '\\"') + '"')
    except json.JSONDecodeError:
        return text.replace("\\n", "\n").replace("\\t", "\t")


def _parse_row(line: str) -> tuple[str, str, str, str] | None:
    try:
        fields = next(csv.reader([line]))
    except (csv.Error, StopIteration):
        return None
    if len(fields) != len(COLUMNS):
        return None
    row = tuple(f.strip() for f in fields)
    if not row[0] or not row[1].lower().startswith(("http://", "https://")):
        return None
    return row  # type: ignore[return-value]


def extract_blocks(text: str) -> list[list[tuple[str, str, str, str]]]:
    """Every run of rows directly under a header line. Headers with no rows are skipped."""
    lines = [
        _strip_line_number(line).rstrip("\r") for line in _unescape_if_needed(text).split("\n")
    ]
    blocks: list[list[tuple[str, str, str, str]]] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() != HEADER:
            i += 1
            continue
        rows = []
        i += 1
        while i < len(lines):
            row = _parse_row(lines[i])
            if row is None:
                break
            rows.append(row)
            i += 1
        if rows:
            blocks.append(rows)
    return blocks


# --- sweeping and merging --------------------------------------------------


@dataclass(frozen=True)
class Block:
    """One tool result's run of rows under the header."""

    rows: tuple[tuple[str, str, str, str], ...]
    session_id: str
    date: str
    timestamp: str


def sweep(files: list[Path]) -> list[Block]:
    """Every distinct block in the archive, oldest first."""
    blocks: list[Block] = []
    for path in files:
        with path.open(encoding="utf-8") as f:
            for line in f:
                if HEADER not in line:
                    continue  # cheap pre-filter: the header survives JSON escaping intact
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                timestamp = str(record.get("timestamp") or "")
                if not timestamp:
                    continue
                session = _session_id(path, record)
                seen_here: set[tuple[tuple[str, str, str, str], ...]] = set()
                for text in tool_result_texts(record):
                    for rows in extract_blocks(text):
                        # One record often repeats a result (message and
                        # toolUseResult); count that once.
                        if tuple(rows) in seen_here:
                            continue
                        seen_here.add(tuple(rows))
                        blocks.append(
                            Block(tuple(rows), session, _local_date(timestamp), timestamp)
                        )
    blocks.sort(key=lambda b: b.timestamp)
    return blocks


def sightings_of(blocks: list[Block]) -> list[Sighting]:
    return [Sighting(row, b.session_id, b.date, b.timestamp) for b in blocks for row in b.rows]


def later_absences(recovered: Recovered, blocks: list[Block]) -> int:
    """How many blocks after this organisation's last sighting do not list it.

    A block can be a partial view (`head`, a grep), so one absence proves
    nothing. A full-looking later block without the row usually means it was
    taken out of the file on purpose — moved to the tombstone, or added as a
    source — and the cross-check against those lists should explain it.
    """
    key = recovered.organisation.casefold()
    return sum(
        1
        for b in blocks
        if b.timestamp > recovered.latest.timestamp
        and all(row[0].casefold() != key for row in b.rows)
    )


def merge(sightings: list[Sighting]) -> list[Recovered]:
    """One entry per organisation (case-insensitive); the latest sighting wins."""
    by_org: dict[str, Recovered] = {}
    for sighting in sightings:  # oldest first, so the last write is the latest
        key = sighting.row[0].casefold()
        entry = by_org.get(key)
        if entry is None:
            entry = by_org[key] = Recovered(sighting.row[0], sighting)
        entry.sightings.append(sighting)
        entry.latest = sighting
        entry.organisation = sighting.row[0]
    return sorted(by_org.values(), key=lambda r: r.organisation.casefold())


def to_yaml(recovered: list[Recovered]) -> str:
    body = [
        {
            **dict(zip(COLUMNS, r.latest.row, strict=True)),
            "session_id": r.latest.session_id,
            "session_date": r.latest.date,
            "seen_in_sessions": r.sessions,
            "conflicting": r.conflicting,
        }
        for r in recovered
    ]
    return yaml.safe_dump(
        {"recovered_skipped_sources": body}, allow_unicode=True, sort_keys=False, width=100
    )


def _summary(recovered: list[Recovered], blocks: list[Block], files: int) -> str:
    per_session: dict[str, list[Block]] = {}
    for b in blocks:
        per_session.setdefault(b.session_id, []).append(b)
    out = [f"swept {files} transcript file(s); rows found in {len(per_session)} session(s):"]
    for session, found in sorted(per_session.items()):
        sizes = ", ".join(f"{len(b.rows)} rows at {b.timestamp[:16]}" for b in found)
        out.append(f"    {session} ({found[0].date}): {sizes}")
    out.append(f"{len(recovered)} distinct organisation(s):")
    for r in recovered:
        gone = later_absences(r, blocks)
        note = f"; absent from {gone} later block(s)" if gone else ""
        out.append(
            f"    {r.organisation}  <- {', '.join(r.sessions)} "
            f"(last seen {r.latest.timestamp[:16]}{note})"
        )
    conflicts = [r for r in recovered if r.conflicting]
    out.append(f"{len(conflicts)} organisation(s) with conflicting content across sightings:")
    for r in conflicts:
        out.append(f"    {r.organisation}:")
        for row in sorted({s.row for s in r.sightings}):
            where = sorted({f"{s.session_id} {s.date}" for s in r.sightings if s.row == row})
            out.append(f"        {list(row[1:])}  <- {', '.join(where)}")
    return "\n".join(out)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python scripts/recover_skipped_sources.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--out", type=Path, required=True, help="YAML file to write")
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--project-glob", default=DEFAULT_PROJECT_GLOB)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if "curated" in args.out.resolve().parts:
        print("refused: --out must not be under data/curated/", file=sys.stderr)
        return 1
    files = transcript_files(args.archive, args.project_glob)
    if not files:
        print(f"no transcripts under {args.archive} matching {args.project_glob}", file=sys.stderr)
        return 1
    blocks = sweep(files)
    recovered = merge(sightings_of(blocks))
    if not recovered:
        print(f"swept {len(files)} file(s) and found no rows under {HEADER!r}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(to_yaml(recovered), encoding="utf-8")
    print(_summary(recovered, blocks, len(files)))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
