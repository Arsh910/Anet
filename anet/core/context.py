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
