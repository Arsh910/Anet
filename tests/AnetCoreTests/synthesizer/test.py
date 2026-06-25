"""Unit tests for anet.core.synthesizer (AdaptOrch Phase 5, Algorithm 2). Offline."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.core.router import Topology
from anet.core.executors import StepResult
from anet.core import synthesizer as syn
from anet.core.synthesizer import synthesize, SynthesisResult
from anet.core.synthesizer.consistency import consistency_score, alignment, lexical_vectors

# Deterministic embedder for all tests (no fastembed model download).
LEX = lexical_vectors


def _approx(a, b=1.0, tol=1e-9):
    return abs(a - b) < tol


def _res(*outs):
    return [StepResult(id=f"s{i}", agent="a", description="d", output=o) for i, o in enumerate(outs)]


def _run(**kw):
    kw.setdefault("embed", LEX)
    return asyncio.run(synthesize(**kw))


async def _merge(outputs, task=""):
    return "MERGED:" + " | ".join(outputs)


async def _arbiter(outputs, task=""):
    return "ARBITER"


# ── Consistency score ───────────────────────────────────────────────────────────

def test_cs_identical_is_one():
    assert _approx(consistency_score(["the cat sat", "the cat sat"], LEX))


def test_cs_disjoint_is_zero():
    assert consistency_score(["alpha beta", "gamma delta"], LEX) == 0.0


def test_cs_single_output_is_one():
    assert consistency_score(["only one"], LEX) == 1.0
    assert consistency_score([], LEX) == 1.0


def test_alignment():
    assert _approx(alignment("alpha beta", ["alpha beta", "alpha beta"], LEX))
    assert alignment("xyz", ["alpha beta"], LEX) == 0.0


# ── Algorithm 2 branches ────────────────────────────────────────────────────────

def test_sequential_returns_last_output():
    r = _run(results=_res("first", "second", "FINAL"), topology=Topology.SEQUENTIAL,
             merge_fn=_merge, arbiter_fn=_arbiter)
    assert r.method == "sequential_last" and r.output == "FINAL"


def test_single_output_passthrough():
    r = _run(results=_res("only"), topology=Topology.PARALLEL, merge_fn=_merge, arbiter_fn=_arbiter)
    assert r.method == "single" and r.output == "only"


def test_consistent_outputs_merge():
    r = _run(results=_res("the cat sat", "the cat sat"), topology=Topology.PARALLEL,
             merge_fn=_merge, arbiter_fn=_arbiter)
    assert r.method == "merge" and r.output.startswith("MERGED:") and _approx(r.consistency)


def test_inconsistent_no_reroute_goes_to_arbiter():
    r = _run(results=_res("alpha beta", "gamma delta"), topology=Topology.PARALLEL,
             merge_fn=_merge, arbiter_fn=_arbiter)   # no reroute_fn / dag
    assert r.method == "arbiter" and r.output == "ARBITER" and r.iterations == 1


def test_hierarchical_arbiter_does_not_reroute():
    called = {"reroute": False}
    async def reroute(g):
        called["reroute"] = True
        return Topology.PARALLEL, _res("alpha beta", "gamma delta")
    r = _run(results=_res("alpha beta", "gamma delta"), topology=Topology.HIERARCHICAL,
             dag=type("D", (), {"gamma": 0.8})(), reroute_fn=reroute,
             merge_fn=_merge, arbiter_fn=_arbiter)
    assert r.method == "arbiter" and called["reroute"] is False and r.iterations == 1


# ── Reroute loop ────────────────────────────────────────────────────────────────

def test_reroute_then_converges_to_merge():
    # First pass inconsistent → arbiter (post low) → reroute → consistent → merge.
    calls = {"n": 0}
    async def reroute(gamma_override):
        calls["n"] += 1
        assert gamma_override > 0.5            # base 0.5 + 0.2 bump
        return Topology.PARALLEL, _res("same answer", "same answer")
    r = _run(results=_res("alpha beta", "gamma delta"), topology=Topology.PARALLEL,
             dag=type("D", (), {"gamma": 0.5})(), reroute_fn=reroute,
             merge_fn=_merge, arbiter_fn=_arbiter)
    assert r.method == "merge" and r.rerouted is True and r.iterations == 2 and calls["n"] == 1


def test_reroute_terminates_at_max_iters():
    async def reroute(g):
        return Topology.PARALLEL, _res("alpha beta", "gamma delta")   # never reconciles
    r = _run(results=_res("alpha beta", "gamma delta"), topology=Topology.PARALLEL,
             dag=type("D", (), {"gamma": 0.0})(), reroute_fn=reroute,
             merge_fn=_merge, arbiter_fn=_arbiter, max_iters=5)
    assert r.method == "arbiter" and r.iterations == 5 and r.rerouted is True


# ── Threshold config ────────────────────────────────────────────────────────────

def test_theta_cs_from_config():
    import anet.core.config_loader as cl
    saved = cl.load
    try:
        cl.load = lambda: {"orchestration": {"theta_cs": 0.9}}
        assert syn.theta_cs() == 0.9
        cl.load = lambda: {}
        assert syn.theta_cs() == 0.7
    finally:
        cl.load = saved


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: synthesizer")
