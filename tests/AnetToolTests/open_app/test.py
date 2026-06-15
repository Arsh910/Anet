"""Unit tests for the open_app tool. Offline — validation only (no desktop actions)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.AnetTools.open_app import run


def _run(p): return asyncio.run(run(p))


def test_unknown_action_errors():
    r = _run({"action": "definitely_not_a_real_action"})
    assert "error" in r and "Unknown action" in r["error"]


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: open_app")
