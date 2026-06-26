"""Unit tests for anet.core.AdaptOrch.decomposer (AdaptOrch Phase 1). Offline, deterministic."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.core.AdaptOrch import decomposer as dec
from anet.core.AdaptOrch import stage_models
from anet.core.AdaptOrch.dag import COMPLEXITY_WEIGHT, COUPLING, DEFAULT_COUPLING, TaskDAG

AGENTS = [
    {"name": "code_agent", "task_types": ["write code", "tests"]},
    {"name": "research_agent", "task_types": ["web search"]},
]


# ── Fast-path ───────────────────────────────────────────────────────────────────

def test_trivial_fast_path():
    d = dec.parse_decomposition({"trivial": True, "reply": "Hello!"})
    assert d.trivial and d.reply == "Hello!" and d.subtasks == []


def test_trivial_has_no_dag():
    d = dec.parse_decomposition({"trivial": True, "reply": "hi"})
    try:
        dec.to_dag(d); assert False
    except ValueError:
        pass


# ── Parsing + canonical mapping ─────────────────────────────────────────────────

def test_complexity_maps_to_weight():
    d = dec.parse_decomposition({"subtasks": [
        {"id": "a", "complexity": "low"},
        {"id": "b", "complexity": "high"},
        {"id": "c", "complexity": "bogus"},   # invalid → default medium
    ]})
    w = {s.id: s.w for s in d.subtasks}
    assert w["a"] == COMPLEXITY_WEIGHT["low"]
    assert w["b"] == COMPLEXITY_WEIGHT["high"]
    assert w["c"] == COMPLEXITY_WEIGHT["medium"]


def test_estimated_tokens_preferred_over_complexity():
    d = dec.parse_decomposition({"subtasks": [
        {"id": "a", "estimated_tokens": 500, "complexity": "low"},   # number wins
        {"id": "b", "complexity": "high"},                           # falls back to bucket
        {"id": "c", "estimated_tokens": 0},                          # invalid → bucket(medium)
        {"id": "e", "estimated_tokens": True},                       # bool is not a token count
    ]})
    w = {s.id: s.w for s in d.subtasks}
    assert w["a"] == 500.0
    assert w["b"] == COMPLEXITY_WEIGHT["high"]
    assert w["c"] == COMPLEXITY_WEIGHT["medium"]
    assert w["e"] == COMPLEXITY_WEIGHT["medium"]


def test_prompt_contains_b1_rules():
    full = "\n".join(m["content"] for m in dec.build_prompt("Fix the bug", AGENTS))
    assert "MAXIMIZE PARALLELISM" in full
    assert "localize files" in full            # the typical coding decomposition
    assert "estimated_tokens" in full


def test_coupling_single_label_applies_to_all_deps():
    d = dec.parse_decomposition({"subtasks": [
        {"id": "a"},
        {"id": "b"},
        {"id": "c", "depends_on": ["a", "b"], "coupling": "weak"},
    ]})
    c = next(s for s in d.subtasks if s.id == "c")
    assert c.coupling == {"a": COUPLING["weak"], "b": COUPLING["weak"]}


def test_coupling_per_dependency_object():
    d = dec.parse_decomposition({"subtasks": [
        {"id": "a"}, {"id": "b"},
        {"id": "c", "depends_on": ["a", "b"], "coupling": {"a": "critical", "b": "none"}},
    ]})
    c = next(s for s in d.subtasks if s.id == "c")
    assert c.coupling == {"a": COUPLING["critical"], "b": COUPLING["none"]}


def test_request_class_normalized():
    assert dec.parse_decomposition({"subtasks": [{"id": "a"}], "request_class": "CODING"}).request_class == "coding"
    assert dec.parse_decomposition({"subtasks": [{"id": "a"}], "request_class": "weird"}).request_class == "general"


def test_subtask_missing_id_raises():
    try:
        dec.parse_decomposition({"subtasks": [{"description": "no id"}]}); assert False
    except ValueError:
        pass


def test_non_trivial_requires_subtasks():
    try:
        dec.parse_decomposition({"trivial": False, "subtasks": []}); assert False
    except ValueError:
        pass


# ── Integration with Phase 2 (dag.build) ────────────────────────────────────────

def test_to_dag_builds_valid_graph():
    d = dec.parse_decomposition({"request_class": "coding", "subtasks": [
        {"id": "find", "agent": "code_agent", "complexity": "low", "depends_on": []},
        {"id": "fix", "agent": "code_agent", "complexity": "high",
         "depends_on": ["find"], "coupling": "strong"},
        {"id": "test", "agent": "code_agent", "complexity": "medium",
         "depends_on": ["fix"], "coupling": "critical"},
    ]})
    g = dec.to_dag(d)
    assert isinstance(g, TaskDAG)
    assert g.num_nodes == 3 and g.omega == 1            # a chain
    assert g.layers == [["find"], ["fix"], ["test"]]
    assert g.delta == COMPLEXITY_WEIGHT["low"] + COMPLEXITY_WEIGHT["high"] + COMPLEXITY_WEIGHT["medium"]


# ── Prompt uses the AdaptOrch template ──────────────────────────────────────────

def test_prompt_contains_adaptorch_template_and_agents():
    msgs = dec.build_prompt("Build a thing", AGENTS)
    user = msgs[-1]["content"]
    assert "decompose it into independent subtasks" in user
    assert "Estimated complexity (tokens: low/medium/high)" in user
    assert "Context coupling with dependencies (none/weak/strong/critical)" in user
    assert "Task: Build a thing" in user
    assert "code_agent" in user and "research_agent" in user


# ── stage_models resolution ─────────────────────────────────────────────────────

def test_stage_model_falls_back_to_manager():
    import anet.core.config_loader as cl
    saved = cl.load
    try:
        cl.load = lambda: {"manager": {"model": "mgr-model", "provider": "openrouter"}}
        assert stage_models.stage_model("decomposer") == ("mgr-model", "openrouter")
        cl.load = lambda: {
            "manager": {"model": "mgr-model", "provider": "openrouter"},
            "orchestration": {"decomposer": {"model": "cheap", "provider": "google"}},
        }
        assert stage_models.stage_model("decomposer") == ("cheap", "google")
        # a stage with no override still uses manager
        assert stage_models.stage_model("synthesizer") == ("mgr-model", "openrouter")
    finally:
        cl.load = saved


# ── Async decompose() with a stubbed model call ─────────────────────────────────

def test_decompose_async_with_stub():
    import json as _json
    async def fake_stage_call(stage, messages, **kw):
        assert stage == "decomposer"
        return _json.dumps({"trivial": False, "request_class": "coding", "subtasks": [
            {"id": "a", "agent": "code_agent", "complexity": "medium", "depends_on": []},
            {"id": "b", "agent": "code_agent", "complexity": "low",
             "depends_on": ["a"], "coupling": "strong"},
        ]})
    saved = stage_models.stage_call
    try:
        stage_models.stage_call = fake_stage_call
        d = asyncio.run(dec.decompose("Refactor the auth module", AGENTS))
        assert not d.trivial and len(d.subtasks) == 2
        g = dec.to_dag(d)
        assert g.layers == [["a"], ["b"]]
    finally:
        stage_models.stage_call = saved


def test_decompose_requires_task():
    try:
        asyncio.run(dec.decompose("   ", AGENTS)); assert False
    except ValueError:
        pass


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: decomposer")
