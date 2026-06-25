"""
stage_models.py — per-stage LLM model resolution for the AdaptOrch pipeline.

Each orchestration stage that needs a model (decomposer, synthesizer, arbiter,
merger) may declare its own model/provider under an `orchestration:` block in
anet.config.yaml. If a stage isn't configured, it falls back to the manager's
model/provider — so by default everything uses the manager model, and you only
override a stage when you want to (e.g. a cheap model for decomposition):

    orchestration:
      decomposer:  { model: gemini-2.5-flash,  provider: google }
      synthesizer: { model: claude-sonnet-4.6, provider: openrouter }

Stages with no model needs (dag, router) never call this. Shared so every stage
resolves its model the same way.
"""
from __future__ import annotations

import json
import re

_DEFAULT_MODEL = "gemini-2.5-flash"
_DEFAULT_PROVIDER_NAME = "openrouter"


def stage_model(stage: str) -> tuple[str, str]:
    """Return (model, provider) for an orchestration stage.

    Resolution: orchestration.<stage>.{model,provider} → manager.{model,provider}
    → built-in defaults.
    """
    try:
        from anet.core.config_loader import load
        cfg = load() or {}
    except Exception:
        cfg = {}
    stage_cfg = ((cfg.get("orchestration") or {}).get(stage) or {})
    mgr = (cfg.get("manager") or {})
    model = stage_cfg.get("model") or mgr.get("model") or _DEFAULT_MODEL
    provider = stage_cfg.get("provider") or mgr.get("provider") or _DEFAULT_PROVIDER_NAME
    return model, provider


def build_stage_client(stage: str):
    """Return (AsyncOpenAI client, model) for the stage, reusing agent_runner's
    provider client builders."""
    model, provider = stage_model(stage)
    from anet.core.agent_runner import (
        build_vertex_client, _build_openai_client, _PROVIDERS, _DEFAULT_PROVIDER,
    )
    if provider in ("vertex_google", "vertex_anthropic", "vertex_claude"):
        return build_vertex_client(), model
    if provider in _PROVIDERS:
        return _build_openai_client(provider), model
    return _build_openai_client(_DEFAULT_PROVIDER), model


async def stage_call(
    stage: str,
    messages: list[dict],
    *,
    max_tokens: int = 1500,
    temperature: float = 0.0,
) -> str:
    """Run a chat completion for the stage and return the text content."""
    client, model = build_stage_client(stage)
    resp = await client.chat.completions.create(
        model=model, messages=messages, max_tokens=max_tokens, temperature=temperature,
    )
    from anet.core import tokens as _tok
    _tok.record(resp, stage=stage)
    return (resp.choices[0].message.content or "").strip()


async def stage_call_stream(
    stage: str,
    messages: list[dict],
    on_token,
    *,
    max_tokens: int = 1800,
    temperature: float = 0.0,
) -> str:
    """Like stage_call, but streams: each content delta is passed to on_token as it
    arrives, and the full text is returned. Captures token usage from the terminal
    chunk (falls back to a plain stream on providers that reject stream_options)."""
    from anet.core import tokens as _tok
    client, model = build_stage_client(stage)
    base = dict(model=model, messages=messages, max_tokens=max_tokens,
                temperature=temperature, stream=True)
    try:
        stream = await client.chat.completions.create(**base, stream_options={"include_usage": True})
    except Exception:
        stream = await client.chat.completions.create(**base)

    out = ""
    async for chunk in stream:
        if getattr(chunk, "usage", None):
            _tok.record(chunk, stage=stage)
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content or ""
        if delta:
            out += delta
            try:
                on_token(delta)
            except Exception:
                pass
    return out.strip()


def extract_json(text: str) -> dict:
    """Best-effort parse of a JSON object from model output (handles ``` fences
    and surrounding prose)."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    m = re.search(r"\{[\s\S]+\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    raise ValueError(f"No JSON object found in model output: {text[:200]!r}")
