"""
process_tool — start a command, capture output until a pattern matches or timeout.

Use case: start `npm run dev`, wait for "ready in" or an error, then stop.
This lets the agent verify a dev server / build actually works without hanging forever.

The command is killed after `timeout` seconds OR when `success_pattern` / `failure_pattern`
is found in stdout+stderr — whichever comes first.
"""
from __future__ import annotations

import asyncio
import re
import sys

SCHEMA = {
    "type": "function",
    "function": {
        "name": "process_tool",
        "description": (
            "Start a shell command, stream its output until a pattern is matched (success_pattern / "
            "failure_pattern) or the timeout expires, then ALWAYS terminate it — it does NOT keep running. "
            "Use this for any streaming or long-running command where you want to stop as soon as expected "
            "output appears, instead of waiting for it to finish: verify a dev server ('npm run dev' until "
            "'ready in'), check a build, OR run a command and stop the moment a specific line is printed "
            "(e.g. success_pattern='5' to stop once '5' appears in the output)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to run.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory. Defaults to current directory.",
                },
                "success_pattern": {
                    "type": "string",
                    "description": (
                        "Regex pattern that, when found in output, means the process started successfully. "
                        "Example: 'ready in|Local:' for Vite, 'compiled successfully' for webpack."
                    ),
                },
                "failure_pattern": {
                    "type": "string",
                    "description": (
                        "Regex pattern that means the process failed. "
                        "Example: 'error|Error|failed'. Defaults to common error keywords."
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max seconds to wait before killing the process. Default 30.",
                },
            },
            "required": ["command"],
        },
    },
}

_DEFAULT_FAILURE = r"(?i)\b(error|failed|cannot find|module not found|enoent|syntaxerror|typeerror)\b"
_DEFAULT_TIMEOUT = 30
_MAX_TIMEOUT     = 120
_MAX_OUTPUT_CHARS = 3000


async def run(params: dict) -> dict:
    command         = params.get("command", "").strip()
    cwd             = params.get("cwd") or None
    success_pattern = params.get("success_pattern") or None
    failure_pattern = params.get("failure_pattern") or _DEFAULT_FAILURE
    timeout         = min(int(params.get("timeout", _DEFAULT_TIMEOUT)), _MAX_TIMEOUT)

    if not command:
        return {"error": "command is required"}

    try:
        success_rx = re.compile(success_pattern) if success_pattern else None
        failure_rx = re.compile(failure_pattern) if failure_pattern else None
    except re.error as exc:
        return {"error": f"invalid regex: {exc}"}

    output_lines: list[str] = []
    matched_success = False
    matched_failure = False
    match_line      = ""

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,   # merge stderr into stdout
            stdin=asyncio.subprocess.DEVNULL,
        )
    except Exception as exc:
        return {"error": f"failed to start process: {exc}"}

    async def _read():
        nonlocal matched_success, matched_failure, match_line
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            output_lines.append(line)
            if success_rx and success_rx.search(line):
                matched_success = True
                match_line = line
                return
            if failure_rx and failure_rx.search(line):
                matched_failure = True
                match_line = line
                return

    try:
        await asyncio.wait_for(_read(), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    finally:
        # Always kill — we never leave the process running
        try:
            proc.kill()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception:
            pass

    output = "\n".join(output_lines)
    if len(output) > _MAX_OUTPUT_CHARS:
        output = output[-_MAX_OUTPUT_CHARS:]  # keep the tail (most recent)

    if matched_success:
        return {
            "status":     "success",
            "match":      match_line,
            "output":     output,
            "message":    f"Process started successfully. Matched: {match_line!r}",
        }
    if matched_failure:
        return {
            "status":     "failure",
            "match":      match_line,
            "output":     output,
            "message":    f"Process failed. Error line: {match_line!r}",
        }
    # Timeout — no pattern matched
    return {
        "status":  "timeout",
        "output":  output,
        "message": f"No pattern matched within {timeout}s. Last output captured above.",
    }
