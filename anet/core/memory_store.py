"""
memory_store.py — long-term memory for ANet, backed by mem0.

This is the single backend for cross-session memory. It wraps a locally-running
mem0 `Memory` instance:

    • vector store : Chroma, persisted at  <home>/memory/chroma   (no server)
    • embedder     : fastembed (BAAI/bge-small-en-v1.5), runs locally/offline
    • LLM          : the user's configured provider (OpenRouter by default),
                     used only for intelligent fact extraction / de-duplication

Nothing here talks to a hosted service: embeddings are computed on-device and the
vector DB is a local folder. The LLM is only invoked when `infer=True` (extracting
salient facts from a conversation, or de-duplicating an explicit save); every write
falls back to verbatim storage if the model is unavailable, so memory never breaks
because a key is missing or a free model is flaky.

The public helpers here keep the SAME shape the rest of ANet already expects
(`id`, `content`, `tags`, `project_path`, `created_at`), so `memory_tool`, the
engine and the orchestrator consume them unchanged.

Identity model (mem0 fields):
    user_id = "anet"          → the single local user; everything is recallable
    metadata.kind             → "fact" | "preference" | "episode"
    metadata.tags             → list[str] (carries the legacy 'preference' marker)
    metadata.project_path     → optional project scope
    run_id                    → session/thread id for episodic summaries
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

# mem0's deps are chatty (chroma hybrid-search notice, spaCy-not-installed, posthog).
# Keep the CLI clean — these are all expected, non-fatal.
for _noisy in ("mem0", "chromadb", "httpx", "posthog", "opentelemetry"):
    logging.getLogger(_noisy).setLevel(logging.ERROR)

_USER_ID = "anet"
_EMBED_MODEL = "BAAI/bge-small-en-v1.5"

# Default memory categories. Used to CLASSIFY each explicit save (by the LLM, not by
# hardcoded tag matching) and to decide retrieval behaviour. Overridable per pack via
# `memory.categories` in anet.config.yaml — categories are data, not code.
#   always_inject: surface this memory whenever its `applies_to` agent runs, even if
#                  it shares no keywords with the task (how standing preferences reach
#                  an agent). Non-always_inject categories are retrieved by relevance.
_DEFAULT_CATEGORIES = [
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

# Steers what mem0 extracts from conversation (injected into its extraction prompt).
# Overridable via `memory.instructions` in the pack config.
_DEFAULT_INSTRUCTIONS = (
    "Capture durable facts that help future sessions: the user's identity and standing "
    "preferences, project locations and tech stacks, important decisions, environment and "
    "setup details, and explicit instructions. Ignore small talk and transient task chatter."
)

# ANet provider name → how mem0 should reach it. OpenRouter is OpenAI-compatible,
# and mem0's 'openai' provider auto-routes to OpenRouter when OPENROUTER_API_KEY is
# present, so we use 'openai' for it and pass the OpenRouter base URL.
_OPENROUTER_BASE = "https://openrouter.ai/api/v1"

_memory: Any = None          # cached mem0 Memory instance
_init_tried = False          # so a failed init isn't retried every call
_init_error = ""             # last init failure, for diagnostics


# ── Configuration ───────────────────────────────────────────────────────────

def _memory_home() -> Path:
    from anet.core import paths
    d = paths.home() / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _llm_config() -> dict:
    """mem0 LLM block, derived from the pack's memory/manager config.

    Defaults to the manager's model/provider; a `memory:` block in anet.config.yaml
    may override with its own `model`/`provider`. Returns a mem0 `llm` config dict.
    """
    model = provider = None
    try:
        from anet.core.config_loader import load, manager_config
        mem = (load().get("memory") or {})
        model, provider = mem.get("model"), mem.get("provider")
        if not model or not provider:
            mgr = manager_config()
            model = model or mgr.get("model")
            provider = provider or mgr.get("provider")
    except Exception:
        pass

    provider = (provider or "openrouter").lower()
    model = model or "nex-agi/nex-n2-pro:free"

    # Normalise legacy aliases.
    if provider in ("claude",):
        provider = "anthropic"

    common = {"model": model, "temperature": 0.1, "max_tokens": 2000}

    # IMPORTANT: mem0 builds the LLM client when the Memory is constructed, so the
    # api_key must be non-empty even if the user hasn't set a key yet — otherwise
    # the whole memory subsystem (including search, which needs NO LLM) fails to
    # initialise. We pass a placeholder so construction always succeeds; only an
    # actual extraction call (infer=True) then fails, and add() falls back to
    # storing verbatim. The user sets the real key with /keys.
    def _key(env: str) -> str:
        return os.getenv(env) or "set-with-/keys"

    if provider in ("openrouter",):
        # OpenRouter is OpenAI-compatible. mem0's openai provider auto-routes when
        # OPENROUTER_API_KEY is in the env; we also set openai_base_url + api_key so
        # construction succeeds (and points at OpenRouter) before a key is added.
        return {"provider": "openai", "config": {
            **common,
            "api_key": _key("OPENROUTER_API_KEY"),
            "openrouter_base_url": _OPENROUTER_BASE,
            "openai_base_url": _OPENROUTER_BASE,
        }}
    if provider in ("openai",):
        return {"provider": "openai",
                "config": {**common, "api_key": _key("OPENAI_API_KEY")}}
    if provider in ("google", "vertex_google"):
        return {"provider": "gemini",
                "config": {**common, "api_key": _key("GOOGLE_API_KEY")}}
    if provider in ("anthropic", "vertex_anthropic", "vertex_claude"):
        return {"provider": "anthropic",
                "config": {**common, "api_key": _key("ANTHROPIC_API_KEY")}}

    # Unknown provider → best-effort OpenRouter (the clean-ship default).
    return {"provider": "openai", "config": {
        **common,
        "api_key": _key("OPENROUTER_API_KEY"),
        "openrouter_base_url": _OPENROUTER_BASE,
        "openai_base_url": _OPENROUTER_BASE,
    }}


def _memory_cfg() -> dict:
    try:
        from anet.core.config_loader import load
        return load().get("memory", {}) or {}
    except Exception:
        return {}


def _categories() -> list[dict]:
    """Memory categories from the pack config, else the built-in defaults."""
    cats = _memory_cfg().get("categories")
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
    return _DEFAULT_CATEGORIES


def _category_map() -> dict[str, bool]:
    """name → always_inject, for fast lookup at retrieval/adapt time."""
    return {c["name"]: c["always_inject"] for c in _categories()}


def _custom_instructions() -> str:
    return str(_memory_cfg().get("instructions") or _DEFAULT_INSTRUCTIONS)


def _known_agents() -> list[str]:
    """Built-in agent names the classifier may scope a memory to."""
    try:
        from anet.AnetAgents.agents_config import AGENTS
        return [a.get("name") for a in AGENTS if a.get("name")]
    except Exception:
        return ["research_agent", "code_agent", "file_agent",
                "computer_agent", "checker_agent"]


def _build_config() -> dict:
    home = _memory_home()
    return {
        "vector_store": {
            "provider": "chroma",
            "config": {"collection_name": "anet", "path": str(home / "chroma")},
        },
        "embedder": {
            "provider": "fastembed",
            "config": {"model": _EMBED_MODEL},
        },
        "llm": _llm_config(),
        "history_db_path": str(home / "history.db"),
        "custom_instructions": _custom_instructions(),
    }


def get_memory() -> Any:
    """Return the cached mem0 Memory instance, or None if mem0 is unavailable.

    First call is slow: it constructs Chroma and downloads the fastembed model
    (~130 MB) once. Subsequent calls are instant. Never raises — on any failure it
    records the reason in `_init_error` and returns None so callers degrade quietly.
    """
    global _memory, _init_tried, _init_error
    if _memory is not None:
        return _memory
    if _init_tried:
        return None
    _init_tried = True
    try:
        from mem0 import Memory
        _memory = Memory.from_config(_build_config())
    except Exception as exc:
        _init_error = str(exc)
        print(f"[memory_store] mem0 unavailable ({exc}); long-term memory disabled.",
              file=sys.stderr)
        _memory = None
    return _memory


def is_available() -> bool:
    return get_memory() is not None


def reset() -> None:
    """Drop the cached instance so the next call rebuilds it (e.g. after a pack/
    config switch that changes the memory model)."""
    global _memory, _init_tried, _init_error
    _memory, _init_tried, _init_error = None, False, ""


# ── Result adapter ────────────────────────────────────────────────────────────

def _fmt_created(raw: Any) -> str:
    """mem0 returns ISO-8601; present it as 'YYYY-MM-DD HH:MM' like the old store."""
    if not raw:
        return ""
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(raw)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(raw)[:16].replace("T", " ")


def _adapt(item: dict) -> dict:
    """Normalise one mem0 record into ANet's memory shape."""
    meta = item.get("metadata") or {}
    tags = meta.get("tags") or []
    # Back-compat: legacy memories tagged 'preference' (pre-classifier) count as a
    # standing preference even without the new metadata.
    always = bool(meta.get("always_inject")) or ("preference" in [t.lower() for t in tags])
    return {
        "id":           item.get("id"),
        "content":      item.get("memory") or item.get("content") or "",
        "tags":         tags,
        "project_path": meta.get("project_path") or None,
        "kind":         meta.get("kind") or "fact",
        "category":     meta.get("category") or ("preference" if always else "fact"),
        "applies_to":   meta.get("applies_to") or ["all"],
        "always_inject": always,
        "created_at":   _fmt_created(item.get("created_at")),
        "score":        item.get("score"),
    }


