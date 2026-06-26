"""Unit tests for anet.core.AdaptOrch.router (AdaptOrch Phase 3, Algorithm 1). Pure, offline."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.core.AdaptOrch.dag import Subtask, build, COUPLING
from anet.core.AdaptOrch import router
from anet.core.AdaptOrch.router import Topology, route, thresholds, log_route


def _st(id, deps=None, w=1.0, coupling=None):
    return Subtask(id=id, w=w, depends_on=deps or [], coupling=coupling or {})


# ── Algorithm 1 — each branch ───────────────────────────────────────────────────

def test_no_edges_routes_parallel():
    dag = build([_st("a"), _st("b"), _st("c")])
    d = route(dag)
    assert d.topology is Topology.PARALLEL and "|E|=0" in d.reason


def test_single_chain_routes_sequential():
    dag = build([_st("a"), _st("b", deps=["a"]), _st("c", deps=["b"])])
    d = route(dag)
    assert d.topology is Topology.SEQUENTIAL and "ω=1" in d.reason


def test_coupled_and_many_routes_hierarchical():
    # hub → 6 children, all critical coupling: |V|=7>5, γ=1.0>0.6
    subs = [_st("hub")]
    for i in range(6):
        subs.append(_st(f"c{i}", deps=["hub"], coupling={"hub": COUPLING["critical"]}))
    dag = build(subs)
    d = route(dag)
    assert d.topology is Topology.HIERARCHICAL
    assert dag.num_nodes == 7 and dag.gamma == 1.0


def test_wide_low_coupling_routes_parallel():
    # hub → 3 children, no coupling: r=3/4>0.5, γ=0
    subs = [_st("hub")] + [_st(f"c{i}", deps=["hub"], coupling={"hub": COUPLING["none"]}) for i in range(3)]
    dag = build(subs)
    d = route(dag)
    assert d.topology is Topology.PARALLEL and "wide" in d.reason


def test_mixed_routes_hybrid():
    # diamond, default (strong) coupling: γ=0.7, |V|=4 (not >5), r=0.5 (not >0.5) → hybrid
    dag = build([_st("a"), _st("b", deps=["a"]), _st("c", deps=["a"]), _st("d", deps=["b", "c"])])
    d = route(dag)
    assert d.topology is Topology.HYBRID


# ── Thresholds ──────────────────────────────────────────────────────────────────

def test_explicit_threshold_override_changes_route():
    # diamond would be hybrid by default; lowering θδ + the diamond's γ>θγ flips to hierarchical
    dag = build([_st("a"), _st("b", deps=["a"]), _st("c", deps=["a"]), _st("d", deps=["b", "c"])])
    d = route(dag, th={"theta_omega": 0.5, "theta_gamma": 0.6, "theta_delta": 2})
    assert d.topology is Topology.HIERARCHICAL   # γ=0.7>0.6 and |V|=4>2


def test_thresholds_from_config():
    import anet.core.config_loader as cl
    saved = cl.load
    try:
        cl.load = lambda: {"orchestration": {"theta_gamma": 0.9, "theta_delta": 1, "theta_omega": 0.5}}
        th = thresholds()
        assert th == {"theta_omega": 0.5, "theta_gamma": 0.9, "theta_delta": 1.0}
    finally:
        cl.load = saved


def test_defaults_when_no_config():
    import anet.core.config_loader as cl
    saved = cl.load
    try:
        cl.load = lambda: {}
        assert thresholds() == {"theta_omega": 0.5, "theta_gamma": 0.6, "theta_delta": 5.0}
    finally:
        cl.load = saved


# ── Reroute (Algorithm 2 hook) ──────────────────────────────────────────────────

def test_gamma_override_for_reroute():
    # wide low-coupling DAG normally parallel; bump γ above θγ → hierarchical (if |V|>θδ)
    subs = [_st("hub")] + [_st(f"c{i}", deps=["hub"], coupling={"hub": COUPLING["none"]}) for i in range(6)]
    dag = build(subs)                              # |V|=7, γ=0
    assert route(dag).topology is Topology.PARALLEL
    d = route(dag, gamma_override=0.9)            # γ'=0.9>0.6 and |V|=7>5
    assert d.topology is Topology.HIERARCHICAL
    assert d.features.get("gamma_routed") == 0.9


# ── Decision metadata + logging ─────────────────────────────────────────────────

def test_decision_carries_features_and_symbol():
    dag = build([_st("a"), _st("b", deps=["a"])])
    d = route(dag)
    assert set(d.features) >= {"omega", "gamma", "num_nodes"}
    assert d.topology.symbol in ("τ_P", "τ_S", "τ_H", "τ_X")
    assert d.route_id and isinstance(d.summary(), str)


def test_log_route_writes_jsonl():
    dag = build([_st("a"), _st("b"), _st("c")])
    d = route(dag)
    p = Path(tempfile.mkdtemp()) / "routing_log.jsonl"
    log_route(d, request_class="coding", log_path=p)
    log_route(d, request_class="coding", outcome={"success": True, "cs": 0.81}, log_path=p)
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[1])
    assert rec["topology"] == "parallel" and rec["request_class"] == "coding"
    assert rec["features"]["omega"] == 3 and rec["outcome"]["success"] is True


def test_log_route_never_raises_on_bad_path():
    dag = build([_st("a")])
    log_route(route(dag), log_path="/no/such/dir/x/y/z.jsonl")   # must not raise


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: router")
