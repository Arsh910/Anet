"""Unit tests for the memory_tool.

IMPORTANT: redirects storage to a temp file so the real ~/.anet/memory.json is
never touched.
"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import anet.AnetTools.memory_tool as mt

# Redirect storage before any test runs.
mt._MEMORY_FILE = Path(tempfile.mkdtemp()) / "memory.json"


def _run(p): return asyncio.run(mt.run(p))


def test_requires_action():
    r = _run({})
    assert "error" in r


def test_save_search_list_delete_clear():
    s = _run({"action": "save", "content": "User's project lives at C:/proj, a FastAPI app", "tags": ["proj"]})
    assert s.get("saved") and s.get("id")
    mem_id = s["id"]

    found = _run({"action": "search", "query": "FastAPI project"})
    assert found.get("count", 0) >= 1

    listed = _run({"action": "list"})
    assert listed.get("count", 0) >= 1

    d = _run({"action": "delete", "id": mem_id})
    assert d.get("deleted")

    c = _run({"action": "clear"})
    assert c.get("cleared")
    assert _run({"action": "list"}).get("count", 0) == 0


def test_save_requires_content():
    r = _run({"action": "save", "content": "  "})
    assert "error" in r


def test_preference_memories():
    _run({"action": "clear"})
    _run({"action": "save", "content": "Prefer 4-space indents and type hints",
          "tags": ["preference", "code_agent"]})
    _run({"action": "save", "content": "Project at C:/proj uses FastAPI", "tags": ["project"]})
    prefs = mt.preference_memories()
    assert len(prefs) == 1 and "indents" in prefs[0]["content"]


def test_score_ignores_stopwords_and_substrings():
    _run({"action": "clear"})
    _run({"action": "save", "content": "Project at C:/proj uses FastAPI", "tags": ["project"]})
    # Shares only stopwords / 'a' substring with the memory → must NOT match.
    assert _run({"action": "search", "query": "what is the average of a list"})["count"] == 0
    # A real shared word → matches.
    assert _run({"action": "search", "query": "FastAPI project"})["count"] >= 1


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: memory_tool")
