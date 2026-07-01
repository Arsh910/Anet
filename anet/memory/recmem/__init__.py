"""
anet.memory.recmem — a native implementation of RecMem (Recurrence-based Memory
Consolidation, arXiv 2605.16045): a 3-tier long-term memory (subconscious →
episodic → semantic) that invokes the LLM only when interactions recur.

Purpose-built for ANet and provider-injected: it runs on the embedder / vector
store / LLM client supplied by `anet.memory.adapters` (fastembed + chromadb + the
user's configured model), the same on-device infra the mem0 backend uses.

Public surface:
    RecMem                       — the orchestrator (observe / remember / recall)
    Embedding, VectorStore, LLMClient, SearchHit, Message, LLMResponse — contracts
"""
from anet.memory.recmem.interfaces import (
    Embedding, VectorStore, SearchHit, LLMClient, Message, LLMResponse,
    LLMError, EmptyResponseException,
)
from anet.memory.recmem.recmem import RecMem

__all__ = [
    "RecMem",
    "Embedding", "VectorStore", "SearchHit",
    "LLMClient", "Message", "LLMResponse", "LLMError", "EmptyResponseException",
]
