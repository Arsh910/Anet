"""
subconscious.py — Tier 1: the raw buffer.

The subconscious is a flat vector store of raw interaction snippets stored as-is,
with NO LLM involvement. It serves two roles:

  1. Cheap retrieval — recent raw context, fetched by embedding similarity.
  2. The recurrence signal — before adding a snippet we ask "how many existing
     snippets are semantically similar to this one?". When that count crosses a
     threshold, the orchestrator consolidates the cluster into durable memory
     (the one place an LLM is used). This is RecMem's cost lever: LLM work happens
     only for interactions that actually recur.
"""
from __future__ import annotations

import uuid
from typing import List, Optional

from anet.memory.recmem.interfaces import Embedding, SearchHit, VectorStore


class SubconsciousMemory:
    def __init__(self, embedder: Embedding, vector_store: VectorStore, collection: str):
        self.embedder = embedder
        self.vs = vector_store
        self.collection = collection

    def add(self, text: str, *, embedding: Optional[List[float]] = None,
            metadata: Optional[dict] = None) -> str:
        emb = embedding if embedding is not None else self.embedder.embed(text)
        rid = str(uuid.uuid4())
        self.vs.add(embedding=emb, payload=text, id=rid,
                    collection_name=self.collection, extra_payload=metadata or {})
        return rid

    def search(self, q_embedding: List[float], *, top_k: int = 10) -> List[SearchHit]:
        return self.vs.search(q_embedding=q_embedding, top_k=top_k,
                              collection_name=self.collection)

    def similar(self, q_embedding: List[float], *, threshold: float,
                top_k: int = 20) -> List[SearchHit]:
        """Existing snippets with similarity ≥ threshold — the recurrence cluster."""
        return [h for h in self.search(q_embedding, top_k=top_k) if h.score >= threshold]

    def remove(self, ids: List[str]) -> None:
        if ids:
            self.vs.remove(ids, self.collection)

    def list_all(self) -> List[SearchHit]:
        return self.vs.list_all(self.collection)

    def reset(self) -> None:
        self.vs.reset(self.collection)
