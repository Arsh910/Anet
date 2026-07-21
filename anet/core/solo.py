"""
solo.py — the Solo engine: one generalist agent, no orchestration.

SoloEngine answers every turn with a single pass through the standard agent
loop (orchestrator.run): the model sees the persona, the conversation window,
and a curated toolset, then replies or calls tools until done. No decomposer,
no routing, no synthesis — the fixed per-turn classification cost the
orchestrating engines pay (measured: AdaptOrch's decomposer spends ~720
tokens to answer "hi" before any real work starts) is zero here.

Toolset defaults to filesystem + shell + web + code_intel (code_agent's
scope, the most versatile built-in domain) plus the COMMON baseline — NOT
every loaded tool. Merging every tool (including MCP servers wired for one
specialist's narrow purpose) was tried first and measured at ~18.9k input
tokens for a plain "hello" with no prompt caching to soften it on non-Claude
models — worse than the decomposer tax this engine exists to avoid.
Overridable per pack via orchestration.solo.{toolsets,tools}.

Delegation still exists: spawn_tool is in every agent's COMMON baseline, so
the model can hand work to a specialist agent when it judges that worthwhile
— per turn, opt-in, never forced.
"""
from __future__ import annotations

import os

from anet.core import orchestrator
from anet.core.engine_base import BaseEngine, EngineResult
from anet.core.context import on_status as _status_var


_ROLE = (
    "You are {name}, a capable general assistant with tools for web "
    "research, file operations, shell commands, and code intelligence. "
    "Answer directly when you can; use tools when the task needs them. "
    "Prefer the fewest tool calls that finish the job. For a large task "
    "with independent parts, you may delegate a part to a specialist "
    "agent via spawn_tool."
)

_DEFAULT_TOOLSETS = ["filesystem", "shell", "web", "code_intel"]


def _solo_config() -> dict:
    """orchestration.solo.{model,provider,max_steps} -> manager -> defaults."""
    try:
        from anet.core.config_loader import load, manager_config
        solo = ((load() or {}).get("orchestration") or {}).get("solo") or {}
        mgr = manager_config() or {}
    except Exception:
        solo, mgr = {}, {}
    return {
        "model":     solo.get("model")    or mgr.get("model")    or "gemini-2.5-flash",
        "provider":  solo.get("provider") or mgr.get("provider") or "openrouter",
        "max_steps": int(solo.get("max_steps") or 60),
    }


def _solo_toolsets() -> tuple[list[str], list[str]]:
    """orchestration.solo.{toolsets,tools} -> the curated default. An
    explicit [] for toolsets means "none" (not "unset, use default") — only
    a missing/non-list key falls back."""
    try:
        from anet.core.config_loader import load
        solo = ((load() or {}).get("orchestration") or {}).get("solo") or {}
    except Exception:
        solo = {}
    toolsets = solo.get("toolsets")
    if not isinstance(toolsets, list):
        toolsets = list(_DEFAULT_TOOLSETS)
    extra = solo.get("tools")
    if not isinstance(extra, list):
        extra = []
    return list(toolsets), list(extra)


def _build_solo_agent(tools: dict) -> dict:
    """The synthetic generalist agent: persona + role, a curated toolset
    filtered to what's actually loaded."""
    from anet.AnetTools.toolsets import expand_tools

    name = os.getenv("ASSISTANT_NAME", "Anet")
    system = _ROLE.format(name=name)
    try:
        from anet.core.config_loader import load_soul
        soul = load_soul()
        if soul:
            system = f"{soul}\n\n{system}"
    except Exception:
        pass

    toolsets, extra_tools = _solo_toolsets()
    resolved = expand_tools({"toolsets": toolsets, "tools": extra_tools})
    agent_tools = [t for t in resolved if t in tools]

    cfg = _solo_config()
    return {
        "name": "solo",
        "system_prompt": system,
        "tools": agent_tools,
        "model": cfg["model"],
        "provider": cfg["provider"],
        "max_steps": cfg["max_steps"],
    }


def _emit(text: str) -> None:
    """Stream the finished reply once through the token sink (orchestrator.run
    is not itself streaming; without this the live preview would stay empty
    until the turn fully completes). Same helper QweenBee's coordinator uses
    for the same reason — its reply is also a non-streamed final text."""
    try:
        from anet.core.context import on_token
        if text:
            on_token.get()(text)
    except Exception:
        pass


class SoloEngine(BaseEngine):
    async def run_turn(self, thread_id: str, store, user_input: str) -> EngineResult:
        on_status = _status_var.get()
        try:
            messages = await store.load(thread_id)
            convo = messages + [{"role": "user", "content": user_input}]
            summary, keep_from = await self._maintain_summary(store, thread_id, convo)

            # History = rolling summary (as a user/assistant pair, only when
            # non-empty) + the verbatim recent window, minus the current
            # user_input (orchestrator.run appends that itself).
            history: list[dict] = []
            if summary.strip():
                history.append({"role": "user",
                                "content": f"[Summary of earlier conversation]\n{summary.strip()}"})
                history.append({"role": "assistant", "content": "Understood."})
            history.extend(convo[keep_from:-1])

            agent = _build_solo_agent(self._tools)
            on_status("solo: thinking...")
            result = await orchestrator.run(agent, self._tools, user_input, history, on_status)
            reply = (result or {}).get("text", "") or "Done."
        except Exception as exc:
            self._notify(f"solo: error ({exc})")
            reply = f"Sorry — something went wrong while handling that turn: {exc}"

        try:
            await self._persist(store, thread_id, user_input, reply, False)
        except Exception:
            pass
        _emit(reply)
        return EngineResult(reply=reply)
