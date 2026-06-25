"""Unit tests for anet.core.executors (AdaptOrch Phase 4). Offline — fake run_subtask."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.core.dag import Subtask, build, COUPLING
from anet.core.router import Topology
from anet.core import executors as ex
from anet.core.executors import ExecContext, StepResult, compose, accumulate_predecessors


def _st(id, deps=None, coupling=None, agent="code_agent"):
    return Subtask(id=id, description=f"do {id}", agent=agent,
                   depends_on=deps or [], coupling=coupling or {})


def _recorder():
    """Returns (calls, run_subtask). calls = [(id, prompt), ...] in execution order."""
    calls = []
    async def run_subtask(st, prompt):
        calls.append((st.id, prompt))
        return StepResult(id=st.id, agent=st.agent, description=st.description, output=f"OUT[{st.id}]")
    return calls, run_subtask


def _ctx(run_subtask, **kw):
    return ExecContext(run_subtask=run_subtask, global_context="WORLD", **kw)


def _prompt_of(calls, sid):
    return next(p for (i, p) in calls if i == sid)


# ── τ_P parallel: all run, isolated context (no peer outputs) ────────────────────

def test_parallel_runs_all_isolated():
    dag = build([_st("a"), _st("b"), _st("c")])
    calls, rs = _recorder()
    results = asyncio.run(ex.execute(Topology.PARALLEL, dag, _ctx(rs)))
    assert {r.id for r in results} == {"a", "b", "c"}
    for sid in ("a", "b", "c"):
        p = _prompt_of(calls, sid)
        assert "WORLD" in p                      # global context present
        assert "OUT[" not in p                   # no peer outputs


# ── τ_S sequential: topo order, sees DIRECT predecessor outputs ──────────────────

def test_sequential_order_and_predecessor_context():
    dag = build([_st("a"), _st("b", deps=["a"]), _st("c", deps=["b"])])
    calls, rs = _recorder()
    asyncio.run(ex.execute(Topology.SEQUENTIAL, dag, _ctx(rs)))
    assert [i for i, _ in calls] == ["a", "b", "c"]      # strict topo order
    assert "OUT[a]" in _prompt_of(calls, "b")            # b sees a
    assert "OUT[b]" in _prompt_of(calls, "c")            # c sees b
    assert "OUT[a]" not in _prompt_of(calls, "c")        # only DIRECT predecessor


def test_sequential_diamond_merges_both_predecessors():
    dag = build([_st("a"), _st("b", deps=["a"]), _st("c", deps=["a"]), _st("d", deps=["b", "c"])])
    calls, rs = _recorder()
    asyncio.run(ex.execute(Topology.SEQUENTIAL, dag, _ctx(rs)))
    pd = _prompt_of(calls, "d")
    assert "OUT[b]" in pd and "OUT[c]" in pd


# ── τ_H hierarchical: dependency order, sub-agents ISOLATED ─────────────────────

def test_hierarchical_isolated_subagents_in_order():
    dag = build([_st("a"), _st("b", deps=["a"]), _st("c", deps=["b"])])
    calls, rs = _recorder()
    asyncio.run(ex.execute(Topology.HIERARCHICAL, dag, _ctx(rs)))
    assert [i for i, _ in calls] == ["a", "b", "c"]      # ordered like the lead delegating
    assert "OUT[a]" not in _prompt_of(calls, "b")        # but isolated — lead integrates
    assert "OUT[b]" not in _prompt_of(calls, "c")


# ── τ_X hybrid: layer by layer; prior layers roll forward, peers don't ──────────

def test_hybrid_layers_roll_forward_not_peers():
    dag = build([_st("a"), _st("b", deps=["a"]), _st("c", deps=["a"]), _st("d", deps=["b", "c"])])
    calls, rs = _recorder()
    asyncio.run(ex.execute(Topology.HYBRID, dag, _ctx(rs)))
    order = [i for i, _ in calls]
    assert order.index("a") < order.index("b") and order.index("a") < order.index("c")
    assert order.index("b") < order.index("d") and order.index("c") < order.index("d")
    # b (layer 1) sees a (layer 0) but not c (its peer in the same layer)
    pb = _prompt_of(calls, "b")
    assert "OUT[a]" in pb and "OUT[c]" not in pb
    # d (layer 2) sees all prior layers
    pd = _prompt_of(calls, "d")
    assert "OUT[a]" in pd and "OUT[b]" in pd and "OUT[c]" in pd


# ── Helpers ──────────────────────────────────────────────────────────────────────

def test_accumulate_ranks_by_coupling_and_truncates():
    st = _st("v", deps=["lo", "hi"], coupling={"lo": COUPLING["weak"], "hi": COUPLING["critical"]})
    outputs = {"lo": "LOWOUT", "hi": "HIGHOUT"}
    # budget big enough for both → high-coupling first
    full = accumulate_predecessors(st, outputs, budget=1000)
    assert full.index("[from hi]") < full.index("[from lo]")
    # tiny budget → only the most-coupled predecessor survives
    tight = accumulate_predecessors(st, outputs, budget=15)
    assert "hi" in tight and "[from lo]" not in tight


def test_compose_structure():
    p = compose("the task", global_context="ctx", forward_context="prior")
    assert "## Shared context" in p and "ctx" in p
    assert "## Relevant prior results" in p and "prior" in p
    assert "## Your task" in p and "the task" in p


def test_registry_dispatch():
    for topo in (Topology.PARALLEL, Topology.SEQUENTIAL, Topology.HIERARCHICAL, Topology.HYBRID):
        assert callable(ex.get_executor(topo))


def test_results_cover_all_subtasks():
    dag = build([_st("a"), _st("b", deps=["a"]), _st("c", deps=["a"])])
    _, rs = _recorder()
    for topo in (Topology.PARALLEL, Topology.SEQUENTIAL, Topology.HIERARCHICAL, Topology.HYBRID):
        results = asyncio.run(ex.execute(topo, dag, _ctx(rs)))
        assert {r.id for r in results} == {"a", "b", "c"}, topo


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: executors")
