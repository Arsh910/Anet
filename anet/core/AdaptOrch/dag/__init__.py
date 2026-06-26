"""
anet.core.AdaptOrch.dag — Phase 2 of AdaptOrch: DAG construction + structural metrics.

Pure Python, no LLM calls, no external deps. Turns a list of decomposed subtasks
into a formal task dependency DAG G_T = (V, E, w, c) and computes the structural
properties the topology router (Phase 3) needs:

    ω (omega)  parallelism width   — how many subtasks can run at once
    δ (delta)  critical path depth  — longest weighted dependency chain
    γ (gamma)  coupling density     — average context-sharing across edges
    layers     topological layering — for the hybrid (τ_X) executor

All algorithms run in O(|V| + |E|) (Kahn layering + a single DP pass), matching
the paper's §4.3 complexity. ω uses the layer-width approximation, which the
paper states suffices for routing.

This module is the foundation: the decomposer (Phase 1) produces Subtasks, build()
turns them into a TaskDAG, and the router (Phase 3) reads its metrics.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── Canonical mapping tables (AdaptOrch eq. 11 + §4.1) ──────────────────────────
# Coupling strength c(u,v) ∈ [0,1] per dependency edge.
COUPLING: dict[str, float] = {
    "none":     0.0,   # outputs fully independent
    "weak":     0.3,   # shared context helpful but not required
    "strong":   0.7,   # output of u is a direct input to v
    "critical": 1.0,   # semantic coherence required
}

# Estimated cost weight w_i from a coarse complexity label.
COMPLEXITY_WEIGHT: dict[str, float] = {
    "low":    1.0,
    "medium": 3.0,
    "high":   9.0,
}

# A declared dependency with no stated coupling is treated as "strong": an edge
# exists precisely because u feeds v, so 0.7 is the safe default (the decomposer
# normally states this explicitly).
DEFAULT_COUPLING = COUPLING["strong"]


class CycleError(ValueError):
    """Raised when the subtasks form a cycle (not a DAG)."""


@dataclass
class Subtask:
    """One node of the task DAG. The decomposer (Phase 1) fills these in; the
    fields beyond id/w/depends_on/coupling are carried through for the executors."""
    id: str
    description: str = ""
    agent: str = ""
    w: float = 1.0                                        # estimated cost weight
    depends_on: list[str] = field(default_factory=list)   # predecessor subtask ids
    coupling: dict[str, float] = field(default_factory=dict)  # {pred_id: c(u,v)}
    success_criteria: str = ""
    check: str = ""


@dataclass
class TaskDAG:
    V: list[Subtask]                       # nodes
    E: list[tuple[str, str, float]]        # edges (u, v, c(u,v))
    w: dict[str, float]                    # id → cost weight
    omega: int                             # parallelism width (max layer size)
    delta: float                           # critical path depth (longest weighted path)
    gamma: float                           # coupling density = Σc / |E|  (0 if no edges)
    layers: list[list[str]]                # topological layers (Kahn)

    @property
    def num_nodes(self) -> int:
        return len(self.V)

    @property
    def num_edges(self) -> int:
        return len(self.E)

    @property
    def parallelism_ratio(self) -> float:
        """r = ω / |V| — the router's wide-DAG test (Algorithm 1, line 2)."""
        return self.omega / len(self.V) if self.V else 0.0

    def features(self) -> dict:
        """Flat structural-feature dict — used by the router and the routing log
        (the future-work learned-routing training signal)."""
        return {
            "num_nodes":         self.num_nodes,
            "num_edges":         self.num_edges,
            "omega":             self.omega,
            "delta":             round(self.delta, 4),
            "gamma":             round(self.gamma, 4),
            "parallelism_ratio": round(self.parallelism_ratio, 4),
            "num_layers":        len(self.layers),
        }

    def summary(self) -> str:
        """One-line human-readable shape, for the status line / UI."""
        return (f"{self.num_nodes} subtasks · {len(self.layers)} layers · "
                f"ω={self.omega} δ={self.delta:g} γ={self.gamma:.2f}")


def build(subtasks: list[Subtask]) -> TaskDAG:
    """Validate the subtasks and construct a TaskDAG with all structural metrics.

    Raises:
        ValueError  — empty input, duplicate ids, or a dependency on an unknown id.
        CycleError  — the dependencies form a cycle (including self-dependency).
    """
    if not subtasks:
        raise ValueError("cannot build a DAG from zero subtasks")

    nodes: dict[str, Subtask] = {s.id: s for s in subtasks}
    if len(nodes) != len(subtasks):
        raise ValueError("duplicate subtask ids in decomposition")

    edges: list[tuple[str, str, float]] = []
    w: dict[str, float] = {}
    indeg: dict[str, int] = {sid: 0 for sid in nodes}
    preds: dict[str, list[str]] = {sid: [] for sid in nodes}
    succs: dict[str, list[str]] = {sid: [] for sid in nodes}

    for s in subtasks:
        w[s.id] = float(s.w)
        for u in s.depends_on:
            if u not in nodes:
                raise ValueError(f"subtask '{s.id}' depends on unknown subtask '{u}'")
            if u == s.id:
                raise CycleError(f"subtask '{s.id}' depends on itself")
            c = float(s.coupling.get(u, DEFAULT_COUPLING))
            edges.append((u, s.id, c))
            preds[s.id].append(u)
            succs[u].append(s.id)
            indeg[s.id] += 1

    # ── Kahn layering (also the acyclicity check) ─────────────────────────────
    deg = dict(indeg)
    remaining = set(nodes)
    layers: list[list[str]] = []
    while remaining:
        layer = sorted(n for n in remaining if deg[n] == 0)
        if not layer:                       # no zero-indegree node left → cycle
            raise CycleError("cycle detected among: " + ", ".join(sorted(remaining)))
        layers.append(layer)
        for n in layer:
            remaining.discard(n)
            for v in succs[n]:
                deg[v] -= 1

    # ── ω: parallelism width ≈ widest layer ───────────────────────────────────
    omega = max((len(l) for l in layers), default=0)

    # ── δ: critical path = longest weighted path (DP over topological order) ──
    dist: dict[str, float] = {}
    for layer in layers:
        for n in layer:
            best_pred = max((dist[u] for u in preds[n]), default=0.0)
            dist[n] = best_pred + w[n]
    delta = max(dist.values(), default=0.0)

    # ── γ: coupling density ───────────────────────────────────────────────────
    gamma = (sum(c for _, _, c in edges) / len(edges)) if edges else 0.0

    return TaskDAG(V=subtasks, E=edges, w=w, omega=omega, delta=delta,
                   gamma=gamma, layers=layers)
