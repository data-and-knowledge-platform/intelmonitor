"""Every model call goes through `Inference`, which writes an InferenceRecord per call and serves
repeat calls (same stage and identical input) from those records, so an interrupted run resumes
without redoing finished work. Each call and cache hit registers its prompt version
(pipeline/prompts.py) and its run's use of it."""

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
from intelmonitor.pipeline.claude import ClaudeBilled, ClaudeRefusal, ask_claude
from intelmonitor.pipeline.params import (
    CLAUDE_API_CONCURRENCY,
    CLAUDE_CONCURRENCY,
    HAIKU_MODEL,
    REFUSAL_FALLBACK_MODEL,
)
from intelmonitor.pipeline.prompts import note_run, register_version, schema_text

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


def cache_key(
    model: str, system: str, prompt: str, schema: dict[str, object], effort: str | None
) -> str:
    """The input_hash a Claude call's record is found by. Only a set effort joins the key, so
    calls without one keep their recorded replies. `im trial` finds a version's labels by it."""
    payload: dict[str, object] = {
        "model": model,
        "system": system,
        "prompt": prompt,
        "schema": schema,
    }
    if effort is not None:
        payload["effort"] = effort
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class Inference:
    """Model calls for one run. Concurrency is capped across all callers on the loop.
    With `recorded_only`, a call with no record raises UnrecordedCall instead of reaching a model,
    as in `im check past-run`. A run_id of None (`im trial`) records calls with no run. With
    `rebuilds`, every stage it calls rewrites all of the run's labels of that stage (`im run`), so
    a stage's first use replaces the run's earlier `run_prompts` rows of the stage."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        run_id: str | None,
        *,
        recorded_only: bool = False,
        rebuilds: bool = False,
    ) -> None:
        self.conn = conn
        self.run_id = run_id
        self.recorded_only = recorded_only
        self.rebuilds = rebuilds
        self.calls = 0
        self.cache_hits = 0
        self._versions: dict[tuple[str, str, str, str, str | None], str] = {}
        self._noted: set[tuple[str, str]] = set()  # (stage, version) noted for the run

    async def claude[T: BaseModel](
        self,
        stage: str,
        input_ref: str,
        prompt: str,
        schema: type[T],
        system: str,
        model: str | None = None,
        effort: EffortLevel | None = None,
        template: str | None = None,
    ) -> T:
        """One structured Claude turn on `model` (default settings.anthropic_model), at `effort`
        (default the model's own), retried on failure; every attempt is recorded. A Haiku refusal
        is asked once more on REFUSAL_FALLBACK_MODEL, recorded as its own call. `template` is what
        `system` was filled from, when it was (the priority call's PRIORITY_PROMPT); the prompt
        version is of the template, or of `system` without one. It is not part of the cache key."""
        model = model or get_settings().anthropic_model
        template = system if template is None else template
        try:
            return await self._claude(
                stage, input_ref, prompt, schema, system, model, effort, template
            )
        except ClaudeRefused:
            if model != HAIKU_MODEL:
                raise
        return await self._claude(
            stage, input_ref, prompt, schema, system, REFUSAL_FALLBACK_MODEL, effort, template
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
        template: str,
    ) -> T:
        key = cache_key(model, system, prompt, schema.model_json_schema(), effort)
        version = self._version(
            stage, template, schema_text(schema.model_json_schema()), model, effort
        )
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
                    # A reply cut off at max_tokens, or refused, was billed, so its record keeps
                    # the cost; a refusal's record keeps its category too.
                    billed = error if isinstance(error, ClaudeBilled) else None
                    self._record(
                        stage,
                        "claude",
                        model,
                        input_ref,
                        key,
                        started,
                        prompt_version=version,
                        input_tokens=billed.input_tokens if billed else None,
                        output_tokens=billed.output_tokens if billed else None,
                        cost_usd=billed.cost_usd if billed else None,
                        refusal_category=(
                            error.category if isinstance(error, ClaudeRefusal) else None
                        ),
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
                    prompt_version=version,
                    output=reply.output.model_dump_json(),
                    model_version=reply.model_version,
                    input_tokens=reply.input_tokens,
                    output_tokens=reply.output_tokens,
                    cost_usd=reply.cost_usd,
                )
                return reply.output
        raise AssertionError("unreachable")

    @property
    def versions(self) -> set[str]:
        """The prompt versions this Inference's calls and cache hits used."""
        return set(self._versions.values())

    def _version(
        self, stage: str, template: str, schema: str, model: str, effort: str | None
    ) -> str:
        """The call's prompt version, registered in the store on its first use, and the run's use
        of it noted once; with `rebuilds`, the stage's first use drops the run's earlier rows."""
        parts = (stage, template, schema, model, effort)
        if (vid := self._versions.get(parts)) is None:
            vid = self._versions[parts] = register_version(self.conn, *parts)
        if self.run_id is not None and (stage, vid) not in self._noted:
            replace = self.rebuilds and all(s != stage for s, _ in self._noted)
            note_run(self.conn, self.run_id, stage, vid, replace=replace)
            self._noted.add((stage, vid))
        return vid

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
        prompt_version: str | None = None,
        output: str | None = None,
        model_version: str | None = None,
        confidence: float | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
        refusal_category: str | None = None,
        error: Exception | None = None,
        status: str | None = None,
    ) -> None:
        self.calls += 1
        self.conn.execute(
            "INSERT INTO inference_records (run_id, stage, engine, model, model_version, "
            "input_ref, input_hash, prompt_version, output, confidence, input_tokens, "
            "output_tokens, cost_usd, duration_ms, status, error, refusal_category, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.run_id,
                stage,
                engine,
                model,
                model_version,
                input_ref,
                key,
                prompt_version,
                output,
                confidence,
                input_tokens,
                output_tokens,
                cost_usd,
                int((time.monotonic() - started) * 1000),
                status or ("error" if error else "ok"),
                f"{type(error).__name__}: {error}"[:2000] if error else None,
                refusal_category,
                datetime.now(UTC).isoformat(),
            ),
        )
        self.conn.commit()