def _results(raw: Any) -> list[dict]:
    """mem0 add/search/get_all all return {'results': [...]} (or a bare list)."""
    if isinstance(raw, dict):
        return raw.get("results") or []
    return raw or []


# ── Classification (LLM-driven, replaces hardcoded tag rules) ──────────────────

def _parse_json(text: str) -> dict:
    import json, re
    text = (text or "").strip()
    for candidate in (text, ):
        try:
            return json.loads(candidate)
        except Exception:
            pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return {}


def classify(content: str) -> dict:
    """Ask the configured LLM to label a memory: which category it belongs to and
    which agents it applies to. Returns {category, applies_to, always_inject}.

    This replaces the old hardcoded `if 'preference' in tags` rule and the agent's
    self-tagging convention — the categories come from config, the decision from the
    model. Best-effort: any failure falls back to a relevance-only 'fact'.
    """
    fallback = {"category": "fact", "applies_to": ["all"], "always_inject": False}
    mem = get_memory()
    if mem is None or not (content or "").strip():
        return fallback

    cats = _categories()
    cat_map = {c["name"]: c["always_inject"] for c in cats}
    cat_lines = "\n".join(f'- {c["name"]}: {c["description"]}' for c in cats)
    agents = _known_agents()

    system = (
        "You label a single memory for an AI assistant's long-term store. "
        "Pick the ONE category that fits best, and list which agents it should "
        "influence. Respond with ONLY a JSON object: "
        '{"category": "<one category name>", "applies_to": ["all"] or [agent names]}. '
        'Use ["all"] unless the memory is clearly specific to particular agents.'
    )
    user = (
        f"Categories:\n{cat_lines}\n\n"
        f"Agents: {', '.join(agents)}\n\n"
        f'Memory: "{content.strip()}"\n\n'
        "Return the JSON."
    )
    try:
        raw = mem.llm.generate_response(
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            response_format={"type": "json_object"},
        )
        data = _parse_json(raw if isinstance(raw, str) else "")
        category = str(data.get("category") or "").strip()
        if category not in cat_map:
            return fallback
        applies = data.get("applies_to") or ["all"]
        if not isinstance(applies, list) or not applies:
            applies = ["all"]
        applies = [str(a) for a in applies]
        # Keep only known agents (or the wildcard); empty → all.
        applies = [a for a in applies if a == "all" or a in agents] or ["all"]
        return {"category": category, "applies_to": applies,
                "always_inject": bool(cat_map[category])}
    except Exception:
        return fallback


