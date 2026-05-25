"""
spawn_tool — dynamically spawn a sub-agent mid-task.

Any agent can call spawn_tool(agent, task) to delegate work to a specialist
at runtime. The sub-agent runs its own full orchestrator loop and returns a
text summary. Depth is capped at 2: agents can spawn sub-agents, but
sub-agents cannot spawn further.

Wired up at startup via configure(tool_map, agents) called from main.py
after _merge_all() completes. No per-agent config needed — injected automatically.
"""
from __future__ import annotations

import contextvars

_MAX_DEPTH = 2

_tool_map: dict = {}
_agents:   list = []

# Tracks nesting depth per async call-chain. ContextVar propagates correctly
# through awaited calls without leaking between independent turns.
_spawn_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "spawn_depth", default=0
)

SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "spawn_tool",
        "description": (
            "Spawn a specialist sub-agent mid-task to handle work outside your own "
            "capabilities. The sub-agent runs independently and returns a text result. "
            "Use when you need another agent's skills without abandoning your current task."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Name of the agent to spawn.",
                },
                "task": {
                    "type": "string",
                    "description": (
                        "Task description for the sub-agent. Be specific — "
                        "it has no other context about what you are doing."
                    ),
                },
                "context": {
                    "type": "string",
                    "description": (
                        "Optional context to share with the sub-agent "
                        "(file paths, prior findings, constraints)."
                    ),
                },
            },
            "required": ["agent", "task"],
        },
    },
}


def configure(tool_map: dict, agents: list) -> None:
    """
    Called from main.py after _merge_all() to wire up the tool_map and agent list.
    Also updates the schema description with the real available agent names.
    """
    global _tool_map, _agents
    _tool_map = tool_map
    _agents   = agents

    names = [a["name"] for a in agents]
    if names:
        SCHEMA["function"]["parameters"]["properties"]["agent"]["description"] = (
            f"Name of the agent to spawn. Available: {', '.join(names)}."
        )


async def run(params: dict) -> dict:
    agent_name = (params.get("agent") or "").strip()
    task       = (params.get("task")  or "").strip()
    context    = (params.get("context") or "").strip()

    if not agent_name:
        return {"error": "agent is required"}
    if not task:
        return {"error": "task is required"}

    # Depth guard — sub-agents cannot keep spawning indefinitely
    depth = _spawn_depth.get()
    if depth >= _MAX_DEPTH:
        return {
            "error": (
                f"Spawn depth limit ({_MAX_DEPTH}) reached — "
                "sub-agents cannot spawn further sub-agents."
            )
        }

    # Resolve agent config
    agent_cfg = next((a for a in _agents if a.get("name") == agent_name), None)
    if agent_cfg is None:
        available = [a["name"] for a in _agents]
        return {
            "error": (
                f"Agent '{agent_name}' not found. "
                f"Available: {', '.join(available)}"
            )
        }

    # Build the task message
    message = task
    if context:
        message = f"{task}\n\nContext from parent agent:\n{context}"

    from anet.core import orchestrator
    from anet.core.context import on_status as _status_var

    # Increment depth for the child chain, reset on return
    token = _spawn_depth.set(depth + 1)
    try:
        result = await orchestrator.run(
            agent=agent_cfg,
            tool_map=_tool_map,
            user_message=message,
            history=[],
            on_status=_status_var.get(),
        )
    except Exception as exc:
        return {"error": f"Sub-agent '{agent_name}' raised: {exc}"}
    finally:
        _spawn_depth.reset(token)

    text = result.get("text", "")
    if not text:
        return {"error": f"Sub-agent '{agent_name}' returned no output."}
    return {"result": text}
