"""
recmem_store.py — the RecMem backend behind the memory_store API.

Maps ANet's long-term-memory contract onto the native RecMem engine
(anet.memory.recmem) plus its provider adapters (anet.memory.adapters):

    add(classify_content=True)  explicit "remember this" → stored VERBATIM in the
                                semantic tier, tagged with the LLM-classified
                                category / applies_to / always_inject metadata. This
                                is what powers standing-preference injection.
    add_conversation(...)       background pass → each turn is observed() into the
                                subconscious; RecMem consolidates a cluster into
                                episodic + semantic memory ONLY when it recurs.
    search / get_all            retrieve across tiers; get_all lists durable memory.
    standing_memories           the always_inject subset (by applies_to), read
                                straight off the semantic tier's metadata.
    classify                    LLM category/agent labelling (categories from config).

The always_inject / applies_to / category / project_path features RecMem has no
native notion of are carried entirely in per-memory metadata, so no feature the
mem0 backend offered is lost.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import warnings
from datetime import datetime, timezone
from typing import Any, Optional

from anet.core import memory_common as mc

# ── Keep first-run model download + library chatter OUT of the TUI ──────────────
# RecMem shares mem0's heavy deps (chromadb + fastembed/onnxruntime). Silence the
# same first-run noise here, since the mem0 module isn't imported on this backend.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("POSTHOG_DISABLED", "True")
for _w in ("chromadb", "fastembed", "huggingface_hub", "onnxruntime", "posthog"):
    for _cat in (DeprecationWarning, UserWarning, FutureWarning):
        warnings.filterwarnings("ignore", category=_cat, module=rf"{_w}.*")
warnings.filterwarnings("ignore", message=r".*unauthenticated requests.*")
warnings.filterwarnings("ignore", message=r".*HF_TOKEN.*")
for _noisy in ("chromadb", "httpx", "httpcore", "posthog", "opentelemetry",
               "huggingface_hub", "fastembed", "urllib3", "filelock"):
    logging.getLogger(_noisy).setLevel(logging.ERROR)

logger = logging.getLogger(__name__)

_NAMESPACE = "anet"
_rm: Any = None            # cached RecMem instance
_init_tried = False
_init_error = ""


# ── Lifecycle ─────────────────────────────────────────────────────────────────

def get_memory() -> Any:
    """Return the cached RecMem instance, or None if it can't be built. First call
    is slow (constructs Chroma + downloads the fastembed model once). Never raises —
    on failure it records `_init_error` and returns None so callers degrade quietly."""
    global _rm, _init_tried, _init_error
    if _rm is not None:
        return _rm
    if _init_tried:
        return None
    _init_tried = True
    try:
        import contextlib, io
        with warnings.catch_warnings(), contextlib.redirect_stderr(io.StringIO()):
            warnings.simplefilter("ignore")
            from anet.memory.adapters import (
                FastEmbedEmbedding, ChromaVectorStore, AnetLLMClient,
            )
            from anet.memory.recmem import RecMem
            persist = str(mc.memory_home() / "recmem")
            cfg = mc.memory_cfg()
            _rm = RecMem(
                embedder=FastEmbedEmbedding(),
                vector_store=ChromaVectorStore(persist),
                llm_client=AnetLLMClient(stage="memory"),
                namespace=_NAMESPACE,
                recurrence_threshold=int(cfg.get("recurrence_threshold", 2)),
                sim_threshold=float(cfg.get("sim_threshold", 0.72)),
                dedup_threshold=float(cfg.get("dedup_threshold", 0.92)),
            )
    except Exception as exc:
        _init_error = str(exc)
        print(f"[recmem_store] RecMem unavailable ({exc}); long-term memory disabled.",
              file=sys.stderr)
        _rm = None
    return _rm


def is_available() -> bool:
    return get_memory() is not None


def reset() -> None:
    """Drop the cached instance so the next call rebuilds it (e.g. after a config
    switch). Does NOT erase stored memories — that's `clear()`."""
    global _rm, _init_tried, _init_error
    _rm, _init_tried, _init_error = None, False, ""


