from contextvars import ContextVar
from typing import Callable, Awaitable

# Status callback — set once per request in main.py, read by any graph node.
# ContextVar propagates safely through asyncio tasks without threading issues.
on_status: ContextVar[Callable[[str], None]] = ContextVar(
    "on_status", default=lambda _: None
)

# Streaming token callback — called once per streamed token in the synthesizer.
# Default is a no-op; main.py installs a Rich console printer per request.
on_token: ContextVar[Callable[[str], None]] = ContextVar(
    "on_token", default=lambda _: None
)

# Confirmation callback — called before any destructive tool action.
# Signature: async (tool_name: str, action: str, args: dict) -> bool
# Returns True = proceed, False = skip this action.
# Default auto-approves (non-interactive mode). main.py installs the real prompt.
async def _auto_approve(tool: str, action: str, args: dict) -> bool:
    return True

on_confirm: ContextVar[Callable[[str, str, dict], Awaitable[bool]]] = ContextVar(
    "on_confirm", default=_auto_approve
)

# Persistent output callback — for content that should appear above the live
# spinner and remain visible after it clears (e.g. file diffs, tool outputs).
# main.py installs a Rich console printer; default is a no-op.
on_output: ContextVar[Callable[[str], None]] = ContextVar(
    "on_output", default=lambda _: None
)

# Ask-user callback — lets a tool pause mid-task and ask the user a clarifying
# question, returning their free-text answer.
# Signature: async (question: str, options: list[str]) -> str
# Default (headless/non-interactive) returns a note so the agent proceeds on its
# own best judgment instead of hanging. main.py installs the real prompt.
async def _no_user(question: str, options=None) -> str:
    return "[no user is available to answer — proceed with your best assumption and note it]"

on_ask: ContextVar[Callable[[str, list], Awaitable[str]]] = ContextVar(
    "on_ask", default=_no_user
)
