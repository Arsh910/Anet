"""Unit tests for anet.core.diet (AgentDiet trajectory reduction).
Pure, offline — the reflect model call is faked via monkeypatch."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.core import diet


def _base():
    """A trajectory base: system + user task (nothing here is ever reduced)."""
    return [
        {"role": "system", "content": "sys prompt"},
        {"role": "user", "content": "fix the bug"},
    ]


def _step(assistant_text, tool_contents, call_name="file_tool"):
    """One agent step: an assistant message with tool calls + its results."""
    msgs = [{
        "role": "assistant",
        "content": assistant_text,
        "tool_calls": [{"id": "c1", "function": {"name": call_name}}],
    }]
    for c in tool_contents:
        msgs.append({"role": "tool", "tool_call_id": "c1", "content": c})
    return msgs


# ── Config ────────────────────────────────────────────────────────────────────

def test_config_defaults_off():
    import anet.core.config_loader as cl
    saved = cl.load
    cl.load = lambda: {}
    try:
        cfg = diet.config()
    finally:
        cl.load = saved
    assert cfg["enabled"] is False
    assert (cfg["a"], cfg["b"], cfg["theta"]) == (2, 1, 500)


def test_config_reads_block_and_coerces():
    import anet.core.config_loader as cl
    saved = cl.load
    cl.load = lambda: {"diet": {"enabled": True, "a": "3", "theta": "1000",
                                "model": "m", "provider": "p"}}
    try:
        cfg = diet.config()
    finally:
        cl.load = saved
    assert cfg["enabled"] is True
    assert cfg["a"] == 3 and cfg["theta"] == 1000 and cfg["b"] == 1
    assert cfg["model"] == "m" and cfg["provider"] == "p"


def test_config_bad_values_fall_back():
    import anet.core.config_loader as cl
    saved = cl.load
    cl.load = lambda: {"diet": {"enabled": True, "a": "not-a-number"}}
    try:
        cfg = diet.config()
    finally:
        cl.load = saved
    assert cfg["a"] == 2


# ── Step boundaries ───────────────────────────────────────────────────────────

def test_find_steps_splits_assistant_plus_tools():
    msgs = _base()
    base_len = len(msgs)
    msgs += _step("step A", ["r1"])
    msgs += _step("step B", ["r2", "r3"])
    msgs += _step("step C", ["r4"])
    steps = diet.find_steps(msgs, base_len)
    assert len(steps) == 3
    assert steps[0] == (2, 4)      # assistant + 1 tool
    assert steps[1] == (4, 7)      # assistant + 2 tools
    assert steps[2] == (7, 9)


def test_find_steps_ignores_the_base():
    msgs = _base()
    steps = diet.find_steps(msgs, len(msgs))
    assert steps == []


def test_tool_indices_only_picks_tool_messages():
    msgs = _base()
    base_len = len(msgs)
    msgs += _step("a", ["r1", "r2"])
    steps = diet.find_steps(msgs, base_len)
    assert diet._tool_indices(msgs, steps[0]) == [3, 4]


# ── maybe_reduce ──────────────────────────────────────────────────────────────

def _run_reduce(msgs, base_len, cfg, fake_reflect):
    saved = diet._reflect
    diet._reflect = fake_reflect
    try:
        return asyncio.run(diet.maybe_reduce(msgs, base_len, "fix the bug", None, cfg))
    finally:
        diet._reflect = saved


_ON = {"enabled": True, "a": 2, "b": 1, "theta": 500, "model": None, "provider": None}


def test_disabled_does_nothing():
    msgs = _base()
    base_len = len(msgs)
    msgs += _step("a", ["X" * 100_000])
    before = msgs[3]["content"]

    async def boom(*a, **k):
        raise AssertionError("should not be called when disabled")

    cfg = dict(_ON, enabled=False)
    assert _run_reduce(msgs, base_len, cfg, boom) == 0
    assert msgs[3]["content"] == before


def test_does_not_touch_recent_steps():
    # Only 2 steps exist and a=2 -> target index is negative -> nothing to do.
    msgs = _base()
    base_len = len(msgs)
    msgs += _step("a", ["X" * 100_000])
    msgs += _step("b", ["Y" * 100_000])

    async def boom(*a, **k):
        raise AssertionError("should not reduce steps still in play")

    assert _run_reduce(msgs, base_len, _ON, boom) == 0


def test_reduces_the_target_step_only():
    msgs = _base()
    base_len = len(msgs)
    msgs += _step("old", ["X" * 100_000])   # step 0 <- the target (a=2, 3 steps)
    msgs += _step("mid", ["Y" * 100_000])
    msgs += _step("new", ["Z" * 100_000])

    async def fake(content, task, ctx, cfg):
        return "[compressed]"

    saved = _run_reduce(msgs, base_len, _ON, fake)
    assert saved > 0
    assert msgs[3]["content"] == "[compressed]"      # step 0 reduced
    assert msgs[5]["content"] == "Y" * 100_000        # step 1 untouched
    assert msgs[7]["content"] == "Z" * 100_000        # step 2 untouched


def test_skips_steps_below_theta():
    msgs = _base()
    base_len = len(msgs)
    msgs += _step("old", ["small"])         # way under theta
    msgs += _step("mid", ["Y" * 100_000])
    msgs += _step("new", ["Z" * 100_000])

    async def boom(*a, **k):
        raise AssertionError("should not spend a model call on a small step")

    assert _run_reduce(msgs, base_len, _ON, boom) == 0
    assert msgs[3]["content"] == "small"


def test_discards_reduction_that_saves_too_little():
    msgs = _base()
    base_len = len(msgs)
    big = "X" * 3000                         # above theta (500 tok = 2000 chars)
    msgs += _step("old", [big])
    msgs += _step("mid", ["Y" * 100_000])
    msgs += _step("new", ["Z" * 100_000])

    async def barely(content, task, ctx, cfg):
        return "X" * 2900                    # saves only 100 chars

    assert _run_reduce(msgs, base_len, _ON, barely) == 0
    assert msgs[3]["content"] == big         # original kept


def test_empty_reflect_result_keeps_original():
    msgs = _base()
    base_len = len(msgs)
    big = "X" * 100_000
    msgs += _step("old", [big])
    msgs += _step("mid", ["Y"])
    msgs += _step("new", ["Z"])

    async def empty(content, task, ctx, cfg):
        return ""

    assert _run_reduce(msgs, base_len, _ON, empty) == 0
    assert msgs[3]["content"] == big


def test_reflect_failure_never_breaks_the_turn():
    msgs = _base()
    base_len = len(msgs)
    big = "X" * 100_000
    msgs += _step("old", [big])
    msgs += _step("mid", ["Y"])
    msgs += _step("new", ["Z"])

    async def boom(content, task, ctx, cfg):
        raise RuntimeError("model down")

    assert _run_reduce(msgs, base_len, _ON, boom) == 0   # swallowed
    assert msgs[3]["content"] == big                      # trajectory intact


def test_base_messages_are_never_reduced():
    msgs = _base()
    base_len = len(msgs)
    msgs[0]["content"] = "S" * 100_000        # a huge system prompt
    msgs += _step("old", ["X" * 100_000])
    msgs += _step("mid", ["Y"])
    msgs += _step("new", ["Z"])

    async def fake(content, task, ctx, cfg):
        return "[compressed]"

    _run_reduce(msgs, base_len, _ON, fake)
    assert msgs[0]["content"] == "S" * 100_000   # system prompt untouched


def test_context_blurb_includes_tool_names_and_is_bounded():
    msgs = _base()
    base_len = len(msgs)
    msgs += _step("looking at widgets", ["r"], call_name="grep_tool")
    msgs += _step("reading it", ["r"], call_name="file_tool")
    steps = diet.find_steps(msgs, base_len)
    blurb = diet._context_blurb(msgs, steps, target=0, b=1)
    assert "grep_tool" in blurb and "looking at widgets" in blurb
    assert len(blurb) <= 2000


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: diet")