# ── Writes ──────────────────────────────────────────────────────────────────

def add(
    content: str,
    *,
    tags: list[str] | None = None,
    project_path: str | None = None,
    kind: str = "fact",
    infer: bool = True,
    classify_content: bool = False,
) -> dict:
    """Store one memory.

    With `infer=True` mem0 uses the LLM to extract/merge/de-duplicate against
    existing memories — its core advantage. If that call fails (no key, flaky free
    model, parse error) we transparently retry with `infer=False` so an explicit
    save is never silently lost.

    With `classify_content=True` (explicit saves), the LLM labels the memory's
    category + which agents it applies to, stored in metadata — no hardcoded tags.
    """
    mem = get_memory()
    if mem is None:
        return {"error": f"memory unavailable: {_init_error or 'mem0 not initialised'}"}

    content = (content or "").strip()
    if not content:
        return {"error": "content is required"}

    metadata: dict = {"kind": kind}
    if tags:
        metadata["tags"] = list(tags)
    if project_path:
        metadata["project_path"] = project_path

    if classify_content:
        label = classify(content)
        metadata["category"]      = label["category"]
        metadata["applies_to"]    = label["applies_to"]
        metadata["always_inject"] = label["always_inject"]

    def _do(use_infer: bool) -> dict:
        return mem.add(content, user_id=_USER_ID, metadata=metadata, infer=use_infer)

    try:
        raw = _do(infer)
    except Exception as exc:
        if not infer:
            return {"error": f"memory add failed: {exc}"}
        # LLM-backed extraction raised → store verbatim so nothing is lost.
        try:
            raw = _do(False)
        except Exception as exc2:
            return {"error": f"memory add failed: {exc2}"}

    added = _results(raw)
    # mem0 sometimes swallows an LLM failure (e.g. a 401) under infer=True and
    # returns no results rather than raising. For an explicit add that means the
    # content vanished — so retry verbatim to guarantee it's stored.
    if infer and not added:
        try:
            raw = _do(False)
            added = _results(raw)
        except Exception:
            pass
    first = added[0] if added else {}
    return {
        "saved": True,
        "id": first.get("id"),
        "results": added,
        "message": f"Memory saved: {content[:80]}{'…' if len(content) > 80 else ''}",
    }


