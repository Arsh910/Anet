"""
agent_runner.py — single model call for one iteration of the agentic loop.

Supported providers (set via agent config or anet.config.yaml):
  openrouter     → OpenRouter               (OPENROUTER_API_KEY)
  google         → Google AI / Gemini       (GOOGLE_API_KEY)
  openai         → OpenAI                   (OPENAI_API_KEY)
  anthropic      → Anthropic direct         (ANTHROPIC_API_KEY)   [alias: claude]
  vertex_google  → Gemini on Vertex AI      (VERTEX_PROJECT_ID + ADC)
  vertex_anthropic → Anthropic on Vertex AI (VERTEX_PROJECT_ID + ADC)  [alias: vertex_claude]

Vertex AI notes:
  - Authenticate once with: gcloud auth application-default login
  - Set VERTEX_PROJECT_ID in .env to your GCP project ID.
  - Set VERTEX_LOCATION (default: us-central1). Claude models require us-east5
    or europe-west1 — check Vertex Model Garden for availability.
  - Gemini model names:  google/gemini-2.5-pro-preview-06-05
  - Claude model names:  anthropic/claude-sonnet-4-5@20251101
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from openai import (
    AsyncOpenAI, APITimeoutError, BadRequestError, InternalServerError, RateLimitError,
)
from openai.types.chat import ChatCompletionMessage
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)

_RETRY_ATTEMPTS = 4
_RETRY_DELAY    = 15   # base seconds between retries (multiplied by attempt number)
                       # Vertex AI free tier has low QPM — 15s/30s/45s backoff avoids pile-up
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
_DEFAULT_MODEL    = "gemini-2.5-flash"


# ── Vertex AI auth ─────────────────────────────────────────────────────────────

_vertex_credentials = None  # cached google.auth credentials (refreshed on demand)


def _get_vertex_token() -> str:
    """Return a fresh Vertex AI access token via Application Default Credentials.
    google.auth.default() reads ADC: service account JSON (GOOGLE_APPLICATION_CREDENTIALS)
    or the token written by 'gcloud auth application-default login'.
    Calling credentials.refresh() is a no-op when the cached token is still valid.
    """
    global _vertex_credentials
    try:
        import google.auth
        import google.auth.transport.requests
    except ImportError:
        raise RuntimeError(
            "google-auth is required for Vertex AI providers. "
            "Run: pip install google-auth"
        )
    if _vertex_credentials is None:
        _vertex_credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
    auth_req = google.auth.transport.requests.Request()
    _vertex_credentials.refresh(auth_req)
    return _vertex_credentials.token


def build_vertex_client() -> AsyncOpenAI:
    """AsyncOpenAI client targeting Vertex AI's unified OpenAI-compatible endpoint.
    Supports both Gemini (google/...) and Claude (anthropic/...) model names.
    """
    project_id = os.getenv("VERTEX_PROJECT_ID")
    location   = os.getenv("VERTEX_LOCATION", "us-central1")
    if not project_id:
        raise RuntimeError(
            "VERTEX_PROJECT_ID is not set. "
            "Add it to .env or set as an environment variable."
        )
    base_url = (
        f"https://{location}-aiplatform.googleapis.com/v1beta1/"
        f"projects/{project_id}/locations/{location}/endpoints/openapi/"
    )
    token = _get_vertex_token()
    return AsyncOpenAI(api_key=token, base_url=base_url, timeout=_MODEL_TIMEOUT)


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


# ── Prompt caching (Anthropic-compatible providers) ───────────────────────────
#
# Marks the long, fixed prefix of an agent's prompt as cacheable so the next
# iteration of the same agent's loop pays ~10% of the original input cost on
# that prefix instead of full price. Three breakpoints per request:
#
#   1. System prompt  — identical every iteration of an agent run.
#   2. Last tool def  — caches the entire tool-definitions block.
#   3. Last message   — caches the growing message history; each new iteration
#                       replays everything up to the previous turn from cache
#                       and only pays full price on the most recent additions.
#
# Three is under Anthropic's max of 4. The "last message" breakpoint moves
# forward each iteration (a new write each turn), but every subsequent iteration
# then reads everything up to that point from cache — net savings grow with
# loop length.
#
# Anthropic native: cache_control goes on content blocks / the last tool.
# OpenRouter passes the same markers through for Claude models.
# Other providers (OpenAI/Gemini) handle caching automatically or not at all — we
# skip the markers for them so the request stays clean.

def _supports_anthropic_cache(provider: str, model: str) -> bool:
    """True if the (provider, model) pair accepts Anthropic-style cache_control
    markers — native Anthropic, or Claude routed through OpenRouter/Vertex.

    This is now only the *known-good* list, used by the native Anthropic path.
    Everything served over an OpenAI-compatible endpoint goes through
    _create_with_cache_fallback, which ATTEMPTS caching regardless of model and
    learns from the response — see there for why.
    """
    if provider in ("anthropic", "claude", "vertex_anthropic", "vertex_claude"):
        return True
    if provider == "openrouter":
        m = (model or "").lower()
        return "claude" in m or m.startswith("anthropic/")
    return False


# Models that answered a cache-marked request with a 400. Populated at runtime,
# per process; a model lands here at most once per session.
_CACHE_UNSUPPORTED: set[str] = set()


async def _create_with_cache_fallback(
    client,
    *,
    provider: str,
    model: str,
    messages: list[dict],
    tool_schemas: list[dict],
    agent: dict,
    label: str = "",
):
    """Run one completion, attempting prompt-cache markers for ANY model.

    Caching used to be gated on a hardcoded "is this Claude?" check, so every
    other model paid full price for a prefix (system prompt + tool schemas +
    the whole trajectory) that is identical on every step of an agent loop.
    That is the single largest avoidable cost in a long task, so the default is
    now to always try.

    The catch: the markers don't just add a field, they restructure `content`
    from a string into a block list. A provider that doesn't understand that
    REJECTS the request (400) rather than ignoring it. So the first time a
    given model 400s on a marked request we retry it once unmarked and record
    the model as unsupported — every later call for that model skips the
    markers outright. Cost of discovery: one failed request per model per
    process. A 400 for any other reason fails the unmarked retry too and
    propagates normally.
    """
    key = f"{provider}:{model}"
    use_cache = key not in _CACHE_UNSUPPORTED

    def _build(with_cache: bool) -> dict:
        msgs, tools = messages, tool_schemas
        if with_cache:
            msgs = _cache_system_message(msgs)
            msgs = _cache_last_message(msgs)
            tools = _cache_last_tool(tools)
        kwargs: dict = {"model": model, "messages": msgs}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        return kwargs

    where = f" ({label})" if label else ""
    attempt = 0
    while True:
        attempt += 1
        try:
            return await client.chat.completions.create(**_build(use_cache))
        except BadRequestError:
            if not use_cache:
                raise          # not about the markers — a real bad request
            _CACHE_UNSUPPORTED.add(key)
            use_cache = False
            attempt -= 1       # probing for cache support isn't a failed try
            print(
                f"[agent_runner] '{model}'{where} rejected prompt-cache markers — "
                f"continuing without caching for this model.",
                file=sys.stderr,
            )
        except _RETRYABLE as exc:
            if attempt >= _RETRY_ATTEMPTS:
                raise
            delay = _RETRY_DELAY * attempt
            print(
                f"[agent_runner] {type(exc).__name__} on '{model}'{where}, "
                f"retrying in {delay}s (attempt {attempt}/{_RETRY_ATTEMPTS})...",
                file=sys.stderr,
            )
            await asyncio.sleep(delay)


_CACHE_CONTROL = {"type": "ephemeral"}


def _cache_system_message(messages: list[dict]) -> list[dict]:
    """Return a new messages list where the first system message's content is a
    content-block list with cache_control on the final block — the breakpoint that
    caches everything up to and including the system prompt."""
    out = list(messages)
    for i, m in enumerate(out):
        if m.get("role") != "system":
            continue
        content = m.get("content")
        if isinstance(content, str) and content:
            out[i] = {
                **m,
                "content": [{
                    "type": "text",
                    "text": content,
                    "cache_control": _CACHE_CONTROL,
                }],
            }
        break
    return out


def _cache_last_tool(tool_schemas: list[dict]) -> list[dict]:
    """Return a new tool list with cache_control on the LAST tool — the breakpoint
    that caches the entire tool-definition block. No-op on empty input."""
    if not tool_schemas:
        return tool_schemas
    out = list(tool_schemas)
    out[-1] = {**out[-1], "cache_control": _CACHE_CONTROL}
    return out


def _cache_last_message(messages: list[dict]) -> list[dict]:
    """Return a new messages list with cache_control on the LAST message's final
    content block — the rolling breakpoint that caches the entire prior history
    so the NEXT agent-loop iteration replays it from cache.

    Handles three content shapes the orchestrator can produce on the last turn:
      • str — typical tool result or user message → wrap in a text block.
      • list[dict] — multimodal or Anthropic-converted blocks → mark the last block.
      • None / empty (assistant with only tool_calls and no text) → skip; falls back
        to the system+tools breakpoints, which is still a meaningful prefix.
    """
    if not messages:
        return messages
    out = list(messages)
    last = dict(out[-1])
    content = last.get("content")
    if isinstance(content, str) and content:
        last["content"] = [{
            "type": "text",
            "text": content,
            "cache_control": _CACHE_CONTROL,
        }]
        out[-1] = last
    elif isinstance(content, list) and content:
        blocks = [dict(b) for b in content]
        blocks[-1] = {**blocks[-1], "cache_control": _CACHE_CONTROL}
        last["content"] = blocks
        out[-1] = last
    return out


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
            "The 'anthropic' package is required for provider='anthropic'. "
            "Run: pip install anthropic"
        )

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    client  = AsyncAnthropic(api_key=api_key, timeout=_MODEL_TIMEOUT)
    system, anthropic_msgs = _convert_to_anthropic_messages(messages)
    tools   = _convert_tools_to_anthropic(tool_schemas) if tool_schemas else []

    # Prompt caching: wrap the system prompt in a cache_control-tagged block,
    # mark the last tool, and mark the last message's final content block so the
    # growing message history also caches forward across loop iterations.
    system_param: str | list[dict] = system
    if system:
        system_param = [{"type": "text", "text": system, "cache_control": _CACHE_CONTROL}]
    if tools:
        tools = _cache_last_tool(tools)
    anthropic_msgs = _cache_last_message(anthropic_msgs)

    call_kwargs: dict = {
        "model":      agent.get("model") or _DEFAULT_MODEL,
        "max_tokens": 8192,
        "messages":   anthropic_msgs,
    }
    if system:
        call_kwargs["system"] = system_param
    if tools:
        call_kwargs["tools"] = tools

    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            response = await client.messages.create(**call_kwargs)
            from anet.core import tokens as _tok
            _tok.record(response, stage=agent.get("name"))
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


# ── Response extraction ─────────────────────────────────────────────────────────

def _message_from_response(response, model: str):
    """Pull the assistant message out of an OpenAI-compatible response, turning the
    'no choices' case into a clear error.

    OpenRouter (and some proxies) return HTTP 200 with `choices = None` and an
    `error` body when the upstream fails — most often the context got too large, or
    a rate limit / provider error. Indexing `choices[0]` then raises the opaque
    "'NoneType' object is not subscriptable"; surface the actual reason instead.
    """
    choices = getattr(response, "choices", None)
    if choices:
        return choices[0].message

    err = None
    try:
        err = getattr(response, "error", None) or (getattr(response, "model_extra", None) or {}).get("error")
    except Exception:
        err = None
    if err:
        detail = err.get("message") if isinstance(err, dict) else str(err)
        raise RuntimeError(f"model '{model}' returned an error: {detail}")
    raise RuntimeError(
        f"model '{model}' returned no choices — the request likely exceeded the "
        f"model's context window (too much tool output) or was rejected by the "
        f"provider. Try a model with a larger context, or /forget to trim history."
    )


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

    # Anthropic direct API (non-OpenAI-compatible). "claude" kept as a legacy alias.
    if provider in ("anthropic", "claude"):
        return await _run_anthropic(agent, tool_schemas, messages)

    # Vertex AI (Gemini or Anthropic via Vertex's unified OpenAI-compat endpoint).
    # "vertex_claude" kept as a legacy alias for "vertex_anthropic".
    if provider in ("vertex_google", "vertex_anthropic", "vertex_claude"):
        client = build_vertex_client()
        model  = agent.get("model") or _DEFAULT_MODEL
        response = await _create_with_cache_fallback(
            client, provider=provider, model=model, messages=messages,
            tool_schemas=tool_schemas, agent=agent, label="vertex",
        )
        from anet.core import tokens as _tok
        _tok.record(response, stage=agent.get("name"))
        return _message_from_response(response, model)

    # All other providers: OpenAI-compatible
    if provider not in _PROVIDERS:
        print(
            f"[agent_runner] WARNING: unknown provider '{provider}', "
            f"falling back to '{_DEFAULT_PROVIDER}'.",
            file=sys.stderr,
        )
        provider = _DEFAULT_PROVIDER

    client = _build_openai_client(provider)
    model  = agent.get("model") or _DEFAULT_MODEL

    # Prompt caching is attempted for every model here, not just Claude — the
    # helper probes once and remembers if an upstream rejects the markers.
    response = await _create_with_cache_fallback(
        client, provider=provider, model=model, messages=messages,
        tool_schemas=tool_schemas, agent=agent,
    )
    from anet.core import tokens as _tok
    _tok.record(response, stage=agent.get("name"))
    return _message_from_response(response, model)
