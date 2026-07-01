"""
memory_common.py — backend-agnostic helpers shared by both long-term memory
backends (mem0 and recmem).

These are pure config readers + record-shape utilities: memory categories (which
drive always_inject / applies_to classification), the extraction instructions, the
known agent names, the on-disk memory home, and small formatting helpers. Keeping
them here means the two backends classify and shape records identically — only the
storage engine and the LLM plumbing differ.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Default memory categories — CLASSIFY each explicit save (by the LLM, not by tag
# matching) and decide retrieval behaviour. Overridable per pack via
# `memory.categories` in anet.config.yaml — categories are data, not code.
#   always_inject: surface this memory whenever its `applies_to` agent runs, even
#                  if it shares no keywords with the task (how standing preferences
#                  reach an agent). Non-always_inject categories are relevance-only.
DEFAULT_CATEGORIES = [
    {"name": "preference", "always_inject": True,
     "description": "A standing instruction or style the user wants applied automatically "
                    "(naming conventions, tone, output format, coding style, do/don't rules)."},
    {"name": "identity", "always_inject": True,
     "description": "Who the user is: name, role, background, languages, interests, team."},
    {"name": "project", "always_inject": False,
     "description": "Facts about a specific project: its path, stack, structure, key decisions."},
    {"name": "environment", "always_inject": False,
     "description": "Setup/tooling/config details: OS, installed tools, credentials location, paths."},
    {"name": "fact", "always_inject": False,
     "description": "Any other useful fact worth recalling later that doesn't fit the above."},
]

DEFAULT_INSTRUCTIONS = (
    "Store ONLY durable facts about the USER that help future sessions: their identity "
    "(name, role, background), standing preferences, the projects they own (paths, tech "
    "stacks), important decisions, and environment/setup details. "
    "Do NOT store one-off actions the assistant performed for them (e.g. 'opened a page', "
    "'ran a command', 'created a file'), transient task state, or small talk — those are "
    "not lasting facts about the user. When in doubt, leave it out."
)


def memory_home() -> Path:
    from anet.core import paths
    d = paths.home() / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


def memory_cfg() -> dict:
    try:
        from anet.core.config_loader import load
        return load().get("memory", {}) or {}
    except Exception:
        return {}


def categories() -> list[dict]:
    cats = memory_cfg().get("categories")
    if isinstance(cats, list) and cats:
        out = []
        for c in cats:
            if isinstance(c, dict) and c.get("name"):
                out.append({
                    "name": str(c["name"]),
                    "description": str(c.get("description") or ""),
                    "always_inject": bool(c.get("always_inject", False)),
                })
        if out:
            return out
    return DEFAULT_CATEGORIES


def category_map() -> dict[str, bool]:
    """name → always_inject."""
    return {c["name"]: c["always_inject"] for c in categories()}


def custom_instructions() -> str:
    return str(memory_cfg().get("instructions") or DEFAULT_INSTRUCTIONS)


def known_agents() -> list[str]:
    try:
        from anet.AnetAgents.agents_config import AGENTS
        return [a.get("name") for a in AGENTS if a.get("name")]
    except Exception:
        return ["research_agent", "code_agent", "computer_agent", "checker_agent"]


def fmt_created(raw: Any) -> str:
    """ISO-8601 → 'YYYY-MM-DD HH:MM'."""
    if not raw:
        return ""
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(raw)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(raw)[:16].replace("T", " ")


def parse_json(text: str) -> dict:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return {}
