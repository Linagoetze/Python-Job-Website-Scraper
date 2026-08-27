"""LLM scoring of stored job descriptions against the owner's profile (WP7).

Scores every unreviewed job whose Layer 5 description is stored, by sending the
description to the Anthropic API with the owner's rubric (`config/profile.md`)
as the system prompt. Results land in the `score*` columns of the job store and
drive the xlsx sort order.

A job is scored at most once per description: the SHA-256 of the text that was
judged is recorded alongside the score, and only a changed description earns a
new API call. Failures are per job — a malformed response or a transient API
error leaves that one job unscored (to be retried next run) and never aborts
the run.

The API key comes from the ANTHROPIC_API_KEY environment variable only. It is
never written to disk or to any config file.

Off by default. `rules.json`'s `scoring_enabled` is authoritative; `run.py`'s
`--score` flag forces this stage on for one run regardless. `anthropic` is an
optional dependency (see requirements.txt) and is imported lazily, inside
`score_new_jobs`, so a run with scoring off never pays its import cost.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from job_scraper.config_loader import load_profile
from job_scraper.storage.db import JobStore

logger = logging.getLogger(__name__)

SCORING_MODEL = "claude-opus-5"

# List prices for SCORING_MODEL in USD per million tokens, for the run-summary
# cost estimate. Cache writes cost 1.25x input, cache reads 0.1x.
_PRICE_INPUT = 5.00
_PRICE_OUTPUT = 25.00
_PRICE_CACHE_WRITE = 6.25
_PRICE_CACHE_READ = 0.50

_SENIORITY_FIT_VALUES = ("good", "stretch", "too_senior", "too_junior", "unclear")
_RELEVANCE_VALUES = ("high", "medium", "low")

# Structured-outputs schema: the API guarantees the response parses against
# this, so client-side validation below should only ever fire on a refusal or
# a truncated response. Numeric ranges are not expressible here and are
# checked in _validate_payload instead.
_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "seniority_fit": {"type": "string", "enum": list(_SENIORITY_FIT_VALUES)},
        "relevance": {"type": "string", "enum": list(_RELEVANCE_VALUES)},
        "reasoning": {"type": "string"},
        "flags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["score", "seniority_fit", "relevance", "reasoning", "flags"],
    "additionalProperties": False,
}

# Kept deliberately small: max_tokens caps thinking plus response text on this
# model, and the JSON payload itself is a few hundred tokens at most.
_MAX_TOKENS = 4096


@dataclass
class ScoringSummary:
    jobs_scored: int
    jobs_failed: int
    jobs_without_description: int
    estimated_cost_usd: float
    # Non-empty when the whole stage was skipped (no key, no profile) rather
    # than run. The run summary prints it so a silent misconfiguration cannot
    # masquerade as "nothing needed scoring".
    skipped_reason: str = ""


def description_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _system_prompt(profile: str) -> str:
    return (
        "You score job postings for one specific candidate. The candidate's "
        "background and preferences follow between <profile> tags. Treat the "
        "profile as the rubric: score strictly against it, not against a "
        "generic notion of a good job.\n\n"
        f"<profile>\n{profile}\n</profile>\n\n"
        "For the posting in the user message, return JSON with exactly these "
        "fields:\n"
        "- score: integer 0-100. Overall fit with the profile: 0 means the "
        "candidate should not apply, 100 means an exceptional match worth "
        "prioritising. Postings that violate a hard constraint in the profile "
        "score below 20.\n"
        "- seniority_fit: one of 'good' (level matches the candidate), "
        "'stretch' (slightly above but plausibly attainable), 'too_senior', "
        "'too_junior', or 'unclear' (the posting does not say).\n"
        "- relevance: 'high', 'medium', or 'low' — how close the role's field "
        "and duties are to what the profile says the candidate is looking "
        "for, independent of seniority.\n"
        "- reasoning: two to four plain-English sentences justifying the "
        "score, naming the specific profile points that helped or hurt.\n"
        "- flags: a list of short warnings the candidate should notice before "
        "applying (e.g. a language requirement, an unusually short contract, "
        "a mandatory relocation). Empty list if there are none.\n\n"
        "Base the score only on what the posting and the profile actually "
        "say. If the description is thin, say so in the reasoning and lean on "
        "'unclear'/'medium' rather than guessing."
    )


def _job_message(job: dict[str, Any]) -> str:
    return (
        f"Title: {job.get('title') or ''}\n"
        f"Company: {job.get('company') or ''}\n"
        f"Location: {job.get('location') or ''}\n"
        f"Source: {job.get('source_name') or ''}\n\n"
        f"Description:\n{job.get('description_text') or ''}"
    )


def _validate_payload(payload: Any) -> dict[str, Any]:
    """Return the validated scoring dict or raise ValueError naming the defect."""
    if not isinstance(payload, dict):
        raise ValueError(f"response is not a JSON object: {type(payload).__name__}")
    score = payload.get("score")
    if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
        raise ValueError(f"score is not an integer in 0-100: {score!r}")
    seniority = payload.get("seniority_fit")
    if seniority not in _SENIORITY_FIT_VALUES:
        raise ValueError(f"seniority_fit not in {_SENIORITY_FIT_VALUES}: {seniority!r}")
    relevance = payload.get("relevance")
    if relevance not in _RELEVANCE_VALUES:
        raise ValueError(f"relevance not in {_RELEVANCE_VALUES}: {relevance!r}")
    reasoning = payload.get("reasoning")
    if not isinstance(reasoning, str) or not reasoning.strip():
        raise ValueError("reasoning is missing or empty")
    flags = payload.get("flags")
    if not isinstance(flags, list) or not all(isinstance(f, str) for f in flags):
        raise ValueError(f"flags is not a list of strings: {flags!r}")
    return {
        "score": score,
        "seniority_fit": seniority,
        "relevance": relevance,
        "reasoning": reasoning.strip(),
        "flags": [f.strip() for f in flags if f.strip()],
    }


def _score_one(client: Any, model: str, system_text: str, job: dict[str, Any]) -> dict[str, Any]:
    """One API call for one job. Returns the validated payload plus usage.

    Raises ValueError on an unusable response and lets API errors propagate to
    the caller, which decides whether they are per-job or fatal to the stage.
    """
    response = client.messages.create(
        model=model,
        max_tokens=_MAX_TOKENS,
        # cache_control: the rubric is identical for every job in the run, so
        # all calls after the first read it from cache instead of re-billing it.
        system=[
            {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}
        ],
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": _RESPONSE_SCHEMA},
        },
        messages=[{"role": "user", "content": _job_message(job)}],
    )
    usage = getattr(response, "usage", None)
    result: dict[str, Any] = {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
    }
    # Safety classifiers on this model can decline a request with a normal
    # HTTP 200; content is then empty or partial, never valid JSON.
    if response.stop_reason == "refusal":
        raise ValueError("the model refused to score this posting")
    if response.stop_reason == "max_tokens":
        raise ValueError("response truncated at max_tokens")
    text = next((b.text for b in response.content if b.type == "text"), "")
    result.update(_validate_payload(json.loads(text)))
    return result


def _estimated_cost(totals: dict[str, int]) -> float:
    return (
        totals["input_tokens"] * _PRICE_INPUT
        + totals["output_tokens"] * _PRICE_OUTPUT
        + totals["cache_write_tokens"] * _PRICE_CACHE_WRITE
        + totals["cache_read_tokens"] * _PRICE_CACHE_READ
    ) / 1_000_000


def score_new_jobs(
    db_path: Path,
    *,
    profile_path: Path | None = None,
    client: Any = None,
    model: str = SCORING_MODEL,
) -> ScoringSummary:
    """Score unreviewed jobs whose stored description has not been scored yet.

    Only status 'new' rows are candidates: review decisions have already been
    made on everything else, so scoring them would spend money on jobs the
    owner will never re-open. A candidate is skipped when its stored score was
    computed from the identical description text (never re-score), or when it
    has no description at all (nothing to judge — typically a pre-WP6 row or a
    failed Layer 5 fetch).

    *client* exists for the tests, which inject a fake; the real client is
    only constructed when there is work to do and ANTHROPIC_API_KEY is set.
    """
    with JobStore(db_path) as store:
        new_jobs = store.jobs_with_status(("new",))
        without_description = [j for j in new_jobs if not j.get("description_text")]
        candidates = [
            j
            for j in new_jobs
            if j.get("description_text")
            and description_sha256(j["description_text"]) != j.get("scored_description_sha256")
        ]
        if without_description:
            logger.debug(
                "Scoring: %d unreviewed jobs have no stored description and cannot be scored",
                len(without_description),
            )
        if not candidates:
            return ScoringSummary(0, 0, len(without_description), 0.0)

        # Imported here, not at module level, so job_scraper.run only pays for
        # the SDK's import cost on a run where scoring actually has work to do.
        import anthropic

        try:
            profile = load_profile(profile_path)
        except FileNotFoundError as exc:
            logger.error("Scoring skipped: %s", exc)
            return ScoringSummary(
                0, 0, len(without_description), 0.0, skipped_reason="profile.md not found"
            )

        if client is None:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                logger.error(
                    "Scoring skipped: ANTHROPIC_API_KEY is not set. Export it in the "
                    "shell that runs the scraper, or set scoring_enabled to false in "
                    "rules.json to turn the stage off."
                )
                return ScoringSummary(
                    0,
                    0,
                    len(without_description),
                    0.0,
                    skipped_reason="ANTHROPIC_API_KEY is not set",
                )
            client = anthropic.Anthropic()

        system_text = _system_prompt(profile)
        totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_write_tokens": 0,
            "cache_read_tokens": 0,
        }
        scored = failed = 0
        logger.info("Scoring %d jobs with %s…", len(candidates), model)
        for job in candidates:
            key = job["dedupe_key"]
            try:
                result = _score_one(client, model, system_text, job)
            except (
                anthropic.AuthenticationError,
                anthropic.PermissionDeniedError,
                anthropic.RateLimitError,
            ) as exc:
                # Every remaining job would hit the same wall — stop the stage
                # rather than burning one doomed request per job. Scores
                # already recorded this run are kept.
                remaining = len(candidates) - scored - failed
                failed += remaining
                logger.error(
                    "Scoring aborted (%s); %d jobs left unscored, they will be "
                    "retried next run",
                    exc.__class__.__name__,
                    remaining,
                )
                break
            except (anthropic.APIError, ValueError, json.JSONDecodeError) as exc:
                failed += 1
                logger.warning("Scoring failed for %r (%s): %s", job.get("title"), key, exc)
                continue

            for field in totals:
                totals[field] += result[field]
            store.record_score(
                key,
                score=result["score"],
                seniority_fit=result["seniority_fit"],
                relevance=result["relevance"],
                reasoning=result["reasoning"],
                flags="; ".join(result["flags"]),
                description_sha256=description_sha256(job["description_text"]),
            )
            scored += 1

        cost = _estimated_cost(totals)
        logger.info(
            "Scoring done: %d scored, %d failed, estimated cost $%.4f",
            scored,
            failed,
            cost,
        )
        return ScoringSummary(scored, failed, len(without_description), cost)
