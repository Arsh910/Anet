"""
Consistency scoring for AdaptOrch Phase 5 (Definition 5 / eq. 17).

CS({o_1..o_k}) = mean pairwise cosine similarity of the outputs' embeddings — a
heuristic proxy for "do these parallel agent outputs agree?". High CS → safe to
merge; low CS → conflict, escalate to the arbiter.

Embeddings reuse the fastembed model ANet already ships for mem0 (no new
dependency, no API call). If fastembed can't load, it falls back to a deterministic
lexical (bag-of-words cosine) vectorizer, so scoring always works offline and is
unit-testable without the 130 MB model.
"""
from __future__ import annotations

import math
import re

_FE_MODEL = None


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def lexical_vectors(texts: list[str]) -> list[list[float]]:
    """Deterministic bag-of-words term-frequency vectors over a shared vocab."""
    toks = [_tokenize(t) for t in texts]
    vocab: dict[str, int] = {}
    for ts in toks:
        for w in ts:
            vocab.setdefault(w, len(vocab))
    vecs: list[list[float]] = []
    for ts in toks:
        v = [0.0] * len(vocab)
        for w in ts:
            v[vocab[w]] += 1.0
        vecs.append(v)
    return vecs


def _fastembed_vectors(texts: list[str]) -> list[list[float]]:
    global _FE_MODEL
    if _FE_MODEL is None:
        from fastembed import TextEmbedding
        _FE_MODEL = TextEmbedding()
    return [list(v) for v in _FE_MODEL.embed(list(texts))]


def default_embed(texts: list[str]) -> list[list[float]]:
    """Embed with fastembed; fall back to lexical vectors if it can't load."""
    try:
        return _fastembed_vectors(texts)
    except Exception:
        return lexical_vectors(texts)


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (na * nb)))   # clamp away float drift


def consistency_score(outputs: list[str], embed=None) -> float:
    """Mean pairwise cosine similarity of the outputs (eq. 17).

    With fewer than two non-empty outputs there's nothing to disagree about, so CS
    is 1.0 by convention. ``embed`` is an injectable texts→vectors function (tests
    pass the lexical one for determinism)."""
    outs = [o for o in outputs if o and o.strip()]
    if len(outs) < 2:
        return 1.0
    embed = embed or default_embed
    vecs = embed(outs)
    sims = [
        cosine(vecs[i], vecs[j])
        for i in range(len(vecs)) for j in range(i + 1, len(vecs))
    ]
    return sum(sims) / len(sims) if sims else 1.0


def alignment(merged: str, outputs: list[str], embed=None) -> float:
    """Mean cosine similarity between a merged/arbitrated output and each original
    output — used as the post-arbitration CS(O) check (Algorithm 2, line 9): low
    alignment means the arbiter couldn't reconcile the conflict, so reroute."""
    outs = [o for o in outputs if o and o.strip()]
    if not merged or not merged.strip() or not outs:
        return 1.0
    embed = embed or default_embed
    vecs = embed([merged] + outs)
    base = vecs[0]
    sims = [cosine(base, v) for v in vecs[1:]]
    return sum(sims) / len(sims) if sims else 1.0
