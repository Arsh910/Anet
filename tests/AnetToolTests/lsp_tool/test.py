"""Unit tests for the lsp_tool. Offline — status + validation (no servers started)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.AnetTools.lsp_tool import run


def _run(p): return asyncio.run(run(p))


def test_status_with_no_servers():
    r = _run({"action": "status"})
    assert "result" in r and "No LSP servers" in r["result"]


def test_non_status_requires_path():
    r = _run({"action": "diagnostics", "path": "  "})
    assert "error" in r and "path is required" in r["error"]


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: lsp_tool")
