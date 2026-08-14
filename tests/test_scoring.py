"""The LLM scoring stage (WP7), with the Anthropic API fully mocked.

No test in this module may perform network I/O: every test either injects a
fake client or asserts that the stage returns before a client would be built.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest

from job_scraper.scoring import ScoringSummary, description_sha256, score_new_jobs
from job_scraper.storage.db import JobStore

_NOW = "2026-08-01T00:00:00+00:00"


def _payload(score: int = 72, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "score": score,
        "seniority_fit": "good",
        "relevance": "high",
        "reasoning": "Matches the profile's target field and seniority.",
        "flags": [],
    }
    payload.update(overrides)
    return payload


def _response(
    payload: Any,
    *,
    stop_reason: str = "end_turn",
    input_tokens: int = 1_000,
    output_tokens: int = 200,
    cache_write: int = 0,
    cache_read: int = 0,
) -> SimpleNamespace:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_write,
            cache_read_input_tokens=cache_read,
        ),
    )


class FakeClient:
    """Stands in for anthropic.Anthropic: returns canned responses in order,
    or raises them when the canned item is an exception."""

    def __init__(self, responses: list[Any]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses)
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _seed(db: Path, jobs: list[dict[str, Any]], statuses: dict[str, str] | None = None) -> None:
    with JobStore(db) as store:
        run_id = store.begin_run(_NOW)
        store.upsert_jobs(jobs, run_id, now=_NOW)
        for key, status in (statuses or {}).items():
            store.set_status([key], status)
        store.finish_run(run_id)


def _job(key: str, description: str = "We are hiring an analyst.") -> dict[str, str]:
    return {
        "dedupe_key": key,
        "source_name": "acme",
        "title": "Analyst",
        "location": "Berlin",
        "detail_url": key,
        "description_text": description,
        "description_fetched_at": _NOW,
    }


def _stored(db: Path, key: str) -> dict[str, Any]:
    with JobStore(db) as store:
        return store.jobs_by_keys([key])[key]


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    p = tmp_path / "profile.md"
    p.write_text("## Background\nEconomist, 2 years experience.\n", encoding="utf-8")
    return p


def test_scores_are_stored_and_summarised(tmp_path: Path, profile: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    _seed(db, [_job("https://x/jobs/1")])
    client = FakeClient([_response(_payload(score=88, flags=["German required"]))])

    summary = score_new_jobs(db, profile_path=profile, client=client)

    assert summary == ScoringSummary(1, 0, 0, summary.estimated_cost_usd)
    row = _stored(db, "https://x/jobs/1")
    assert row["score"] == 88
    assert row["score_seniority_fit"] == "good"
    assert row["score_relevance"] == "high"
    assert "profile" in row["score_reasoning"]
    assert row["score_flags"] == "German required"
    assert row["scored_at"]
    assert row["scored_description_sha256"] == description_sha256("We are hiring an analyst.")


def test_the_rubric_and_description_reach_the_api(tmp_path: Path, profile: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    _seed(db, [_job("https://x/jobs/1", description="Junior data analyst, Berlin office.")])
    client = FakeClient([_response(_payload())])

    score_new_jobs(db, profile_path=profile, client=client)

    (call,) = client.calls
    assert "Economist, 2 years experience." in call["system"][0]["text"]
    assert "Junior data analyst, Berlin office." in call["messages"][0]["content"]


def test_a_scored_job_is_never_rescored(tmp_path: Path, profile: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    _seed(db, [_job("https://x/jobs/1")])
    score_new_jobs(db, profile_path=profile, client=FakeClient([_response(_payload())]))

    client = FakeClient([])
    summary = score_new_jobs(db, profile_path=profile, client=client)

    assert client.calls == [], "an unchanged description costs no API call"
    assert summary == ScoringSummary(0, 0, 0, 0.0)


def test_a_changed_description_is_rescored(tmp_path: Path, profile: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    _seed(db, [_job("https://x/jobs/1", description="Old text.")])
    score_new_jobs(db, profile_path=profile, client=FakeClient([_response(_payload(score=40))]))

    _seed(db, [_job("https://x/jobs/1", description="New, much more junior text.")])
    client = FakeClient([_response(_payload(score=90))])
    summary = score_new_jobs(db, profile_path=profile, client=client)

    assert len(client.calls) == 1
    assert summary.jobs_scored == 1
    row = _stored(db, "https://x/jobs/1")
    assert row["score"] == 90
    assert row["scored_description_sha256"] == description_sha256("New, much more junior text.")


def test_only_unreviewed_jobs_with_a_description_are_candidates(
    tmp_path: Path, profile: Path
) -> None:
    db = tmp_path / "jobs.sqlite3"
    _seed(
        db,
        [
            _job("https://x/jobs/reviewed"),
            _job("https://x/jobs/bare", description=""),
        ],
        statuses={"https://x/jobs/reviewed": "seen"},
    )
    client = FakeClient([])

    summary = score_new_jobs(db, profile_path=profile, client=client)

    assert client.calls == []
    assert summary == ScoringSummary(0, 0, 1, 0.0)


def test_an_invalid_response_fails_that_job_only(tmp_path: Path, profile: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    _seed(db, [_job("https://x/jobs/1"), _job("https://x/jobs/2")])
    client = FakeClient(
        [_response(_payload(score=150)), _response(_payload(score=65))]  # 150 is out of range
    )

    summary = score_new_jobs(db, profile_path=profile, client=client)

    assert summary.jobs_scored == 1
    assert summary.jobs_failed == 1
    failed, scored = _stored(db, "https://x/jobs/1"), _stored(db, "https://x/jobs/2")
    assert failed["score"] is None
    assert failed["scored_description_sha256"] == "", "the failure stays retryable next run"
    assert scored["score"] == 65


def test_unparseable_json_fails_that_job_only(tmp_path: Path, profile: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    _seed(db, [_job("https://x/jobs/1"), _job("https://x/jobs/2")])
    client = FakeClient([_response("not json at all"), _response(_payload())])

    summary = score_new_jobs(db, profile_path=profile, client=client)

    assert summary.jobs_scored == 1
    assert summary.jobs_failed == 1


def test_a_refusal_fails_that_job_only(tmp_path: Path, profile: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    _seed(db, [_job("https://x/jobs/1")])
    client = FakeClient([_response(_payload(), stop_reason="refusal")])

    summary = score_new_jobs(db, profile_path=profile, client=client)

    assert summary.jobs_failed == 1
    assert _stored(db, "https://x/jobs/1")["score"] is None


def test_an_auth_error_aborts_the_stage_but_keeps_earlier_scores(
    tmp_path: Path, profile: Path
) -> None:
    db = tmp_path / "jobs.sqlite3"
    _seed(db, [_job(f"https://x/jobs/{i}") for i in range(3)])
    auth_error = anthropic.AuthenticationError(
        "bad key",
        response=httpx.Response(401, request=httpx.Request("POST", "https://api.anthropic.com")),
        body=None,
    )
    client = FakeClient([_response(_payload(score=55)), auth_error])

    summary = score_new_jobs(db, profile_path=profile, client=client)

    assert len(client.calls) == 2, "the stage stops instead of burning a request per job"
    assert summary.jobs_scored == 1
    assert summary.jobs_failed == 2
    assert _stored(db, "https://x/jobs/0")["score"] == 55


def test_missing_api_key_skips_the_stage(
    tmp_path: Path, profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "jobs.sqlite3"
    _seed(db, [_job("https://x/jobs/1")])
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    summary = score_new_jobs(db, profile_path=profile)

    assert summary.skipped_reason == "ANTHROPIC_API_KEY is not set"
    assert summary.jobs_scored == 0
    assert _stored(db, "https://x/jobs/1")["score"] is None


def test_missing_profile_skips_the_stage(tmp_path: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    _seed(db, [_job("https://x/jobs/1")])

    summary = score_new_jobs(db, profile_path=tmp_path / "does-not-exist.md")

    assert summary.skipped_reason == "profile.md not found"
    assert summary.jobs_scored == 0


def test_cost_estimate_uses_the_reported_usage(tmp_path: Path, profile: Path) -> None:
    db = tmp_path / "jobs.sqlite3"
    _seed(db, [_job("https://x/jobs/1"), _job("https://x/jobs/2")])
    client = FakeClient(
        [
            _response(_payload(), input_tokens=1_000, output_tokens=100, cache_write=2_000),
            _response(_payload(), input_tokens=1_000, output_tokens=100, cache_read=2_000),
        ]
    )

    summary = score_new_jobs(db, profile_path=profile, client=client)

    # 2,000 input @ $5/M + 200 output @ $25/M + 2,000 cache-write @ $6.25/M
    # + 2,000 cache-read @ $0.50/M.
    expected = (2_000 * 5.00 + 200 * 25.00 + 2_000 * 6.25 + 2_000 * 0.50) / 1_000_000
    assert summary.estimated_cost_usd == pytest.approx(expected)
