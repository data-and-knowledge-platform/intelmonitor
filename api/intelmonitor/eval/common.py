"""Shared setup for the evaluations: the eval directory, the models, and one cached,
budget-capped Claude client for every eval-side model call."""

import asyncio
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
from pydantic import BaseModel

from intelmonitor.config import REPO_ROOT
from intelmonitor.pipeline.claude import scrub_parent_env

EVAL_DIR = Path(os.environ.get("IM_EVAL_DIR", REPO_ROOT / "eval-runs" / "phase-a-2026-10-05"))

# The model the phase A V2 variant answered on, whose recorded spend still counts (`v2_spend`).
ANSWER_MODEL = "claude-sonnet-5-5"
JUDGE_MODEL = "claude-opus-5-5"
SPEND_CAP_USD = 60.0
# With IM_EVAL_CACHED_ONLY=1 any eval-side call without a cached reply fails.
CACHED_ONLY = os.environ.get("IM_EVAL_CACHED_ONLY") == "1"


# ---- Claude for eval-side calls: cached by input hash, budget-capped

CACHE_DIR = EVAL_DIR / "cache"
_limit: asyncio.Semaphore | None = None


class BudgetExceeded(RuntimeError):
    pass


class NotCached(RuntimeError):
    pass


def _cache_files() -> list[Path]:
    return list(CACHE_DIR.glob("*/*.json")) if CACHE_DIR.exists() else []


def eval_spend() -> dict[str, float]:
    """Recorded cost of eval-side calls by group (the cache directory's first level)."""
    out: dict[str, float] = {}
    for path in _cache_files():
        out[path.parent.name] = out.get(path.parent.name, 0.0) + float(
            json.loads(path.read_text())["usage"]["cost_usd"] or 0.0
        )
    return out


def v2_spend() -> float:
    """Cost of the Claude calls the phase A V2 variant made in its own store copy, before the
    standing-answer eval was removed; still counted against the cap."""
    path = EVAL_DIR / "db" / "v2.db"
    if not path.exists():
        return 0.0
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT coalesce(sum(cost_usd), 0) FROM inference_records WHERE model = ?",
            (ANSWER_MODEL,),
        ).fetchone()
    return float(row[0])


def total_spend() -> float:
    return sum(eval_spend().values()) + v2_spend()


def check_budget(reserve: float = 1.0) -> None:
    if (spent := total_spend()) + reserve > SPEND_CAP_USD:
        raise BudgetExceeded(f"spent ${spent:.2f}; the next call could pass ${SPEND_CAP_USD}")


def _key(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


async def ask[T: BaseModel](
    group: str,
    model: str,
    system: str,
    prompt: str,
    schema: type[T],
    *,
    tools: list[str] | None = None,
    max_turns: int | None = None,
    reserve: float = 1.0,
) -> T:
    """One structured Claude turn through the Agent SDK. Replies are cached under
    cache/<group>/<input hash>.json with their usage, so a rerun costs nothing."""
    global _limit
    schema_json = schema.model_json_schema()
    key = _key(
        {"model": model, "system": system, "prompt": prompt, "schema": schema_json, "tools": tools}
    )
    path = CACHE_DIR / group / f"{key}.json"
    if path.exists():
        return schema.model_validate(json.loads(path.read_text())["output"])
    if CACHED_ONLY:
        raise NotCached(f"{group}: no cached reply for {key[:12]}")
    if _limit is None:
        _limit = asyncio.Semaphore(4)
    async with _limit:
        check_budget(reserve)
        scrub_parent_env()
        options = ClaudeAgentOptions(
            model=model,
            system_prompt=system,
            tools=tools or [],
            allowed_tools=tools or [],
            setting_sources=[],
            strict_mcp_config=True,
            skills=[],
            max_turns=max_turns,
            output_format={"type": "json_schema", "schema": schema_json},
        )
        error: Exception | None = None
        for attempt in range(3):
            started = time.monotonic()
            try:
                result: ResultMessage | None = None
                async for message in query(prompt=prompt, options=options):
                    if isinstance(message, ResultMessage):
                        result = message
                if result is None or result.is_error or result.structured_output is None:
                    raise RuntimeError(f"no structured output: {result.result if result else ''}")
                output = schema.model_validate(result.structured_output)
            except Exception as e:  # retried: transient CLI and validation failures
                error = e
                await asyncio.sleep(10 * (attempt + 1))
                continue
            usage = result.usage or {}
            record = {
                "group": group,
                "model": model,
                "models_used": list(result.model_usage or {}),
                "output": output.model_dump(mode="json"),
                "usage": {
                    "cost_usd": result.total_cost_usd,
                    "input_tokens": sum(
                        int(v)
                        for k in (
                            "input_tokens",
                            "cache_read_input_tokens",
                            "cache_creation_input_tokens",
                        )
                        if isinstance(v := usage.get(k), int)
                    ),
                    "output_tokens": usage.get("output_tokens"),
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "turns": result.num_turns,
                },
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(record, indent=1))
            return output
        raise RuntimeError(f"{group}: failed after 3 attempts") from error
