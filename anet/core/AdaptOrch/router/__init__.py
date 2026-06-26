"""
anet.core.AdaptOrch.router — Phase 3 of AdaptOrch: Topology Routing (Algorithm 1).

Pure, deterministic, O(1) given a built TaskDAG: reads the structural metrics the
DAG already computed (ω, γ, |V|, |E|, r = ω/|V|) and returns one of four canonical
topologies. No LLM.

    Algorithm 1
    1. |E| == 0                      → τ_P  (parallel: no dependencies)
    2. ω == 1                        → τ_S  (sequential: single chain)
    3. γ > θ_γ  and  |V| > θ_δ       → τ_H  (hierarchical: coupled + many subtasks)
    4. r > θ_ω  and  γ ≤ θ_γ         → τ_P  (parallel: wide, low coupling)
    5. otherwise                     → τ_X  (hybrid: parallel within layers, sequential between)

Thresholds default to the paper's calibrated values (θ_ω=0.5, θ_γ=0.6, θ_δ=5) and
are overridable per pack via the `orchestration:` block in anet.config.yaml.

The router also exposes log_route(): it appends each decision's DAG features +
chosen topology to <home>/orchestration/routing_log.jsonl. That JSONL is the
training signal for future-work #1 (learned routing) — so we collect it from the
first decision, not after the fact.
"""
from __future__ import annotations

import enum
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from anet.core.AdaptOrch.dag import TaskDAG

# Paper §4.3 calibrated defaults.
_DEFAULT_THRESHOLDS = {"theta_omega": 0.5, "theta_gamma": 0.6, "theta_delta": 5.0}

_SYMBOL = {
    "parallel": "τ_P", "sequential": "τ_S", "hierarchical": "τ_H", "hybrid": "τ_X",
}


class Topology(str, enum.Enum):
    PARALLEL     = "parallel"
    SEQUENTIAL   = "sequential"
    HIERARCHICAL = "hierarchical"
    HYBRID       = "hybrid"

    @property
    def symbol(self) -> str:
        return _SYMBOL[self.value]


@dataclass
class RouteDecision:
    topology: Topology
    reason: str                       # which rule fired, with the numbers
    thresholds: dict                  # θ values actually used
    features: dict                    # dag.features() snapshot (the log signal)
    route_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def summary(self) -> str:
        return f"{self.topology.symbol} ({self.topology.value}) — {self.reason}"


def thresholds() -> dict:
    """Resolve routing thresholds: orchestration.theta_* in config → paper defaults."""
    try:
        from anet.core.config_loader import load
        orch = (load() or {}).get("orchestration") or {}
    except Exception:
        orch = {}
    out = {}
    for k, default in _DEFAULT_THRESHOLDS.items():
        try:
            out[k] = float(orch.get(k, default))
        except (TypeError, ValueError):
            out[k] = default
    return out


def route(dag: TaskDAG, *, th: dict | None = None, gamma_override: float | None = None) -> RouteDecision:
    """Run Algorithm 1 and return the chosen topology with its rationale.

    gamma_override lets the synthesis reroute (Algorithm 2) re-route with an
    inflated coupling estimate (γ' = γ + 0.2) without rebuilding the DAG.
    """
    th = th or thresholds()
    t_omega, t_gamma, t_delta = th["theta_omega"], th["theta_gamma"], th["theta_delta"]

    V, E = dag.num_nodes, dag.num_edges
    omega = dag.omega
    gamma = dag.gamma if gamma_override is None else gamma_override
    r = dag.parallelism_ratio

    if E == 0:
        topo = Topology.PARALLEL
        reason = f"|E|=0 — all {V} subtasks independent"
    elif omega == 1:
        topo = Topology.SEQUENTIAL
        reason = "ω=1 — single dependency chain"
    elif gamma > t_gamma and V > t_delta:
        topo = Topology.HIERARCHICAL
        reason = f"γ={gamma:.2f}>θγ={t_gamma:g} and |V|={V}>θδ={t_delta:g} — coupled + many subtasks"
    elif r > t_omega and gamma <= t_gamma:
        topo = Topology.PARALLEL
        reason = f"r={r:.2f}>θω={t_omega:g} and γ={gamma:.2f}≤θγ={t_gamma:g} — wide, low coupling"
    else:
        topo = Topology.HYBRID
        reason = f"mixed (r={r:.2f}, γ={gamma:.2f}) — parallel within layers, sequential between"

    features = dag.features()
    if gamma_override is not None:
        features = {**features, "gamma_routed": round(gamma, 4)}
    return RouteDecision(topology=topo, reason=reason, thresholds=th, features=features)


def log_route(decision: RouteDecision, *, request_class: str | None = None,
              outcome: dict | None = None, log_path=None) -> None:
    """Append the routing decision (features + chosen topology) to the routing log.

    Best-effort: never raises. This is the (DAG features → chosen topology) dataset
    that future-work learned routing trains on. `outcome` (final consistency score,
    success) can be supplied later by the synthesizer and is merged in if given.
    """
    try:
        if log_path is None:
            from anet.core import paths
            d = paths.home() / "orchestration"
            d.mkdir(parents=True, exist_ok=True)
            log_path = d / "routing_log.jsonl"
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "route_id": decision.route_id,
            "topology": decision.topology.value,
            "reason": decision.reason,
            "request_class": request_class,
            "features": decision.features,
            "thresholds": decision.thresholds,
        }
        if outcome:
            rec["outcome"] = outcome
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass
