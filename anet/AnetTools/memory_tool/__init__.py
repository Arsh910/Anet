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
import os
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
                        "Tag a standing preference (coding style, tone, conventions) with "
                        "'preference' so it is applied automatically on relevant tasks even when "
                        "its words don't match the request; add an agent name like 'code_agent' "
                        "to scope it to that agent, or leave it global. Used by: save."
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


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file + rename so a crash/concurrent write can never leave
    a truncated or corrupt store behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _persist(memories: list[dict]) -> None:
    _atomic_write(_MEMORY_FILE, json.dumps(memories, indent=2, ensure_ascii=False))


def _short_id() -> str:
    return "mem-" + uuid.uuid4().hex[:6]


# ── Scoring ────────────────────────────────────────────────────────────────────

# Very common function words — ignored when scoring so "a/the/of/is" don't make
# every memory look relevant.
_STOPWORDS = {
    "the", "and", "for", "that", "this", "are", "with", "you", "your", "from",
    "have", "has", "had", "was", "were", "will", "would", "can", "could", "should",
    "what", "which", "who", "how", "all", "any", "but", "not", "use", "using",
    "make", "want", "need", "get", "got", "into", "over", "out", "about",
}


def _tokens(text: str) -> set[str]:
    """Meaningful words: length > 2 and not a common stopword."""
    return {
        w for w in re.findall(r"\w+", text.lower())
        if len(w) > 2 and w not in _STOPWORDS
    }


def _score(memory: dict, query: str) -> float:
    """Keyword overlap score (0–1) — fraction of meaningful query words that
    appear as whole words in the memory's content / tags / project path."""
    qwords = _tokens(query)
    if not qwords:
        return 0.0
    hwords = _tokens(" ".join([
        memory.get("content", ""),
        " ".join(memory.get("tags", [])),
        memory.get("project_path", "") or "",
    ]))
    return len(qwords & hwords) / len(qwords)


# ── Optional semantic search (Phase A) ─────────────────────────────────────────
# If `fastembed` is installed, search BLENDS keyword overlap with embedding
# similarity, so paraphrases match too ("avg of a list" ↔ "mean of numbers").
# Without it, search is pure keyword — the default, zero extra dependencies.
#   pip install fastembed
# Vectors are cached in a sidecar file next to memory.json so the memory store
# stays human-readable.

_EMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_SEM_FLOOR        = 0.55   # min cosine for a semantic-only match to count (tunable)
_embedder         = None
_embedder_tried   = False


def _get_embedder():
    """Lazy singleton. Returns the fastembed model, or None if unavailable."""
    global _embedder, _embedder_tried
    if _embedder_tried:
        return _embedder
    _embedder_tried = True
    try:
        from fastembed import TextEmbedding
        _embedder = TextEmbedding(model_name=_EMBED_MODEL_NAME)
    except Exception:
        _embedder = None   # not installed / model download failed → keyword fallback
    return _embedder


def _embed(texts: list[str]) -> list[list[float]] | None:
    emb = _get_embedder()
    if emb is None:
        return None
    try:
        return [list(map(float, v)) for v in emb.embed(texts)]
    except Exception:
        return None


def _vector_file() -> Path:
    # Computed dynamically so it follows _MEMORY_FILE (tests monkeypatch that).
    return _MEMORY_FILE.parent / "memory_vectors.json"


def _load_vectors() -> dict:
    f = _vector_file()
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _persist_vectors(vectors: dict) -> None:
    f = _vector_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(vectors), encoding="utf-8")


def _cosine(a: list[float], b: list[float]) -> float:
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na  = math.sqrt(sum(x * x for x in a))
    nb  = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _embed_text_for(memory: dict) -> str:
    return " ".join([
        memory.get("content", ""),
        " ".join(memory.get("tags", [])),
    ]).strip()


def _semantic_scores(query: str, memories: list[dict]) -> dict:
    """Return {id: cosine} for memories. Computes+caches any missing vectors.
    Empty dict when embeddings are unavailable (→ pure keyword)."""
    if not memories or _get_embedder() is None:
        return {}
    qv = _embed([query])
    if not qv:
        return {}
    qvec    = qv[0]
    vectors = _load_vectors()
    missing = [m for m in memories if m["id"] not in vectors]
    if missing:
        new = _embed([_embed_text_for(m) for m in missing])
        if new:
            for m, v in zip(missing, new):
                vectors[m["id"]] = v
            _persist_vectors(vectors)
    return {
        m["id"]: _cosine(qvec, vectors[m["id"]])
        for m in memories if m["id"] in vectors
    }


# ── Public search (importable as a sync function by the engine) ────────────────

def search_memories(
    query: str,
    project_path: str | None = None,
    max_results: int = 5,
    min_score: float = 0.0,
) -> list[dict]:
    """
    Keyword (+ optional semantic) search over stored memories.
    Called synchronously by the planner and by agent memory injection.
    `min_score` drops weak matches — raise it where precision matters (agent
    injection) so a single generic word ("function") doesn't pull in a doc.
    """
    memories = _load()
    if project_path:
        # Include global memories + project-scoped memories
        memories = [
            m for m in memories
            if not m.get("project_path") or m["project_path"] == project_path
        ]

    sem = _semantic_scores(query, memories)   # {} when fastembed isn't installed

    scored = []
    for m in memories:
        kw = _score(m, query)                          # keyword always counts
        s  = sem.get(m["id"], 0.0)                      # semantic only above the floor
        combined = kw + (s if s >= _SEM_FLOOR else 0.0)
        if combined > 0 and combined >= min_score:
            scored.append((m, combined))
    scored.sort(key=lambda x: (-x[1], x[0].get("created_at", "")))
    return [m for m, _ in scored[:max_results]]


def preference_memories() -> list[dict]:
    """All memories tagged 'preference' — standing prefs (coding style, tone, …).
    These are injected by agent-type (Phase B), independent of keyword/semantic
    relevance, because a style preference rarely shares words with the task."""
    return [
        m for m in _load()
        if "preference" in [t.lower() for t in m.get("tags", [])]
    ]


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
    vectors = _load_vectors()
    if vectors.pop(mem_id, None) is not None:
        _persist_vectors(vectors)
    return {"deleted": True, "id": mem_id}


def _do_clear(params: dict) -> dict:
    memories = _load()
    count    = len(memories)
    _persist([])
    _persist_vectors({})
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
