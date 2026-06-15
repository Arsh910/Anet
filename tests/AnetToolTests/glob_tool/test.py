"""Unit tests for the glob_tool. Offline — uses a temp tree."""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.AnetTools.glob_tool import run


def _run(p): return asyncio.run(run(p))


def test_requires_pattern():
    r = _run({"pattern": "   "})
    assert "error" in r


def test_finds_matching_files():
    d = Path(tempfile.mkdtemp())
    (d / "a.py").write_text("x", encoding="utf-8")
    (d / "b.py").write_text("y", encoding="utf-8")
    (d / "c.txt").write_text("z", encoding="utf-8")
    r = _run({"pattern": "*.py", "path": str(d)})
    assert r["num_files"] == 2 and "a.py" in r["result"] and "c.txt" not in r["result"]


def test_missing_path_errors():
    r = _run({"pattern": "*.py", "path": "/no/such/dir/xyz123"})
    assert "error" in r


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: glob_tool")
