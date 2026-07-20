"""Unit tests for anet.QweenBee.executors.temporal (Phase 3: EXECUTEGENERATEDDAG)
and anet.QweenBee.evidence. Pure, offline — run_subtask is faked, no LLM calls."""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.QweenBee.dag import Subtask
from anet.QweenBee.executors import ExecContext, StepResult
from anet.QweenBee.executors import temporal
from anet.QweenBee.tgraph import TemporalDAG
from anet.QweenBee import tgraph, evidence


def _st(id):
    return Subtask(id=id, description=f"do {id}", agent="code_agent")


def make_ctx(fail_nodes=None):
    """Fake run_subtask: records (node_id, prompt) calls and returns a versioned
    output "{id}-v{n}" per call (n increments per node), so tests can tell a
    node's round-0 belief apart from a later revision."""
    fail_nodes = fail_nodes or set()
    calls: list[tuple[str, str]] = []
    counts: dict[str, int] = {}

    async def run_subtask(st, prompt):
        calls.append((st.id, prompt))
        n = counts.get(st.id, 0) + 1
        counts[st.id] = n
        if st.id in fail_nodes:
            return StepResult(id=st.id, agent=st.agent, description=st.description,
                              output="[subtask failed: boom]", success=False, error="boom")
        return StepResult(id=st.id, agent=st.agent, description=st.description,
                          output=f"{st.id}-v{n}")

    ctx = ExecContext(run_subtask=run_subtask, global_context="", on_status=lambda _s: None)
    return ctx, calls


# ── 1. Source init + star receive ───────────────────────────────────────────

def test_source_init_and_star_receive():
    subs = [_st("a"), _st("b"), _st("c"), _st("h")]
    g = TemporalDAG(nodes=["a", "b", "c", "h"], T=1,
                    edges=[("a", "h", 1), ("b", "h", 1), ("c", "h", 1)], holder="h")
    ctx, calls = make_ctx()
    results, trace = asyncio.run(temporal.run(g, subs, ctx))

    round0 = [t for t in trace if t.round == 0]
    assert {t.node for t in round0} == {"a", "b", "c"}
    assert all(t.inbox_from == [] for t in round0)

    round1 = [t for t in trace if t.round == 1]
    assert len(round1) == 1 and round1[0].node == "h"
    assert round1[0].inbox_from == ["a", "b", "c"]

    h_prompt = [p for nid, p in calls if nid == "h"][0]
    assert "[from a]" in h_prompt and "[from b]" in h_prompt and "[from c]" in h_prompt
    assert "a-v1" in h_prompt and "b-v1" in h_prompt and "c-v1" in h_prompt

    by_id = {r.id: r for r in results}
    assert by_id["h"].output == "h-v1"


# ── 2. Barrier semantics ─────────────────────────────────────────────────────

def test_barrier_semantics_same_round():
    # a->b and b->c both at t=1: b is forced to init in round 0 (rule 3, it must
    # send at t=1 before it would naturally receive), then revises in round 1
    # while c also runs in round 1 — c must see b's round-0 belief, not b's
    # same-round update.
    subs = [_st("a"), _st("b"), _st("c")]
    g = TemporalDAG(nodes=["a", "b", "c"], T=1, edges=[("a", "b", 1), ("b", "c", 1)], holder="c")
    ctx, calls = make_ctx()
    results, trace = asyncio.run(temporal.run(g, subs, ctx))

    c_prompt = [p for nid, p in calls if nid == "c"][0]
    assert "b-v1" in c_prompt
    assert "b-v2" not in c_prompt

    by_id = {r.id: r for r in results}
    assert by_id["c"].output == "c-v1"
    assert by_id["b"].output == "b-v2"   # b's FINAL (latest) belief is its round-1 update


# ── 3. Revision + trace shape ───────────────────────────────────────────────

def test_revision_prompt_and_trace_shape():
    subs = [_st("a"), _st("b"), _st("h")]
    g = TemporalDAG(nodes=["a", "b", "h"], T=2, edges=[("a", "h", 1), ("b", "h", 2)], holder="h")
    ctx, calls = make_ctx()
    results, trace = asyncio.run(temporal.run(g, subs, ctx))

    h_prompts = [p for nid, p in calls if nid == "h"]
    assert len(h_prompts) == 2
    assert "[your previous output]" not in h_prompts[0]
    assert "[your previous output]\nh-v1" in h_prompts[1]
    assert "[from b]" in h_prompts[1]

    assert len(trace) == 4
    assert sorted((t.round, t.node) for t in trace) == [(0, "a"), (0, "b"), (1, "h"), (2, "h")]
    h_traces = [t for t in trace if t.node == "h"]
    assert h_traces[0].inbox_from == ["a"] and h_traces[0].round == 1
    assert h_traces[1].inbox_from == ["b"] and h_traces[1].round == 2

    by_id = {r.id: r for r in results}
    assert by_id["h"].output == "h-v2"


