"""Unit tests for the ask_user tool. Offline, deterministic."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.AnetTools.ask_user import run
from anet.core import context


def _run(p): return asyncio.run(run(p))


def test_requires_question():
    r = _run({"question": "   "})
    assert "error" in r


def test_headless_default_does_not_hang():
    # No interactive callback installed → default returns a note, never blocks.
    r = _run({"question": "Postgres or SQLite?"})
    assert "result" in r and "no user" in r["result"].lower()


def test_returns_user_answer():
    async def fake(q, opts):
        return "use postgres"
    tok = context.on_ask.set(fake)
    try:
        r = _run({"question": "Which database?"})
        assert r["result"] == "use postgres"
    finally:
        context.on_ask.reset(tok)


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: ask_user")
