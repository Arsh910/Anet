"""Unit tests for the diagnose_tool. Offline — validation paths only.

(Running real linters needs ruff/pyright/eslint installed, so the unit layer
only asserts the input guards; linter runs belong in the integration layer.)
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.AnetTools.diagnose_tool import run


def _run(p): return asyncio.run(run(p))


def test_requires_path():
    r = _run({"path": "  "})
    assert "error" in r and "path is required" in r["error"]


def test_path_not_found():
    r = _run({"path": "/no/such/path/xyz123.py"})
    assert "error" in r and "not found" in r["error"].lower()


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: diagnose_tool")
