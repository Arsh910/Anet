"""
prompts.py — the LLM prompts RecMem uses during consolidation.

Consolidation is the ONLY place RecMem spends LLM tokens, and it fires only when a
cluster of semantically-similar raw interactions has recurred (see recmem.py). The
prompt turns that raw cluster into two kinds of durable memory:

    episodes — concise summaries of what happened ("the user debugged a failing
               import in app.py and fixed it by adding a __future__ import")
    facts    — de-contextualised, reusable statements about the user or world
               ("the user runs Python 3.11 on Windows")

Output is strict JSON so the engine can parse it without an LLM in the loop.
"""
from __future__ import annotations

CONSOLIDATION_SYSTEM = (
    "You consolidate a cluster of related raw interaction snippets from an AI "
    "assistant's memory into durable long-term memories. Produce TWO kinds:\n"
    "  • episodes: short third-person summaries of what happened / was done. Keep "
    "concrete details that would matter later (names, paths, decisions, outcomes).\n"
    "  • facts: durable, de-contextualised statements about the USER, their "
    "projects, environment, or preferences — the kind of thing worth recalling in a "
    "totally different future session.\n"
    "Rules: merge duplicates, drop small talk and one-off transient actions, keep "
    "each item a single self-contained sentence. If there is nothing durable, return "
    "empty lists. Respond with ONLY a JSON object of this exact shape:\n"
    '{"episodes": ["..."], "facts": ["..."]}'
)


def consolidation_user(snippets: list[str]) -> str:
    body = "\n".join(f"- {s.strip()}" for s in snippets if s and s.strip())
    return f"Related interaction snippets:\n{body}\n\nReturn the JSON."