# ── 4. Forced init (sends before it would naturally receive) ───────────────

def test_forced_init_sends_before_receiving():
    # x sends to y at t=1 but its own inbound edge (from m) isn't until t=2:
    # without forced init, x would have no belief to send at t=1.
    subs = [_st("m"), _st("x"), _st("y")]
    g = TemporalDAG(nodes=["m", "x", "y"], T=2, edges=[("x", "y", 1), ("m", "x", 2)], holder="y")
    ctx, calls = make_ctx()
    results, trace = asyncio.run(temporal.run(g, subs, ctx))

    assert {t.node for t in trace if t.round == 0} == {"m", "x"}
    x_round0 = [t for t in trace if t.node == "x" and t.round == 0][0]
    assert x_round0.inbox_from == []

    y_prompt = [p for nid, p in calls if nid == "y"][0]
    assert "x-v1" in y_prompt

    by_id = {r.id: r for r in results}
    assert by_id["y"].output == "y-v1"


# ── 5. Stranded-node sweep ───────────────────────────────────────────────────

def test_stranded_node_gets_swept():
    # Intentionally malformed graph (edge time beyond T) — tgraph.validate()
    # would reject this; run() must still guarantee every subtask executes.
    subs = [_st("a"), _st("stray")]
    g = TemporalDAG(nodes=["a", "stray"], T=1, edges=[("a", "stray", 5)], holder="a")
    ctx, calls = make_ctx()
    results, trace = asyncio.run(temporal.run(g, subs, ctx))

    stray_traces = [t for t in trace if t.node == "stray"]
    assert len(stray_traces) == 1
    assert stray_traces[0].round == 2   # T+1 sweep round
    assert stray_traces[0].inbox_from == []

    by_id = {r.id: r for r in results}
    assert by_id["stray"].output == "stray-v1"


# ── 6. Round instructions ────────────────────────────────────────────────────

def test_round_instruction_appears_only_in_its_round():
    subs = [_st("a"), _st("b")]
    g = TemporalDAG(nodes=["a", "b"], T=1, edges=[("a", "b", 1)], holder="b",
                    round_instructions={1: "audit the merge"})
    ctx, calls = make_ctx()
    asyncio.run(temporal.run(g, subs, ctx))

    a_prompt = [p for nid, p in calls if nid == "a"][0]
    b_prompt = [p for nid, p in calls if nid == "b"][0]
    assert "This round: audit the merge" not in a_prompt
    assert "This round: audit the merge" in b_prompt


# ── 7. Failure flow ──────────────────────────────────────────────────────────

def test_failure_flows_to_receiver_and_does_not_raise():
    subs = [_st("a"), _st("b")]
    g = TemporalDAG(nodes=["a", "b"], T=1, edges=[("a", "b", 1)], holder="b")
    ctx, calls = make_ctx(fail_nodes={"a"})
    results, trace = asyncio.run(temporal.run(g, subs, ctx))

    by_id = {r.id: r for r in results}
    assert by_id["a"].success is False
    b_prompt = [p for nid, p in calls if nid == "b"][0]
    assert "[subtask failed" in b_prompt
    a_trace = [t for t in trace if t.node == "a"][0]
    assert a_trace.success is False


# ── 8. evidence.log_run ──────────────────────────────────────────────────────

def test_evidence_log_run_writes_jsonl():
    g = TemporalDAG(nodes=["a", "b"], T=1, edges=[("a", "b", 1)], holder="b")
    p = Path(tempfile.mkdtemp()) / "queenbee_evidence.jsonl"
    evidence.log_run(graph=g, source="planner", motifs=tgraph.motifs(g),
                     request_class="coding", spec={"T": 1}, outcome={"consistency": 0.9},
                     log_path=p)
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["source"] == "planner" and rec["holder"] == "b"
    assert rec["outcome"]["consistency"] == 0.9
    assert "graph_hash" in rec and isinstance(rec["graph_hash"], str)


def test_evidence_log_run_never_raises_on_bad_path():
    g = TemporalDAG(nodes=["a"], T=1, edges=[], holder="a")
    evidence.log_run(graph=g, source="fallback", motifs=tgraph.motifs(g),
                     request_class=None, spec=None, outcome=None,
                     log_path="/no/such/dir/x/y/z.jsonl")


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: qweenbee_temporal")
