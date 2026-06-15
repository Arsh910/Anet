"""Unit tests for the web_search tool. Offline — validation only (no network)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.AnetTools.web_search import run, _timelimit


def _run(p): return asyncio.run(run(p))


def test_requires_query():
    r = _run({"query": "   "})
    assert "error" in r


def test_timelimit_mapping():
    assert _timelimit(None) is None
    assert _timelimit(1) == "d"
    assert _timelimit(7) == "w"
    assert _timelimit(30) == "m"


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: web_search")
