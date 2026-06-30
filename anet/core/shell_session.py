"""
shell_session.py — the bridge that makes a running shell command viewable and
interactive from the TUI (the Ctrl+O "shell view").

`shell_tool` streams a command's output into a `ShellSession` and registers it as
an active session while it runs. The CLI, on Ctrl+O, looks up the sessions
started this turn, shows their live (or final) output, and lets the user type a
line that is piped to the command's stdin — so an installer that pauses on a
prompt (e.g. Playwright asking to proceed) can be answered instead of hanging
until the timeout.

The registry tracks **every** session started since the most recent `reset()` —
running ones and already-finished ones alike. That way the Ctrl+O picker can
surface a shell whose output was useful even after the command exited, instead
of dropping it the moment the foreground command moves on.

`reset()` is called at the start of each turn so stale shells from the previous
turn don't pollute the menu.
"""
from __future__ import annotations

import asyncio
import time


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
        self.started_at: float = time.time()
        self.ended_at:   float | None = None

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


# ── Session registry (every shell started this turn, in start order) ───────────

_sessions: list[ShellSession] = []


def set_active(session: ShellSession) -> None:
    """Register a newly-started session. Multiple sessions may be tracked at once
    (parallel shells, or a finished shell still kept around for the Ctrl+O picker)."""
    _sessions.append(session)


def clear_active(session: ShellSession | None = None) -> None:
    """Mark a session as finished — keeps it in the registry so the Ctrl+O picker
    can still surface its output; `reset()` is what actually drops it.
    `session is None` is a legacy no-arg path that just stamps any unfinished entry."""
    if session is not None:
        if session.ended_at is None:
            session.ended_at = time.time()
        return
    for s in _sessions:
        if s.ended_at is None:
            s.ended_at = time.time()


def get_active() -> ShellSession | None:
    """The most-recently-started session that is still running (None if nothing is).
    Kept for callers that only care about a single foreground session — e.g. the
    'Ctrl+O availability' hint emitted by shell_tool."""
    for s in reversed(_sessions):
        if not s.done:
            return s
    return None


def list_sessions() -> list[ShellSession]:
    """All sessions started since the last reset(), in start order. Includes
    finished ones (the Ctrl+O picker can still show their final output)."""
    return list(_sessions)


def list_running() -> list[ShellSession]:
    return [s for s in _sessions if not s.done]


def reset() -> None:
    """Drop the registry. Called at the start of each turn so stale entries
    from the previous turn don't pollute the Ctrl+O picker."""
    _sessions.clear()
