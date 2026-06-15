"""Unit tests for the process_tool. Offline — validation paths only."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.AnetTools.process_tool import run


def _run(p): return asyncio.run(run(p))


def test_requires_command():
    r = _run({"command": "   "})
    assert "error" in r


def test_invalid_regex_errors():
    r = _run({"command": "echo hi", "failure_pattern": "(unclosed"})
    assert "error" in r and "regex" in r["error"].lower()


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: process_tool")
