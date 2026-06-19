"""
shell_tool — runs shell commands for the code_agent.

Used for: running tests, linters, formatters, build scripts.
Safety:   timeout (default 30s, max 600s). stdout + stderr captured (merged, in order).

Output is streamed into a ShellSession while the command runs, so the user can press
Ctrl+O to watch it live and answer any prompt the command raises (e.g. an installer
asking to proceed) — the input is piped to the command's stdin. The timeout is
ignored while the user is viewing, and extended whenever they send input, so an
interactive command isn't killed mid-exchange.
"""
from __future__ import annotations

import asyncio
import codecs
import contextlib
import shlex
import sys
from pathlib import Path

from anet.core import shell_session as _ss


SCHEMA = {
    "type": "function",
    "function": {
        "name": "shell_tool",
        "description": (
            "Run a shell command and return its output. "
            "Use for: running tests (pytest, npm test), linters (ruff, eslint), "
            "formatters (black, prettier), build scripts, or package installs. "
            "Prefer non-interactive flags (e.g. -y/--yes) when available. If a command "
            "does pause for input, the user can answer it live (Ctrl+O), so it won't "
            "necessarily hang — but set a generous `timeout` for installs (300+). "
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
        # stdin=PIPE so the user can answer a prompt via the Ctrl+O view; stderr
        # merged into stdout so the live view shows output in true order.
        if sys.platform == "win32":
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.PIPE,
                cwd=work_dir,
            )
        else:
            args = shlex.split(command)
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.PIPE,
                cwd=work_dir,
            )

        loop = asyncio.get_event_loop()
        session = _ss.ShellSession(command, timeout)
        session.attach(proc)
        session.deadline = loop.time() + timeout
        _ss.set_active(session)
        _hint(command)

        timed_out = False
        decoder = codecs.getincrementaldecoder("utf-8")("replace")

        async def _pump() -> None:
            # One persistent reader. read() (not readline) returns on ANY available
            # bytes — so a prompt with no trailing newline still shows — and returns
            # b"" at EOF (including right after a kill). It is never cancelled mid-read,
            # which avoids ProactorEventLoop's overlapped-read noise on Windows.
            while True:
                data = await proc.stdout.read(4096)
                if not data:
                    break
                session.append(decoder.decode(data))
            session.append(decoder.decode(b"", final=True))

        pump = asyncio.create_task(_pump())
        try:
            while not pump.done():
                # Skip the deadline entirely while the user is watching/answering.
                if not session.viewing and loop.time() > session.deadline:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()        # closes the pipe → _pump sees EOF and ends
                    timed_out = True
                    break
                await asyncio.sleep(0.1)
            # On a normal exit the pump is already done (instant). On a kill, don't
            # wait long for the pipe to EOF on Windows — we already have the buffer.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(pump, timeout=0.5 if timed_out else 5)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=0.5 if timed_out else 5)
        finally:
            if not pump.done():
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await pump
            session.done = True
            session.exit_code = proc.returncode
            with contextlib.suppress(Exception):
                if proc.stdin and not proc.stdin.is_closing():
                    proc.stdin.close()
            _ss.clear_active(session)

        combined = session.snapshot().strip()
        if len(combined) > _MAX_OUTPUT:
            # Keep the head AND the tail: for installs/builds the command, and the
            # result/errors, sit at opposite ends — truncating only the head would
            # hide how it finished (and make the model re-run it).
            head = _MAX_OUTPUT // 3
            tail = _MAX_OUTPUT - head
            cut  = len(combined) - _MAX_OUTPUT
            combined = (combined[:head]
                        + f"\n… [{cut} chars truncated] …\n"
                        + combined[-tail:])

        if timed_out:
            msg = (
                f"Command timed out after {timeout}s: {command}\n"
                "It may have been waiting for input — the user can press Ctrl+O to "
                "view and answer the command while it runs (or add a non-interactive "
                "flag like -y)."
            )
            if combined:
                msg += f"\nPartial output:\n{combined}"
            return {"error": msg}

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


def _hint(command: str) -> None:
    """Print a persistent one-liner that the running command can be viewed/answered
    (stays on screen, unlike the transient spinner)."""
    with contextlib.suppress(Exception):
        from anet.core.context import on_notice
        short = command if len(command) <= 56 else command[:53] + "…"
        on_notice.get()(f"shell: {short}  ·  Ctrl+O to view / answer prompts")
