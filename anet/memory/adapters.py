"""
adapters.py — ANet's concrete implementations of RecMem's three provider contracts,
so the engine runs on exactly the on-device infra the mem0 backend already uses:

    Embedding   → fastembed  (BAAI/bge-small-en-v1.5, ONNX, on-device, no API call)
    VectorStore → chromadb   (local persistent store, cosine space)
    LLMClient   → a sync OpenAI-compatible client resolved from ANet's config
                  (used ONLY for RecMem's recurrence consolidation)

All RecMem data (the three Chroma collections per namespace) lives under
<home>/memory/recmem/.
"""
from __future__ import annotations

import os
import threading
from typing import List, Optional

from anet.memory.recmem.interfaces import (
    Embedding, VectorStore, SearchHit, LLMClient, Message, LLMResponse,
    EmptyResponseException,
)

_EMBED_MODEL = "BAAI/bge-small-en-v1.5"


# ── Embedding: fastembed ────────────────────────────────────────────────────────

class FastEmbedEmbedding(Embedding):
    """fastembed-backed embedder (ONNX, on-device, no key). The model is loaded once
    and shared across every RecMem instance; loading is thread-safe."""

    _model = None
    _lock = threading.Lock()

    def __init__(self, model_name: str = _EMBED_MODEL):
        self._model_name = model_name
        self._dim: Optional[int] = None

    def _get_model(self):
        if FastEmbedEmbedding._model is None:
            with FastEmbedEmbedding._lock:
                if FastEmbedEmbedding._model is None:
                    from fastembed import TextEmbedding
                    FastEmbedEmbedding._model = TextEmbedding(model_name=self._model_name)
        return FastEmbedEmbedding._model

    def embed(self, text: str) -> List[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        vecs = [list(map(float, v)) for v in self._get_model().embed(list(texts))]
        if self._dim is None and vecs:
            self._dim = len(vecs[0])
        return vecs

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self.embed("dimension probe"))
        return self._dim


# ── VectorStore: chromadb ───────────────────────────────────────────────────────

_CONTENT_KEY = "_content"   # content is stored in chroma's `documents`; this marker
                            # guarantees the metadata dict is never empty (chroma rejects {}).


def _scalar_meta(extra: Optional[dict]) -> dict:
    """Chroma metadata values must be str/int/float/bool. Coerce/skip others and
    ensure the dict is non-empty."""
    out: dict = {}
    for k, v in (extra or {}).items():
        if isinstance(v, (str, int, float, bool)):
            out[str(k)] = v
        elif isinstance(v, (list, tuple)):
            # store lists (e.g. applies_to, tags) as a JSON string; facade decodes.
            import json
            out[str(k)] = json.dumps(list(v))
    out.setdefault(_CONTENT_KEY, "1")
    return out


class ChromaVectorStore(VectorStore):
    """chromadb-backed vector store (local, persistent). One Chroma collection per
    RecMem collection name; cosine space."""

    def __init__(self, persist_dir: str):
        import chromadb
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._cols: dict = {}
        self._lock = threading.Lock()

    def _col(self, name: str):
        col = self._cols.get(name)
        if col is None:
            with self._lock:
                col = self._client.get_or_create_collection(
                    name=name, metadata={"hnsw:space": "cosine"})
                self._cols[name] = col
        return col

    def add(self, *, embedding, payload, id, collection_name, extra_payload=None) -> None:
        self.add_batch(embeddings=[embedding], payloads=[payload], ids=[id],
                       collection_name=collection_name, extra_payloads=[extra_payload])

    def add_batch(self, *, embeddings, payloads, ids, collection_name,
                  extra_payloads=None) -> None:
        if not ids:
            return
        metas = [_scalar_meta(extra_payloads[i] if extra_payloads else None)
                 for i in range(len(ids))]
        self._col(collection_name).upsert(
            ids=list(ids), embeddings=[list(e) for e in embeddings],
            documents=list(payloads), metadatas=metas)

    def _to_hit(self, id_, doc, meta, distance) -> SearchHit:
        meta = dict(meta or {})
        meta.pop(_CONTENT_KEY, None)
        score = 1.0 - float(distance) if distance is not None else 0.0
        return SearchHit(id=id_, content=doc or "", score=score, extra_payload=meta)

    def search(self, *, q_embedding, top_k, collection_name) -> List[SearchHit]:
        col = self._col(collection_name)
        try:
            res = col.query(query_embeddings=[list(q_embedding)], n_results=max(1, top_k),
                            include=["documents", "metadatas", "distances"])
        except Exception:
            return []
        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        hits = []
        for i in range(len(ids)):
            hits.append(self._to_hit(
                ids[i], docs[i] if i < len(docs) else "",
                metas[i] if i < len(metas) else {},
                dists[i] if i < len(dists) else None))
        return hits

    def remove(self, ids: List[str], collection_name: str) -> None:
        if ids:
            try:
                self._col(collection_name).delete(ids=list(ids))
            except Exception:
                pass

    def reset(self, collection_name: str) -> None:
        try:
            self._client.delete_collection(collection_name)
        except Exception:
            pass
        self._cols.pop(collection_name, None)

    def list_all(self, collection_name: str) -> List[SearchHit]:
        try:
            res = self._col(collection_name).get(include=["documents", "metadatas"])
        except Exception:
            return []
        ids = res.get("ids") or []
        docs = res.get("documents") or []
        metas = res.get("metadatas") or []
        hits = []
        for i in range(len(ids)):
            hits.append(self._to_hit(
                ids[i], docs[i] if i < len(docs) else "",
                metas[i] if i < len(metas) else {}, None))
        return hits


# ── LLMClient: sync, ANet-config-resolved OpenAI-compatible client ──────────────

class AnetLLMClient(LLMClient):
    """Synchronous chat client for RecMem's consolidation calls. Resolves
    model+provider from ANet's config (the `memory` stage, falling back to the
    manager model) and talks to the matching OpenAI-compatible endpoint. Token
    usage is forwarded to ANet's per-turn accounting under the 'memory' stage."""

    def __init__(self, stage: str = "memory"):
        self._stage = stage

    def _client_and_model(self):
        from anet.core.AdaptOrch.stage_models import stage_model
        from anet.core.agent_runner import _PROVIDERS, _DEFAULT_PROVIDER
        model, provider = stage_model(self._stage)
        provider = (provider or _DEFAULT_PROVIDER).lower()
        if provider in ("claude",):
            provider = "openrouter"   # no sync anthropic path here; route via OpenRouter
        cfg = _PROVIDERS.get(provider) or _PROVIDERS[_DEFAULT_PROVIDER]
        from openai import OpenAI
        api_key = os.getenv(cfg["env_key"]) or "missing"
        return OpenAI(api_key=api_key, base_url=cfg["base_url"], timeout=120), model

    def complete(self, messages: List[Message], *, json_mode: bool = False,
                 temperature: float = 0.0, max_tokens: int = 1200) -> LLMResponse:
        client, model = self._client_and_model()
        kwargs = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception:
            # Some providers reject response_format — retry without it.
            if json_mode:
                kwargs.pop("response_format", None)
                resp = client.chat.completions.create(**kwargs)
            else:
                raise
        try:
            from anet.core import tokens as _tok
            _tok.record(resp, stage="memory")
        except Exception:
            pass
        content = (resp.choices[0].message.content or "").strip()
        if not content:
            raise EmptyResponseException("empty response from memory LLM")
        return LLMResponse(content=content)
