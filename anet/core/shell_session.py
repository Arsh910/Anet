"""
shell_session.py — the bridge that makes a running shell command viewable and
interactive from the TUI (the Ctrl+O "shell view").

`shell_tool` streams a command's output into a `ShellSession` and registers it as
the active session while it runs. The CLI, on Ctrl+O, looks up the active session,
shows its live output, and lets the user type a line that is piped to the command's
stdin — so an installer that pauses on a prompt (e.g. Playwright asking to proceed)
can be answered instead of hanging until the timeout.

Only ONE session is "active" at a time (the foreground command). This module is the
single shared point of contact, so the tool (which owns the process) and the CLI
(which owns the screen) stay decoupled.
"""
from __future__ import annotations

import asyncio


class ShellSession:
    """A running shell command's live output buffer + a writer to its stdin."""

    def __init__(self, command: str, timeout: float) -> None:
        self.command   = command
        self.timeout   = timeout
        self._chunks:  list[str] = []          # decoded output, in order
        self._proc:    "asyncio.subprocess.Process | None" = None
        self.done      = False
        self.exit_code: int | None = None
        # Wall-clock deadline (loop time). Extended when the user sends input, and
        # ignored entirely while the user is viewing — so interacting never trips
        # the timeout. A truly stuck, unwatched command still dies at the deadline.
        self.deadline: float | None = None
        self.viewing   = False

    # ── output (written by shell_tool, read by the view) ──────────────────────
    def append(self, text: str) -> None:
        if text:
            self._chunks.append(text)

    def snapshot(self) -> str:
        return "".join(self._chunks)

    # ── input (written by the view, piped to the process) ─────────────────────
    def attach(self, proc) -> None:
        self._proc = proc

    async def write_input(self, line: str) -> bool:
        """Send one line (newline appended) to the command's stdin. Extends the
        deadline so an interactive command isn't killed mid-exchange."""
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdin.is_closing():
            return False
        try:
            proc.stdin.write((line + "\n").encode("utf-8", "replace"))
            await proc.stdin.drain()
            if self.deadline is not None:
                self.deadline = asyncio.get_event_loop().time() + self.timeout
            return True
        except Exception:
            return False


# ── Active-session registry (single foreground command) ────────────────────────

_active: ShellSession | None = None


def set_active(session: ShellSession) -> None:
    global _active
    _active = session


def get_active() -> ShellSession | None:
    return _active


def clear_active(session: ShellSession | None = None) -> None:
    """Clear the active session. If `session` is given, only clear when it's the
    one still registered (so a finished command doesn't clear a newer one)."""
    global _active
    if session is None or _active is session:
        _active = None
