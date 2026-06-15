"""
edit_tool — targeted file editing, inspired by Claude Code's FileEditTool.

Makes surgical old_string → new_string replacements without rewriting whole files.

Key features:
  - Fuzzy matching       : tolerates whitespace / indentation / quote / escape
                           drift via a 9-strategy chain (see fuzzy_match.py)
  - Staleness guard      : rejects if file was modified since it was last read
  - Multiple-match guard : rejects ambiguous edits unless replace_all=True
  - File creation        : old_string='' with new_string creates the file
"""
from __future__ import annotations

import difflib
from pathlib import Path

from anet.AnetTools.edit_tool.fuzzy_match import (
    format_no_match_hint,
    fuzzy_find_and_replace,
)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "edit_tool",
        "description": (
            "Make a targeted edit to a file by replacing old_string with new_string. "
            "ALWAYS read the file first so you know the exact content. "
            "Use this instead of write_file whenever you are changing part of an existing file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file to edit.",
                },
                "old_string": {
                    "type": "string",
                    "description": (
                        "The exact text to find and replace. "
                        "Must be unique in the file unless replace_all=true. "
                        "Pass empty string to create a new file (new_string becomes the content)."
                    ),
                },
                "new_string": {
                    "type": "string",
                    "description": "The text to replace old_string with.",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace every occurrence of old_string. Default false.",
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
}

# ── Staleness tracking ────────────────────────────────────────────────────────
# Records (mtime, size) when a file is read so we can detect concurrent changes.

_read_cache: dict[str, tuple[float, int]] = {}


def _record_read(path: Path) -> None:
    stat = path.stat()
    _read_cache[str(path)] = (stat.st_mtime, stat.st_size)


def _is_stale(path: Path) -> bool:
    """True if the file changed on disk since the agent last read it."""
    key = str(path)
    if key not in _read_cache:
        return False   # never read → not stale, just untracked
    stat = path.stat()
    cached_mtime, cached_size = _read_cache[key]
    return stat.st_mtime != cached_mtime or stat.st_size != cached_size


# ── Entry point ───────────────────────────────────────────────────────────────

async def run(params: dict) -> dict:
    path_str   = params.get("path", "").strip()
    old_string = params.get("old_string", "")
    new_string = params.get("new_string", "")
    replace_all = bool(params.get("replace_all", False))

    if not path_str:
        return {"error": "path is required"}
    if old_string == new_string:
        return {"error": "old_string and new_string are identical — nothing to change"}

    path = Path(path_str)

    # ── File creation mode (old_string == '') ─────────────────────────────────
    if old_string == "":
        if path.exists():
            return {"error": f"File already exists: {path}. Use old_string/new_string to edit it."}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_string, encoding="utf-8")
        _record_read(path)
        return {"result": f"Created: {path}"}

    # ── File must exist for editing ───────────────────────────────────────────
    if not path.exists():
        # Suggest similar filenames
        siblings = [p.name for p in path.parent.glob("*") if p.is_file()] if path.parent.exists() else []
        hint = f" Similar files nearby: {', '.join(siblings[:5])}" if siblings else ""
        return {"error": f"File not found: {path}.{hint}"}

    # ── Staleness check ───────────────────────────────────────────────────────
    if _is_stale(path):
        return {
            "error": (
                f"File has changed since you last read it: {path}\n"
                "Read it again with file_tool(action='read_file') before editing."
            )
        }

    # ── Read current content ──────────────────────────────────────────────────
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"error": f"Could not read file: {exc}"}

    # ── Find + replace via the multi-strategy fuzzy matcher ───────────────────
    updated, match_count, strategy, error = fuzzy_find_and_replace(
        content, old_string, new_string, replace_all,
    )

    if error:
        hint = format_no_match_hint(error, match_count, old_string, content)
        return {"error": f"{error} ({path}).{hint}"}

    try:
        path.write_text(updated, encoding="utf-8")
    except OSError as exc:
        return {"error": f"Could not write file: {exc}"}

    _record_read(path)   # update cache so next edit doesn't see false staleness

    # ── Generate unified diff for visibility ─────────────────────────────────
    diff_lines = list(difflib.unified_diff(
        content.splitlines(keepends=True),
        updated.splitlines(keepends=True),
        fromfile=f"a/{path.name}",
        tofile=f"b/{path.name}",
        lineterm="\n",
    ))
    diff_text = "".join(diff_lines).rstrip()

    # Note when a non-exact strategy matched so the model knows its old_string
    # drifted from the file — useful signal for it to read more carefully next time.
    strat_note = f" via {strategy} match" if strategy and strategy != "exact" else ""
    summary = f"Edited {path} ({match_count} occurrence(s) replaced{strat_note})"
    result  = f"{summary}\n\n{diff_text}" if diff_text else summary
    return {"result": result}
