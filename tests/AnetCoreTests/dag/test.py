"""Unit tests for anet.core.dag (AdaptOrch Phase 2). Pure, offline, deterministic."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.core.dag import (
    Subtask, TaskDAG, build, CycleError, COUPLING, COMPLEXITY_WEIGHT, DEFAULT_COUPLING,
)


def _st(id, deps=None, w=1.0, coupling=None):
    return Subtask(id=id, w=w, depends_on=deps or [], coupling=coupling or {})


# ── Validation ──────────────────────────────────────────────────────────────────

def test_empty_raises():
    try:
        build([])
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_duplicate_ids_raise():
    try:
        build([_st("a"), _st("a")])
        assert False
    except ValueError:
        pass


def test_unknown_dependency_raises():
    try:
        build([_st("a", deps=["ghost"])])
        assert False
    except ValueError:
        pass


def test_self_dependency_is_cycle():
    try:
        build([_st("a", deps=["a"])])
        assert False
    except CycleError:
        pass


def test_cycle_detected():
    # a → b → a
    try:
        build([_st("a", deps=["b"]), _st("b", deps=["a"])])
        assert False
    except CycleError:
        pass


# ── Structure: fully parallel (no edges) ────────────────────────────────────────

def test_parallel_no_edges():
    d = build([_st("a"), _st("b"), _st("c")])
    assert d.num_edges == 0
    assert d.omega == 3                 # all three in one layer
    assert len(d.layers) == 1
    assert d.gamma == 0.0               # no edges → 0 by convention
    assert d.delta == 1.0              # heaviest single node
    assert d.parallelism_ratio == 1.0


# ── Structure: fully sequential (a chain) ───────────────────────────────────────

def test_sequential_chain():
    d = build([_st("a"), _st("b", deps=["a"]), _st("c", deps=["b"])])
    assert d.omega == 1                 # one node per layer
    assert len(d.layers) == 3
    assert d.layers == [["a"], ["b"], ["c"]]
    assert d.delta == 3.0              # 1+1+1 critical path


# ── Critical path uses weights ──────────────────────────────────────────────────

def test_critical_path_weighted():
    # a(1) → b(9) → d(1)  vs  a(1) → c(3) → d(1)
    d = build([
        _st("a", w=1),
        _st("b", deps=["a"], w=9),
        _st("c", deps=["a"], w=3),
        _st("d", deps=["b", "c"], w=1),
    ])
    assert d.delta == 11.0            # 1 + 9 + 1, the heavy branch
    assert d.omega == 2               # b and c share a layer


# ── Coupling density γ ──────────────────────────────────────────────────────────

def test_gamma_from_explicit_coupling():
    d = build([
        _st("a"),
        _st("b", deps=["a"], coupling={"a": COUPLING["weak"]}),       # 0.3
        _st("c", deps=["a"], coupling={"a": COUPLING["critical"]}),    # 1.0
    ])
    assert abs(d.gamma - (0.3 + 1.0) / 2) < 1e-9


def test_default_coupling_when_unspecified():
    d = build([_st("a"), _st("b", deps=["a"])])
    assert d.E[0][2] == DEFAULT_COUPLING
    assert abs(d.gamma - DEFAULT_COUPLING) < 1e-9


# ── Layering (hybrid τ_X) ───────────────────────────────────────────────────────

def test_diamond_layers():
    # a → {b, c} → d
    d = build([
        _st("a"),
        _st("b", deps=["a"]),
        _st("c", deps=["a"]),
        _st("d", deps=["b", "c"]),
    ])
    assert d.layers == [["a"], ["b", "c"], ["d"]]
    assert d.omega == 2
    assert d.features()["num_layers"] == 3


def test_features_and_summary():
    d = build([_st("a"), _st("b", deps=["a"])])
    f = d.features()
    assert set(f) == {"num_nodes", "num_edges", "omega", "delta", "gamma",
                      "parallelism_ratio", "num_layers"}
    assert isinstance(d.summary(), str) and "subtasks" in d.summary()


def test_complexity_weight_table():
    assert COMPLEXITY_WEIGHT == {"low": 1.0, "medium": 3.0, "high": 9.0}


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: dag")
