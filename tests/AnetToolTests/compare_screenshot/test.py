"""Unit tests for the compare_screenshot tool. Offline — validation only.

(This tool is disabled by default and the vision/compare path needs a model +
screen, so the unit layer only checks the action guards.)
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.AnetTools.compare_screenshot.index import run


def _run(p): return asyncio.run(run(p))


def test_requires_action():
    r = _run({})
    assert "error" in r


def test_unknown_action_errors():
    r = _run({"action": "not_real"})
    assert "error" in r and "Unknown action" in r["error"]


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: compare_screenshot")
