"""A real Claude agent for AgentDojo runs, replacing AgentDojo's `AnthropicLLM`.

AgentDojo's client (pinned commit 089ed468) doesn't work on current Claude models:
- it sends `temperature=0.0`, which current models reject with a 400;
- `max_tokens=1024` is too small now that adaptive thinking is on by default;
- it asks for thinking with `budget_tokens`, which current models reject;
- it rebuilds each assistant turn from its own message format, which reorders blocks
  (tool calls last), and it can't convert `redacted_thinking` blocks back at all.
  Current models check that replayed thinking blocks come back unchanged.

`ClaudeLLM` keeps every assistant turn exactly as the API returned it and replays that
content verbatim, so edits made later (e.g. `GatedToolsExecutor` parsing string lists
in tool arguments) never reach the API. It also records token usage per model call.

Needs the `anthropic` SDK (installed with AgentDojo). The API key comes from the
`ANTHROPIC_API_KEY` environment variable; this module never reads or stores it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.agent_pipeline.llms.anthropic_llm import (
    _anthropic_to_assistant_message,
    _function_to_anthropic,
    _merge_tool_result_messages,
    _message_to_anthropic,
)
from agentdojo.functions_runtime import EmptyEnv, Env, FunctionsRuntime
from agentdojo.types import ChatMessage
from anthropic import APIConnectionError, APIStatusError, RateLimitError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_random_exponential

# $ per 1M tokens (input, output), list prices for prompts <= 100K tokens, from the
# claude-api skill's cached pricing (2026-10-06). Check the live pricing page before
# budgeting. Cache reads cost 0.1x input, 5-minute cache writes 1.25x input.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-fable-5-1": (10.0, 50.0),
}

VALID_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def call_cost(model: str, usage: dict) -> float | None:
    if model not in PRICES:
        return None
    p_in, p_out = PRICES[model]
    tokens_in = (
        usage.get("input_tokens", 0)
        + 1.25 * usage.get("cache_creation_input_tokens", 0)
        + 0.1 * usage.get("cache_read_input_tokens", 0)
    )
    return (tokens_in * p_in + usage.get("output_tokens", 0) * p_out) / 1e6


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, (RateLimitError, APIConnectionError)):
        return True
    return isinstance(exc, APIStatusError) and exc.status_code >= 500


def _strip_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_none(v) for v in value]
    return value


class ClaudeLLM(BasePipelineElement):
    """AgentDojo pipeline element calling the Claude Messages API.

    Args:
        client: a synchronous `anthropic.Anthropic` client (safe to share across threads).
        model: model id, e.g. "claude-haiku-5-5".
        max_tokens: cap per model call, thinking included.
        effort: optional `output_config.effort` ("low" ... "max"); None = model default.
        cache: put a top-level `cache_control` on each request so the growing
            conversation prefix is read from the prompt cache.
    """

    def __init__(self, client: Any, model: str, max_tokens: int = 16000, effort: str | None = None,
                 cache: bool = True) -> None:
        self.client = client
        self.model = model
        self.name = model
        self.max_tokens = max_tokens
        self.effort = effort
        self.cache = cache
        # Tool-call ids of each assistant turn -> that turn's content exactly as returned.
        self._raw: dict[tuple[str, ...], list[dict]] = {}
        # One entry per model call: usage, stop reason.
        self.calls: list[dict] = []
        self.last_messages: list[ChatMessage] = []

    def _to_anthropic(self, messages: Sequence[ChatMessage]) -> tuple[str | None, list[dict]]:
        system, out = None, []
        for i, m in enumerate(messages):
            if m["role"] == "system" and i == 0:
                system = m["content"][0]["content"]
                continue
            if m["role"] == "assistant":
                key = tuple(tc.id for tc in m["tool_calls"] or [])
                if key in self._raw:
                    out.append({"role": "assistant", "content": self._raw[key]})
                    continue
            out.append(_message_to_anthropic(m))
        return system, _merge_tool_result_messages(out)

    @retry(wait=wait_random_exponential(multiplier=2, max=60), stop=stop_after_attempt(6),
           retry=retry_if_exception(_retryable), reraise=True)
    def _request(self, system: str | None, messages: list[dict], tools: list[dict]) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": messages,
            "thinking": {"type": "adaptive"},
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        if self.cache:
            kwargs["cache_control"] = {"type": "ephemeral"}
        with self.client.messages.stream(**kwargs) as stream:
            return stream.get_final_message()

    def query(
        self,
        query: str,
        runtime: FunctionsRuntime,
        env: Env = EmptyEnv(),
        messages: Sequence[ChatMessage] = [],
        extra_args: dict = {},
    ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        system, anthropic_messages = self._to_anthropic(messages)
        tools = [_function_to_anthropic(t) for t in runtime.functions.values()]
        completion = self._request(system, anthropic_messages, tools)

        usage = completion.usage
        self.calls.append({
            "stop_reason": completion.stop_reason,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_creation_input_tokens": usage.cache_creation_input_tokens or 0,
            "cache_read_input_tokens": usage.cache_read_input_tokens or 0,
        })

        output = _anthropic_to_assistant_message(completion)
        if output["tool_calls"]:
            valid = [tc for tc in output["tool_calls"] if VALID_TOOL_NAME.match(tc.function)]
            if len(valid) == len(output["tool_calls"]):
                raw = [_strip_none(b.model_dump(mode="json")) for b in completion.content]
                self._raw[tuple(tc.id for tc in valid)] = raw
            # else: the turn is rebuilt without the invalid calls (AgentDojo's behaviour).
            output["tool_calls"] = valid
        self.last_messages = [*messages, output]
        return query, runtime, env, self.last_messages, extra_args

    def usage(self) -> dict:
        keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        total = {k: sum(c[k] for c in self.calls) for k in keys}
        total["model_calls"] = len(self.calls)
        total["cost_usd"] = call_cost(self.model, total)
        total["stop_reasons"] = [c["stop_reason"] for c in self.calls]
        return total
