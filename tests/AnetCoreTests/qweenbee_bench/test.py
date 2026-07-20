"""Unit tests for anet.QweenBee.bench, anet.QweenBee.evolve, and the Phase 5
multi-candidate ranking in anet.QweenBee.planner. Pure, offline — no real LLM
calls; the planner/evolve LLM boundary is faked via manual monkeypatching
(save/restore, matching the router test suite's pattern)."""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.QweenBee import bench, evolve as evolve_mod, skills, planner as plannermod
from anet.QweenBee.dag import Subtask
from anet.QweenBee.tgraph import TemporalDAG

_MOTIFS = {"steps": 2, "messages": 3, "max_fan_in": 2, "fan_in_bucket": "pair",
          "has_sink": True, "num_sinks": 1, "has_audit_edge": False, "reduction_depth": 2}


def _row(run_id, request_class, n_nodes, graph_hash, *, source="planner",
        consistency=1.0, ts="t0"):
    return {
        "ts": ts, "run_id": run_id, "source": source, "graph_hash": graph_hash,
        "T": 2, "n_nodes": n_nodes, "n_edges": 3, "holder": "s3",
        "motifs": dict(_MOTIFS), "request_class": request_class,
        "spec": {"T": 1, "rounds": [], "holder": "s1"} if source != "fallback" else None,
        "outcome": {"failures": 0, "consistency": consistency, "tokens": 0},
    }


def _write(rows, path):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _tmp(name):
    return Path(tempfile.mkdtemp()) / name


# ── 1. Suite loading ──────────────────────────────────────────────────────────

def test_load_suite_all_and_splits():
    tasks_all = bench.load_suite()
    assert len(tasks_all) == 12
    train = bench.load_suite(split="train")
    val = bench.load_suite(split="val")
    assert len(train) == 8 and len(val) == 4
    assert all(t["split"] == "train" for t in train)
    assert all(t["split"] == "val" for t in val)


def test_load_suite_limit_truncates():
    assert len(bench.load_suite(limit=3)) == 3
    assert len(bench.load_suite(limit=0)) == 12   # 0 = no limit


def test_load_suite_unknown_split_raises():
    try:
        bench.load_suite(split="bogus")
        assert False, "expected ValueError"
    except ValueError:
        pass


# ── 2. The acceptance gate ───────────────────────────────────────────────────

def test_accept_gate():
    assert evolve_mod.accept(0.50, 0.47, epsilon=0.02) is True
    assert evolve_mod.accept(0.50, 0.49, epsilon=0.02) is False
    assert evolve_mod.accept(0.50, 0.50, epsilon=0.02) is False        # equal J -> reject
    assert evolve_mod.accept(0.50, 0.48, epsilon=0.02) is True         # exactly at the margin


# ── 3. Bank locking ───────────────────────────────────────────────────────────

def test_locked_bank_blocks_auto_refresh():
    p = _tmp("evidence.jsonl")
    bankp = _tmp("bank.json")
    rows = [_row(f"r{i}", "coding", 3, "hashA", ts=f"t{i}") for i in range(3)]
    _write(rows, p)

    skills.retrieve("coding", 3, bank_path=bankp, evidence_path=p)
    assert skills.load_bank(bankp)["evidence_rows"] == 3

    locked = skills.load_bank(bankp)
    locked["locked"] = True
    skills.save_bank(locked, bankp)

    more = [_row(f"c{i}", "coding", 3, "hashC", ts=f"c{i}") for i in range(3)]
    with open(p, "a", encoding="utf-8") as f:
        for r in more:
            f.write(json.dumps(r) + "\n")

    skills.retrieve("coding", 3, bank_path=bankp, evidence_path=p)
    assert skills.load_bank(bankp)["evidence_rows"] == 3   # unchanged: locked, no refresh
    assert skills.lock_state(bankp) is True


def test_unlocked_bank_still_auto_refreshes():
    # Phase 4 behavior must survive untouched for banks that were never evolved.
    p = _tmp("evidence.jsonl")
    bankp = _tmp("bank.json")
    rows = [_row(f"r{i}", "coding", 3, "hashA", ts=f"t{i}") for i in range(3)]
    _write(rows, p)
    skills.retrieve("coding", 3, bank_path=bankp, evidence_path=p)
    more = [_row(f"c{i}", "coding", 3, "hashC", ts=f"c{i}") for i in range(3)]
    with open(p, "a", encoding="utf-8") as f:
        for r in more:
            f.write(json.dumps(r) + "\n")
    skills.retrieve("coding", 3, bank_path=bankp, evidence_path=p)
    assert skills.load_bank(bankp)["evidence_rows"] == 6


