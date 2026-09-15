"""SP2: recovering `skipped_sources.csv` from the session transcript archive.

The transcripts here are synthetic and the organisations invented. The real
archive holds absolute paths, blocklist rows and job titles, so nothing from it
is ever copied into a test.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import recover_skipped_sources as recover

HEADER = recover.HEADER
ROW_A = "Contoso,https://jobs.contoso.example/,For-profit,persistent 403"
ROW_B = "Fabrikam,https://careers.fabrikam.example/,Non-profit,requires login"


def tool_result(text: str, timestamp: str, session: str) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": timestamp,
        "sessionId": session,
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t", "content": text}],
        },
        "toolUseResult": {"stdout": text},
    }


def write_session(path: Path, *records: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    return tmp_path / "projects"


def run(archive: Path, out: Path) -> tuple[int, dict[str, dict[str, Any]]]:
    code = recover.main(["--archive", str(archive), "--out", str(out)])
    if code != 0:
        return code, {}
    rows = yaml.safe_load(out.read_text(encoding="utf-8"))["recovered_skipped_sources"]
    return code, {r["organisation"]: r for r in rows}


def test_a_block_is_read_from_a_tool_result(archive: Path, tmp_path: Path) -> None:
    write_session(
        archive / "-proj-job-scraper-project" / "s1.jsonl",
        tool_result(f"{HEADER}\n{ROW_A}\n{ROW_B}\n", "2026-07-30T08:00:00Z", "s1"),
    )
    code, rows = run(archive, tmp_path / "out.yaml")
    assert code == 0
    assert rows["Contoso"]["reason"] == "persistent 403"
    assert rows["Fabrikam"]["session_id"] == "s1"
    assert rows["Fabrikam"]["session_date"] == "2026-07-30"


def test_line_numbered_file_reads_are_unwrapped(archive: Path, tmp_path: Path) -> None:
    text = f"     1\t{HEADER}\n     2\t{ROW_A}\n     3\t\n"
    write_session(
        archive / "-proj-job-scraper-project" / "s1.jsonl",
        tool_result(text, "2026-07-30T08:00:00Z", "s1"),
    )
    assert set(run(archive, tmp_path / "out.yaml")[1]) == {"Contoso"}


def test_a_doubly_escaped_block_is_unescaped(archive: Path, tmp_path: Path) -> None:
    text = json.dumps(f"{HEADER}\n{ROW_A}\n")[1:-1]  # literal backslash-n, one line
    write_session(
        archive / "-proj-job-scraper-project" / "s1.jsonl",
        tool_result(text, "2026-07-30T08:00:00Z", "s1"),
    )
    assert set(run(archive, tmp_path / "out.yaml")[1]) == {"Contoso"}


def test_a_quoted_header_is_not_a_block(archive: Path, tmp_path: Path) -> None:
    """Plan files quote the header in prose, and prompts repeat it."""
    prose = f"rows appear under the header `{HEADER}`.\n{HEADER}\nHOW. Write it.\n"
    prompt = {"type": "user", "timestamp": "2026-09-15T08:00:00Z", "message": {"content": prose}}
    write_session(
        archive / "-proj-job-scraper-project" / "s1.jsonl",
        prompt,
        tool_result(prose, "2026-09-15T08:00:00Z", "s1"),
    )
    assert run(archive, tmp_path / "out.yaml")[0] == 1


def test_the_worktree_and_subagent_transcripts_are_swept(archive: Path, tmp_path: Path) -> None:
    write_session(
        archive / "-proj-job-scraper-project" / "s1" / "subagents" / "agent-x.jsonl",
        tool_result(f"{HEADER}\n{ROW_A}\n", "2026-07-30T08:00:00Z", "s1"),
    )
    write_session(
        archive / "-proj-job-scraper-project--claude-worktrees-x" / "s2.jsonl",
        tool_result(f"{HEADER}\n{ROW_B}\n", "2026-08-02T08:00:00Z", "s2"),
    )
    write_session(
        archive / "-some-other-project" / "s3.jsonl",
        tool_result(f"{HEADER}\nNorthwind,https://nw.example/,x,y\n", "2026-08-02T08:00:00Z", "s3"),
    )
    code, rows = run(archive, tmp_path / "out.yaml")
    assert code == 0
    assert set(rows) == {"Contoso", "Fabrikam"}


def test_a_later_session_supersedes_and_the_conflict_is_reported(
    archive: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    later = "Contoso,https://jobs.contoso.example/,For-profit,now requires login"
    write_session(
        archive / "-proj-job-scraper-project" / "new.jsonl",
        tool_result(f"{HEADER}\n{later}\n", "2026-08-02T08:00:00Z", "new"),
    )
    write_session(
        archive / "-proj-job-scraper-project" / "old.jsonl",
        tool_result(f"{HEADER}\n{ROW_A}\n", "2026-06-12T08:00:00Z", "old"),
    )
    code, rows = run(archive, tmp_path / "out.yaml")
    assert code == 0
    assert rows["Contoso"]["reason"] == "now requires login"
    assert rows["Contoso"]["session_id"] == "new"
    assert rows["Contoso"]["seen_in_sessions"] == ["new", "old"]
    assert rows["Contoso"]["conflicting"] is True
    assert "1 organisation(s) with conflicting content" in capsys.readouterr().out


def test_a_row_dropped_from_a_later_block_is_reported(
    archive: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_session(
        archive / "-proj-job-scraper-project" / "s1.jsonl",
        tool_result(f"{HEADER}\n{ROW_A}\n{ROW_B}\n", "2026-07-30T08:00:00Z", "s1"),
        tool_result(f"{HEADER}\n{ROW_B}\n", "2026-07-30T09:00:00Z", "s1"),
    )
    code, rows = run(archive, tmp_path / "out.yaml")
    assert code == 0 and set(rows) == {"Contoso", "Fabrikam"}, "the union keeps both"
    assert "absent from 1 later block(s)" in capsys.readouterr().out


def test_the_output_may_not_go_into_data_curated(archive: Path, tmp_path: Path) -> None:
    out = tmp_path / "data" / "curated" / "recovered.yaml"
    assert recover.main(["--archive", str(archive), "--out", str(out)]) == 1
    assert not out.exists()


def test_help_writes_nothing(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exit_info:
        recover.main(["--help"])
    assert exit_info.value.code == 0
    assert list(tmp_path.iterdir()) == []


def test_the_script_runs_as_a_real_process_from_any_directory(
    archive: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """A full sweep, not just `--help`: SP1's migration script failed only when run by path.

    Run from an unrelated directory, so nothing resolves through the working
    directory by accident.
    """
    import subprocess

    write_session(
        archive / "-proj-job-scraper-project" / "s1.jsonl",
        tool_result(f"{HEADER}\n{ROW_A}\n", "2026-07-30T08:00:00Z", "s1"),
    )
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    out = tmp_path_factory.mktemp("out") / "recovered.yaml"
    script = Path(__file__).resolve().parent.parent / "scripts" / "recover_skipped_sources.py"
    done = subprocess.run(
        [sys.executable, str(script), "--archive", str(archive), "--out", str(out)],
        cwd=elsewhere,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert "1 distinct organisation(s)" in done.stdout
    rows = yaml.safe_load(out.read_text(encoding="utf-8"))["recovered_skipped_sources"]
    assert [r["organisation"] for r in rows] == ["Contoso"]
    assert list(elsewhere.iterdir()) == [], "nothing written to the working directory"
