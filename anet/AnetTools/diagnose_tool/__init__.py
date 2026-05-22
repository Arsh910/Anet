"""
diagnose_tool — run linters and type-checkers on source files.

Auto-detects language from file extension and runs available tools.
Returns structured diagnostics (file, line, col, severity, message) + raw output.

Python : ruff (lint) and/or pyright (types) and/or mypy (types)
JS/TS  : eslint (lint) and/or tsc (types)
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

SCHEMA = {
    "type": "function",
    "function": {
        "name": "diagnose_tool",
        "description": (
            "Run linters and type-checkers on a file or directory and return structured diagnostics. "
            "Auto-detects language from file extension and runs all available checkers. "
            "Call this after editing code to verify correctness before reporting done. "
            "Python: ruff (lint) + pyright (types). "
            "JS/TS: eslint (lint) + tsc --noEmit (types). "
            "Returns error count, warning count, and per-line diagnostics."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to a file or directory to check.",
                },
                "checker": {
                    "type": "string",
                    "enum": ["auto", "ruff", "pyright", "mypy", "eslint", "tsc"],
                    "description": (
                        "Which checker to run. 'auto' (default) runs all available checkers "
                        "for the detected language."
                    ),
                },
                "fix": {
                    "type": "boolean",
                    "description": (
                        "Auto-fix fixable issues where supported (ruff --fix, eslint --fix). "
                        "Default false."
                    ),
                },
                "cwd": {
                    "type": "string",
                    "description": (
                        "Working directory for the checker. "
                        "Defaults to the file's parent directory. "
                        "Set this to the project root so checkers pick up config files."
                    ),
                },
            },
            "required": ["path"],
        },
    },
}

_TIMEOUT = 60  # seconds per checker run


# ── Subprocess helper ──────────────────────────────────────────────────────────

async def _run(args: list[str], cwd: str | None = None) -> tuple[int, str, str]:
    """Run a subprocess; return (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT)
        return (
            proc.returncode,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return -1, "", f"Timed out after {_TIMEOUT}s"
    except FileNotFoundError:
        return -2, "", f"Not installed: {args[0]}"


# ── Output parsers ─────────────────────────────────────────────────────────────

def _parse_ruff(raw: str) -> list[dict]:
    """ruff check --output-format json"""
    try:
        items = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    out = []
    for item in items:
        loc  = item.get("location", {})
        code = item.get("code", "")
        out.append({
            "file":     item.get("filename", ""),
            "line":     loc.get("row", 0),
            "col":      loc.get("column", 0),
            "code":     code,
            "message":  item.get("message", ""),
            "severity": "error" if code.startswith("E") else "warning",
        })
    return out


def _parse_pyright(raw: str) -> list[dict]:
    """pyright --outputjson  (0-indexed lines → convert to 1-indexed)"""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    out = []
    for diag in data.get("generalDiagnostics", []):
        start = diag.get("range", {}).get("start", {})
        out.append({
            "file":     diag.get("file", ""),
            "line":     start.get("line", 0) + 1,
            "col":      start.get("character", 0) + 1,
            "code":     diag.get("rule", ""),
            "message":  diag.get("message", ""),
            "severity": diag.get("severity", "error"),
        })
    return out


_LINE_PATTERN = re.compile(
    r"^(.+?):(\d+)(?::(\d+))?: (error|warning|note): (.+)$"
)

def _parse_line_based(raw: str) -> list[dict]:
    """Generic parser for mypy / pyright text mode: file:line:col: severity: msg"""
    out = []
    for line in raw.splitlines():
        m = _LINE_PATTERN.match(line.strip())
        if m:
            out.append({
                "file":     m.group(1),
                "line":     int(m.group(2)),
                "col":      int(m.group(3) or 0),
                "code":     "",
                "message":  m.group(5),
                "severity": m.group(4),
            })
    return out


def _parse_eslint(raw: str) -> list[dict]:
    """eslint --format json"""
    try:
        files = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    out = []
    for f in files:
        fpath = f.get("filePath", "")
        for msg in f.get("messages", []):
            out.append({
                "file":     fpath,
                "line":     msg.get("line", 0),
                "col":      msg.get("column", 0),
                "code":     str(msg.get("ruleId") or ""),
                "message":  msg.get("message", ""),
                "severity": "error" if msg.get("severity", 1) == 2 else "warning",
            })
    return out


def _parse_tsc(raw: str) -> list[dict]:
    """tsc --noEmit  →  file(line,col): error TSxxxx: message"""
    pattern = re.compile(r"^(.+?)\((\d+),(\d+)\): (error|warning) (TS\d+): (.+)$")
    out = []
    for line in raw.splitlines():
        m = pattern.match(line.strip())
        if m:
            out.append({
                "file":     m.group(1),
                "line":     int(m.group(2)),
                "col":      int(m.group(3)),
                "code":     m.group(5),
                "message":  m.group(6),
                "severity": m.group(4),
            })
    return out


# ── Per-checker runners ────────────────────────────────────────────────────────

async def _check_ruff(path: str, fix: bool, cwd: str) -> dict:
    args = ["ruff", "check", "--output-format", "json"]
    if fix:
        args.append("--fix")
    args.append(path)
    code, out, err = await _run(args, cwd)
    if code == -2:
        return {"checker": "ruff", "available": False}
    errors = _parse_ruff(out)
    return _result("ruff", errors, out + err)


async def _check_pyright(path: str, cwd: str) -> dict:
    code, out, err = await _run(["pyright", "--outputjson", path], cwd)
    if code == -2:
        return {"checker": "pyright", "available": False}
    errors = _parse_pyright(out)
    if not errors:
        errors = _parse_line_based(out + err)  # text fallback
    return _result("pyright", errors, out + err)


async def _check_mypy(path: str, cwd: str) -> dict:
    args = ["mypy", "--show-column-numbers", "--no-error-summary", path]
    code, out, err = await _run(args, cwd)
    if code == -2:
        return {"checker": "mypy", "available": False}
    errors = _parse_line_based(out + err)
    return _result("mypy", errors, out + err)


async def _check_eslint(path: str, fix: bool, cwd: str) -> dict:
    args = ["eslint", "--format", "json"]
    if fix:
        args.append("--fix")
    args.append(path)
    code, out, err = await _run(args, cwd)
    if code == -2:
        return {"checker": "eslint", "available": False}
    errors = _parse_eslint(out)
    return _result("eslint", errors, out + err)


async def _check_tsc(cwd: str) -> dict:
    # Run from project root so tsconfig.json is respected; no file arg needed.
    code, out, err = await _run(["tsc", "--noEmit"], cwd)
    if code == -2:
        return {"checker": "tsc", "available": False}
    errors = _parse_tsc(out + err)
    return _result("tsc", errors, out + err)


def _result(checker: str, errors: list[dict], raw: str) -> dict:
    ec = sum(1 for e in errors if e["severity"] == "error")
    wc = sum(1 for e in errors if e["severity"] == "warning")
    return {
        "checker":       checker,
        "available":     True,
        "error_count":   ec,
        "warning_count": wc,
        "errors":        errors,
        "raw":           raw.strip()[:4000],  # cap raw output per checker
    }


# ── Language detection ─────────────────────────────────────────────────────────

def _language(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix in (".ts", ".tsx"):
        return "typescript"
    if suffix in (".js", ".jsx", ".mjs", ".cjs"):
        return "javascript"
    return "unknown"


# ── Entry point ───────────────────────────────────────────────────────────────

async def run(params: dict) -> dict:
    path_str = params.get("path", "").strip()
    checker  = params.get("checker", "auto")
    fix      = bool(params.get("fix", False))
    cwd_in   = params.get("cwd", "").strip()

    if not path_str:
        return {"error": "path is required"}

    path = Path(path_str)
    if not path.exists():
        return {"error": f"Path not found: {path}"}

    cwd  = cwd_in or str(path.parent if path.is_file() else path)
    lang = _language(path) if path.is_file() else "unknown"

    # ── Dispatch ──────────────────────────────────────────────────────────────
    if checker == "auto":
        if lang == "python":
            results = list(await asyncio.gather(
                _check_ruff(path_str, fix, cwd),
                _check_pyright(path_str, cwd),
            ))
        elif lang in ("typescript", "javascript"):
            results = list(await asyncio.gather(
                _check_eslint(path_str, fix, cwd),
                _check_tsc(cwd),
            ))
        else:
            # Best-effort: run ruff and eslint, keep whichever is available
            results = list(await asyncio.gather(
                _check_ruff(path_str, fix, cwd),
                _check_eslint(path_str, fix, cwd),
            ))
    elif checker == "ruff":
        results = [await _check_ruff(path_str, fix, cwd)]
    elif checker == "pyright":
        results = [await _check_pyright(path_str, cwd)]
    elif checker == "mypy":
        results = [await _check_mypy(path_str, cwd)]
    elif checker == "eslint":
        results = [await _check_eslint(path_str, fix, cwd)]
    elif checker == "tsc":
        results = [await _check_tsc(cwd)]
    else:
        return {"error": f"Unknown checker '{checker}'"}

    available = [r for r in results if r.get("available", True)]
    if not available:
        names = ", ".join(r["checker"] for r in results)
        return {
            "error": (
                f"No checkers found on PATH ({names}). "
                "Install with:  pip install ruff pyright   or   npm install -g eslint typescript"
            )
        }

    total_errors   = sum(r.get("error_count", 0)   for r in available)
    total_warnings = sum(r.get("warning_count", 0) for r in available)

    status  = "PASS" if total_errors == 0 else "FAIL"
    w_part  = f", {total_warnings} warning(s)" if total_warnings else ""
    summary = f"{status} — {total_errors} error(s){w_part} in {path}"

    return {
        "summary":        summary,
        "total_errors":   total_errors,
        "total_warnings": total_warnings,
        "language":       lang,
        "checkers":       available,
    }