def add_conversation(messages: list[dict], *, run_id: str | None = None) -> dict:
    """Feed recent conversation turns to mem0 for LLM-driven fact extraction.

    This replaces the old standalone memory_agent: mem0 decides what is worth
    remembering, de-duplicates against the store, and resolves contradictions —
    all in one call. Requires a working LLM; returns {'skipped': ...} otherwise.
    """
    mem = get_memory()
    if mem is None:
        return {"skipped": "memory unavailable"}

    turns = [
        {"role": m.get("role", "user"), "content": (m.get("content") or "").strip()}
        for m in messages
        if (m.get("content") or "").strip() and m.get("role") in ("user", "assistant")
    ]
    if not turns:
        return {"skipped": "no content"}

    try:
        raw = mem.add(turns, user_id=_USER_ID, run_id=run_id,
                      metadata={"kind": "episode"}, infer=True)
        return {"results": _results(raw)}
    except Exception as exc:
        # No verbatim fallback here: dumping raw dialogue in as "memories" would
        # pollute the store. Extraction simply waits for the next interval.
        return {"skipped": f"extraction failed: {exc}"}


def delete(memory_id: str) -> dict:
    mem = get_memory()
    if mem is None:
        return {"error": "memory unavailable"}
    try:
        mem.delete(memory_id)
        return {"deleted": True, "id": memory_id}
    except Exception as exc:
        return {"error": f"delete failed: {exc}"}


def clear() -> dict:
    mem = get_memory()
    if mem is None:
        return {"error": "memory unavailable"}
    try:
        existing = _results(mem.get_all(filters={"user_id": _USER_ID}, top_k=10000))
        for item in existing:
            try:
                mem.delete(item.get("id"))
            except Exception:
                pass
        return {"cleared": True, "count": len(existing)}
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
    """Semantic search over stored memories, newest-relevant first.

    `project_path` keeps global memories plus those scoped to that project.
    `min_score` drops weak matches (mem0 similarity score, 0–1).
    """
    mem = get_memory()
    if mem is None or not (query or "").strip():
        return []
    try:
        raw = mem.search(query, filters={"user_id": _USER_ID},
                         top_k=max(limit * 3, limit), threshold=min_score or 0.0)
    except Exception:
        return []

    out: list[dict] = []
    for item in _results(raw):
        rec = _adapt(item)
        if min_score and (rec.get("score") or 0.0) < min_score:
            continue
        if project_path:
            pp = rec.get("project_path")
            if pp and pp != project_path:
                continue
        out.append(rec)
    return out[:limit]


def get_all(project_path: str | None = None, limit: int = 200) -> list[dict]:
    mem = get_memory()
    if mem is None:
        return []
    try:
        raw = mem.get_all(filters={"user_id": _USER_ID}, top_k=limit)
    except Exception:
        return []
    out = [_adapt(i) for i in _results(raw)]
    if project_path:
        out = [m for m in out if not m.get("project_path") or m["project_path"] == project_path]
    return out


def standing_memories(agent_name: str | None = None) -> list[dict]:
    """Memories whose category is `always_inject` — standing instructions that
    should reach an agent regardless of keyword relevance. If `agent_name` is given,
    keep only those whose `applies_to` is "all" or includes that agent.
    """
    out = []
    for m in get_all(limit=500):
        if not m.get("always_inject"):
            continue
        applies = m.get("applies_to") or ["all"]
        if agent_name and "all" not in applies and agent_name not in applies:
            continue
        out.append(m)
    return out


# Back-compat alias: older call sites used preference_memories()/preferences().
def preferences() -> list[dict]:
    return standing_memories()
