"""Unit tests for the code_execution tool. Offline, deterministic."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.AnetTools.code_execution import run


def _run(p): return asyncio.run(run(p))


def test_requires_code():
    r = _run({"code": "   "})
    assert "error" in r


def test_auto_prints_trailing_expression():
    r = _run({"code": "a = 5\nb = 37\na + b + 3"})
    assert r["result"] == "45" and r["success"]


def test_explicit_print_and_stdlib():
    r = _run({"code": "import json\nprint(json.dumps({'x': [1, 2, 3]}))"})
    assert '"x": [1, 2, 3]' in r["result"]


def test_none_not_printed():
    r = _run({"code": "print('hi')\nNone"})
    assert r["result"] == "hi"


def test_runtime_error_captured():
    r = _run({"code": "1/0"})
    assert r["success"] is False and "ZeroDivisionError" in r["result"]


def test_timeout():
    r = _run({"code": "import time\ntime.sleep(5)", "timeout": 1})
    assert "error" in r and "timed out" in r["error"]


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: code_execution")
