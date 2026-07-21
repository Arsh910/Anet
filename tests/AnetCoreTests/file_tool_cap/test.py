"""Unit tests for file_tool's read cap — the guard that stops a whole-file
read from dumping tens of thousands of tokens into the agent's trajectory.
Pure, offline; uses temp files."""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.AnetTools import file_tool
from anet.AnetTools.file_tool import _cap_read, _MAX_READ_LINES, _MAX_READ_CHARS


def _read(path):
    return asyncio.run(file_tool.run(
        {"action": "read_file", "path": str(path), "_agent_name": "test"}))


def _tmp(text, name="f.py"):
    p = Path(tempfile.mkdtemp()) / name
    p.write_text(text, encoding="utf-8")
    return p


# ── _cap_read (pure) ──────────────────────────────────────────────────────────

def test_short_text_passes_through_untouched():
    text = "line one\nline two\nline three"
    out, extra = _cap_read(text, "x.py")
    assert out == text
    assert extra == {}      # no truncation metadata when nothing was cut


def test_line_cap_trims_and_reports():
    text = "\n".join(f"line {i}" for i in range(_MAX_READ_LINES + 500))
    out, extra = _cap_read(text, "x.py")
    assert extra["truncated"] is True
    assert extra["total_lines"] == _MAX_READ_LINES + 500
    assert extra["lines_shown"] <= _MAX_READ_LINES
    assert "truncated" in out


def test_char_cap_applies_even_when_line_count_is_small():
    # 10 lines, but each enormous — the line cap alone wouldn't catch this.
    text = "\n".join("X" * 20_000 for _ in range(10))
    out, extra = _cap_read(text, "x.py")
    assert extra["truncated"] is True
    assert len(out) < len(text)
    assert "char limit" in out


def test_note_points_at_the_next_unread_line():
    text = "\n".join(f"line {i}" for i in range(_MAX_READ_LINES + 100))
    out, extra = _cap_read(text, "big.py")
    assert f"start_line={extra['lines_shown'] + 1}" in out
    assert "read_lines" in out and "big.py" in out


def test_capped_output_respects_both_limits():
    text = "\n".join("Y" * 500 for _ in range(_MAX_READ_LINES * 2))
    out, extra = _cap_read(text, "x.py")
    body = out.split("\n\n[... truncated")[0]
    assert len(body) <= _MAX_READ_CHARS
    assert len(body.splitlines()) <= _MAX_READ_LINES


# ── read_file end to end ──────────────────────────────────────────────────────

def test_read_file_small_is_not_truncated():
    p = _tmp("def f():\n    return 1\n")
    r = _read(p)
    assert r.get("truncated") is None
    assert "def f()" in r["result"]


def test_read_file_large_is_truncated_with_metadata():
    p = _tmp("\n".join(f"# line {i}" for i in range(5000)))
    r = _read(p)
    assert r["truncated"] is True
    assert r["total_lines"] == 5000
    assert r["lines_shown"] < 5000
    assert len(r["result"]) < 5000 * 10


def test_truncated_read_still_returns_the_beginning_of_the_file():
    p = _tmp("FIRST_MARKER\n" + "\n".join(f"# line {i}" for i in range(5000)))
    r = _read(p)
    assert r["result"].startswith("FIRST_MARKER")


def test_read_lines_can_reach_past_the_cap():
    # The truncation note tells the model to page with read_lines — make sure
    # that path actually works, so nothing in a big file is unreachable.
    p = _tmp("\n".join(f"# line {i}" for i in range(5000)))
    r = _read(p)
    nxt = r["lines_shown"] + 1
    paged = asyncio.run(file_tool.run({
        "action": "read_lines", "path": str(p),
        "start_line": nxt, "end_line": nxt + 5, "_agent_name": "test",
    }))
    assert "error" not in paged
    assert f"# line {nxt - 1}" in str(paged["result"])


# ── read_lines range handling (regression) ───────────────────────────────────
# The schema advertises start_line/end_line but the code read start/end, so
# every call silently returned the default first 50 lines. No error, just the
# wrong content — which is why it survived unnoticed.

def _lines(path, **kw):
    args = {"action": "read_lines", "path": str(path), "_agent_name": "test", **kw}
    return asyncio.run(file_tool.run(args))


def test_read_lines_honours_schema_parameter_names():
    p = _tmp("\n".join(f"# line {i}" for i in range(500)))
    r = _lines(p, start_line=100, end_line=104)
    got = r["result"].splitlines()
    assert got[0] == "# line 99"      # line 100, 1-indexed
    assert len(got) == 5


def test_read_lines_still_accepts_legacy_names():
    p = _tmp("\n".join(f"# line {i}" for i in range(500)))
    r = _lines(p, start=100, end=104)
    assert r["result"].splitlines()[0] == "# line 99"


def test_read_lines_defaults_when_no_range_given():
    p = _tmp("\n".join(f"# line {i}" for i in range(500)))
    r = _lines(p)
    got = r["result"].splitlines()
    assert got[0] == "# line 0" and len(got) == 50


# ── Path resolution: reads find real files, writes stay sandboxed ───────────
# Relative paths used to be redirected into anet_files/<agent>/ for every
# agent except code_agent. That broke reads of repo-relative paths (the agent
# got a sandbox miss and fell back to globbing to rediscover the path), while
# the sandbox is still exactly what we want for writes.

def test_relative_read_finds_a_real_file_for_any_agent():
    r = asyncio.run(file_tool.run({
        "action": "read_file", "path": "anet/core/diet.py", "_agent_name": "solo"}))
    assert "result" in r, r.get("error")
    assert "AgentDiet" in r["result"]


def test_relative_write_is_still_sandboxed():
    r = asyncio.run(file_tool.run({
        "action": "write_file", "path": "unit_test_probe.txt",
        "content": "x", "_agent_name": "unit_test_agent"}))
    assert "anet_files" in str(r.get("path", r))


def test_relative_write_cannot_clobber_an_existing_repo_file():
    r = asyncio.run(file_tool.run({
        "action": "write_file", "path": "README.md",
        "content": "should not land in the repo", "_agent_name": "unit_test_agent"}))
    assert "anet_files" in str(r.get("path", r))
    assert Path("README.md").read_text(encoding="utf-8").lstrip().startswith("<p align")


def test_missing_relative_read_still_falls_back_to_the_sandbox():
    r = asyncio.run(file_tool.run({
        "action": "read_file", "path": "definitely_not_here_12345.txt",
        "_agent_name": "unit_test_agent"}))
    assert "error" in r and "anet_files" in r["error"]


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: file_tool_cap")
