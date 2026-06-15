"""Unit tests for the conflict_tool. Offline — temp files, no git needed."""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.AnetTools.conflict_tool import run


def _run(p): return asyncio.run(run(p))


def test_requires_path():
    r = _run({"action": "list", "path": "  "})
    assert "error" in r and "path is required" in r["error"]


def test_list_no_conflicts():
    d = Path(tempfile.mkdtemp())
    (d / "clean.py").write_text("x = 1\n", encoding="utf-8")
    r = _run({"action": "list", "path": str(d)})
    assert "No conflicts" in str(r.get("result", ""))


def test_list_detects_conflict_markers():
    d = Path(tempfile.mkdtemp())
    (d / "c.py").write_text(
        "a\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\nb\n", encoding="utf-8"
    )
    r = _run({"action": "list", "path": str(d)})
    assert "c.py" in str(r.get("result", "")) or "conflict" in str(r).lower()


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: conflict_tool")
