"""
agent_runner.py — single model call for one iteration of the agentic loop.

Supported providers (set via agent config or anet.config.yaml):
  openrouter  → OpenRouter          (OPENROUTER_API_KEY)
  google      → Google AI / Gemini  (GOOGLE_API_KEY)
  openai      → OpenAI              (OPENAI_API_KEY)
  claude      → Anthropic           (ANTHROPIC_API_KEY)

To add a new OpenAI-compatible provider: add one entry to _PROVIDERS.
For Anthropic (non-compatible), the _run_anthropic path handles it.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from openai import AsyncOpenAI, APITimeoutError, InternalServerError, RateLimitError
from openai.types.chat import ChatCompletionMessage
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)

_RETRY_ATTEMPTS = 4
_RETRY_DELAY    = 5    # base seconds between retries (multiplied by attempt number)
_MODEL_TIMEOUT  = 150  # seconds — abort a hung model call instead of waiting the default 600s

# Errors worth retrying with backoff (transient infrastructure issues)
_RETRYABLE = (RateLimitError, InternalServerError, APITimeoutError)

# ── OpenAI-compatible provider registry ──────────────────────────────────────

_PROVIDERS: dict[str, dict] = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "env_key":  "OPENROUTER_API_KEY",
    },
    "google": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "env_key":  "GOOGLE_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "env_key":  "OPENAI_API_KEY",
    },
}

_DEFAULT_PROVIDER = "openrouter"


def _build_openai_client(provider: str) -> AsyncOpenAI:
    cfg     = _PROVIDERS[provider]
    api_key = os.getenv(cfg["env_key"])
    if not api_key:
        print(
            f"[agent_runner] WARNING: env var '{cfg['env_key']}' is not set "
            f"for provider '{provider}'.",
            file=sys.stderr,
        )
    return AsyncOpenAI(api_key=api_key or "missing", base_url=cfg["base_url"], timeout=_MODEL_TIMEOUT)


# ── Anthropic adapter ─────────────────────────────────────────────────────────

def _convert_to_anthropic_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """Split OpenAI-format messages into (system_prompt, anthropic_messages)."""
    system = ""
    out: list[dict] = []

    for msg in messages:
        role    = msg.get("role", "")
        content = msg.get("content") or ""

        if role == "system":
            system = content

        elif role == "user":
            out.append({"role": "user", "content": content})

        elif role == "assistant":
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                blocks: list[dict] = []
                if content:
                    blocks.append({"type": "text", "text": content})
                for tc in tool_calls:
                    blocks.append({
                        "type":  "tool_use",
                        "id":    tc["id"],
                        "name":  tc["function"]["name"],
                        "input": json.loads(tc["function"]["arguments"] or "{}"),
                    })
                out.append({"role": "assistant", "content": blocks})
            else:
                out.append({"role": "assistant", "content": content})

        elif role == "tool":
            # Anthropic expects tool results as a user message
            out.append({
                "role": "user",
                "content": [{
                    "type":        "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content":     content,
                }],
            })

    return system, out


def _convert_tools_to_anthropic(tool_schemas: list[dict]) -> list[dict]:
    """Convert OpenAI function schemas → Anthropic tool format."""
    tools = []
    for schema in tool_schemas:
        fn = schema.get("function", {})
        tools.append({
            "name":         fn.get("name", ""),
            "description":  fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return tools


def _anthropic_response_to_message(response) -> ChatCompletionMessage:
    """Convert an Anthropic Message → OpenAI ChatCompletionMessage."""
    text_content = ""
    tool_calls: list[ChatCompletionMessageToolCall] = []

    for block in response.content:
        if block.type == "text":
            text_content = block.text
        elif block.type == "tool_use":
            tool_calls.append(
                ChatCompletionMessageToolCall(
                    id=block.id,
                    type="function",
                    function=Function(
                        name=block.name,
                        arguments=json.dumps(block.input),
                    ),
                )
            )

    return ChatCompletionMessage(
        role="assistant",
        content=text_content or None,
        tool_calls=tool_calls or None,
    )


async def _run_anthropic(
    agent: dict,
    tool_schemas: list[dict],
    messages: list[dict],
) -> ChatCompletionMessage:
    try:
        from anthropic import AsyncAnthropic, RateLimitError as AnthropicRateLimitError
    except ImportError:
        raise RuntimeError(
            "The 'anthropic' package is required for provider='claude'. "
            "Run: pip install anthropic"
        )

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    client  = AsyncAnthropic(api_key=api_key, timeout=_MODEL_TIMEOUT)
    system, anthropic_msgs = _convert_to_anthropic_messages(messages)
    tools   = _convert_tools_to_anthropic(tool_schemas) if tool_schemas else []

    call_kwargs: dict = {
        "model":      agent["model"],
        "max_tokens": 8192,
        "messages":   anthropic_msgs,
    }
    if system:
        call_kwargs["system"] = system
    if tools:
        call_kwargs["tools"] = tools

    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            response = await client.messages.create(**call_kwargs)
            return _anthropic_response_to_message(response)
        except Exception as exc:
            is_retryable = isinstance(exc, AnthropicRateLimitError) or getattr(exc, "status_code", 0) in (429, 500, 503)
            if is_retryable and attempt < _RETRY_ATTEMPTS:
                delay = _RETRY_DELAY * attempt
                print(
                    f"[agent_runner] {type(exc).__name__} on '{agent['model']}', "
                    f"retrying in {delay}s (attempt {attempt}/{_RETRY_ATTEMPTS})...",
                    file=sys.stderr,
                )
                await asyncio.sleep(delay)
            else:
                raise


# ── Main runner ───────────────────────────────────────────────────────────────

async def run(
    agent: dict,
    tool_map: dict,
    messages: list[dict],
) -> ChatCompletionMessage:
    """
    Call the model once and return the assistant message object.
    Reads agent["provider"] to pick the right API. Raises on unrecoverable errors.
    """
    provider = agent.get("provider") or _DEFAULT_PROVIDER

    # Collect tool schemas for this agent
    tool_schemas: list[dict] = []
    for tool_name in agent.get("tools", []):
        if tool_name in tool_map:
            tool_schemas.append(tool_map[tool_name]["schema"])
        else:
            print(
                f"[agent_runner] WARNING: agent '{agent['name']}' references "
                f"tool '{tool_name}' which is not in the tool map — skipping.",
                file=sys.stderr,
            )

    # Anthropic uses a separate non-OpenAI-compatible path
    if provider == "claude":
        return await _run_anthropic(agent, tool_schemas, messages)

    # All other providers: OpenAI-compatible
    if provider not in _PROVIDERS:
        print(
            f"[agent_runner] WARNING: unknown provider '{provider}', "
            f"falling back to '{_DEFAULT_PROVIDER}'.",
            file=sys.stderr,
        )
        provider = _DEFAULT_PROVIDER

    client = _build_openai_client(provider)

    call_kwargs: dict = {
        "model":    agent["model"],
        "messages": messages,
    }
    if tool_schemas:
        call_kwargs["tools"]       = tool_schemas
        call_kwargs["tool_choice"] = "auto"

    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            response = await client.chat.completions.create(**call_kwargs)
            return response.choices[0].message
        except _RETRYABLE as exc:
            if attempt < _RETRY_ATTEMPTS:
                delay = _RETRY_DELAY * attempt  # 5s, 10s, 15s
                print(
                    f"[agent_runner] {type(exc).__name__} on '{agent['model']}', "
                    f"retrying in {delay}s (attempt {attempt}/{_RETRY_ATTEMPTS})...",
                    file=sys.stderr,
                )
                await asyncio.sleep(delay)
            else:
                raise
