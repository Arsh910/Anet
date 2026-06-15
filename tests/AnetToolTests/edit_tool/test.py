"""Unit tests for the edit_tool. Offline — uses a temp file."""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.AnetTools.edit_tool import run


def _run(p): return asyncio.run(run(p))


def _tmp(content=""):
    p = Path(tempfile.mkdtemp()) / "f.py"
    p.write_text(content, encoding="utf-8")
    return p


def test_exact_replace():
    p = _tmp("def foo():\n    return 1\n")
    r = _run({"path": str(p), "old_string": "return 1", "new_string": "return 2"})
    assert "result" in r and "return 2" in p.read_text()


def test_fuzzy_indentation_drift():
    p = _tmp("class A:\n    def m(self):\n        x = 1\n        return x\n")
    r = _run({"path": str(p),
              "old_string": "def m(self):\n    x = 1\n    return x",
              "new_string": "def m(self):\n    return 42"})
    assert "result" in r and "return 42" in p.read_text()


def test_no_match_errors_without_changing_file():
    p = _tmp("def calculate_total(items):\n    return sum(items)\n")
    before = p.read_text()
    r = _run({"path": str(p), "old_string": "def calculate_sum(items):", "new_string": "x"})
    assert "error" in r and p.read_text() == before


def test_ambiguous_match_requires_replace_all():
    p = _tmp("x = 1\nx = 1\n")
    r = _run({"path": str(p), "old_string": "x = 1", "new_string": "x = 2"})
    assert "error" in r


def test_file_creation_mode():
    d = Path(tempfile.mkdtemp())
    target = d / "new.txt"
    r = _run({"path": str(target), "old_string": "", "new_string": "hello"})
    assert "result" in r and target.read_text() == "hello"


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: edit_tool")
