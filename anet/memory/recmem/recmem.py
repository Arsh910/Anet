"""
recmem.py — the RecMem orchestrator.

Wires the three tiers into the recurrence-consolidation loop from the paper
(arXiv 2605.16045):

    observe(text)   raw interaction → subconscious. Count how many existing
                    subconscious snippets are semantically similar; once that
                    cluster reaches `recurrence_threshold`, run ONE LLM call to
                    distill it into episodic summaries + semantic facts, then prune
                    the consumed raw snippets. LLM tokens are spent only on
                    interactions that actually recur — the whole point of RecMem.

    remember(text)  explicit durable save → straight into semantic (verbatim,
                    de-duplicated, no recurrence gate). This is how "save this"
                    from a user/agent is guaranteed to persist.

    recall(query)   retrieve across all three tiers, merged by similarity and
                    de-duplicated by content. Never calls the LLM.

Everything is provider-injected (embedder / vector_store / llm_client), so the
engine is fully unit-testable with in-memory fakes.
"""
from __future__ import annotations

import json
import logging
from typing import List, Optional

from anet.memory.recmem.interfaces import (
    Embedding, LLMClient, Message, SearchHit, VectorStore,
)
from anet.memory.recmem.subconscious import SubconsciousMemory
from anet.memory.recmem.consolidated import ConsolidatedStore
from anet.memory.recmem.prompts import CONSOLIDATION_SYSTEM, consolidation_user

logger = logging.getLogger(__name__)

# Collection-name suffixes per tier (scoped by a base namespace).
_SUB = "sub"
_EPI = "epi"
_SEM = "sem"


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # tolerant fallback: first {...} block
    import re
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    # optional dependency: json-repair, if installed
    try:
        from json_repair import repair_json
        return json.loads(repair_json(text))
    except Exception:
        return {}


class RecMem:
    """Recurrence-based 3-tier memory. One instance per namespace (ANet uses a
    single namespace for the local user)."""

    def __init__(
        self,
        *,
        embedder: Embedding,
        vector_store: VectorStore,
        llm_client: LLMClient,
        namespace: str = "anet",
        recurrence_threshold: int = 2,
        sim_threshold: float = 0.72,
        dedup_threshold: float = 0.92,
    ):
        self.embedder = embedder
        self.vs = vector_store
        self.llm = llm_client
        self.namespace = namespace
        self.recurrence_threshold = max(1, recurrence_threshold)
        self.sim_threshold = sim_threshold
        self.sub = SubconsciousMemory(embedder, vector_store, f"{namespace}_{_SUB}")
        self.epi = ConsolidatedStore(embedder, vector_store, f"{namespace}_{_EPI}",
                                     dedup_threshold=dedup_threshold)
        self.sem = ConsolidatedStore(embedder, vector_store, f"{namespace}_{_SEM}",
                                     dedup_threshold=dedup_threshold)

    # ── Writes ────────────────────────────────────────────────────────────────

    def observe(self, text: str, *, metadata: Optional[dict] = None) -> dict:
        """Record a raw interaction and consolidate its cluster if it has recurred.
        Returns {id, consolidated: bool, episodes: int, facts: int}."""
        text = (text or "").strip()
        if not text:
            return {"id": "", "consolidated": False, "episodes": 0, "facts": 0}
        emb = self.embedder.embed(text)
        cluster = self.sub.similar(emb, threshold=self.sim_threshold)
        rid = self.sub.add(text, embedding=emb, metadata=metadata)

        # +1 for the snippet we just added: does the recurring cluster meet the bar?
        if len(cluster) + 1 < self.recurrence_threshold:
            return {"id": rid, "consolidated": False, "episodes": 0, "facts": 0}

        texts = [text] + [h.content for h in cluster]
        ids = [rid] + [h.id for h in cluster]
        eps, facts = self._consolidate(texts)
        # Prune the raw snippets we just distilled so they don't re-trigger.
        self.sub.remove(ids)
        return {"id": rid, "consolidated": True, "episodes": eps, "facts": facts}

    def remember(self, text: str, *, metadata: Optional[dict] = None,
                 tier: str = "semantic", dedup: bool = True) -> str:
        """Explicit durable save (bypasses the recurrence gate). Goes to semantic by
        default; pass tier='episodic' for an event summary. Returns the memory id."""
        store = self.epi if tier == "episodic" else self.sem
        return store.add_text(text, metadata=metadata, dedup=dedup)

    def _consolidate(self, snippets: List[str]) -> tuple[int, int]:
        """One LLM call: distill a recurring cluster into episodes + facts."""
        try:
            resp = self.llm.complete(
                [Message("system", CONSOLIDATION_SYSTEM),
                 Message("user", consolidation_user(snippets))],
                json_mode=True, temperature=0.0, max_tokens=1000,
            )
            data = _parse_json(resp.content)
        except Exception as exc:
            logger.warning("recmem: consolidation failed (%s); leaving raw snippets", exc)
            return 0, 0
        episodes = [e for e in (data.get("episodes") or []) if isinstance(e, str)]
        facts = [f for f in (data.get("facts") or []) if isinstance(f, str)]
        for e in episodes:
            self.epi.add_text(e)
        for f in facts:
            self.sem.add_text(f)
        return len(episodes), len(facts)

    # ── Reads ─────────────────────────────────────────────────────────────────

    def recall(self, query: str, *, top_k: int = 5, threshold: float = 0.0,
               include_subconscious: bool = True) -> List[SearchHit]:
        """Retrieve across tiers, merged by score and de-duplicated by content.
        Durable tiers (semantic, episodic) are preferred over raw subconscious."""
        query = (query or "").strip()
        if not query:
            return []
        q = self.embedder.embed(query)
        stores = [self.sem, self.epi]
        hits: List[SearchHit] = []
        for s in stores:
            hits += s.search(q, top_k=top_k)
        if include_subconscious:
            hits += self.sub.search(q, top_k=top_k)

        best: dict[str, SearchHit] = {}
        for h in hits:
            if h.score < threshold:
                continue
            key = h.content.strip().lower()
            if key not in best or h.score > best[key].score:
                best[key] = h
        out = sorted(best.values(), key=lambda h: h.score, reverse=True)
        return out[:top_k]

    def durable_memories(self) -> List[SearchHit]:
        """All consolidated memories (semantic + episodic) — for listing/export."""
        return self.sem.list_all() + self.epi.list_all()

    def delete(self, memory_id: str) -> bool:
        for store in (self.sem, self.epi, self.sub):
            store.remove([memory_id])
        return True

    def reset(self) -> None:
        self.sub.reset()
        self.epi.reset()
        self.sem.reset()
