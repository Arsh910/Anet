"""Unit tests for the download_file tool. Offline — validation only (no network)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.AnetTools.download_file import run


def _run(p): return asyncio.run(run(p))


def test_requires_url():
    r = _run({"url": "   "})
    assert "error" in r and "url is required" in r["error"]


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: download_file")
