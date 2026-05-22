"""
conflict_tool — resolve git merge conflicts.

Each conflict is numbered (1-based). The agent lists conflicts, inspects
each one, and resolves by choosing @ours, @theirs, @base, or custom text.

Supports both standard merge style and diff3 style (with base section).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SCHEMA = {
    "type": "function",
    "function": {
        "name": "conflict_tool",
        "description": (
            "Resolve git merge conflicts. "
            "list: scan a file or directory for conflicts (returns numbered list). "
            "show: inspect conflict N in detail (ours/theirs/base content). "
            "resolve: fix conflict N with @ours, @theirs, @base, or custom text. "
            "resolve_all: fix every conflict in a file with the same strategy."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "show", "resolve", "resolve_all"],
                    "description": "Operation to perform.",
                },
                "path": {
                    "type": "string",
                    "description": "Absolute path to a file or directory.",
                },
                "conflict_n": {
                    "type": "integer",
                    "description": "Conflict number to operate on (1-based, from list output). Required for show and resolve.",
                },
                "resolution": {
                    "type": "string",
                    "description": (
                        "@ours — keep our (HEAD) version. "
                        "@theirs — take the incoming version. "
                        "@base — use the common ancestor (diff3 only). "
                        "Any other string — use as literal replacement text."
                    ),
                },
            },
            "required": ["action", "path"],
        },
    },
}

# ── Conflict marker patterns ──────────────────────────────────────────────────

_RE_START = re.compile(r'^<{7}(?: (.+))?$')   # <<<<<<< label
_RE_BASE  = re.compile(r'^\|{7}')              # ||||||| (diff3 base start)
_RE_SEP   = re.compile(r'^={7}$')              # =======
_RE_END   = re.compile(r'^>{7}(?: (.+))?$')    # >>>>>>> label

# Extensions to scan when listing conflicts in a directory
_SCAN_EXTS: set[str] = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".c", ".cpp", ".h", ".hpp",
    ".java", ".rb", ".php", ".cs", ".swift", ".kt", ".yaml", ".yml", ".json",
    ".toml", ".md", ".txt", ".html", ".css", ".scss", ".sh", ".bash",
}


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class Conflict:
    n: int
    start_line: int      # 1-based, line of <<<<<<<
    end_line: int        # 1-based, line of >>>>>>>
    ours_label: str
    theirs_label: str
    ours: list[str] = field(default_factory=list)    # raw lines (with \n)
    base: list[str] = field(default_factory=list)    # empty unless diff3
    theirs: list[str] = field(default_factory=list)  # raw lines (with \n)


# ── Parser ────────────────────────────────────────────────────────────────────

def _parse_conflicts(lines: list[str]) -> list[Conflict]:
    """Parse all conflict blocks from a list of raw lines."""
    conflicts: list[Conflict] = []
    state = "normal"
    current: Optional[Conflict] = None

    for i, raw in enumerate(lines):
        line = raw.rstrip("\n\r")
        lineno = i + 1

        if state == "normal":
            m = _RE_START.match(line)
            if m:
                current = Conflict(
                    n=len(conflicts) + 1,
                    start_line=lineno,
                    end_line=-1,
                    ours_label=(m.group(1) or "HEAD").strip(),
                    theirs_label="",
                )
                state = "ours"

        elif state == "ours":
            if _RE_BASE.match(line):
                state = "base"
            elif _RE_SEP.match(line):
                state = "theirs"
            else:
                current.ours.append(raw)

        elif state == "base":
            if _RE_SEP.match(line):
                state = "theirs"
            else:
                current.base.append(raw)

        elif state == "theirs":
            m = _RE_END.match(line)
            if m:
                current.theirs_label = (m.group(1) or "incoming").strip()
                current.end_line = lineno
                conflicts.append(current)
                current = None
                state = "normal"
            else:
                current.theirs.append(raw)

    return conflicts


# ── Resolution ────────────────────────────────────────────────────────────────

def _apply_resolution(lines: list[str], conflict: Conflict, resolution: str) -> list[str]:
    """Replace the conflict block (start_line..end_line inclusive) with resolved content."""
    if resolution == "@ours":
        replacement = conflict.ours
    elif resolution == "@theirs":
        replacement = conflict.theirs
    elif resolution == "@base":
        replacement = conflict.base if conflict.base else conflict.ours
    else:
        # Custom text — ensure it ends with a newline and split into lines
        text = resolution if resolution.endswith("\n") else resolution + "\n"
        replacement = text.splitlines(keepends=True)

    before = lines[: conflict.start_line - 1]
    after  = lines[conflict.end_line :]
    return before + replacement + after


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_lines(path: Path) -> list[str] | str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except OSError as exc:
        return f"Cannot read {path}: {exc}"


def _preview(raw_lines: list[str], max_chars: int = 80) -> str:
    text = "".join(raw_lines[:3]).strip()
    text = text.replace("\n", " / ")
    return text[:max_chars] + ("…" if len(text) > max_chars else "")


# ── Entry point ───────────────────────────────────────────────────────────────

async def run(params: dict) -> dict:
    action   = params.get("action", "")
    path_str = params.get("path", "").strip()

    if not path_str:
        return {"error": "path is required"}

    path = Path(path_str)

    # ── list ─────────────────────────────────────────────────────────────────
    if action == "list":
        if path.is_dir():
            results = []
            for fp in sorted(path.rglob("*")):
                if not fp.is_file():
                    continue
                if fp.suffix and fp.suffix not in _SCAN_EXTS:
                    continue
                try:
                    content = fp.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if "<<<<<<<" not in content:
                    continue
                lines = content.splitlines(keepends=True)
                n = len(_parse_conflicts(lines))
                if n:
                    results.append((str(fp), n))
            if not results:
                return {"result": "No conflicts found in directory."}
            out = [f"Conflicts found in {len(results)} file(s):"]
            for fp_str, n in results:
                out.append(f"  {fp_str}  ({n} conflict(s))")
            return {"result": "\n".join(out)}

        if not path.exists():
            return {"error": f"File not found: {path}"}
        lines = _read_lines(path)
        if isinstance(lines, str):
            return {"error": lines}
        conflicts = _parse_conflicts(lines)
        if not conflicts:
            return {"result": f"No conflicts found in {path}"}

        out = [f"{len(conflicts)} conflict(s) in {path}:\n"]
        for c in conflicts:
            tag = " [diff3]" if c.base else ""
            out.append(
                f"  [{c.n}]{tag}  lines {c.start_line}-{c.end_line}\n"
                f"     ours   ({c.ours_label}): {_preview(c.ours)}\n"
                f"     theirs ({c.theirs_label}): {_preview(c.theirs)}\n"
            )
        return {"result": "\n".join(out)}

    # ── show ──────────────────────────────────────────────────────────────────
    elif action == "show":
        n = params.get("conflict_n")
        if n is None:
            return {"error": "conflict_n is required for show"}
        if not path.exists():
            return {"error": f"File not found: {path}"}
        lines = _read_lines(path)
        if isinstance(lines, str):
            return {"error": lines}
        conflicts = _parse_conflicts(lines)
        match = next((c for c in conflicts if c.n == n), None)
        if match is None:
            return {"error": f"Conflict {n} not found — file has {len(conflicts)} conflict(s)."}
        c = match
        parts = [f"Conflict {c.n}  (lines {c.start_line}-{c.end_line})"]
        parts.append(f"\n── OURS [{c.ours_label}] ──────────────────\n" + "".join(c.ours).rstrip())
        if c.base:
            parts.append("\n── BASE ─────────────────────────────────\n" + "".join(c.base).rstrip())
        parts.append(f"\n── THEIRS [{c.theirs_label}] ─────────────────\n" + "".join(c.theirs).rstrip())
        parts.append(
            f"\nResolve: conflict_tool(action='resolve', path='{path}', "
            f"conflict_n={c.n}, resolution='@ours|@theirs|@base|<custom>')"
        )
        return {"result": "\n".join(parts)}

    # ── resolve ───────────────────────────────────────────────────────────────
    elif action == "resolve":
        n          = params.get("conflict_n")
        resolution = params.get("resolution", "")
        if n is None:
            return {"error": "conflict_n is required for resolve"}
        if not resolution:
            return {"error": "resolution is required (@ours, @theirs, @base, or custom text)"}
        if not path.exists():
            return {"error": f"File not found: {path}"}
        lines = _read_lines(path)
        if isinstance(lines, str):
            return {"error": lines}
        conflicts = _parse_conflicts(lines)
        match = next((c for c in conflicts if c.n == n), None)
        if match is None:
            return {"error": f"Conflict {n} not found — file has {len(conflicts)} conflict(s)."}
        updated = _apply_resolution(lines, match, resolution)
        try:
            path.write_text("".join(updated), encoding="utf-8")
        except OSError as exc:
            return {"error": f"Could not write file: {exc}"}
        remaining = len(_parse_conflicts(updated))
        suffix = f"  {remaining} conflict(s) remaining." if remaining else "  File is clean ✓"
        return {"result": f"Resolved conflict {n} ({resolution}) in {path}.{suffix}"}

    # ── resolve_all ───────────────────────────────────────────────────────────
    elif action == "resolve_all":
        resolution = params.get("resolution", "")
        if not resolution:
            return {"error": "resolution is required (@ours, @theirs, or @base)"}
        if resolution not in ("@ours", "@theirs", "@base"):
            return {"error": "resolve_all only supports @ours, @theirs, or @base (not custom text — use resolve for that)"}
        if not path.exists():
            return {"error": f"File not found: {path}"}
        lines = _read_lines(path)
        if isinstance(lines, str):
            return {"error": lines}
        conflicts = _parse_conflicts(lines)
        if not conflicts:
            return {"result": f"No conflicts in {path}"}
        # Apply in reverse order so earlier line numbers stay valid
        for c in reversed(conflicts):
            lines = _apply_resolution(lines, c, resolution)
        try:
            path.write_text("".join(lines), encoding="utf-8")
        except OSError as exc:
            return {"error": f"Could not write file: {exc}"}
        return {"result": f"Resolved all {len(conflicts)} conflict(s) in {path} with {resolution}.  File is clean ✓"}

    else:
        return {"error": f"Unknown action '{action}'. Valid actions: list, show, resolve, resolve_all"}
