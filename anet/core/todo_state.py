"""
todo_state.py — shared in-process todo list.

Imported by todo_tool (writes) and main.py _LiveStatus (reads).
Thread-safe; no persistence needed — list is per-session in memory.
"""
from __future__ import annotations
import threading

_lock  = threading.Lock()
_todos: list[dict] = []   # [{"id": str, "content": str, "status": str}]


def set_todos(todos: list[dict]) -> None:
    with _lock:
        _todos.clear()
        _todos.extend(todos)


def update_todo(todo_id: str, status: str) -> bool:
    """Return True if the item was found and updated."""
    with _lock:
        for item in _todos:
            if item["id"] == todo_id:
                item["status"] = status
                return True
    return False


def get_todos() -> list[dict]:
    with _lock:
        return list(_todos)


def clear_todos() -> None:
    with _lock:
        _todos.clear()
