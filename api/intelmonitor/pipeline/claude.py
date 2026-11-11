"""Claude with structured JSON output: straight to the Anthropic API on ANTHROPIC_API_KEY, billed
per token, or, with settings.claude_via_api off, through the Claude Agent SDK on the user's Claude
login."""

import asyncio
import os
from dataclasses import dataclass
from weakref import WeakKeyDictionary

from anthropic import AsyncAnthropic, transform_schema
from anthropic.types.beta import BetaOutputConfigParam
from claude_agent_sdk import ClaudeAgentOptions, EffortLevel, ResultMessage, query
from pydantic import BaseModel

from intelmonitor.config import get_settings


def scrub_parent_env() -> None:
    """Set up the environment the SDK's CLI subprocess inherits: no parent Claude Code session,
    and ANTHROPIC_API_KEY when settings.claude_via_api, so the agent and evals bill the API too.
    Without it, an empty key is dropped so the CLI runs on the user's Claude login."""
    for key in list(os.environ):
        if key == "CLAUDECODE" or key.startswith("CLAUDE_CODE_"):
            del os.environ[key]
    if get_settings().claude_via_api:
        os.environ["ANTHROPIC_API_KEY"] = _api_key()
    elif not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ.pop("ANTHROPIC_API_KEY", None)


def _api_key() -> str:
    if not (key := get_settings().anthropic_api_key):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Set it, or CLAUDE_VIA_API=0 to run on the Claude login."
        )
    return key


@dataclass(frozen=True)
class ClaudeReply[T: BaseModel]:
    output: T
    model_version: str | None  # the model the CLI reports having used
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None


async def ask_claude[T: BaseModel](
    prompt: str,
    schema: type[T],
    system: str | None = None,
    model: str | None = None,
    effort: EffortLevel | None = None,
) -> ClaudeReply[T]:
    """Run one tool-free Claude turn and validate its structured output against `schema`, on
    `model` (default settings.anthropic_model), at `effort` (default the model's own)."""
    if get_settings().claude_via_api:
        return await _ask_api(prompt, schema, system, model, effort)
    scrub_parent_env()
    options = ClaudeAgentOptions(
        model=model or get_settings().anthropic_model,
        system_prompt=system,
        tools=[],
        # No settings, MCP servers (including account connectors) or skills: their tool
        # definitions would add tens of thousands of input tokens to every call.
        setting_sources=[],
        strict_mcp_config=True,
        skills=[],
        output_format={"type": "json_schema", "schema": schema.model_json_schema()},
        effort=effort,
    )
    result: ResultMessage | None = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            result = message
    if result is None or result.is_error or result.structured_output is None:
        detail = result.result if result else "no result message"
        raise RuntimeError(f"Claude returned no structured output: {detail}")
    usage = result.usage or {}
    return ClaudeReply(
        output=schema.model_validate(result.structured_output),
        model_version=_model_version(result, options.model),
        input_tokens=_tokens(
            usage, "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"
        ),
        output_tokens=_tokens(usage, "output_tokens"),
        cost_usd=result.total_cost_usd,
    )


def _model_version(result: ResultMessage, requested: str | None) -> str | None:
    """The CLI also makes small side calls on another model; report the one that answered."""
    used = list(result.model_usage or {})
    return next(
        (m for m in used if requested and m.startswith(requested)), used[-1] if used else None
    )


def _tokens(usage: dict[str, object], *keys: str) -> int | None:
    counts = [v for k in keys if isinstance(v := usage.get(k), int)]
    return sum(counts) if counts else None


class ClaudeRefusal(RuntimeError):
    """The API declined the request (stop_reason "refusal")."""


# USD per million tokens: input, output, cache read, cache write (5-minute TTL).
_PRICES = {"claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.50), "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0)}
_MAX_TOKENS = 16000
_clients: WeakKeyDictionary[asyncio.AbstractEventLoop, AsyncAnthropic] = WeakKeyDictionary()


def _client() -> AsyncAnthropic:
    """One client per event loop: its connection pool belongs to the loop it was opened on."""
    loop = asyncio.get_running_loop()
    if (client := _clients.get(loop)) is None:
        # Rate limits are met with the SDK's own backoff before Inference retries the call.
        client = _clients[loop] = AsyncAnthropic(api_key=_api_key(), max_retries=6)
    return client


async def _ask_api[T: BaseModel](
    prompt: str, schema: type[T], system: str | None, model: str | None, effort: EffortLevel | None
) -> ClaudeReply[T]:
    model = model or get_settings().anthropic_model
    config: BetaOutputConfigParam = {
        "format": {"type": "json_schema", "schema": transform_schema(schema)}
    }
    if effort:
        config["effort"] = effort
    response = await _client().beta.messages.create(
        model=model,
        max_tokens=_MAX_TOKENS,
        # Stages reuse one system prompt across documents, so it is cached once it is long enough.
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if system
        else [],
        messages=[{"role": "user", "content": prompt}],
        output_config=config,
        # A cyber decline reruns on Claude's recommended fallback; bio declines are not rerun.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    # A refusal can cut the JSON off mid-string, so the stop reason is read before the output.
    if response.stop_reason == "refusal":
        raise ClaudeRefusal(f"refused: {response.stop_details}")
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"Claude stopped before finishing its output ({response.stop_reason})")
    text = next(b.text for b in response.content if b.type == "text")
    u = response.usage
    read, written = u.cache_read_input_tokens or 0, u.cache_creation_input_tokens or 0
    prices = _PRICES.get(model)
    cost = (
        (
            u.input_tokens * prices[0]
            + u.output_tokens * prices[1]
            + read * prices[2]
            + written * prices[3]
        )
        / 1e6
        if prices
        else None
    )
    return ClaudeReply(
        output=schema.model_validate_json(text),
        model_version=response.model,
        input_tokens=u.input_tokens + read + written,
        output_tokens=u.output_tokens,
        cost_usd=cost,
    )