# ── 4. Quarantine on reject (evolve() with a faked bench boundary) ─────────

def test_evolve_reject_writes_quarantine_and_restores_bank():
    evp = _tmp("evidence.jsonl")
    bankp = _tmp("bank.json")
    qp = _tmp("quarantine.json")
    suitep = _tmp("suite.json")

    seed_rows = [_row(f"r{i}", "coding", 3, "hashA", ts=f"t{i}") for i in range(3)]
    _write(seed_rows, evp)
    skills.save_bank({"generated_at": None, "evidence_rows": 0, "skills": [], "locked": False}, bankp)

    val_tasks = [{"id": "v1", "split": "val", "request_class": "coding", "prompt": "x"}]
    with open(suitep, "w", encoding="utf-8") as f:
        json.dump(val_tasks, f)

    pass_a_rows = [_row("pa1", "coding", 3, "hashA", consistency=1.0, ts="pa1")]   # good
    pass_b_rows = [_row("pb1", "coding", 3, "hashA", consistency=0.0, ts="pb1")]   # bad
    calls = {"n": 0}

    async def fake_run_suite(engine_name, engine, tasks, **kw):
        idx = calls["n"]; calls["n"] += 1
        rows = pass_a_rows if idx == 0 else pass_b_rows
        with open(evp, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        return []

    saved = (bench.build_agents_and_tools, bench.make_engines, bench.run_suite)
    bench.build_agents_and_tools = lambda: ([], {})
    bench.make_engines = lambda names, a, t: {n: object() for n in names}
    bench.run_suite = fake_run_suite
    try:
        result = asyncio.run(evolve_mod.evolve(
            suite_path=suitep, bank_path=bankp, evidence_path=evp,
            quarantine_path=qp, epsilon=0.02))
    finally:
        bench.build_agents_and_tools, bench.make_engines, bench.run_suite = saved

    assert result["status"] == "rejected"
    assert qp.exists()
    q = json.loads(qp.read_text(encoding="utf-8"))
    assert abs(q["j_old"] - result["j_old"]) < 1e-9
    assert abs(q["j_new"] - result["j_new"]) < 1e-9

    active = skills.load_bank(bankp)
    assert active["locked"] is True
    assert active["skills"] == []   # S was empty -> restored


# ── 5. No-op detection ────────────────────────────────────────────────────────

def test_no_op_signature_detects_identical_skills():
    a = [{"id": "x1", "stats": {"n": 3, "mean_loss": 0.1, "risk_loss": 0.2}, "evidence": ["r1"]}]
    b = [{"id": "x1", "stats": {"n": 3, "mean_loss": 0.1, "risk_loss": 0.2}, "evidence": ["r2", "r3"]}]
    assert evolve_mod._skills_signature(a) == evolve_mod._skills_signature(b)   # stats-only

    c = [{"id": "x1", "stats": {"n": 3, "mean_loss": 0.1, "risk_loss": 0.25}}]
    assert evolve_mod._skills_signature(a) != evolve_mod._skills_signature(c)


# ── 6. Multi-candidate ranking (Step 5) ─────────────────────────────────────

def test_rank_candidate_scoring_and_unscored_last():
    g_scored = TemporalDAG(nodes=["a", "b"], T=1, edges=[("a", "b", 1)], holder="b")
    g_unscored = TemporalDAG(nodes=["a", "b", "c"], T=2,
                             edges=[("a", "b", 1), ("b", "c", 2)], holder="c")
    table = {"steps=1": {"n": 5, "mean_loss": 0.05, "risk_loss": 0.10}}
    assert plannermod._rank_candidate(g_scored, table) == 0.10
    assert plannermod._rank_candidate(g_unscored, table) is None


def test_candidate_sort_key_ties_and_none_last():
    scored = [(None, 1, "specB", "gB"), (0.3, 0, "specA", "gA"), (0.3, 2, "specC", "gC")]
    scored.sort(key=lambda t: (t[0] is None, t[0] if t[0] is not None else 0.0, t[1]))
    assert [s[1] for s in scored] == [0, 2, 1]   # tie keeps generation order; None sorts last


def test_plan_end_to_end_multi_candidate_picks_scored_over_unscored():
    from anet.QweenBee import stage_models as sm
    from anet.QweenBee import tgraph as tg

    subs = [Subtask(id="s1", description="a", agent="code_agent"),
           Subtask(id="s2", description="b", agent="code_agent", depends_on=["s1"])]
    good_spec = {"T": 1, "rounds": [{"t": 1, "edges": [["s1", "s2"]]}], "holder": "s2"}
    bad_spec = {"T": 2, "rounds": [{"t": 1, "edges": []}, {"t": 2, "edges": [["s1", "s2"]]}], "holder": "s2"}
    specs = [good_spec, bad_spec]
    calls: list[str] = []

    async def fake_stage_call(stage, messages, max_tokens=1200, **kw):
        calls.append(messages[-1]["content"])
        return json.dumps(specs[len(calls) - 1])

    saved_call, saved_motif, saved_limits = sm.stage_call, skills.motif_table, tg.limits
    sm.stage_call = fake_stage_call
    skills.motif_table = lambda evidence_path=None: {"steps=1": {"n": 5, "mean_loss": 0.05, "risk_loss": 0.10}}
    base_limits = saved_limits()
    tg.limits = lambda: {**base_limits, "planner_candidates": 2}
    try:
        pd = asyncio.run(plannermod.plan("do stuff", subs))
    finally:
        sm.stage_call, skills.motif_table, tg.limits = saved_call, saved_motif, saved_limits

    assert pd.source == "planner"
    assert pd.graph.T == 1   # the scored candidate wins over the unscored one
    assert pd.candidates_info == {"candidates": 2, "candidate_rank_scores": [0.10, None]}
    assert "structurally DIFFERENT" in calls[1]


def test_plan_default_k1_unaffected():
    # planner_candidates defaults to 1 -> candidates_info stays None (no behavior change).
    from anet.QweenBee import stage_models as sm

    subs = [Subtask(id="s1", description="a", agent="code_agent")]
    spec = {"T": 1, "rounds": [{"t": 1, "edges": []}], "holder": "s1"}

    async def fake_stage_call(stage, messages, max_tokens=1200, **kw):
        return json.dumps(spec)

    saved_call = sm.stage_call
    sm.stage_call = fake_stage_call
    try:
        pd = asyncio.run(plannermod.plan("do stuff", subs))
    finally:
        sm.stage_call = saved_call

    assert pd.source == "planner"
    assert pd.candidates_info is None


# ── 7. Result aggregation ────────────────────────────────────────────────────

def test_aggregate_results_latest_wins():
    rows = [
        {"engine": "adaptorch", "task_id": "t1", "ok": True, "seconds": 10, "tokens": 100, "qweenbee": None},
        {"engine": "adaptorch", "task_id": "t1", "ok": True, "seconds": 20, "tokens": 200, "qweenbee": None},
    ]
    latest = bench.aggregate_results(rows)
    assert latest[("adaptorch", "t1")]["seconds"] == 20


def test_summarize_computes_means():
    rows = [
        {"engine": "adaptorch", "task_id": "t1", "ok": True, "seconds": 10.0, "tokens": 100, "qweenbee": None},
        {"engine": "adaptorch", "task_id": "t2", "ok": False, "seconds": 5.0, "tokens": 50, "qweenbee": None},
        {"engine": "qweenbee", "task_id": "t1", "ok": True, "seconds": 8.0, "tokens": 80,
         "qweenbee": {"source": "planner", "consistency": 0.9, "skills_used": False}},
        {"engine": "qweenbee", "task_id": "t2", "ok": True, "seconds": 6.0, "tokens": 60,
         "qweenbee": {"source": "fallback", "consistency": 0.5, "skills_used": False}},
    ]
    summary = bench.summarize(rows)
    assert summary["adaptorch"]["n"] == 2 and summary["adaptorch"]["ok"] == 1
    assert summary["adaptorch"]["mean_seconds"] == 7.5
    assert summary["qweenbee"]["mean_consistency"] == 0.7
    assert summary["qweenbee"]["fallback_rate"] == 0.5


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: qweenbee_bench")
