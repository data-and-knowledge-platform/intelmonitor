"""Every model call goes through `Inference`, which writes an InferenceRecord per call and serves
repeat calls (same stage and identical input) from those records, so an interrupted run resumes
without redoing finished work."""

import asyncio
import hashlib
import json
import sqlite3
import time
from datetime import UTC, datetime
from weakref import WeakKeyDictionary

from claude_agent_sdk import EffortLevel
from pydantic import BaseModel

from intelmonitor.config import get_settings
from intelmonitor.pipeline.claude import ClaudeCutOff, ClaudeRefusal, ask_claude
from intelmonitor.pipeline.params import (
    CLAUDE_API_CONCURRENCY,
    CLAUDE_CONCURRENCY,
    HAIKU_MODEL,
    REFUSAL_FALLBACK_MODEL,
)

_CLAUDE_ATTEMPTS = 3
_CLAUDE_BACKOFF_SECONDS = 20.0


class ClaudeRefused(RuntimeError):
    """Claude declined the input (a usage-policy refusal). Retrying the same input will not
    help, so the refusal is recorded like an answer and callers fall back."""


def _is_refusal(error: Exception) -> bool:
    if isinstance(error, ClaudeRefusal):
        return True
    text = str(error)
    return "anthropic.com/legal/aup" in text or "can't help with this" in text


class UnrecordedCall(RuntimeError):
    """A call with no record, made by an Inference that may only replay recorded ones."""


class StepFailed(RuntimeError):
    """Some items of a pipeline step failed; the rest finished and their calls are cached."""

    def __init__(self, step: str, failures: list[str]) -> None:
        super().__init__(f"{step}: {len(failures)} failed, first: {failures[0]}")
        self.failures = failures


_limits: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = WeakKeyDictionary()


def _claude_limit() -> asyncio.Semaphore:
    """The semaphore shared by every Inference on the running event loop, so parallel API
    requests stay within the same cap as one pipeline run."""
    loop = asyncio.get_running_loop()
    if (limit := _limits.get(loop)) is None:
        limit = _limits[loop] = asyncio.Semaphore(
            CLAUDE_API_CONCURRENCY if get_settings().claude_via_api else CLAUDE_CONCURRENCY
        )
    return limit


def _hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class Inference:
    """Model calls for one run. Concurrency is capped across all callers on the loop.
    With `recorded_only`, a call with no record raises UnrecordedCall instead of reaching a model,
    as in `im check past-run`."""

    def __init__(
        self, conn: sqlite3.Connection, run_id: str | None, *, recorded_only: bool = False
    ) -> None:
        self.conn = conn
        self.run_id = run_id
        self.recorded_only = recorded_only
        self.calls = 0
        self.cache_hits = 0

    async def claude[T: BaseModel](
        self,
        stage: str,
        input_ref: str,
        prompt: str,
        schema: type[T],
        system: str,
        model: str | None = None,
        effort: EffortLevel | None = None,
    ) -> T:
        """One structured Claude turn on `model` (default settings.anthropic_model), at `effort`
        (default the model's own), retried on failure; every attempt is recorded. A Haiku refusal
        is asked once more on REFUSAL_FALLBACK_MODEL, recorded as its own call."""
        model = model or get_settings().anthropic_model
        try:
            return await self._claude(stage, input_ref, prompt, schema, system, model, effort)
        except ClaudeRefused:
            if model != HAIKU_MODEL:
                raise
        return await self._claude(
            stage, input_ref, prompt, schema, system, REFUSAL_FALLBACK_MODEL, effort
        )

    async def _claude[T: BaseModel](
        self,
        stage: str,
        input_ref: str,
        prompt: str,
        schema: type[T],
        system: str,
        model: str,
        effort: EffortLevel | None,
    ) -> T:
        payload: dict[str, object] = {
            "model": model,
            "system": system,
            "prompt": prompt,
            "schema": schema.model_json_schema(),
        }
        # Only a set effort joins the key, so calls without one keep their recorded replies.
        if effort is not None:
            payload["effort"] = effort
        key = _hash(payload)
        if (cached := self._cached(stage, key)) is not None:
            return schema.model_validate_json(cached)
        if self._cached(stage, key, status="refused") is not None:
            raise ClaudeRefused(f"{stage} {input_ref}: refused earlier")
        self._require_record(stage, input_ref)
        async with _claude_limit():
            for attempt in range(1, _CLAUDE_ATTEMPTS + 1):
                started = time.monotonic()
                try:
                    reply = await ask_claude(prompt, schema, system, model, effort)
                except Exception as error:
                    refused = _is_refusal(error)
                    status = "refused" if refused else "error"
                    # A reply cut off at max_tokens was billed, so its record keeps the cost.
                    cut = error if isinstance(error, ClaudeCutOff) else None
                    self._record(
                        stage,
                        "claude",
                        model,
                        input_ref,
                        key,
                        started,
                        input_tokens=cut.input_tokens if cut else None,
                        output_tokens=cut.output_tokens if cut else None,
                        cost_usd=cut.cost_usd if cut else None,
                        error=error,
                        status=status,
                    )
                    if refused:
                        raise ClaudeRefused(f"{stage} {input_ref}: {error}") from error
                    if attempt == _CLAUDE_ATTEMPTS:
                        raise
                    await asyncio.sleep(_CLAUDE_BACKOFF_SECONDS * attempt)
                    continue
                self._record(
                    stage,
                    "claude",
                    model,
                    input_ref,
                    key,
                    started,
                    output=reply.output.model_dump_json(),
                    model_version=reply.model_version,
                    input_tokens=reply.input_tokens,
                    output_tokens=reply.output_tokens,
                    cost_usd=reply.cost_usd,
                )
                return reply.output
        raise AssertionError("unreachable")

    def _require_record(self, stage: str, input_ref: str) -> None:
        if self.recorded_only:
            raise UnrecordedCall(f"{stage} {input_ref}: no recorded call")

    def _cached(self, stage: str, key: str, status: str = "ok") -> str | None:
        row = self.conn.execute(
            "SELECT output FROM inference_records "
            "WHERE stage = ? AND input_hash = ? AND status = ? ORDER BY id DESC LIMIT 1",
            (stage, key, status),
        ).fetchone()
        if row is None:
            return None
        self.cache_hits += 1
        return str(row["output"] or "")

    def _record(
        self,
        stage: str,
        engine: str,
        model: str,
        input_ref: str,
        key: str,
        started: float,
        *,
        output: str | None = None,
        model_version: str | None = None,
        confidence: float | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
        error: Exception | None = None,
        status: str | None = None,
    ) -> None:
        self.calls += 1
        self.conn.execute(
            "INSERT INTO inference_records (run_id, stage, engine, model, model_version, "
            "input_ref, input_hash, output, confidence, input_tokens, output_tokens, cost_usd, "
            "duration_ms, status, error, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.run_id,
                stage,
                engine,
                model,
                model_version,
                input_ref,
                key,
                output,
                confidence,
                input_tokens,
                output_tokens,
                cost_usd,
                int((time.monotonic() - started) * 1000),
                status or ("error" if error else "ok"),
                f"{type(error).__name__}: {error}"[:2000] if error else None,
                datetime.now(UTC).isoformat(),
            ),
        )
        self.conn.commit()
