"""
memory_tool — persistent cross-session memory for ANet agents.

Stores memories in ~/.anet/memory.json.
Each memory has: id, content, project_path (optional), tags, created_at.

Actions:
  save    — store a new memory
  search  — keyword-score search over stored memories
  list    — list all memories (optionally filtered by project)
  delete  — remove a memory by id
  clear   — remove ALL memories (use with caution)
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path

# ── Storage ────────────────────────────────────────────────────────────────────

_MEMORY_FILE = Path.home() / ".anet" / "memory.json"

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
                        "Short labels for easier retrieval, e.g. ['python', 'api', 'auth']. "
                        "Used by: save."
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

# ── Storage helpers ────────────────────────────────────────────────────────────

def _load() -> list[dict]:
    if not _MEMORY_FILE.exists():
        return []
    try:
        return json.loads(_MEMORY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _persist(memories: list[dict]) -> None:
    _MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _MEMORY_FILE.write_text(
        json.dumps(memories, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _short_id() -> str:
    return "mem-" + uuid.uuid4().hex[:6]


# ── Scoring ────────────────────────────────────────────────────────────────────

def _score(memory: dict, query: str) -> float:
    """Keyword overlap score — higher is more relevant."""
    words = set(re.findall(r"\w+", query.lower()))
    if not words:
        return 0.0
    haystack = " ".join([
        memory.get("content", ""),
        " ".join(memory.get("tags", [])),
        memory.get("project_path", "") or "",
    ]).lower()
    hits = sum(1 for w in words if w in haystack)
    return hits / len(words)  # 0.0 – 1.0


# ── Public search (importable as a sync function by graph_builder) ─────────────

def search_memories(
    query: str,
    project_path: str | None = None,
    max_results: int = 5,
) -> list[dict]:
    """
    Keyword search over stored memories.
    Called synchronously by the planner before building a plan.
    """
    memories = _load()
    if project_path:
        # Include global memories + project-scoped memories
        memories = [
            m for m in memories
            if not m.get("project_path") or m["project_path"] == project_path
        ]
    scored = [(m, _score(m, query)) for m in memories]
    scored = [(m, s) for m, s in scored if s > 0]
    scored.sort(key=lambda x: (-x[1], x[0].get("created_at", "")))
    return [m for m, _ in scored[:max_results]]


# ── Action handlers ────────────────────────────────────────────────────────────

def _do_save(params: dict) -> dict:
    content = (params.get("content") or "").strip()
    if not content:
        return {"error": "content is required for save"}

    memories  = _load()
    new_entry = {
        "id":           _short_id(),
        "content":      content,
        "project_path": params.get("project_path") or None,
        "tags":         params.get("tags") or [],
        "created_at":   datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    memories.append(new_entry)
    _persist(memories)
    return {
        "saved": True,
        "id":    new_entry["id"],
        "message": f"Memory saved (id={new_entry['id']}): {content[:80]}{'…' if len(content) > 80 else ''}",
    }


def _do_search(params: dict) -> dict:
    query       = (params.get("query") or "").strip()
    project     = params.get("project_path")
    max_results = int(params.get("max_results") or 10)

    if not query:
        return {"error": "query is required for search"}

    results = search_memories(query, project_path=project, max_results=max_results)
    if not results:
        return {"results": [], "count": 0, "message": f"No memories found for '{query}'."}

    return {
        "results": results,
        "count":   len(results),
    }


def _do_list(params: dict) -> dict:
    project     = params.get("project_path")
    max_results = int(params.get("max_results") or 50)
    memories    = _load()

    if project:
        memories = [m for m in memories if m.get("project_path") == project]

    memories = sorted(memories, key=lambda m: m.get("created_at", ""), reverse=True)
    memories = memories[:max_results]

    if not memories:
        scope = f" for project '{project}'" if project else ""
        return {"memories": [], "count": 0, "message": f"No memories stored{scope}."}

    return {"memories": memories, "count": len(memories)}


def _do_delete(params: dict) -> dict:
    mem_id   = (params.get("id") or "").strip()
    if not mem_id:
        return {"error": "id is required for delete"}

    memories = _load()
    before   = len(memories)
    memories = [m for m in memories if m["id"] != mem_id]

    if len(memories) == before:
        return {"error": f"No memory found with id '{mem_id}'"}

    _persist(memories)
    return {"deleted": True, "id": mem_id}


def _do_clear(params: dict) -> dict:
    memories = _load()
    count    = len(memories)
    _persist([])
    return {"cleared": True, "count": count, "message": f"Cleared {count} memory(ies)."}


# ── Dispatch ───────────────────────────────────────────────────────────────────

_HANDLERS = {
    "save":   _do_save,
    "search": _do_search,
    "list":   _do_list,
    "delete": _do_delete,
    "clear":  _do_clear,
}


async def run(params: dict) -> dict:
    action = (params.get("action") or "").strip()
    if not action:
        return {"error": "action is required (save | search | list | delete | clear)"}
    if action not in _HANDLERS:
        return {"error": f"Unknown action '{action}'. Valid: {', '.join(_HANDLERS)}"}
    try:
        return _HANDLERS[action](params)
    except Exception as exc:
        return {"error": f"memory_tool {action} failed: {exc}"}
