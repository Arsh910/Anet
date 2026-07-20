"""
tgraph.py — the temporal communication DAG (QueenBee Planner §3.2, Eq. 2).

Unlike anet.QweenBee.dag.TaskDAG (a static task-dependency graph: which subtask
needs which other subtask's output, once), a TemporalDAG is time-unrolled: an
edge (src, dst, t) means "src's belief as of the end of round t-1 is delivered
to dst at round t". The same node can appear in many rounds; what looks like
feedback at the static level (a -> b, then b -> a) is still acyclic here because
the second message can only use state already committed after the first round.

Barrier semantics (paper §3.2): edges in the same round always see the
*previous* round's committed state, never a same-round update from another
edge that landed first. tgraph encodes this in motifs()'s reduction_depth and
is the contract the planner (Phase 2) and temporal executor (Phase 3) share.

Pure Python, no LLM calls, no external deps — mirrors anet.QweenBee.dag's
"structural metrics only" scope.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

_DEFAULT_LIMITS = {"t_max": 4, "m_max": 16, "beta": 3, "planner_candidates": 1}


@dataclass
class TemporalDAG:
    """One generated communication design (paper's G = (N, T, E, h, I, R)).

    `nodes` are subtask ids (each node = one subtask + its already-assigned
    agent — the planner routes messages, it does not reassign work). `edges`
    is the flat (src, dst, t) list; round_instructions carries the paper's
    per-round receiver guidance I_t; R (the merge rule) is not data here — it
    is the executor's fixed belief-update procedure (Phase 3)."""
    nodes: list[str]
    T: int
    edges: list[tuple[str, str, int]]
    holder: str
    round_instructions: dict[int, str] = field(default_factory=dict)
    rationale: str = ""


def limits() -> dict:
    """Resolve t_max/m_max/beta/planner_candidates: orchestration.{...} in
    config → paper-scale defaults. Same resolution pattern as
    router.thresholds(). planner_candidates defaults to 1 (today's single-shot
    generation); >1 turns on Phase 5's motif-ranked multi-candidate planning."""
    return _resolve_limits()


def _resolve_limits() -> dict:
    # Named separately from the public limits() so validate()'s `limits=`
    # keyword parameter can share that name without shadowing this function.
    try:
        from anet.core.config_loader import load
        orch = (load() or {}).get("orchestration") or {}
    except Exception:
        orch = {}
    out = {}
    for k, default in _DEFAULT_LIMITS.items():
        try:
            out[k] = int(orch.get(k, default))
        except (TypeError, ValueError):
            out[k] = default
    return out


def validate(g: TemporalDAG, *, limits: dict | None = None) -> list[str]:
    """Feasibility check (Eq. 3). Returns [] if OK, else human-readable
    violation strings — these feed the planner's one-shot repair prompt, so
    each message names the specific offending edge/value.

    Acyclicity needs no check: edges only ever go round t-1 -> t by
    construction, so a temporal DAG cannot contain a cycle."""
    lims = _resolve_limits() if limits is None else limits
    violations: list[str] = []
    node_set = set(g.nodes)

    if g.holder not in node_set:
        violations.append(f"holder '{g.holder}' is not among the graph's nodes")
    if g.T > lims["t_max"]:
        violations.append(f"T={g.T} exceeds t_max={lims['t_max']}")
    if len(g.edges) > lims["m_max"]:
        violations.append(f"{len(g.edges)} edges exceeds m_max={lims['m_max']}")

    fan_in: dict[tuple[str, int], int] = {}
    seen: set[tuple[str, str, int]] = set()
    for i, (src, dst, t) in enumerate(g.edges):
        if src not in node_set:
            violations.append(f"edge {i} references unknown source node '{src}'")
        if dst not in node_set:
            violations.append(f"edge {i} references unknown destination node '{dst}'")
        if src == dst:
            violations.append(f"edge {i} is a self-edge on '{src}' at t={t} (not allowed — "
                              "a node's own state carries forward automatically)")
        if not (1 <= t <= g.T):
            violations.append(f"edge {i} has t={t} outside valid range [1, {g.T}]")
        key = (src, dst, t)
        if key in seen:
            violations.append(f"duplicate edge {key}")
        seen.add(key)
        fan_in[(dst, t)] = fan_in.get((dst, t), 0) + 1

    beta = lims["beta"]
    for (dst, t), n in fan_in.items():
        if n > beta:
            violations.append(f"node '{dst}' has fan-in {n} at t={t}, exceeding beta={beta}")

    return violations


def motifs(g: TemporalDAG) -> dict:
    """φ(G) — the paper's 8 label-invariant structural features (§3.4). These
    are what Phase 4's skill bank assigns credit to, not the raw graph."""
    outdeg: dict[str, int] = {n: 0 for n in g.nodes}
    indeg: dict[str, int] = {n: 0 for n in g.nodes}
    fan_in: dict[tuple[str, int], int] = {}
    last_recv_t: dict[str, int] = {}
    for src, dst, t in g.edges:
        outdeg[src] = outdeg.get(src, 0) + 1
        indeg[dst] = indeg.get(dst, 0) + 1
        fan_in[(dst, t)] = fan_in.get((dst, t), 0) + 1
        last_recv_t[dst] = max(last_recv_t.get(dst, 0), t)

    max_fan_in = max(fan_in.values(), default=0)
    if max_fan_in <= 1:
        fan_in_bucket = "single"
    elif max_fan_in == 2:
        fan_in_bucket = "pair"
    else:
        fan_in_bucket = "wide"

    sinks = [n for n in g.nodes if indeg.get(n, 0) > 0 and outdeg.get(n, 0) == 0]

    holder_last_recv = last_recv_t.get(g.holder, 0)
    has_audit_edge = any(src == g.holder and t > holder_last_recv for src, _dst, t in g.edges)

    # reduction_depth: longest hop-chain into the holder, respecting barrier
    # semantics — a round's updates are computed from the PRIOR round's dist,
    # then applied together (mirrors the executor's own round-commit order).
    dist: dict[str, int] = {n: 0 for n in g.nodes}
    for t in range(1, g.T + 1):
        round_updates: dict[str, int] = {}
        for src, dst, et in g.edges:
            if et != t:
                continue
            cand = dist[src] + 1
            if cand > round_updates.get(dst, 0):
                round_updates[dst] = cand
        for dst, val in round_updates.items():
            if val > dist[dst]:
                dist[dst] = val
    reduction_depth = dist.get(g.holder, 0)

    return {
        "steps": g.T,
        "messages": len(g.edges),
        "max_fan_in": max_fan_in,
        "fan_in_bucket": fan_in_bucket,
        "has_sink": bool(sinks),
        "num_sinks": len(sinks),
        "has_audit_edge": has_audit_edge,
        "reduction_depth": reduction_depth,
    }


def canonical_hash(g: TemporalDAG) -> str:
    """Temporal-topology equivalence hash (§3.5C): two graphs that differ only
    in node naming hash identically, so Phase 4's skill bank can merge
    evidence for the same shape instead of splitting credit across aliases.

    Relabels nodes by first appearance in a t-ascending edge traversal (a
    heuristic canonicalization, not full graph-isomorphism solving — good
    enough for dedup of planner output, where identical shapes normally come
    from identical generation order with different subtask ids)."""
    order: dict[str, int] = {}
    norm_edges: list[tuple[int, int, int]] = []
    for src, dst, t in sorted(g.edges, key=lambda e: e[2]):
        if src not in order:
            order[src] = len(order)
        if dst not in order:
            order[dst] = len(order)
        norm_edges.append((order[src], order[dst], t))

    holder_pos = order.get(g.holder, -1)
    payload = json.dumps(
        {"edges": sorted(norm_edges), "T": g.T, "holder_pos": holder_pos},
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()
