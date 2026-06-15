"""Unit tests for the grep_tool. Offline — uses a temp tree (rg or py fallback)."""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.AnetTools.grep_tool import run


def _run(p): return asyncio.run(run(p))


def test_requires_pattern():
    r = _run({"pattern": "   "})
    assert "error" in r


def test_missing_path_errors():
    r = _run({"pattern": "x", "path": "/no/such/dir/xyz123"})
    assert "error" in r


def test_finds_match_content():
    d = Path(tempfile.mkdtemp())
    (d / "f.py").write_text("alpha\nNEEDLE here\nbeta\n", encoding="utf-8")
    r = _run({"pattern": "NEEDLE", "path": str(d), "output_mode": "content"})
    assert "NEEDLE" in str(r.get("result", ""))


def test_files_with_matches():
    d = Path(tempfile.mkdtemp())
    (d / "hit.py").write_text("token = 1\n", encoding="utf-8")
    (d / "miss.py").write_text("nope\n", encoding="utf-8")
    r = _run({"pattern": "token", "path": str(d), "output_mode": "files_with_matches"})
    out = str(r.get("result", ""))
    assert "hit.py" in out and "miss.py" not in out


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: grep_tool")
