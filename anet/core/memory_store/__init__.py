"""
memory_store — long-term memory for ANet, with a pluggable backend.

Callers use `from anet.core import memory_store; memory_store.add(...)` exactly as
before; this package re-exports the public API of the backend selected by
`memory.backend` in anet.config.yaml:

    recmem  (default) → anet.core.memory_store.recmem_store   — native 3-tier
                        recurrence memory (subconscious→episodic→semantic), LLM
                        consolidation only on recurrence. Data: <home>/memory/recmem/.
    mem0              → anet.core.memory_store.mem0_store      — mem0-backed flat
                        vector store. Data: <home>/memory/chroma/.

Both expose the identical function surface (add / add_conversation / search /
get_all / delete / clear / standing_memories / preferences / classify /
get_memory / is_available / reset), so every caller — memory_tool, the
orchestrator's per-agent injection, app.py — is backend-agnostic.

Backend is resolved once at import. A pack/config switch that changes the backend
takes effect on the next process start (like the other pack-scoped settings).
"""
from __future__ import annotations


def _backend_name() -> str:
    try:
        from anet.core.config_loader import load
        return str(((load() or {}).get("memory") or {}).get("backend") or "recmem").lower()
    except Exception:
        return "recmem"


_BACKEND = _backend_name()

if _BACKEND == "mem0":
    from anet.core.memory_store import mem0_store as _b
else:
    from anet.core.memory_store import recmem_store as _b

# ── Re-export the public API of the selected backend ──────────────────────────
get_memory        = _b.get_memory
is_available      = _b.is_available
reset             = _b.reset
add               = _b.add
add_conversation  = _b.add_conversation
delete            = _b.delete
clear             = _b.clear
search            = _b.search
get_all           = _b.get_all
standing_memories = _b.standing_memories
preferences       = _b.preferences
classify          = _b.classify

# Introspection: which backend is live (used by /memory display + diagnostics).
backend_name = _BACKEND
