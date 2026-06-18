"""Unit tests for the memory_tool (mem0-backed).

IMPORTANT: redirects ANET_HOME to a temp dir BEFORE importing the memory layer, so
the real ~/.anet/memory store is never touched, and disables the legacy-file
migration so it can't read the real ~/.anet/memory.json.

Note: unlike the other tool tests, this one needs mem0 installed (it's a core
dependency). The first run downloads the fastembed embedding model (~130 MB). If
mem0 can't initialise, the tests skip rather than fail.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

# Isolate storage before the memory layer is imported.
os.environ["ANET_HOME"] = tempfile.mkdtemp(prefix="anet_memtest_")

import anet.AnetTools.memory_tool as mt          # noqa: E402
from anet.core import memory_store               # noqa: E402

# Never touch the real legacy file during tests.
mt._LEGACY_FILE = Path(os.environ["ANET_HOME"]) / "no_such_legacy.json"
mt._migrated = True

_AVAILABLE = memory_store.is_available()


def _run(p):
    return asyncio.run(mt.run(p))


def test_requires_action():
    r = _run({})
    assert "error" in r


def test_save_search_list_delete_clear():
    if not _AVAILABLE:
        print("  skip (mem0 unavailable)"); return
    _run({"action": "clear"})
    s = _run({"action": "save", "content": "User's project lives at C:/proj, a FastAPI app", "tags": ["proj"]})
    assert s.get("saved") and s.get("id"), s
    mem_id = s["id"]

    found = _run({"action": "search", "query": "FastAPI project"})
    assert found.get("count", 0) >= 1, found

    listed = _run({"action": "list"})
    assert listed.get("count", 0) >= 1, listed

    d = _run({"action": "delete", "id": mem_id})
    assert d.get("deleted"), d

    c = _run({"action": "clear"})
    assert c.get("cleared"), c
    assert _run({"action": "list"}).get("count", 0) == 0


def test_save_requires_content():
    r = _run({"action": "save", "content": "  "})
    assert "error" in r


def test_standing_memory_scoping():
    """Standing (always_inject) memories are retrieved by their LLM-assigned
    `applies_to` metadata, scoped per agent — no tag convention. (We inject the
    metadata directly here so the test is deterministic without an LLM key.)"""
    if not _AVAILABLE:
        print("  skip (mem0 unavailable)"); return
    _run({"action": "clear"})
    mem = memory_store.get_memory()
    mem.add("Prefix functions with anet_", user_id="anet",
            metadata={"category": "preference", "always_inject": True,
                      "applies_to": ["code_agent"]}, infer=False)
    mem.add("Be terse", user_id="anet",
            metadata={"category": "preference", "always_inject": True,
                      "applies_to": ["all"]}, infer=False)
    mem.add("Project uses FastAPI", user_id="anet",
            metadata={"category": "project", "always_inject": False}, infer=False)

    code = {m["content"] for m in mt.preference_memories("code_agent")}
    research = {m["content"] for m in mt.preference_memories("research_agent")}
    assert "Prefix functions with anet_" in code and "Be terse" in code, code
    assert "Prefix functions with anet_" not in research and "Be terse" in research, research
    # the non-always_inject project fact is not a standing memory
    assert "Project uses FastAPI" not in code


def test_semantic_recall():
    """mem0 search is semantic: a paraphrase that shares no keywords still matches."""
    if not _AVAILABLE:
        print("  skip (mem0 unavailable)"); return
    _run({"action": "clear"})
    _run({"action": "save", "content": "The user's backend is written in FastAPI", "tags": ["stack"]})
    # No shared content words with "FastAPI backend" beyond the concept.
    hits = _run({"action": "search", "query": "what web framework does the user use"})
    assert hits.get("count", 0) >= 1, hits


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: memory_tool")
