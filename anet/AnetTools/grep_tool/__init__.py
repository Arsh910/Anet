"""
grep_tool — search file contents by regex, inspired by Claude Code's GrepTool.

Uses ripgrep (rg) when available; falls back to Python re for portability.
Supports multiple output modes, context lines, file type filtering, and result caps.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

SCHEMA = {
    "type": "function",
    "function": {
        "name": "grep_tool",
        "description": (
            "Search file contents using a regex pattern. "
            "Use output_mode='files_with_matches' (default) to find which files contain a pattern. "
            "Use output_mode='content' to see matching lines with context. "
            "Use output_mode='count' to count matches per file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regular expression to search for.",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search. Defaults to current directory.",
                },
                "glob": {
                    "type": "string",
                    "description": "Glob pattern to filter files, e.g. '*.py' or '**/*.ts'.",
                },
                "output_mode": {
                    "type": "string",
                    "enum": ["files_with_matches", "content", "count"],
                    "description": (
                        "files_with_matches=list of matching file paths (default), "
                        "content=matching lines with optional context, "
                        "count=number of matches per file."
                    ),
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Case-insensitive search.",
                },
                "line_numbers": {
                    "type": "boolean",
                    "description": "Show line numbers (default true for content mode).",
                },
                "after": {
                    "type": "integer",
                    "description": "Lines of context after each match (content mode).",
                },
                "before": {
                    "type": "integer",
                    "description": "Lines of context before each match (content mode).",
                },
                "context": {
                    "type": "integer",
                    "description": "Lines of context before and after each match (content mode).",
                },
                "head_limit": {
                    "type": "integer",
                    "description": "Max results to return. Default 250. Pass 0 for unlimited.",
                },
                "multiline": {
                    "type": "boolean",
                    "description": "Allow patterns to span multiple lines.",
                },
            },
            "required": ["pattern"],
        },
    },
}

_EXCLUDE_DIRS = {".git", ".svn", ".hg", "node_modules", "__pycache__",
                 ".venv", "venv", "env", "dist", "build", ".anet"}
_MAX_FILE_BYTES = 5 * 1024 * 1024   # 5 MB — skip larger files
_RG_TIMEOUT = 30                     # seconds


async def _run_rg(args: list[str]) -> tuple[str, bool]:
    """Run ripgrep and return (output, success)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "rg", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_RG_TIMEOUT)
        return stdout.decode("utf-8", errors="replace"), proc.returncode == 0
    except (FileNotFoundError, asyncio.TimeoutError):
        return "", False


def _python_search(
    pattern: str,
    root: Path,
    glob_pat: str | None,
    flags: int,
    output_mode: str,
    context_before: int,
    context_after: int,
    show_lineno: bool,
    head_limit: int,
    multiline: bool,
) -> dict:
    """Pure-Python fallback search when ripgrep is not installed."""
    try:
        re_flags = flags | (re.DOTALL if multiline else 0)
        rx = re.compile(pattern, re_flags)
    except re.error as exc:
        return {"error": f"Invalid regex: {exc}"}

    results: list[str] = []
    count_map: dict[str, int] = {}

    files = []
    if root.is_file():
        files = [root]
    else:
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            if any(part in _EXCLUDE_DIRS for part in p.parts):
                continue
            if glob_pat and not p.match(glob_pat):
                continue
            if p.stat().st_size > _MAX_FILE_BYTES:
                continue
            files.append(p)

    truncated = False
    for fpath in files:
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        if output_mode == "files_with_matches":
            if rx.search(text):
                results.append(str(fpath))
                if head_limit and len(results) >= head_limit:
                    truncated = True
                    break

        elif output_mode == "count":
            n = len(rx.findall(text))
            if n:
                count_map[str(fpath)] = n

        elif output_mode == "content":
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if rx.search(line):
                    start = max(0, i - context_before)
                    end   = min(len(lines), i + context_after + 1)
                    for j in range(start, end):
                        sep = ":" if j == i else "-"
                        prefix = f"{j+1}{sep}" if show_lineno else ""
                        results.append(f"{fpath}{sep}{prefix}{lines[j]}")
                    results.append("--")
                    if head_limit and len(results) >= head_limit:
                        truncated = True
                        break
            if truncated:
                break

    if output_mode == "count":
        lines = [f"{v} {k}" for k, v in sorted(count_map.items())]
        if head_limit:
            lines = lines[:head_limit]
        output = "\n".join(lines)
    else:
        if truncated:
            results.append(f"... [truncated at {head_limit} results]")
        output = "\n".join(results)

    return {"result": output or "(no matches)"}


async def run(params: dict) -> dict:
    pattern     = params.get("pattern", "").strip()
    path_str    = params.get("path", "").strip()
    glob_pat    = params.get("glob", "")
    output_mode = params.get("output_mode", "files_with_matches")
    case_insens = bool(params.get("case_insensitive", params.get("-i", False)))
    show_lineno = bool(params.get("line_numbers",     params.get("-n", True)))
    ctx_after   = int(params.get("after",             params.get("-A", 0)))
    ctx_before  = int(params.get("before",            params.get("-B", 0)))
    ctx_both    = int(params.get("context",           params.get("-C", 0)))
    head_limit  = int(params.get("head_limit", 250))
    multiline   = bool(params.get("multiline", False))

    if not pattern:
        return {"error": "pattern is required"}

    if ctx_both:
        ctx_before = ctx_after = ctx_both

    search_path = Path(path_str).resolve() if path_str else Path.cwd()
    if not search_path.exists():
        return {"error": f"Path not found: {search_path}"}

    # ── Try ripgrep first ─────────────────────────────────────────────────────
    rg_args = [pattern, str(search_path), "--no-heading"]

    if output_mode == "files_with_matches":
        rg_args += ["-l"]
    elif output_mode == "count":
        rg_args += ["-c"]
    # content mode: default (show matching lines)

    if case_insens:
        rg_args += ["-i"]
    if show_lineno and output_mode == "content":
        rg_args += ["-n"]
    if ctx_after:
        rg_args += [f"-A{ctx_after}"]
    if ctx_before:
        rg_args += [f"-B{ctx_before}"]
    if glob_pat:
        rg_args += [f"--glob={glob_pat}"]
    if multiline:
        rg_args += ["-U", "--multiline-dotall"]
    if head_limit:
        rg_args += [f"--max-count={head_limit}"]

    # Always exclude noisy dirs
    for d in _EXCLUDE_DIRS:
        rg_args += [f"--glob=!{d}"]

    output, ok = await _run_rg(rg_args)
    if ok or (not ok and output):
        # rg returns exit code 1 for "no matches" but still succeeds
        return {"result": output.strip() or "(no matches)"}

    # ── Python fallback ───────────────────────────────────────────────────────
    re_flags = re.IGNORECASE if case_insens else 0
    return _python_search(
        pattern, search_path, glob_pat or None, re_flags,
        output_mode, ctx_before, ctx_after,
        show_lineno, head_limit, multiline,
    )
