"""
engine_base.py — shared engine infrastructure.

Defines the BaseEngine class that every orchestration engine (AdaptOrchEngine
today, future paper-based engines later) inherits from. Owns per-thread state
(turn counters, rolling summaries), the rolling-summary maintenance, and the
persistence helper. Engine-specific pipelines (e.g. AdaptOrch's 5-phase
decompose → DAG → route → execute → synthesize) live in their respective
subclasses; this file is engine-agnostic.

Per-engine entry point: subclasses must implement `run_turn`.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

from openai import AsyncOpenAI

from anet.core.context import on_status as _status_var


# ── Manager model config (shared across engines — summary call, etc.) ─────────

_MANAGER_PROVIDERS = {
    "google":     ("https://generativelanguage.googleapis.com/v1beta/openai/", "GOOGLE_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1",                             "OPENROUTER_API_KEY"),
    "openai":     ("https://api.openai.com/v1",                                "OPENAI_API_KEY"),
}


def _manager_cfg() -> tuple[str, str]:
    try:
        from anet.core.config_loader import manager_config
        cfg = manager_config()
        return (cfg.get("model") or "gemini-2.5-pro"), (cfg.get("provider") or "google")
    except Exception:
        return "gemini-2.5-pro", "google"


def _manager_client() -> tuple[AsyncOpenAI, str]:
    model, provider = _manager_cfg()
    if provider in ("vertex_google", "vertex_anthropic", "vertex_claude"):
        from anet.core.agent_runner import build_vertex_client
        return build_vertex_client(), model
    base_url, env_key = _MANAGER_PROVIDERS.get(provider, _MANAGER_PROVIDERS["google"])
    api_key = os.getenv(env_key)
    if not api_key:
        raise RuntimeError(f"{env_key} not set (needed for manager provider='{provider}')")
    return AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=120), model


def _context_settings() -> tuple[int, int]:
    """(recent_tokens, min_recent) for the short-term window — how many tokens of
    recent turns to keep verbatim, and the floor of recent messages always kept."""
    try:
        from anet.core.config_loader import load
        cfg = load().get("context", {}) or {}
        return int(cfg.get("recent_tokens", 3000)), int(cfg.get("min_recent", 4))
    except Exception:
        return 3000, 4


# ── EngineResult ──────────────────────────────────────────────────────────────

@dataclass
class EngineResult:
    reply: str
    step_results: list[dict] = field(default_factory=list)


# ── BaseEngine ────────────────────────────────────────────────────────────────

class BaseEngine:
    """Shared base for every orchestration engine.

    Holds per-thread state (turn counters, rolling summaries) and provides
    rolling-summary maintenance + the persistence helper. Subclasses implement
    `run_turn` with their own orchestration pipeline.

    API surface used by main.py:
      result = await engine.run_turn(thread_id, store, user_input)
    """

    def __init__(
        self,
        agents:        list[dict],
        tools:         dict,
        manager_tools: dict | None = None,
    ) -> None:
        self._agents       = agents
        self._agent_map    = {a["name"]: a for a in agents}
        self._tools        = tools
        self._manager_tools = manager_tools or {}
        # Incremental memory: turn counters per thread
        self._turn_counts: dict[str, int] = {}
        # Short-term memory: latest rolling summary per thread (for the agent path)
        self._summaries: dict[str, str] = {}

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _notify(self, msg: str) -> None:
        _status_var.get()(msg)

    # ── Entry point — subclasses implement ────────────────────────────────────

    async def run_turn(self, thread_id: str, store, user_input: str) -> EngineResult:
        raise NotImplementedError("BaseEngine subclasses must implement run_turn")

    # ── Short-term memory (rolling summary + token-budgeted window) ───────────

    async def _maintain_summary(self, store, thread_id: str,
                                messages: list[dict]) -> tuple[str, int]:
        """Keep the rolling summary current and return (summary, keep_from).

        `messages[keep_from:]` are sent verbatim; everything before is covered
        by `summary`. Summarisation fires only when turns overflow the
        recent-token budget — typically once every several turns, not every turn.
        """
        from anet.core import context_window as cw

        recent_tokens, min_recent = _context_settings()
        try:
            summary, count = await store.get_summary(thread_id)
        except Exception:
            summary, count = "", 0
        count = max(0, min(count, len(messages)))

        keep_from, overflow = cw.plan_window(messages, recent_tokens, min_recent, count)

        if overflow:
            try:
                client, model = _manager_client()
                msgs = cw.build_summary_messages(summary, overflow)
                # Cache the fixed summariser system prompt for repeated overflows.
                from anet.core.agent_runner import _supports_anthropic_cache, _cache_system_message
                _, provider = _manager_cfg()
                if _supports_anthropic_cache(provider, model):
                    msgs = _cache_system_message(msgs)
                resp = await client.chat.completions.create(
                    model=model, temperature=0, max_tokens=700, messages=msgs,
                )
                from anet.core import tokens as _tok
                _tok.record(resp, stage="compress")
                new_summary = (resp.choices[0].message.content or "").strip()
                if new_summary:
                    summary = new_summary
                    await store.set_summary(thread_id, summary, keep_from)
                else:
                    keep_from = count  # empty summary → keep overflow verbatim
            except Exception as exc:
                # Summariser unavailable → don't advance past what's summarised, so
                # no turn is dropped from view without coverage (may exceed budget).
                print(f"[engine] summary update skipped: {exc}", file=sys.stderr)
                keep_from = count

        self._summaries[thread_id] = summary
        return summary, keep_from

    # ── Persistence helper ────────────────────────────────────────────────────

    @staticmethod
    async def _persist(store, thread_id: str, user_input: str, reply: str,
                       is_async_resume: bool) -> None:
        if not is_async_resume:
            await store.append(thread_id, "user", user_input)
        await store.append(thread_id, "assistant", reply)
