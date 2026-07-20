"""Unit tests for anet.QweenBee.tgraph + anet.QweenBee.planner (Phase 2:
the LLM planner's temporal communication DAG). Pure, offline, deterministic —
no LLM calls (plan() itself is exercised in shadow mode manually; these test
the pure helpers around it: parsing, validation, motifs, hashing, fallback,
prompt construction)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.QweenBee import tgraph
from anet.QweenBee.tgraph import TemporalDAG
from anet.QweenBee.dag import Subtask
from anet.QweenBee import planner as plannermod
from anet.QweenBee.planner import build_prompt, parse_plan


def _st(id, deps=None, w=1.0):
    return Subtask(id=id, w=w, depends_on=deps or [], description=f"do {id}", agent="code_agent")


_LIMS = {"t_max": 4, "m_max": 16, "beta": 3}


# ── parse_plan: happy path ──────────────────────────────────────────────────

def test_parse_plan_happy_path_both_edge_formats():
    data = {
        "T": 2,
        "rounds": [
            {"t": 1, "edges": [["s1", "s3"], ["s2", "s3"]], "instruction": "merge inputs"},
            {"t": 2, "edges": [{"from": "s3", "to": "s1"}]},
        ],
        "holder": "s3",
        "rationale": "staged merge then audit",
    }
    g = parse_plan(data, ["s1", "s2", "s3"])
    assert g.T == 2
    assert set(g.edges) == {("s1", "s3", 1), ("s2", "s3", 1), ("s3", "s1", 2)}
    assert g.holder == "s3"
    assert g.round_instructions == {1: "merge inputs"}
    assert g.rationale == "staged merge then audit"


def test_parse_plan_infers_T_when_omitted():
    data = {"rounds": [{"t": 1, "edges": [["s1", "s2"]]}, {"t": 2, "edges": [["s2", "s1"]]}], "holder": "s1"}
    g = parse_plan(data, ["s1", "s2"])
    assert g.T == 2


# ── parse_plan: rejections ──────────────────────────────────────────────────

def test_parse_plan_rejects_unknown_node():
    data = {"rounds": [{"t": 1, "edges": [["s1", "sX"]]}], "holder": "s1"}
    try:
        parse_plan(data, ["s1", "s2"])
        assert False, "expected ValueError"
    except ValueError as e:
        assert "unknown node" in str(e) and "sX" in str(e)


def test_parse_plan_rejects_malformed_edge():
    data = {"rounds": [{"t": 1, "edges": [["s1"]]}], "holder": "s1"}
    try:
        parse_plan(data, ["s1", "s2"])
        assert False, "expected ValueError"
    except ValueError as e:
        assert "malformed" in str(e)


def test_parse_plan_rejects_missing_holder():
    data = {"rounds": [{"t": 1, "edges": [["s1", "s2"]]}]}
    try:
        parse_plan(data, ["s1", "s2"])
        assert False, "expected ValueError"
    except ValueError as e:
        assert "holder" in str(e)


def test_parse_plan_rejects_holder_outside_nodes():
    data = {"rounds": [{"t": 1, "edges": []}], "holder": "sZ"}
    try:
        parse_plan(data, ["s1", "s2"])
        assert False, "expected ValueError"
    except ValueError as e:
        assert "sZ" in str(e)


# ── validate: each Eq. 3 violation ──────────────────────────────────────────

def test_validate_ok_graph_has_no_violations():
    g = TemporalDAG(nodes=["a", "b"], T=1, edges=[("a", "b", 1)], holder="b")
    assert tgraph.validate(g, limits=_LIMS) == []


def test_validate_fan_in_exceeds_beta():
    g = TemporalDAG(nodes=["a", "b", "c", "d"], T=1,
                    edges=[("a", "d", 1), ("b", "d", 1), ("c", "d", 1)], holder="d")
    v = tgraph.validate(g, limits={"t_max": 4, "m_max": 16, "beta": 2})
    assert any("fan-in" in x for x in v)


def test_validate_T_exceeds_t_max():
    g = TemporalDAG(nodes=["a", "b"], T=5, edges=[("a", "b", 1)], holder="b")
    v = tgraph.validate(g, limits=_LIMS)
    assert any("exceeds t_max" in x for x in v)


def test_validate_edges_exceed_m_max():
    g = TemporalDAG(nodes=["a", "b", "c", "d"], T=1,
                    edges=[("a", "b", 1), ("a", "c", 1), ("a", "d", 1)], holder="d")
    v = tgraph.validate(g, limits={"t_max": 4, "m_max": 2, "beta": 5})
    assert any("exceeds m_max" in x for x in v)


def test_validate_bad_holder():
    g = TemporalDAG(nodes=["a", "b"], T=1, edges=[], holder="z")
    v = tgraph.validate(g, limits=_LIMS)
    assert any("is not among the graph's nodes" in x for x in v)


def test_validate_t_out_of_range():
    g = TemporalDAG(nodes=["a", "b"], T=1, edges=[("a", "b", 2)], holder="b")
    v = tgraph.validate(g, limits=_LIMS)
    assert any("outside valid range" in x for x in v)


def test_validate_duplicate_edge():
    g = TemporalDAG(nodes=["a", "b"], T=1, edges=[("a", "b", 1), ("a", "b", 1)], holder="b")
    v = tgraph.validate(g, limits=_LIMS)
    assert any("duplicate edge" in x for x in v)


def test_validate_self_edge():
    g = TemporalDAG(nodes=["a"], T=1, edges=[("a", "a", 1)], holder="a")
    v = tgraph.validate(g, limits=_LIMS)
    assert any("self-edge" in x for x in v)


# ── Fallback graph (static TaskDAG layers -> temporal graph) ───────────────

def test_fallback_single_sink_diamond():
    subs = [_st("a"), _st("b", deps=["a"]), _st("c", deps=["a"]), _st("d", deps=["b", "c"])]
    fb = plannermod._fallback_graph(subs, _LIMS)
    assert fb.holder == "d"
    assert fb.T == 2   # 3 layers -> T = 3-1
    assert set(fb.edges) == {("a", "b", 1), ("a", "c", 1), ("b", "d", 2), ("c", "d", 2)}
    assert tgraph.validate(fb, limits=_LIMS) == []


def test_fallback_caps_fan_in():
    subs = [_st("b1"), _st("b2"), _st("b3"), _st("b4"),
            _st("sink", deps=["b1", "b2", "b3", "b4"])]
    lims = {"t_max": 4, "m_max": 16, "beta": 2}
    fb = plannermod._fallback_graph(subs, lims)
    assert fb.holder == "sink"
    assert tgraph.validate(fb, limits=lims) == []
    fan_in = {}
    for s, d, t in fb.edges:
        fan_in[(d, t)] = fan_in.get((d, t), 0) + 1
    assert all(n <= 2 for n in fan_in.values())
    assert fb.T > 1   # staged reduction needed an extra round


def test_fallback_clamps_rounds_to_t_max():
    subs = [_st("a"), _st("b", deps=["a"]), _st("c", deps=["a"]), _st("d", deps=["b", "c"])]
    lims = {"t_max": 1, "m_max": 16, "beta": 5}
    fb = plannermod._fallback_graph(subs, lims)
    assert fb.T == 1
    assert set(fb.edges) == {("a", "b", 1), ("a", "c", 1), ("b", "d", 1), ("c", "d", 1)}
    assert tgraph.validate(fb, limits=lims) == []


def test_fallback_single_node():
    fb = plannermod._fallback_graph([_st("only")], _LIMS)
    assert fb.holder == "only"
    assert fb.edges == []
    assert tgraph.validate(fb, limits=_LIMS) == []


# ── motifs φ(G) ──────────────────────────────────────────────────────────────

def test_motifs_star_reduce():
    g = TemporalDAG(nodes=["a", "b", "h"], T=1, edges=[("a", "h", 1), ("b", "h", 1)], holder="h")
    m = tgraph.motifs(g)
    assert m["steps"] == 1
    assert m["messages"] == 2
    assert m["max_fan_in"] == 2
    assert m["fan_in_bucket"] == "pair"
    assert m["has_sink"] is True
    assert m["num_sinks"] == 1
    assert m["has_audit_edge"] is False
    assert m["reduction_depth"] == 1


def test_motifs_chain():
    g = TemporalDAG(nodes=["a", "b", "c"], T=2, edges=[("a", "b", 1), ("b", "c", 2)], holder="c")
    m = tgraph.motifs(g)
    assert m["max_fan_in"] == 1
    assert m["fan_in_bucket"] == "single"
    assert m["reduction_depth"] == 2


def test_motifs_audit_edge():
    g = TemporalDAG(nodes=["w1", "w2", "chk"], T=2,
                    edges=[("w1", "chk", 1), ("chk", "w2", 2)], holder="chk")
    m = tgraph.motifs(g)
    assert m["has_audit_edge"] is True


# ── canonical_hash (structural dedup) ───────────────────────────────────────

def test_canonical_hash_invariant_to_relabeling():
    g1 = TemporalDAG(nodes=["s1", "s2", "s3"], T=2,
                     edges=[("s1", "s3", 1), ("s2", "s3", 1), ("s3", "s1", 2)], holder="s3")
    g2 = TemporalDAG(nodes=["x", "y", "z"], T=2,
                     edges=[("x", "z", 1), ("y", "z", 1), ("z", "x", 2)], holder="z")
    assert tgraph.canonical_hash(g1) == tgraph.canonical_hash(g2)


def test_canonical_hash_differs_for_different_shape():
    g1 = TemporalDAG(nodes=["s1", "s2", "s3"], T=2,
                     edges=[("s1", "s3", 1), ("s2", "s3", 1), ("s3", "s1", 2)], holder="s3")
    g2 = TemporalDAG(nodes=["p", "q"], T=1, edges=[("p", "q", 1)], holder="q")
    assert tgraph.canonical_hash(g1) != tgraph.canonical_hash(g2)


# ── build_prompt ─────────────────────────────────────────────────────────────

def test_build_prompt_embeds_limits_and_subtask_ids():
    subs = [_st("s1"), _st("s2", deps=["s1"])]
    msgs = build_prompt("build a thing", subs, _LIMS)
    assert len(msgs) == 2 and msgs[0]["role"] == "system" and msgs[1]["role"] == "user"
    sys_text = msgs[0]["content"]
    assert "T <= 4" in sys_text
    assert "16 total edges" in sys_text
    assert "3 inbound edges" in sys_text
    user_text = msgs[1]["content"]
    assert "s1" in user_text and "s2" in user_text
    assert "depends_on: s1" in user_text
    assert "build a thing" in user_text


def test_build_prompt_includes_skills_ctx_verbatim():
    subs = [_st("s1")]
    msgs = build_prompt("task", subs, _LIMS, skills_ctx="Preserve: staged reduction works well")
    assert "Preserve: staged reduction works well" in msgs[1]["content"]


def test_build_prompt_omits_skills_block_when_empty():
    subs = [_st("s1")]
    msgs = build_prompt("task", subs, _LIMS)
    assert "Design rules from prior runs" not in msgs[1]["content"]


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: qweenbee_planner")
