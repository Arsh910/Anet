"""Unit tests for the checker tool. Offline — validation paths (no model call)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.AnetTools.checker.index import run


def _run(p): return asyncio.run(run(p))


def test_requires_action():
    r = _run({})
    assert "error" in r and "action" in r["error"].lower()


def test_unknown_action():
    r = _run({"action": "bogus_action"})
    assert "error" in r and "Unknown action" in r["error"]


def test_missing_required_param():
    # A known action with its required params omitted should be reported as missing.
    r = _run({"action": "check_path"})
    assert "error" in r


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: checker")
