"""Unit tests for the synthesis dispatcher (AdaptOrch Phase 5, operator model). Offline."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.core.AdaptOrch.router import Topology
from anet.core.AdaptOrch.executors import StepResult
from anet.core.AdaptOrch import synthesizer as syn
from anet.core.AdaptOrch import stage_models
from anet.core.AdaptOrch.synthesizer import synthesize, select_operator
from anet.core.AdaptOrch.synthesizer.consistency import lexical_vectors, consistency_score

LEX = lexical_vectors


def _res(*outs):
    return [StepResult(id=f"s{i}", agent="a", description="d", output=o) for i, o in enumerate(outs)]


# Canned LLM: combine/resolve calls return a tagged echo so we can see which fired.
async def _fake_stream(stage, messages, on_token, **kw):
    sys_prompt = messages[0]["content"]
    tag = "COMPOSE" if "DIFFERENT part" in sys_prompt else \
          "AGGREGATE" if "unified summary" in sys_prompt else \
          "RANK" if "Rank them" in sys_prompt else \
          "RESOLVE" if "CONTRADICTORY" in sys_prompt else "LLM"
    return f"{tag}_OUT"


def _install_fake():
    stage_models.stage_call_stream = _fake_stream
    async def _call(stage, messages, **kw): return await _fake_stream(stage, messages, None, **kw)
    stage_models.stage_call = _call


def _run(**kw):
    kw.setdefault("embed", LEX)
    _install_fake()
    return asyncio.run(synthesize(**kw))


# ── select_operator (pure) ───────────────────────────────────────────────────────

def test_select_single_and_sequential():
    assert select_operator(topology=Topology.PARALLEL, request_class="general", n_outputs=1) == "single"
    assert select_operator(topology=Topology.SEQUENTIAL, request_class="general", n_outputs=3) == "sequential_last"


def test_select_default_by_class():
    assert select_operator(topology=Topology.PARALLEL, request_class="coding", n_outputs=3) == "compose"
    assert select_operator(topology=Topology.PARALLEL, request_class="general", n_outputs=3) == "compose"
    assert select_operator(topology=Topology.PARALLEL, request_class="rag", n_outputs=3) == "aggregate"


def test_select_hint_wins():
    assert select_operator(topology=Topology.PARALLEL, request_class="coding", n_outputs=3, hint="vote") == "vote"
    # but passthrough still wins over hint when there's nothing to combine
    assert select_operator(topology=Topology.SEQUENTIAL, request_class="coding", n_outputs=3, hint="vote") == "sequential_last"


# ── The T2 fix: distinct parallel outputs → compose, NOT arbiter ────────────────

def test_distinct_parallel_outputs_compose_not_resolve():
    # three unrelated facts → low CS, but must compose (no spurious arbiter)
    outs = ["Python 3.14 is the latest", "Argentina won the 2022 World Cup", "Water boils at 100C"]
    assert consistency_score(outs, LEX) < 0.7          # low CS, like the real T2
    r = _run(results=_res(*outs), topology=Topology.PARALLEL, request_class="general")
    assert r.operator == "compose" and r.output == "COMPOSE_OUT"   # combined, never resolved


def test_rag_aggregates():
    r = _run(results=_res("finding A", "finding B"), topology=Topology.PARALLEL, request_class="rag")
    assert r.operator == "aggregate" and r.output == "AGGREGATE_OUT"


# ── Passthrough ──────────────────────────────────────────────────────────────────

def test_sequential_returns_last():
    r = _run(results=_res("first", "FINAL"), topology=Topology.SEQUENTIAL, request_class="coding")
    assert r.operator == "sequential_last" and r.output == "FINAL"


def test_single_passthrough():
    r = _run(results=_res("only"), topology=Topology.PARALLEL, request_class="general")
    assert r.operator == "single" and r.output == "only"


# ── Vote (hint) ──────────────────────────────────────────────────────────────────

def test_vote_majority_picks_consensus_no_llm():
    # two agree, one dissents → consensus wins, no LLM call
    r = _run(results=_res("the answer is 42", "the answer is 42", "the answer is 7"),
             topology=Topology.PARALLEL, request_class="reasoning", synthesis="vote")
    assert r.operator == "vote" and "42" in r.output


def test_vote_no_majority_escalates_to_resolve():
    # all three disjoint → no majority → resolve (arbiter)
    r = _run(results=_res("alpha", "beta", "gamma"), topology=Topology.PARALLEL,
             request_class="reasoning", synthesis="vote")
    assert r.operator == "resolve" and r.output == "RESOLVE_OUT"


# ── Resolve (hint) + reroute ─────────────────────────────────────────────────────

def test_resolve_hint_arbitrates():
    r = _run(results=_res("3.12 added X", "3.12 removed X"), topology=Topology.PARALLEL,
             request_class="general", synthesis="resolve")
    assert r.operator == "resolve" and r.output == "RESOLVE_OUT"


def test_resolve_reroute_terminates_at_max_iters():
    async def reroute(g):
        return Topology.PARALLEL, _res("3.12 added X", "3.12 removed X")
    r = _run(results=_res("3.12 added X", "3.12 removed X"), topology=Topology.PARALLEL,
             request_class="general", synthesis="resolve",
             dag=type("D", (), {"gamma": 0.0})(), reroute_fn=reroute, max_iters=4)
    assert r.operator == "resolve" and r.iterations == 4 and r.rerouted is True


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
