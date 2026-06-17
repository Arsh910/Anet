"""
memory_agent.py — Background memory review agent.

Runs every N turns as a fire-and-forget asyncio task from engine.run_turn().
In a single LLM call it:
  1. Updates memory/USER.md (high-level user profile)
  2. Saves discrete facts to memory_tool (~/.anet/memory.json)

Deliberately isolated from engine.py to avoid circular imports.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from openai import AsyncOpenAI

_PROVIDERS = {
    "google":     ("https://generativelanguage.googleapis.com/v1beta/openai/", "GOOGLE_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1",                             "OPENROUTER_API_KEY"),
    "openai":     ("https://api.openai.com/v1",                                "OPENAI_API_KEY"),
}

_SYSTEM_PROMPT = """\
You are a memory extraction system for an AI assistant. Analyze the recent conversation and return a JSON object with exactly two keys:

"user_md": The complete updated USER.md content. This is the user's PROFILE — who they are. Preserve ALL existing sections and entries — never remove anything. Only ADD new profile facts: identity (name, role), interests, tech stack, languages, working style, standing preferences, tools they use. If nothing new, return the current USER.md unchanged.

"facts": An array of NEW, concrete facts worth RETRIEVING for a future TASK — and ONLY those. Good facts: a project's location/path, a specific project's stack, key technical decisions, important file paths, environment/setup details, task context (e.g. "User's main project is at C:\\projects\\myapp, a React + FastAPI app"). Do NOT put identity, interests, preferences, languages, skills, or working style here — those belong in USER.md ONLY; never duplicate profile material as facts. Only include facts not already in USER.md or the existing memory list. Return [] if nothing new.

Respond with ONLY the JSON object — no prose, no markdown fences."""


def _client() -> tuple[AsyncOpenAI, str]:
    """Return (client, model) for the memory agent, with fallback to manager config."""
    try:
        from anet.core.config_loader import load as _cfg, manager_config
        mem = _cfg().get("memory", {})
        model    = mem.get("model") or None
        provider = mem.get("provider") or None
        if not model or not provider:
            mgr  = manager_config()
            model    = mgr.get("model") or "gemini-2.5-flash"
            provider = mgr.get("provider") or "google"
    except Exception:
        model, provider = "gemini-2.5-flash", "google"

    if provider in ("vertex_google", "vertex_anthropic", "vertex_claude"):
        from anet.core.agent_runner import build_vertex_client
        return build_vertex_client(), model

    base_url, env_key = _PROVIDERS.get(provider, _PROVIDERS["google"])
    api_key = os.getenv(env_key)
    if not api_key:
        raise RuntimeError(f"{env_key} not set (needed for memory agent provider='{provider}')")
    return AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=60), model


def _parse_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{[\s\S]+\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"No JSON in response: {text[:120]!r}")


async def run_memory_review(messages: list[dict], thread_id: str) -> None:
    """
    Analyze recent conversation and update USER.md + memory_tool.
    Called as asyncio.create_task() — never raises.
    """
    try:
        if not messages:
            return

        recent = messages[-30:]
        history_text = "\n".join(
            f"{m['role'].upper()}: {(m.get('content') or '').strip()[:400]}"
            for m in recent
            if (m.get("content") or "").strip()
        )
        if not history_text.strip():
            return

        from anet.core.paths import user_profile_path
        _USER_PROFILE_PATH = user_profile_path()
        current_md = (
            _USER_PROFILE_PATH.read_text(encoding="utf-8").strip()
            if _USER_PROFILE_PATH.exists() else ""
        )

        # Fetch a snapshot of existing memories to avoid duplicates
        existing_snippet = ""
        try:
            from anet.AnetTools.memory_tool import search_memories
            keywords = " ".join(
                w for m in recent[-8:]
                for w in (m.get("content") or "").split()[:12]
            )
            existing = search_memories(keywords, max_results=12)
            if existing:
                existing_snippet = "Existing memories (do NOT duplicate):\n" + "\n".join(
                    f"- {e['content']}" for e in existing
                )
        except Exception:
            pass

        client, model = _client()
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=900,
            temperature=0,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Current USER.md:\n{current_md}\n\n"
                        + (f"{existing_snippet}\n\n" if existing_snippet else "")
                        + f"Recent conversation:\n{history_text}"
                    ),
                },
            ],
        )

        raw = (resp.choices[0].message.content or "").strip()
        result = _parse_json(raw)

        # ── 1. Update USER.md ─────────────────────────────────────────────────
        new_md = (result.get("user_md") or "").strip()
        if new_md and len(new_md) > 30 and new_md != current_md:
            _USER_PROFILE_PATH.write_text(new_md, encoding="utf-8")
            print(f"[memory_agent] USER.md updated", file=sys.stderr)

        # ── 2. Save discrete facts to memory_tool ────────────────────────────
        facts = result.get("facts")
        if facts and isinstance(facts, list):
            from anet.AnetTools.memory_tool import run as _mem_run
            saved = 0
            for fact in facts[:5]:
                fact = str(fact).strip()
                if fact:
                    r = await _mem_run({"action": "save", "content": fact})
                    if r.get("saved"):
                        saved += 1
            if saved:
                print(f"[memory_agent] saved {saved} new fact(s) to memory_tool", file=sys.stderr)

    except Exception as exc:
        print(f"[memory_agent] error: {exc}", file=sys.stderr)
