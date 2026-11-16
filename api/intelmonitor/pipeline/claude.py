"""Claude with structured JSON output: straight to the Anthropic API on ANTHROPIC_API_KEY, billed
per token, or, with settings.claude_via_api off, through the Claude Agent SDK on the user's Claude
login. Claude Haiku runs with thinking off; the other models think adaptively, set explicitly
(Sonnet 5.5 runs adaptive when no thinking setting is sent, so the setting changes no behaviour
and, being outside the cache key, no recorded reply).

ask_claude makes one tool-free call. ask_claude_web runs one turn with the web search and web
fetch server tools, resending it on every pause until the model ends it; it always bills the API."""

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from weakref import WeakKeyDictionary

from anthropic import AsyncAnthropic, omit, transform_schema
from anthropic.types.beta import (
    BetaMessage,
    BetaMessageParam,
    BetaOutputConfigParam,
    BetaTextBlockParam,
    BetaToolUnionParam,
    BetaUsage,
)
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
# USD per web search, $10 per 1,000 (pricing page, read 17 Nov 2026). A failed search is not
# billed and is not counted in usage. Web fetch, and the code execution that the _20260209 and
# later web tools run for dynamic filtering, cost only their tokens.
_WEB_SEARCH_USD = 0.01


def _cost(model: str, u: BetaUsage) -> float | None:
    """A response's measured cost from its usage: tokens at the model's prices, plus the web
    search fee; None for a model with no price here."""
    if (prices := _PRICES.get(model)) is None:
        return None
    read, written = u.cache_read_input_tokens or 0, u.cache_creation_input_tokens or 0
    searches = u.server_tool_use.web_search_requests if u.server_tool_use else 0
    tokens = (
        u.input_tokens * prices[0]
        + u.output_tokens * prices[1]
        + read * prices[2]
        + written * prices[3]
    )
    return tokens / 1e6 + searches * _WEB_SEARCH_USD


_MAX_TOKENS = 16000
# Haiku's replies (screen, same_event) stay under 1,000 output tokens; a
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
    cost = _cost(model, u)
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


# The web tools at their latest versions (web search and web fetch pages, read 17 Nov 2026). Claude
# may call them from code it runs to filter what it reads (dynamic filtering); a result that code
# consumed stays in the response (response_inclusion "full", the default), so the text of every
# page fetched can be read back from the turn.
WEB_TOOLS: list[BetaToolUnionParam] = [
    {"type": "web_search_20260318", "name": "web_search"},
    {"type": "web_fetch_20260318", "name": "web_fetch"},
]
# Per request, so per resume; streamed, so a long request meets no HTTP timeout. Sonnet 5.5's
# limit: one trial entity wrote 55,096 output tokens in a request, and a request that ends on
# max_tokens leaves its entity incomplete after the searches are paid for.
_WEB_MAX_TOKENS = 128000


@dataclass
class WebUsage:
    """What a web turn used, summed over its requests: the requests sent, how many of them resumed
    a paused turn, searches and page fetches (failed fetches included, failed searches not), tokens
    (input_tokens uncached) and the measured cost, web search fee included."""

    requests: int = 0
    resumes: int = 0
    searches: int = 0
    fetches: int = 0
    input_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, model: str, response: BetaMessage) -> None:
        u = response.usage
        self.requests += 1
        if u.server_tool_use:
            self.searches += u.server_tool_use.web_search_requests
            self.fetches += u.server_tool_use.web_fetch_requests
        self.input_tokens += u.input_tokens
        self.cache_write_tokens += u.cache_creation_input_tokens or 0
        self.cache_read_tokens += u.cache_read_input_tokens or 0
        self.output_tokens += u.output_tokens
        priced = response.model if response.model in _PRICES else model
        self.cost_usd += _cost(priced, u) or 0.0


@dataclass
class WebTurn[T: BaseModel]:
    """How a web turn ended. `stop_reason` is "end_turn" when `output` holds the answer; else
    "refusal" (with `refusal`, its category), "max_tokens", or "stopped" when `keep_going` said
    stop. `messages` is the conversation with every response of the turn appended, in order, so
    a caller can add a user message and ask again; `responses` holds those responses."""

    stop_reason: str
    output: T | None
    messages: list[BetaMessageParam]
    responses: list[BetaMessage] = field(default_factory=lambda: [])
    refusal: str | None = None


async def ask_claude_web[T: BaseModel](
    messages: list[BetaMessageParam],
    schema: type[T],
    system: str,
    model: str,
    effort: EffortLevel,
    usage: WebUsage,
    keep_going: Callable[[WebUsage], bool] = lambda _: True,
) -> WebTurn[T]:
    """Run one turn with WEB_TOOLS on the API, adaptive thinking at `effort`, its answer in
    `schema`'s structured output. The server pauses a long turn (stop_reason "pause_turn", after
    10 rounds of its own loop); each pause is sent back as it is, with its code execution
    container, until the model ends its turn some other way. Nothing caps the searches or the
    resumes; `keep_going` is asked after every pause, before the turn is sent back, and stops the
    turn when it says no.

    `usage`, the caller's, is added to as each response arrives, so the cost of a turn that raises
    part way is kept. The system prompt is cached, and so is the growing conversation (a top-level
    cache_control; the server also caches each tool result inside a request), so a resume reads
    what came before from the cache. An API error raises after the SDK's own retries."""
    config: BetaOutputConfigParam = {
        "format": {"type": "json_schema", "schema": transform_schema(schema)},
        "effort": effort,
    }
    blocks: list[BetaTextBlockParam] = [
        {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
    ]
    messages = list(messages)
    responses: list[BetaMessage] = []
    container: str | None = None
    while True:
        async with _client().beta.messages.stream(
            model=model,
            max_tokens=_WEB_MAX_TOKENS,
            system=blocks,
            messages=messages,
            tools=WEB_TOOLS,
            output_config=config,
            thinking={"type": "adaptive"},
            cache_control={"type": "ephemeral"},
            container=container or omit,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            response = await stream.get_final_message()
        usage.add(model, response)
        responses.append(response)
        messages.append(response.to_param())
        if response.stop_reason != "pause_turn":
            break
        usage.resumes += 1
        container = response.container.id if response.container else None
        if not keep_going(usage):
            return WebTurn("stopped", None, messages, responses)
    if response.stop_reason == "refusal":
        details = response.stop_details
        category = (details.category if details else None) or "no category"
        return WebTurn("refusal", None, messages, responses, refusal=category)
    if response.stop_reason != "end_turn":
        return WebTurn(response.stop_reason or "none", None, messages, responses)
    text = next(b.text for b in reversed(response.content) if b.type == "text")
    return WebTurn("end_turn", schema.model_validate_json(text), messages, responses)
