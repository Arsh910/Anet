"""
memory_tool — persistent cross-session memory for ANet agents.

Thin tool-facing adapter over `anet.core.memory_store` (mem0-backed: local Chroma
vector store + on-device fastembed embeddings + the user's configured LLM for
intelligent fact extraction/de-duplication). The agent-visible interface is
unchanged: save / search / list / delete / clear.

`search_memories()` and `preference_memories()` are imported synchronously by the
engine (plan-time recall) and the orchestrator (per-agent memory injection); they
keep their original return shape (id / content / tags / project_path / created_at).

On first use the old flat-file store (~/.anet/memory.json) is migrated into mem0
once, then renamed aside so it isn't re-imported.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from anet.core import memory_store

# ── Schema (unchanged agent-facing contract) ────────────────────────────────────

SCHEMA = {
    "type": "function",
    "function": {
        "name": "memory_tool",
        "description": (
            "Save and retrieve persistent memories that survive across sessions. "
            "Use save to store important facts: project locations, user preferences, "
            "decisions made, technology choices, file paths, anything worth remembering. "
            "Use search to recall past context before starting a task. "
            "Use list to see everything stored. Use delete to remove stale entries."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["save", "search", "list", "delete", "clear"],
                    "description": (
                        "save: store a new memory. "
                        "search: find relevant memories by keyword. "
                        "list: show all memories (optionally filtered by project_path). "
                        "delete: remove one memory by id. "
                        "clear: wipe all memories."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": (
                        "The memory text to store. Be specific: include paths, names, "
                        "technology choices, and any detail that would be useful in a future session. "
                        "Used by: save."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "Keywords to search for. Used by: search.",
                },
                "project_path": {
                    "type": "string",
                    "description": (
                        "Absolute path to the project this memory belongs to. "
                        "Omit for global memories (preferences, general facts). "
                        "Used by: save, search, list."
                    ),
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional short free-form labels, e.g. ['python', 'api', 'auth']. "
                        "You do NOT need to categorise the memory or mark preferences — Anet "
                        "classifies each saved memory automatically (whether it's a standing "
                        "preference, an identity fact, project info, etc.) and decides which "
                        "agents it applies to. Just save the content clearly. Used by: save."
                    ),
                },
                "id": {
                    "type": "string",
                    "description": "Memory ID to delete. Used by: delete.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max memories to return. Default 10. Used by: search, list.",
                },
            },
            "required": ["action"],
        },
    },
}


# ── One-time migration of the legacy flat-file store ────────────────────────────

_LEGACY_FILE = Path.home() / ".anet" / "memory.json"
_migrated = False


def _migrate_legacy() -> None:
    """Import ~/.anet/memory.json into mem0 once, then rename it aside.

    Stored verbatim (infer=False) so nothing is dropped or rephrased. Idempotent:
    the rename means a second run finds nothing to do."""
    global _migrated
    if _migrated:
        return
    _migrated = True
    if not _LEGACY_FILE.exists():
        return
    if not memory_store.is_available():
        return
    try:
        old = json.loads(_LEGACY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(old, list) or not old:
        _retire_legacy()
        return

    count = 0
    for m in old:
        content = (m.get("content") or "").strip()
        if not content:
            continue
        r = memory_store.add(
            content,
            tags=m.get("tags") or [],
            project_path=m.get("project_path") or None,
            infer=False,                      # preserve every legacy fact verbatim
        )
        if r.get("saved"):
            count += 1
    print(f"[memory_tool] migrated {count} memory(ies) from memory.json into mem0",
          file=sys.stderr)
    _retire_legacy()


def _retire_legacy() -> None:
    try:
        _LEGACY_FILE.rename(_LEGACY_FILE.with_suffix(".json.migrated"))
    except Exception:
        pass


# ── Public read API (imported by engine + orchestrator) ─────────────────────────

def search_memories(
    query: str,
    project_path: str | None = None,
    max_results: int = 5,
    min_score: float = 0.0,
) -> list[dict]:
    """Semantic search over stored memories. Called synchronously by the planner
    and by per-agent memory injection."""
    _migrate_legacy()
    return memory_store.search(
        query, project_path=project_path, limit=max_results, min_score=min_score
    )


def preference_memories(agent_name: str | None = None) -> list[dict]:
    """Standing memories (always_inject categories) — coding style, tone, identity,
    etc. — injected regardless of keyword/semantic relevance. Optionally scoped to
    the memories that apply to `agent_name`."""
    _migrate_legacy()
    return memory_store.standing_memories(agent_name)


# ── Action handlers ────────────────────────────────────────────────────────────

def _do_save(params: dict) -> dict:
    content = (params.get("content") or "").strip()
    if not content:
        return {"error": "content is required for save"}
    # Explicit saves store verbatim (infer=False): when an agent or the user says
    # "remember this", the exact text must be persisted — deterministic, free, and
    # never silently dropped (mem0's infer=True may decide content isn't worth
    # keeping, or swallow an LLM error and store nothing). Intelligent extraction /
    # de-duplication is applied on the automatic conversation pass instead.
    #
    # classify_content=True lets the LLM label the memory's category + which agents
    # it applies to (driven by the categories in anet.config.yaml) — no hardcoded
    # 'preference' tag and no convention the model has to follow.
    return memory_store.add(
        content,
        tags=params.get("tags") or [],
        project_path=params.get("project_path") or None,
        infer=False,
        classify_content=True,
    )


def _do_search(params: dict) -> dict:
    query = (params.get("query") or "").strip()
    if not query:
        return {"error": "query is required for search"}
    max_results = int(params.get("max_results") or 10)
    results = search_memories(
        query, project_path=params.get("project_path"), max_results=max_results
    )
    if not results:
        return {"results": [], "count": 0, "message": f"No memories found for '{query}'."}
    return {"results": results, "count": len(results)}


def _do_list(params: dict) -> dict:
    _migrate_legacy()
    max_results = int(params.get("max_results") or 50)
    memories = memory_store.get_all(project_path=params.get("project_path"))
    memories = sorted(memories, key=lambda m: m.get("created_at", ""), reverse=True)
    memories = memories[:max_results]
    if not memories:
        scope = f" for project '{params.get('project_path')}'" if params.get("project_path") else ""
        return {"memories": [], "count": 0, "message": f"No memories stored{scope}."}
    return {"memories": memories, "count": len(memories)}


def _do_delete(params: dict) -> dict:
    mem_id = (params.get("id") or "").strip()
    if not mem_id:
        return {"error": "id is required for delete"}
    return memory_store.delete(mem_id)


def _do_clear(params: dict) -> dict:
    r = memory_store.clear()
    if r.get("cleared"):
        r["message"] = f"Cleared {r.get('count', 0)} memory(ies)."
    return r


# ── Dispatch ───────────────────────────────────────────────────────────────────

_HANDLERS = {
    "save":   _do_save,
    "search": _do_search,
    "list":   _do_list,
    "delete": _do_delete,
    "clear":  _do_clear,
}


async def run(params: dict) -> dict:
    import asyncio
    action = (params.get("action") or "").strip()
    if not action:
        return {"error": "action is required (save | search | list | delete | clear)"}
    if action not in _HANDLERS:
        return {"error": f"Unknown action '{action}'. Valid: {', '.join(_HANDLERS)}"}
    try:
        # Handlers do blocking I/O (mem0 vector store + an LLM classify call on save),
        # so run them off the event loop.
        return await asyncio.to_thread(_HANDLERS[action], params)
    except Exception as exc:
        return {"error": f"memory_tool {action} failed: {exc}"}
