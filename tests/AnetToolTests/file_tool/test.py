"""Unit tests for the file_tool. Offline — absolute temp paths (no redirect)."""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.AnetTools.file_tool import run


def _run(p): return asyncio.run(run(p))


def test_requires_action():
    r = _run({})
    assert "error" in r


def test_unknown_action():
    r = _run({"action": "bogus"})
    assert "error" in r and "Unknown action" in r["error"]


def test_write_then_read():
    p = Path(tempfile.mkdtemp()) / "note.txt"
    w = _run({"action": "write_file", "path": str(p), "content": "hello world", "_agent_name": "code_agent"})
    assert "error" not in w and p.exists()
    r = _run({"action": "read_file", "path": str(p), "_agent_name": "code_agent"})
    assert "hello world" in str(r.get("result", ""))


def test_list_directory():
    d = Path(tempfile.mkdtemp())
    (d / "a.txt").write_text("a", encoding="utf-8")
    (d / "b.txt").write_text("b", encoding="utf-8")
    r = _run({"action": "list_directory", "path": str(d), "_agent_name": "code_agent"})
    out = str(r.get("result", "")) + str(r)
    assert "a.txt" in out and "b.txt" in out


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: file_tool")
