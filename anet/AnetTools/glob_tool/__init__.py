"""
glob_tool — find files by pattern, inspired by Claude Code's GlobTool.

Results are sorted by modification time (most recently changed first),
which matches what developers care about during active editing.
"""
from __future__ import annotations

from pathlib import Path

SCHEMA = {
    "type": "function",
    "function": {
        "name": "glob_tool",
        "description": (
            "Find files matching a glob pattern. "
            "Results are sorted by modification time (most recently changed first). "
            "Use to locate files before reading or editing them. "
            "Examples: '**/*.py', 'src/**/*.ts', '*.json'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match. Use ** for recursive search.",
                },
                "path": {
                    "type": "string",
                    "description": "Root directory to search. Defaults to current directory.",
                },
                "head_limit": {
                    "type": "integer",
                    "description": "Max files to return. Default 100.",
                },
            },
            "required": ["pattern"],
        },
    },
}

_EXCLUDE_DIRS = {".git", ".svn", ".hg", "node_modules", "__pycache__",
                 ".venv", "venv", "env", "dist", "build", ".anet",
                 ".pytest_cache", ".mypy_cache", ".ruff_cache"}


async def run(params: dict) -> dict:
    pattern    = params.get("pattern", "").strip()
    path_str   = params.get("path", "").strip()
    head_limit = int(params.get("head_limit", 100))

    if not pattern:
        return {"error": "pattern is required"}

    root = Path(path_str).resolve() if path_str else Path.cwd()
    if not root.exists():
        return {"error": f"Path not found: {root}"}

    try:
        matches = [
            p for p in root.glob(pattern)
            if p.is_file()
            and not any(part in _EXCLUDE_DIRS for part in p.relative_to(root).parts)
        ]
    except Exception as exc:
        return {"error": f"Glob failed: {exc}"}

    # Sort by mtime descending — most recently modified first
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    truncated = len(matches) > head_limit
    matches   = matches[:head_limit]

    lines = [str(p) for p in matches]
    if truncated:
        lines.append(f"... [truncated — showing {head_limit} of more results]")

    return {
        "result":    "\n".join(lines) if lines else "(no files matched)",
        "num_files": len(matches),
        "truncated": truncated,
    }
