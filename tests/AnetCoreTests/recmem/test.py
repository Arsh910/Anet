"""Unit tests for the native RecMem memory backend — the engine
(anet.memory.recmem) and the recmem_store facade (anet.core.memory_store.recmem_store).

Fully offline and deterministic: uses in-memory fakes for the embedder / vector
store / LLM, so it needs no chromadb, no fastembed model download, and no API key.
"""
import math
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

# Isolate storage before importing the memory layer.
os.environ["ANET_HOME"] = tempfile.mkdtemp(prefix="anet_recmem_test_")

from anet.memory.recmem.interfaces import (
    Embedding, VectorStore, SearchHit, LLMClient, LLMResponse,
)
from anet.memory.recmem import RecMem
from anet.core.memory_store import recmem_store as rs


# ── Fakes ─────────────────────────────────────────────────────────────────────

class HashEmbedding(Embedding):
    """Deterministic bag-of-words hashing embedder (hashlib, not salted hash())."""
    D = 256
    def embed(self, text):
        import hashlib
        v = [0.0] * self.D
        for w in text.lower().split():
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.D] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]
    @property
    def dim(self): return self.D


class InMemVS(VectorStore):
    def __init__(self): self.c = {}
    def _cl(self, n): return self.c.setdefault(n, {})
    def add(self, *, embedding, payload, id, collection_name, extra_payload=None):
        self._cl(collection_name)[id] = (embedding, payload, dict(extra_payload or {}))
    def add_batch(self, *, embeddings, payloads, ids, collection_name, extra_payloads=None):
        for i, id_ in enumerate(ids):
            self.add(embedding=embeddings[i], payload=payloads[i], id=id_,
                     collection_name=collection_name,
                     extra_payload=(extra_payloads or [None] * len(ids))[i])
    def search(self, *, q_embedding, top_k, collection_name):
        hits = [SearchHit(i, d, sum(x * y for x, y in zip(q_embedding, e)), m)
                for i, (e, d, m) in self._cl(collection_name).items()]
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]
    def remove(self, ids, collection_name):
        for i in ids: self._cl(collection_name).pop(i, None)
    def reset(self, collection_name): self.c[collection_name] = {}
    def list_all(self, collection_name):
        return [SearchHit(i, d, 0.0, m) for i, (e, d, m) in self._cl(collection_name).items()]


class FakeLLM(LLMClient):
    def __init__(self): self.calls = 0
    def complete(self, messages, *, json_mode=False, temperature=0.0, max_tokens=1200):
        self.calls += 1
        if "label a single memory" in messages[0].content:      # classify()
            return LLMResponse('{"category": "preference", "applies_to": ["code_agent"]}')
        return LLMResponse('{"episodes": ["the user set up recmem"], '     # consolidation
                           '"facts": ["the user runs Python 3.11 on Windows"]}')


def _engine(**kw):
    return RecMem(embedder=HashEmbedding(), vector_store=InMemVS(), llm_client=FakeLLM(),
                  recurrence_threshold=2, sim_threshold=0.5, **kw)


# ── Engine: recurrence consolidation ─────────────────────────────────────────────

def test_remember_recall_no_llm():
    rm = _engine()
    rm.remember("the user's project lives at C:/thinkbig/Anet, a Python app")
    hits = rm.recall("where is the project", top_k=3)
    assert any("thinkbig" in h.content for h in hits), hits
    assert rm.llm.calls == 0, "remember() must not call the LLM"


def test_recurrence_triggers_one_consolidation():
    rm = _engine()
    rm.observe("the user is running python 3.11 on windows")
    r = rm.observe("the user runs python 3.11 on windows here")
    assert r["consolidated"] is True, r
    assert rm.llm.calls == 1, ("exactly one consolidation call", rm.llm.calls)
    # consolidated fact recallable; raw cluster pruned
    assert any("Python 3.11" in h.content for h in rm.recall("python version", top_k=5))
    assert rm.sub.list_all() == [], "raw snippets pruned after consolidation"


def test_single_observation_does_not_consolidate():
    rm = _engine()
    rm.observe("a one-off unrelated remark about the weather")
    assert rm.llm.calls == 0


def test_dedup_skips_near_duplicates():
    rm = _engine()
    a = rm.remember("the user prefers 4-space indentation")
    b = rm.remember("the user prefers 4-space indentation")   # identical → deduped
    assert a == b, "near-duplicate durable save should return the existing id"


def test_reset_wipes_all_tiers():
    rm = _engine()
    rm.remember("x lives at /tmp/x")
    rm.reset()
    assert rm.recall("x", top_k=5) == []


# ── Facade: the memory_store API mapping ─────────────────────────────────────────

def _install_facade():
    rs._rm = _engine()
    rs._init_tried = True
    return rs._rm


def test_facade_explicit_save_is_standing_scoped():
    _install_facade()
    r = rs.add("Prefix all functions with anet_", classify_content=True)
    assert r.get("saved") and r.get("id"), r
    code = {m["content"] for m in rs.standing_memories("code_agent")}
    research = {m["content"] for m in rs.standing_memories("research_agent")}
    assert "Prefix all functions with anet_" in code, code
    assert "Prefix all functions with anet_" not in research, research


def test_facade_plain_fact_searchable_not_standing():
    _install_facade()
    rs.add("The project uses FastAPI and lives at C:/proj", classify_content=False)
    assert any("FastAPI" in h["content"] for h in rs.search("what web framework", limit=5))
    assert any("FastAPI" in m["content"] for m in rs.get_all())
    assert not any("FastAPI" in m["content"] for m in rs.standing_memories())


def test_facade_record_shape():
    _install_facade()
    rs.add("indent with anet_ prefix", classify_content=True)
    rec = rs.search("anet_ prefix", limit=1)[0]
    assert set(rec) >= {"id", "content", "tags", "project_path", "kind", "category",
                        "applies_to", "always_inject", "created_at", "score"}, rec
    assert isinstance(rec["applies_to"], list), "applies_to decoded from JSON to list"


def test_facade_delete_and_clear():
    _install_facade()
    rs.add("temp fact one", classify_content=False)
    sid = rs.search("temp fact one", limit=1)[0]["id"]
    assert rs.delete(sid).get("deleted"), "delete"
    assert rs.clear().get("cleared"), "clear"
    assert rs.get_all() == [] and rs.standing_memories() == []


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: recmem")
