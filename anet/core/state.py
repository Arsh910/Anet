from __future__ import annotations
from typing import Annotated, TypedDict
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    # Full conversation
    messages: Annotated[list, add_messages]

    # Plan produced by planner — steps with id, depends_on, wait_for_async
    plan: list[dict]

    # Per-step status: {str(step_id): "pending|running|completed|offloaded|failed"}
    step_statuses: dict

    # Step IDs just executed by executor (for checker to validate)
    active_step_ids: list

    # Retry count for current active batch
    attempts: int

    # Accumulated results
    step_results: list[dict]
    last_result: str
    last_check: dict
    last_step_count: int

    # Async task tracking
    offloaded_tasks: dict   # {task_id: {step_id, agent, poll_path, result_key}}
    async_results: dict     # {result_key: result_value} — completed async outputs
    pending_steps: list     # steps blocked on async tasks (stored for resume)

    # Final output
    final_reply: str
