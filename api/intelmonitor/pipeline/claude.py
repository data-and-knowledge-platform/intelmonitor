"""Claude with structured JSON output: straight to the Anthropic API on ANTHROPIC_API_KEY, billed
per token, or, with settings.claude_via_api off, through the Claude Agent SDK on the user's Claude
login. Claude Haiku runs with thinking off; the other models think adaptively, set explicitly
(Sonnet 5.5 runs adaptive when no thinking setting is sent, so the setting changes no behaviour
and, being outside the cache key, no recorded reply)."""

import asyncio
import os
from dataclasses import dataclass
from weakref import WeakKeyDictionary

from anthropic import AsyncAnthropic, transform_schema
from anthropic.types.beta import BetaMessageParam, BetaOutputConfigParam, BetaTextBlockParam
from claude_agent_sdk import ClaudeAgentOptions, EffortLevel, ResultMessage, query
from pydantic import BaseModel

from intelmonitor.config import get_settings
from intelmonitor.pipeline.params import HAIKU_MODEL


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
    model = model or get_settings().anthropic_model
    if get_settings().claude_via_api:
        return await _ask_api(prompt, schema, system, model, effort)
    scrub_parent_env()
    options = ClaudeAgentOptions(
        model=model,
        system_prompt=system,
        tools=[],
        # No settings, MCP servers (including account connectors) or skills: their tool
        # definitions would add tens of thousands of input tokens to every call.
        setting_sources=[],
        strict_mcp_config=True,
        skills=[],
        output_format={"type": "json_schema", "schema": schema.model_json_schema()},
        effort=effort,
        thinking={"type": "disabled"} if model == HAIKU_MODEL else {"type": "adaptive"},
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


class ClaudeBilled(RuntimeError):
    """A call the API answered and billed without a usable output. It carries the call's tokens
    and cost, so the error record keeps them."""

    def __init__(
        self, message: str, input_tokens: int, output_tokens: int, cost_usd: float | None
    ) -> None:
        super().__init__(message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cost_usd = cost_usd


class ClaudeRefusal(ClaudeBilled):
    """The API declined the request (stop_reason "refusal"); `category` is its
    stop_details.category (cyber, bio, reasoning_extraction, ...), None when it names none."""

    def __init__(
        self,
        message: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float | None,
        category: str | None,
    ) -> None:
        super().__init__(message, input_tokens, output_tokens, cost_usd)
        self.category = category


class ClaudeCutOff(ClaudeBilled):
    """The API stopped before the output was complete (max_tokens, say)."""


# USD per million tokens: input, output, cache read, cache write (5-minute TTL).
_PRICES = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10, 2.50),
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
}
_MAX_TOKENS = 16000
# Haiku's replies (screen, same_event, theme, custom_match) stay under 1,000 output tokens; a
# runaway reply stops here within seconds instead of running to _MAX_TOKENS.
_HAIKU_MAX_TOKENS = 2000
_clients: WeakKeyDictionary[asyncio.AbstractEventLoop, AsyncAnthropic] = WeakKeyDictionary()


def _client() -> AsyncAnthropic:
    """One client per event loop: its connection pool belongs to the loop it was opened on."""
    loop = asyncio.get_running_loop()
    if (client := _clients.get(loop)) is None:
        # Rate limits are met with the SDK's own backoff before Inference retries the call.
        client = _clients[loop] = AsyncAnthropic(api_key=_api_key(), max_retries=6)
    return client


async def _ask_api[T: BaseModel](
    prompt: str, schema: type[T], system: str | None, model: str, effort: EffortLevel | None
) -> ClaudeReply[T]:
    config: BetaOutputConfigParam = {
        "format": {"type": "json_schema", "schema": transform_schema(schema)}
    }
    if effort:
        config["effort"] = effort
    # Stages reuse one system prompt across documents, so it is cached once it is long enough.
    blocks: list[BetaTextBlockParam] = (
        [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}] if system else []
    )
    messages: list[BetaMessageParam] = [{"role": "user", "content": prompt}]
    if model == HAIKU_MODEL:
        # Haiku has no server-side fallback; Inference reruns a refusal on Sonnet.
        response = await _client().beta.messages.create(
            model=model,
            max_tokens=_HAIKU_MAX_TOKENS,
            system=blocks,
            messages=messages,
            output_config=config,
            thinking={"type": "disabled"},
        )
    else:
        response = await _client().beta.messages.create(
            model=model,
            max_tokens=_MAX_TOKENS,
            system=blocks,
            messages=messages,
            output_config=config,
            thinking={"type": "adaptive"},
            # A cyber decline reruns on Claude's recommended fallback; bio declines are not rerun.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
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
    # A refusal can cut the JSON off mid-string, so the stop reason is read before the output.
    if response.stop_reason == "refusal":
        details = response.stop_details
        category = details.category if details else None
        explanation = details.explanation if details else None
        raise ClaudeRefusal(
            f"refused: {category or 'no category'}: {explanation or 'no explanation'}",
            u.input_tokens + read + written,
            u.output_tokens,
            cost,
            category,
        )
    if response.stop_reason != "end_turn":
        raise ClaudeCutOff(
            f"Claude stopped before finishing its output ({response.stop_reason})",
            u.input_tokens + read + written,
            u.output_tokens,
            cost,
        )
    text = next(b.text for b in response.content if b.type == "text")
    return ClaudeReply(
        output=schema.model_validate_json(text),
        model_version=response.model,
        input_tokens=u.input_tokens + read + written,
        output_tokens=u.output_tokens,
        cost_usd=cost,
    )
