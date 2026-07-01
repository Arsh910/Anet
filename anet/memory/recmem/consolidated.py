"""
consolidated.py — Tiers 2 & 3: the durable stores (episodic and semantic).

Both the episodic tier (event summaries) and the semantic tier (de-contextualised
facts) are structurally identical: a vector store of LLM-distilled text. So one
class backs both, differing only by collection name. Writes here are de-duplicated
by embedding similarity — a near-identical memory is skipped rather than piling up
(cheap, no LLM), which keeps the durable tiers from bloating as interactions recur.
"""
from __future__ import annotations

import uuid
from typing import List, Optional

from anet.memory.recmem.interfaces import Embedding, SearchHit, VectorStore


class ConsolidatedStore:
    def __init__(self, embedder: Embedding, vector_store: VectorStore, collection: str,
                 *, dedup_threshold: float = 0.92):
        self.embedder = embedder
        self.vs = vector_store
        self.collection = collection
        self.dedup_threshold = dedup_threshold

    def add_text(self, text: str, *, metadata: Optional[dict] = None,
                 dedup: bool = True) -> str:
        """Store one durable memory. If a near-duplicate already exists (similarity ≥
        dedup_threshold) the existing id is returned instead of adding a copy."""
        text = (text or "").strip()
        if not text:
            return ""
        emb = self.embedder.embed(text)
        if dedup:
            existing = self.search(emb, top_k=1)
            if existing and existing[0].score >= self.dedup_threshold:
                return existing[0].id
        rid = str(uuid.uuid4())
        self.vs.add(embedding=emb, payload=text, id=rid,
                    collection_name=self.collection, extra_payload=metadata or {})
        return rid

    def search(self, q_embedding: List[float], *, top_k: int = 5) -> List[SearchHit]:
        return self.vs.search(q_embedding=q_embedding, top_k=top_k,
                              collection_name=self.collection)

    def remove(self, ids: List[str]) -> None:
        if ids:
            self.vs.remove(ids, self.collection)

    def list_all(self) -> List[SearchHit]:
        return self.vs.list_all(self.collection)

    def reset(self) -> None:
        self.vs.reset(self.collection)
