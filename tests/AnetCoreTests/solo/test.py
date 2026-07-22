"""Unit tests for anet.core.solo (the Solo engine: one generalist agent, no
orchestration). Pure, offline — orchestrator.run and config_loader are faked
via manual monkeypatch (save/restore, same pattern as router/qweenbee_bench)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import anet.core.orchestrator as orchestrator
from anet.core import solo
from anet.core.solo import SoloEngine


class FakeStore:
    """In-memory stand-in for ConversationStore."""
    def __init__(self, messages=None, summary="", summarized_count=0):
        self.messages = list(messages or [])
        self.summary = summary
        self.summarized_count = summarized_count
        self.appended: list[tuple[str, str]] = []

    async def load(self, thread_id):
        return list(self.messages)

    async def get_summary(self, thread_id):
        return (self.summary, self.summarized_count)

    async def append(self, thread_id, role, content):
        self.appended.append((role, content))
        self.messages.append({"role": role, "content": content})

    async def set_summary(self, thread_id, summary, summarized_count):
        self.summary = summary
        self.summarized_count = summarized_count


# ── 1. Agent construction ────────────────────────────────────────────────────

def test_build_solo_agent_default_toolset_is_curated_not_everything():
    tools = {
        "file_tool": {}, "edit_tool": {}, "conflict_tool": {},          # filesystem
        "shell_tool": {}, "process_tool": {}, "code_execution": {},     # shell
        "web_search": {}, "web_fetch": {}, "download_file": {},         # web
        "lsp_tool": {}, "diagnose_tool": {},                            # code_intel
        "grep_tool": {}, "glob_tool": {}, "memory_tool": {},
        "todo_tool": {}, "ask_user": {}, "spawn_tool": {},              # COMMON
        "open_app": {},          # desktop — excluded by default
        "playwright_click": {},  # a stand-in MCP tool — excluded by default
    }
    agent = solo._build_solo_agent(tools)
    assert agent["name"] == "solo"
    assert "capable general assistant" in agent["system_prompt"]
    assert "open_app" not in agent["tools"]
    assert "playwright_click" not in agent["tools"]
    assert "file_tool" in agent["tools"] and "shell_tool" in agent["tools"]
    assert "web_search" in agent["tools"] and "lsp_tool" in agent["tools"]
    assert "grep_tool" in agent["tools"]   # COMMON baseline


def test_build_solo_agent_filters_to_actually_loaded_tools():
    # filesystem/shell/web/code_intel resolve to more tool names than are
    # actually present here — only the loaded ones should survive.
    agent = solo._build_solo_agent({"file_tool": {}, "grep_tool": {}})
    assert set(agent["tools"]) == {"file_tool", "grep_tool"}


def test_build_solo_agent_toolsets_overridable_via_config():
    import anet.core.config_loader as cl
    saved = cl.load
    cl.load = lambda: {"orchestration": {"solo": {"toolsets": ["desktop"]}}}
    try:
        agent = solo._build_solo_agent({"open_app": {}, "file_tool": {}})
    finally:
        cl.load = saved
    assert "open_app" in agent["tools"]
    assert "file_tool" not in agent["tools"]


def test_build_solo_agent_empty_toolsets_means_common_only():
    import anet.core.config_loader as cl
    saved = cl.load
    cl.load = lambda: {"orchestration": {"solo": {"toolsets": [], "tools": ["checker"]}}}
    try:
        agent = solo._build_solo_agent({"checker": {}, "file_tool": {}, "grep_tool": {}})
    finally:
        cl.load = saved
    assert "checker" in agent["tools"]
    assert "grep_tool" in agent["tools"]     # COMMON always applies
    assert "file_tool" not in agent["tools"]  # filesystem toolset excluded by explicit []


def test_build_solo_agent_persona_prepended_when_present():
    import anet.core.config_loader as cl
    saved = cl.load_soul
    cl.load_soul = lambda: "MY PERSONA TEXT"
    try:
        agent = solo._build_solo_agent({})
    finally:
        cl.load_soul = saved
    assert agent["system_prompt"].startswith("MY PERSONA TEXT")
    assert "capable general assistant" in agent["system_prompt"]


def test_build_solo_agent_survives_soul_load_failure():
    import anet.core.config_loader as cl
    saved = cl.load_soul

    def _raise():
        raise RuntimeError("boom")

    cl.load_soul = _raise
    try:
        agent = solo._build_solo_agent({})
    finally:
        cl.load_soul = saved
    assert "capable general assistant" in agent["system_prompt"]


# ── 2. Config resolution ──────────────────────────────────────────────────────

def test_solo_config_explicit_override_wins():
    import anet.core.config_loader as cl
    saved = cl.load
    cl.load = lambda: {
        "orchestration": {"solo": {"model": "m1", "provider": "p1", "max_steps": "45"}},
        "manager": {"model": "m2", "provider": "p2"},
    }
    try:
        cfg = solo._solo_config()
    finally:
        cl.load = saved
    assert cfg == {"model": "m1", "provider": "p1", "max_steps": 45}


def test_solo_config_falls_back_to_manager():
    import anet.core.config_loader as cl
    saved = cl.load
    cl.load = lambda: {"manager": {"model": "m2", "provider": "p2"}}
    try:
        cfg = solo._solo_config()
    finally:
        cl.load = saved
    assert cfg == {"model": "m2", "provider": "p2", "max_steps": 20}


def test_solo_config_defaults_when_nothing_set():
    import anet.core.config_loader as cl
    saved = cl.load
    cl.load = lambda: {}
    try:
        cfg = solo._solo_config()
    finally:
        cl.load = saved
    assert cfg == {"model": "gemini-2.5-flash", "provider": "openrouter", "max_steps": 20}


# ── 3. run_turn happy path ────────────────────────────────────────────────────

def test_run_turn_happy_path():
    store = FakeStore()
    calls = []

    async def fake_run(agent, tool_map, user_message, history, on_status):
        calls.append((agent, tool_map, user_message, history))
        return {"text": "hello"}

    saved = orchestrator.run
    orchestrator.run = fake_run
    try:
        engine = SoloEngine([], {"t1": {}})
        result = asyncio.run(engine.run_turn("thread1", store, "hi"))
    finally:
        orchestrator.run = saved

    assert result.reply == "hello"
    assert store.appended == [("user", "hi"), ("assistant", "hello")]
    assert len(calls) == 1
    _agent, _tools, user_message, history = calls[0]
    assert user_message == "hi"
    assert all(m.get("content") != "hi" for m in history)   # current turn not duplicated in history


# ── 4. History window ─────────────────────────────────────────────────────────

def test_history_window_no_summary():
    prior = [
        {"role": "user", "content": "msg1"},
        {"role": "assistant", "content": "reply1"},
        {"role": "user", "content": "msg2"},
        {"role": "assistant", "content": "reply2"},
    ]
    store = FakeStore(messages=prior)
    captured = {}

    async def fake_run(agent, tool_map, user_message, history, on_status):
        captured["history"] = history
        return {"text": "ok"}

    saved = orchestrator.run
    orchestrator.run = fake_run
    try:
        engine = SoloEngine([], {})
        asyncio.run(engine.run_turn("t", store, "new message"))
    finally:
        orchestrator.run = saved

    assert captured["history"] == prior


def test_history_window_with_summary():
    prior = [
        {"role": "user", "content": "old1"},
        {"role": "assistant", "content": "old1-reply"},
        {"role": "user", "content": "recent1"},
        {"role": "assistant", "content": "recent1-reply"},
    ]
    store = FakeStore(messages=prior)
    captured = {}

    async def fake_run(agent, tool_map, user_message, history, on_status):
        captured["history"] = history
        return {"text": "ok"}

    async def fake_maintain_summary(store_, thread_id, messages):
        return ("SUMMARY TEXT", 2)   # first 2 messages summarized; keep_from=2

    saved_run = orchestrator.run
    orchestrator.run = fake_run
    engine = SoloEngine([], {})
    engine._maintain_summary = fake_maintain_summary
    try:
        asyncio.run(engine.run_turn("t", store, "new message"))
    finally:
        orchestrator.run = saved_run

    history = captured["history"]
    assert history[0] == {"role": "user", "content": "[Summary of earlier conversation]\nSUMMARY TEXT"}
    assert history[1] == {"role": "assistant", "content": "Understood."}
    assert history[2:] == prior[2:]


# ── 5. Error path ──────────────────────────────────────────────────────────────

def test_run_turn_error_path_still_persists():
    store = FakeStore()

    async def fake_run(agent, tool_map, user_message, history, on_status):
        raise RuntimeError("boom")

    saved = orchestrator.run
    orchestrator.run = fake_run
    try:
        engine = SoloEngine([], {})
        result = asyncio.run(engine.run_turn("t", store, "hi"))
    finally:
        orchestrator.run = saved

    assert result.reply.startswith("Sorry")
    assert len(store.appended) == 2   # still persisted despite the error


# ── 6. Empty-reply fallback ───────────────────────────────────────────────────

def test_run_turn_empty_reply_falls_back_to_done():
    store = FakeStore()

    async def fake_run(agent, tool_map, user_message, history, on_status):
        return {"text": ""}

    saved = orchestrator.run
    orchestrator.run = fake_run
    try:
        engine = SoloEngine([], {})
        result = asyncio.run(engine.run_turn("t", store, "hi"))
    finally:
        orchestrator.run = saved

    assert result.reply == "Done."


# ── 7. Engine selection (module-level equivalents of _make_engine's logic) ──

def test_active_engine_accepts_solo():
    import anet.cli.app as app
    import anet.core.config_loader as cl
    saved = cl.load
    cl.load = lambda: {"orchestration": {"engine": "solo"}}
    try:
        assert app._active_engine() == "solo"
    finally:
        cl.load = saved


def test_active_engine_unknown_falls_back_to_adaptorch():
    import anet.cli.app as app
    import anet.core.config_loader as cl
    saved = cl.load
    cl.load = lambda: {"orchestration": {"engine": "bogus"}}
    try:
        assert app._active_engine() == "adaptorch"
    finally:
        cl.load = saved


def test_set_active_engine_rejects_unknown_name():
    import anet.cli.app as app
    assert app._set_active_engine("bogus") is False


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: solo")
