"""
todo_tool — session-scoped task checklist for the agent.

The agent should:
1. Call write() at the START of any multi-step task to lay out the plan.
2. Call update() to mark items in_progress (when starting) and completed (when done).
3. Call read() any time it needs to recall what's left.

The checklist is visible in the user's live spinner — they can see progress in real time.
"""
from __future__ import annotations

from anet.core import todo_state

SCHEMA = {
    "type": "function",
    "function": {
        "name": "todo_tool",
        "description": (
            "Manage a task checklist. "
            "write: set the full plan at the start of a multi-step task. "
            "update: mark an item in_progress or completed as you work. "
            "read: see what's pending. "
            "clear: remove the list when the task is fully done."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["write", "update", "read", "clear"],
                    "description": (
                        "write = replace entire list with new todos. "
                        "update = change one item's status. "
                        "read = return the current list. "
                        "clear = remove all items."
                    ),
                },
                "todos": {
                    "type": "array",
                    "description": "For write: list of todo items. Each needs 'id' and 'content'.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id":      {"type": "string", "description": "Short unique ID, e.g. '1', 'install', 'config-tw'"},
                            "content": {"type": "string", "description": "What needs to be done."},
                            "status":  {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed", "failed"],
                                "description": "Default: pending",
                            },
                        },
                        "required": ["id", "content"],
                    },
                },
                "id": {
                    "type": "string",
                    "description": "For update: the todo item ID to change.",
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "failed"],
                    "description": "For update: the new status.",
                },
            },
            "required": ["action"],
        },
    },
}

_STATUS_ICON = {
    "pending":     "☐",
    "in_progress": "●",
    "completed":   "✓",
    "failed":      "✗",
}


def _format(todos: list[dict]) -> str:
    if not todos:
        return "(empty)"
    lines = []
    for item in todos:
        icon    = _STATUS_ICON.get(item.get("status", "pending"), "?")
        content = item.get("content", "")
        status  = item.get("status", "pending")
        lines.append(f"  {icon} [{status}] {content}")
    done  = sum(1 for t in todos if t.get("status") == "completed")
    total = len(todos)
    lines.append(f"\n  Progress: {done}/{total} completed")
    return "\n".join(lines)


async def run(params: dict) -> dict:
    action = params.get("action", "").strip()

    if action == "write":
        raw = params.get("todos") or []
        if not raw:
            return {"error": "todos list is required for write action"}
        items = [
            {
                "id":      str(t.get("id", i + 1)),
                "content": t.get("content", ""),
                "status":  t.get("status", "pending"),
            }
            for i, t in enumerate(raw)
        ]
        todo_state.set_todos(items)
        return {"result": f"Todo list set ({len(items)} items):\n{_format(items)}"}

    if action == "update":
        todo_id = str(params.get("id", "")).strip()
        status  = params.get("status", "").strip()
        if not todo_id:
            return {"error": "id is required for update action"}
        if status not in ("pending", "in_progress", "completed", "failed"):
            return {"error": f"invalid status '{status}' — use pending/in_progress/completed/failed"}
        found = todo_state.update_todo(todo_id, status)
        if not found:
            return {"error": f"no todo with id '{todo_id}'"}
        todos = todo_state.get_todos()
        return {"result": f"Updated '{todo_id}' → {status}\n{_format(todos)}"}

    if action == "read":
        todos = todo_state.get_todos()
        return {"result": _format(todos)}

    if action == "clear":
        todo_state.clear_todos()
        return {"result": "Todo list cleared."}

    return {"error": f"unknown action '{action}' — use write/update/read/clear"}
