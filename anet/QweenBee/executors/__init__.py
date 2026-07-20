"""
anet.QweenBee.executors — execution-side shared types + context assembly.

The single temporal executor (executors/temporal.py) is PURE topology +
context-assembly logic: it doesn't call an LLM or know about agents/tools
directly. It schedules subtasks and builds each one's prompt, then calls
ctx.run_subtask(subtask, prompt) — an injected coroutine that the engine
wires to the real agent loop (orchestrator.run). That keeps the
scheduling/context behaviour unit-testable with a fake run_subtask.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from anet.QweenBee.dag import Subtask


@dataclass
class StepResult:
    id: str
    agent: str
    description: str
    output: str
    success: bool = True
    error: str | None = None


# run_subtask: async (subtask, assembled_prompt) -> StepResult
RunSubtask = Callable[[Subtask, str], Awaitable["StepResult"]]


@dataclass
class ExecContext:
    run_subtask: RunSubtask                       # how to actually execute one subtask
    global_context: str = ""                      # rolling summary / shared world-state
    on_status: Callable[[str], None] = lambda _s: None
    seq_budget: int = 6000                        # char budget for accumulated context


# ── Shared context assembly ─────────────────────────────────────────────────────

def compose(description: str, global_context: str = "", forward_context: str = "") -> str:
    """Build the prompt a subtask's agent receives: shared context (if any), then
    relevant prior results (if any), then the task itself."""
    parts: list[str] = []
    if global_context and global_context.strip():
        parts.append(f"## Shared context\n{global_context.strip()}")
    if forward_context and forward_context.strip():
        parts.append(f"## Relevant prior results\n{forward_context.strip()}")
    parts.append(f"## Your task\n{description.strip()}")
    return "\n\n".join(parts)
