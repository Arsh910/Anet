"""
edit_tool — targeted file editing, inspired by Claude Code's FileEditTool.

Makes surgical old_string → new_string replacements without rewriting whole files.

Key features:
  - Quote normalization  : curly " ' vs straight " ' won't break matches
  - Staleness guard      : rejects if file was modified since it was last read
  - Multiple-match guard : rejects ambiguous edits unless replace_all=True
  - File creation        : old_string='' with new_string creates the file
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from pathlib import Path
from typing import Optional

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

# ── Quote normalisation ───────────────────────────────────────────────────────
# Models often output straight quotes; source files may have curly/smart quotes.
# Normalise both sides before comparing so edits don't fail on typography.

_QUOTE_MAP = str.maketrans({
    "‘": "'",  # left single
    "’": "'",  # right single
    "‚": "'",  # single low-9
    "‛": "'",  # single high-reversed-9
    "“": '"',  # left double
    "”": '"',  # right double
    "„": '"',  # double low-9
    "‟": '"',  # double high-reversed-9
    "′": "'",  # prime
    "″": '"',  # double prime
    "«": '"',  # left-pointing double angle
    "»": '"',  # right-pointing double angle
})


def _normalize(text: str) -> str:
    """Normalize quotes and unicode whitespace for fuzzy matching."""
    return unicodedata.normalize("NFC", text).translate(_QUOTE_MAP)


def _find_occurrences(content: str, needle: str) -> list[int]:
    """Return list of start indices where needle appears in content."""
    if not needle:
        return []
    positions = []
    start = 0
    while True:
        idx = content.find(needle, start)
        if idx == -1:
            break
        positions.append(idx)
        start = idx + 1
    return positions


def _find_with_normalization(content: str, needle: str) -> tuple[list[int], str]:
    """
    Try exact match first; fall back to quote-normalized match.
    Returns (positions, actual_needle_used).
    """
    positions = _find_occurrences(content, needle)
    if positions:
        return positions, needle

    # Try normalizing both sides
    norm_content = _normalize(content)
    norm_needle  = _normalize(needle)
    positions    = _find_occurrences(norm_content, norm_needle)
    if positions:
        # Map back to actual character positions in original content
        # (lengths may differ after normalization — safe because our map is 1:1 char)
        return positions, norm_needle

    return [], needle


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

    # ── Find the target string (with normalization fallback) ──────────────────
    positions, actual_needle = _find_with_normalization(content, old_string)

    if not positions:
        # Offer a useful hint: show lines that partially match
        lines = content.splitlines()
        first_word = old_string.strip().split()[0] if old_string.strip() else ""
        hints = [f"  line {i+1}: {l}" for i, l in enumerate(lines)
                 if first_word and first_word.lower() in l.lower()][:3]
        hint = ("\nLines containing first word:\n" + "\n".join(hints)) if hints else ""
        return {"error": f"old_string not found in {path}.{hint}"}

    if len(positions) > 1 and not replace_all:
        return {
            "error": (
                f"old_string appears {len(positions)} times in {path}. "
                "Add more surrounding context to make it unique, "
                "or set replace_all=true to replace every occurrence."
            )
        }

    # ── Apply the edit ────────────────────────────────────────────────────────
    if actual_needle != old_string:
        # Normalized match — work on normalized content
        norm_content = _normalize(content)
        updated = norm_content.replace(actual_needle, new_string, -1 if replace_all else 1)
    else:
        updated = content.replace(old_string, new_string, -1 if replace_all else 1)

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

    n = len(positions)
    replaced = n if replace_all else 1
    summary = f"Edited {path} ({replaced} occurrence(s) replaced)"
    result  = f"{summary}\n\n{diff_text}" if diff_text else summary
    return {"result": result}