# ── Record shaping ──────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_list(v: Any) -> list:
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.strip():
        try:
            d = json.loads(v)
            return d if isinstance(d, list) else [v]
        except Exception:
            return [v]
    return []


def _adapt(hit, *, default_category: str = "fact") -> dict:
    """RecMem SearchHit → ANet's memory record shape (matches the mem0 backend)."""
    meta = hit.extra_payload or {}
    tags = _decode_list(meta.get("tags"))
    applies = _decode_list(meta.get("applies_to")) or ["all"]
    always = bool(meta.get("always_inject"))
    category = meta.get("category") or ("preference" if always else default_category)
    return {
        "id":           hit.id,
        "content":      hit.content or "",
        "tags":         tags,
        "project_path": meta.get("project_path") or None,
        "kind":         meta.get("kind") or "fact",
        "category":     category,
        "applies_to":   [str(a) for a in applies],
        "always_inject": always,
        "created_at":   mc.fmt_created(meta.get("created_at")),
        "score":        hit.score,
    }


# ── Classification (LLM-driven; categories from config) ────────────────────────

def classify(content: str) -> dict:
    """Label a memory: its category + which agents it applies to.
    Returns {category, applies_to, always_inject}. Best-effort — any failure falls
    back to a relevance-only 'fact'."""
    fallback = {"category": "fact", "applies_to": ["all"], "always_inject": False}
    rm = get_memory()
    if rm is None or not (content or "").strip():
        return fallback

    cats = mc.categories()
    cat_map = {c["name"]: c["always_inject"] for c in cats}
    cat_lines = "\n".join(f'- {c["name"]}: {c["description"]}' for c in cats)
    agents = mc.known_agents()
    system = (
        "You label a single memory for an AI assistant's long-term store. Pick the "
        "ONE category that fits best, and list which agents it should influence. "
        'Respond with ONLY a JSON object: {"category": "<one category name>", '
        '"applies_to": ["all"] or [agent names]}. Use ["all"] unless the memory is '
        "clearly specific to particular agents."
    )
    user = (f"Categories:\n{cat_lines}\n\nAgents: {', '.join(agents)}\n\n"
            f'Memory: "{content.strip()}"\n\nReturn the JSON.')
    try:
        from anet.memory.recmem import Message
        resp = rm.llm.complete(
            [Message("system", system), Message("user", user)],
            json_mode=True, temperature=0.0, max_tokens=200,
        )
        data = mc.parse_json(resp.content)
        category = str(data.get("category") or "").strip()
        if category not in cat_map:
            return fallback
        applies = data.get("applies_to") or ["all"]
        if not isinstance(applies, list) or not applies:
            applies = ["all"]
        applies = [str(a) for a in applies if str(a) == "all" or str(a) in agents] or ["all"]
        return {"category": category, "applies_to": applies,
                "always_inject": bool(cat_map[category])}
    except Exception:
        return fallback


# ── Writes ──────────────────────────────────────────────────────────────────────

def add(
    content: str,
    *,
    tags: list[str] | None = None,
    project_path: str | None = None,
    kind: str = "fact",
    infer: bool = True,           # accepted for API parity; explicit saves store verbatim
    classify_content: bool = False,
) -> dict:
    """Store one explicit memory VERBATIM in the durable (semantic) tier. When
    `classify_content=True` the LLM labels its category / applies_to / always_inject
    so standing-preference injection works. `infer` is accepted for API compatibility
    with the mem0 backend but not used (RecMem's inference happens on the recurrence
    pass via add_conversation, not on a single explicit save)."""
    rm = get_memory()
    if rm is None:
        return {"error": f"memory unavailable: {_init_error or 'recmem not initialised'}"}
    content = (content or "").strip()
    if not content:
        return {"error": "content is required"}

    meta: dict = {"kind": kind, "created_at": _now_iso()}
    if tags:
        meta["tags"] = json.dumps(list(tags))
    if project_path:
        meta["project_path"] = project_path
    if classify_content:
        label = classify(content)
        meta["category"]      = label["category"]
        meta["applies_to"]    = json.dumps(label["applies_to"])
        meta["always_inject"] = label["always_inject"]

    try:
        mem_id = rm.remember(content, metadata=meta, tier="semantic", dedup=True)
    except Exception as exc:
        return {"error": f"memory add failed: {exc}"}
    return {
        "saved": True,
        "id": mem_id,
        "results": [{"id": mem_id, "memory": content}],
        "message": f"Memory saved: {content[:80]}{'…' if len(content) > 80 else ''}",
    }


