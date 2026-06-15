"""Unit tests for the todo_tool. Offline — in-memory session state."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.AnetTools.todo_tool import run


def _run(p): return asyncio.run(run(p))


def test_requires_action():
    r = _run({})
    assert "error" in r


def test_write_read_update_clear():
    w = _run({"action": "write", "todos": [
        {"id": "1", "content": "install deps"},
        {"id": "2", "content": "write tests"},
    ]})
    assert "error" not in w

    r = _run({"action": "read"})
    assert "install deps" in r["result"] and "write tests" in r["result"]

    u = _run({"action": "update", "id": "1", "status": "completed"})
    assert "completed" in u["result"]

    bad = _run({"action": "update", "id": "999", "status": "completed"})
    assert "error" in bad

    c = _run({"action": "clear"})
    assert "cleared" in c["result"].lower()


def test_write_requires_todos():
    r = _run({"action": "write", "todos": []})
    assert "error" in r


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: todo_tool")
