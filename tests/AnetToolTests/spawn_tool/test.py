"""Unit tests for the spawn_tool. Offline — validation paths (no model)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.AnetTools.spawn_tool import run


def _run(p): return asyncio.run(run(p))


def test_requires_agent():
    r = _run({"task": "do something"})
    assert "error" in r and "agent" in r["error"].lower()


def test_requires_task():
    r = _run({"agent": "code_agent"})
    assert "error" in r and "task" in r["error"].lower()


def test_unknown_agent():
    # No agents configured in a bare test process → unknown agent.
    r = _run({"agent": "nonexistent_agent", "task": "x"})
    assert "error" in r


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: spawn_tool")