def add_conversation(messages: list[dict], *, run_id: str | None = None) -> dict:
    """Observe recent conversation turns into the subconscious. RecMem consolidates a
    cluster into episodic/semantic memory ONLY when it recurs — so most calls spend
    zero LLM tokens. Returns {results:[...]} with per-turn consolidation info."""
    rm = get_memory()
    if rm is None:
        return {"skipped": "memory unavailable"}
    turns = [
        (m.get("role", "user"), (m.get("content") or "").strip())
        for m in messages
        if (m.get("content") or "").strip() and m.get("role") in ("user", "assistant")
    ]
    if not turns:
        return {"skipped": "no content"}
    meta = {"kind": "episode"}
    if run_id:
        meta["run_id"] = run_id
    results = []
    try:
        for role, text in turns:
            r = rm.observe(f"{role}: {text}", metadata=dict(meta))
            if r.get("consolidated"):
                results.append(r)
        return {"results": results}
    except Exception as exc:
        return {"skipped": f"observation failed: {exc}"}


def delete(memory_id: str) -> dict:
    rm = get_memory()
    if rm is None:
        return {"error": "memory unavailable"}
    try:
        rm.delete(memory_id)
        return {"deleted": True, "id": memory_id}
    except Exception as exc:
        return {"error": f"delete failed: {exc}"}


def clear() -> dict:
    rm = get_memory()
    if rm is None:
        return {"error": "memory unavailable"}
    try:
        n = len(rm.durable_memories()) + len(rm.sub.list_all())
        rm.reset()
        return {"cleared": True, "count": n}
    except Exception as exc:
        return {"error": f"clear failed: {exc}"}


# ── Reads ─────────────────────────────────────────────────────────────────────

def search(
    query: str,
    *,
    project_path: str | None = None,
    limit: int = 5,
    min_score: float = 0.0,
) -> list[dict]:
    """Semantic search across all tiers, most-relevant first. `project_path` keeps
    global memories plus those scoped to that project."""
    rm = get_memory()
    if rm is None or not (query or "").strip():
        return []
    try:
        hits = rm.recall(query, top_k=max(limit * 3, limit), threshold=min_score or 0.0)
    except Exception:
        return []
    out: list[dict] = []
    for h in hits:
        rec = _adapt(h)
        if project_path:
            pp = rec.get("project_path")
            if pp and pp != project_path:
                continue
        out.append(rec)
    return out[:limit]


def get_all(project_path: str | None = None, limit: int = 200) -> list[dict]:
    """All durable memories (semantic + episodic tiers)."""
    rm = get_memory()
    if rm is None:
        return []
    try:
        hits = rm.durable_memories()
    except Exception:
        return []
    out = [_adapt(h) for h in hits]
    if project_path:
        out = [m for m in out if not m.get("project_path") or m["project_path"] == project_path]
    return out[:limit]


def standing_memories(agent_name: str | None = None) -> list[dict]:
    """Always_inject memories — standing instructions injected regardless of keyword
    relevance. Scoped to `agent_name` via each memory's `applies_to`."""
    rm = get_memory()
    if rm is None:
        return []
    out = []
    try:
        hits = rm.sem.list_all()   # explicit saves (with classification) live here
    except Exception:
        return []
    for h in hits:
        rec = _adapt(h)
        if not rec.get("always_inject"):
            continue
        applies = rec.get("applies_to") or ["all"]
        if agent_name and "all" not in applies and agent_name not in applies:
            continue
        out.append(rec)
    return out


def preferences() -> list[dict]:
    return standing_memories()
