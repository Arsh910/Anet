"""Unit tests for the shell_tool. Runs only safe, fast local commands."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.AnetTools.shell_tool import run, _is_blocked, _is_success


def _run(p): return asyncio.run(run(p))


def test_requires_command():
    r = _run({"command": "   "})
    assert "error" in r


def test_blocks_destructive():
    assert _is_blocked("rm -rf /tmp/x")
    assert _is_blocked("git reset --hard")
    assert not _is_blocked("python -m pytest")
    r = _run({"command": "rm -rf /tmp/whatever"})
    assert "error" in r and "blocked" in r["error"].lower()


def test_rejects_interactive():
    r = _run({"command": "sudo apt install foo"})
    assert "error" in r


def test_runs_echo():
    r = _run({"command": "echo anet_hello"})
    assert "anet_hello" in r.get("result", "") and r.get("success")


def test_exit_ok_table():
    assert _is_success("grep foo", 1) is True     # grep: 1 = no match, not failure
    assert _is_success("python x.py", 1) is False


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: shell_tool")
