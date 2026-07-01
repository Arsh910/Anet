"""
interfaces.py — the three pluggable provider contracts RecMem runs on, plus the
small value types they exchange.

RecMem itself is storage/model-agnostic: it talks only to these ABCs, and ANet
supplies concrete implementations in `anet.memory.adapters` (fastembed / chromadb /
a sync OpenAI-compatible client). Keeping the contracts here means the engine
files (subconscious / consolidated / recmem) never import a concrete backend and
stay unit-testable with trivial in-memory fakes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


# ── Embedding ─────────────────────────────────────────────────────────────────

class Embedding(ABC):
    """Turns text into a dense vector. Retrieval and recurrence detection both run
    entirely on these vectors — no LLM call — which is what makes RecMem cheap."""

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        ...

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]

    @property
    @abstractmethod
    def dim(self) -> int:
        ...


# ── Vector store ────────────────────────────────────────────────────────────────

@dataclass
class SearchHit:
    """One retrieved memory. `score` is cosine similarity in [0, 1] (higher = closer).
    `extra_payload` carries whatever metadata was stored with the memory."""
    id: str
    content: str
    score: float
    extra_payload: dict = field(default_factory=dict)


class VectorStore(ABC):
    """A collection-scoped vector store. Each RecMem tier is one collection; the
    same store instance backs all of them."""

    @abstractmethod
    def add(self, *, embedding: List[float], payload: str, id: str,
            collection_name: str, extra_payload: Optional[dict] = None) -> None:
        ...

    @abstractmethod
    def add_batch(self, *, embeddings: List[List[float]], payloads: List[str],
                  ids: List[str], collection_name: str,
                  extra_payloads: Optional[List[Optional[dict]]] = None) -> None:
        ...

    @abstractmethod
    def search(self, *, q_embedding: List[float], top_k: int,
               collection_name: str) -> List[SearchHit]:
        ...

    @abstractmethod
    def remove(self, ids: List[str], collection_name: str) -> None:
        ...

    @abstractmethod
    def reset(self, collection_name: str) -> None:
        ...

    @abstractmethod
    def list_all(self, collection_name: str) -> List[SearchHit]:
        """Every memory in the collection (score 0.0 — no query). For listing/export."""
        ...


# ── LLM client ────────────────────────────────────────────────────────────────

@dataclass
class Message:
    role: str
    content: str


@dataclass
class LLMResponse:
    content: str


class LLMError(Exception):
    pass


class EmptyResponseException(LLMError):
    pass


class LLMClient(ABC):
    """Synchronous chat client. RecMem calls it ONLY to consolidate recurring raw
    interactions into episodic/semantic memories — never for retrieval."""

    @abstractmethod
    def complete(self, messages: List[Message], *, json_mode: bool = False,
                 temperature: float = 0.0, max_tokens: int = 1200) -> LLMResponse:
        ...
