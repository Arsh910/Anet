"""
shell_tool — runs shell commands for the code_agent.

Used for: running tests, linters, formatters, build scripts.
Safety:   30 second timeout by default. stdout + stderr both captured.
"""
from __future__ import annotations

import asyncio
import shlex
import sys
from pathlib import Path


SCHEMA = {
    "type": "function",
    "function": {
        "name": "shell_tool",
        "description": (
            "Run a shell command and return its output. "
            "Use for: running tests (pytest, npm test), linters (ruff, eslint), "
            "formatters (black, prettier), or build scripts. "
            "Do NOT use for destructive operations (rm -rf, git reset --hard, etc.)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to run. Example: 'python -m pytest tests/ -v'",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory. Default: current directory.",
                },
                "timeout": {
                    "type": "integer",
                    "description": (
                        "Timeout in seconds. Default: 30. Max: 600. "
                        "Use 300+ for package installs (npm install, pip install, npx create-*)."
                    ),
                },
            },
            "required": ["command"],
        },
    },
}

# Commands that are never allowed regardless of context
_BLOCKED = {
    "rm -rf", "rmdir /s", "del /f", "format ", ":(){:|:&};:",
    "git push --force", "git reset --hard", "drop table", "drop database",
    "shutdown", "reboot", "mkfs",
}

# Commands that require a live TTY / user interaction — reject with a helpful message
_INTERACTIVE = {
    "gcloud auth", "firebase login", "heroku login", "gh auth login",
    "ssh-keygen", "gpg --gen-key", "sudo ", "su ",
    "docker login", "az login", "aws configure",
}

_MAX_OUTPUT = 8_000   # truncate output to this many chars

# Commands where a specific non-zero exit code is not an error condition.
# Maps command name → set of "ok" exit codes beyond 0.
_EXIT_OK: dict[str, set[int]] = {
    "grep":    {1},   # 1 = no matches found
    "rg":      {1},   # 1 = no matches found
    "diff":    {1},   # 1 = files differ (requested comparison, not a failure)
    "git":     {1},   # git diff / git grep return 1 on no output
    "find":    {1},   # GNU find -quit path
}


def _is_success(command: str, returncode: int) -> bool:
    if returncode == 0:
        return True
    cmd0 = command.lstrip().split()[0].lower() if command.strip() else ""
    # Strip path prefix (e.g. /usr/bin/grep → grep)
    cmd0 = cmd0.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return returncode in _EXIT_OK.get(cmd0, set())


def _is_blocked(cmd: str) -> bool:
    lower = cmd.lower()
    return any(b in lower for b in _BLOCKED)


async def run(params: dict) -> dict:
    command = params.get("command", "").strip()
    cwd     = params.get("cwd")
    timeout = min(int(params.get("timeout", 30)), 600)

    if not command:
        return {"error": "No command provided."}

    if _is_blocked(command):
        return {"error": f"Command blocked for safety: '{command}'"}

    lower = command.lower()
    for pat in _INTERACTIVE:
        if pat in lower:
            return {
                "error": (
                    f"This command requires user interaction (no TTY available): '{command}'\n"
                    "Ask the user to run it manually in their terminal."
                )
            }

    # Validate cwd
    work_dir = None
    if cwd:
        work_dir = Path(cwd)
        if not work_dir.exists():
            return {"error": f"Directory not found: {cwd}"}
        work_dir = str(work_dir)

    try:
        if sys.platform == "win32":
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,  # no stdin → interactive prompts fail fast
                cwd=work_dir,
            )
        else:
            args = shlex.split(command)
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=work_dir,
            )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return {"error": f"Command timed out after {timeout}s: {command}"}

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        combined = (out + ("\n[stderr]\n" + err if err.strip() else "")).strip()

        if len(combined) > _MAX_OUTPUT:
            combined = combined[:_MAX_OUTPUT] + f"\n… [output truncated at {_MAX_OUTPUT} chars]"

        return {
            "result":      combined or "(no output)",
            "exit_code":   proc.returncode,
            "success":     _is_success(command, proc.returncode),
        }

    except FileNotFoundError:
        cmd_name = command.split()[0]
        return {"error": f"Command not found: '{cmd_name}'. Is it installed and on PATH?"}
    except Exception as exc:
        return {"error": str(exc)}
