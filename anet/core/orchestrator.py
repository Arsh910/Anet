"""
orchestrator.py — the agentic loop.

Drives an agent through as many model ↔ tool-call iterations as needed
(capped at MAX_ITERATIONS) and returns the final text reply.

Rules:
  - Never references a specific agent name or tool name.
  - All status updates go through the on_status callback; nothing is printed directly.
  - The messages list is the complete shared state; every assistant turn and
    every tool result is appended before the next model call.
"""

import json
import sys
from datetime import datetime
from typing import Callable

from anet.core import agent_runner
from anet.core.context import on_confirm as _confirm_var

MAX_ITERATIONS = 10

# ── Confirmation policy ───────────────────────────────────────────────────────
# Maps tool name → set of actions that require user approval.
# None means ALL actions for that tool require approval.

_CONFIRM_TOOLS: dict[str, set[str] | None] = {
    "shell_tool": None,   # every command needs approval
    "file_tool": {        # only write/destructive actions
        "write_file", "create_folder", "delete_file",
        "copy_file", "move_file", "rename_file",
        "zip_files", "unzip_file",
    },
    "edit_tool": None,    # every edit needs approval (modifies files)
    "open_app": {         # only actions that change desktop state
        "launch_and_type", "type_text", "click_element", "keyboard_shortcut",
    },
}


def _needs_confirm(tool_name: str, arguments: dict) -> bool:
    if tool_name not in _CONFIRM_TOOLS:
        return False
    allowed = _CONFIRM_TOOLS[tool_name]
    if allowed is None:
        return True
    return arguments.get("action", "") in allowed


# ── Action-enum fallback resolver ─────────────────────────────────────────────

def _resolve_action(called_name: str, tool_map: dict) -> tuple[str, str] | None:
    """
    When the model calls a name that isn't a registered tool, check whether
    that name is an action enum value inside any tool's SCHEMA.

    Returns (tool_name, action_name) if a match is found, else None.

    This lets tools expose multiple capabilities under one entry (action-enum
    pattern) while remaining robust to models that call action names directly.

    Generic: works for any current or future tool that uses an action enum.
    """
    for tool_name, info in tool_map.items():
        action_enum: list[str] = (
            info.get("schema", {})
            .get("function", {})
            .get("parameters", {})
            .get("properties", {})
            .get("action", {})
            .get("enum", [])
        )
        if called_name in action_enum:
            return tool_name, called_name
    return None


# ── Message helpers ────────────────────────────────────────────────────────────

def _message_to_dict(message) -> dict:
    """Convert a ChatCompletionMessage object to a plain dict for the messages list.

    Preserves provider-specific extra fields (e.g. Gemini's thought_signature)
    so that thinking models don't reject the looped-back message.
    """
    d: dict = {"role": "assistant", "content": message.content}

    # Preserve extra fields on the top-level message (e.g. Gemini reasoning content)
    if hasattr(message, "model_extra") and message.model_extra:
        d.update(message.model_extra)

    if message.tool_calls:
        tool_calls = []
        for tc in message.tool_calls:
            fn: dict = {
                "name":      tc.function.name,
                "arguments": tc.function.arguments,
            }
            # Preserve extra function-level fields (e.g. Gemini thought_signature)
            if hasattr(tc.function, "model_extra") and tc.function.model_extra:
                fn.update(tc.function.model_extra)

            call: dict = {"id": tc.id, "type": "function", "function": fn}
            # Preserve extra tool-call-level fields if any
            if hasattr(tc, "model_extra") and tc.model_extra:
                call.update(tc.model_extra)

            tool_calls.append(call)
        d["tool_calls"] = tool_calls

    return d


# ── Main loop ──────────────────────────────────────────────────────────────────

async def run(
    agent: dict,
    tool_map: dict,
    user_message: str,
    history: list[dict],
    on_status: Callable[[str], None],
) -> dict:
    """
    Execute the agentic loop and return a result dict:
      {"text": str, "task_id": str | None, "poll_path": str, "result_key": str}

    Parameters
    ----------
    agent       : agent config dict (name, model, system_prompt, tools, …)
    tool_map    : { tool_name: { "run": async_fn, "schema": dict, … } }
    user_message: the current user turn
    history     : flat conversation history (role/content dicts)
    on_status   : callback for status strings (printed by the caller)
    """
    date_ctx = f"Current date and time: {datetime.now().strftime('%A, %B %d, %Y  %H:%M')} (local)."
    messages: list[dict] = [{"role": "system", "content": f"{date_ctx}\n\n{agent['system_prompt']}"}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    last_text: str = ""
    last_tool_result: str = ""
    offloaded: dict | None = None

    for iteration in range(1, MAX_ITERATIONS + 1):
        try:
            response_message = await agent_runner.run(agent, tool_map, messages)
        except Exception as exc:
            on_status(f"[error] model call failed: {exc}")
            text = last_text or f"An error occurred while contacting the model: {exc}"
            return {"text": text, "task_id": None, "poll_path": "", "result_key": ""}

        # ── Plain-text response → done ────────────────────────────────────────
        if not response_message.tool_calls:
            # Fall back to last tool result if the LLM returned no text
            last_text = response_message.content or last_tool_result or ""
            break

        # ── Tool-call response → execute each tool, then loop ─────────────────
        last_text = response_message.content or ""

        messages.append(_message_to_dict(response_message))

        for tool_call in response_message.tool_calls:
            called_name = tool_call.function.name

            try:
                arguments = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                arguments = {}

            # Show tool name + action (if present) so multi-action tools are debuggable
            action_hint = f"[{arguments['action']}]" if "action" in arguments else ""
            on_status(f"using tool: {called_name} {action_hint}".strip() + "...")

            # Track which tool name was actually resolved (for async detection)
            resolved_tool_name: str | None = None

            # ── Primary: exact tool name match ────────────────────────────────
            if called_name in tool_map:
                resolved_tool_name = called_name

                # Ask user before destructive actions
                if _needs_confirm(called_name, arguments):
                    confirm_fn = _confirm_var.get()
                    allowed = await confirm_fn(called_name, arguments.get("action", "run"), arguments)
                    if not allowed:
                        on_status(f"  [skipped] user declined {called_name}")
                        result = {"error": f"User declined action '{arguments.get('action', called_name)}'."}
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(result),
                        })
                        continue

                try:
                    result = await tool_map[called_name]["run"](arguments)
                except Exception as exc:
                    print(f"[orchestrator] tool '{called_name}' raised: {exc}", file=sys.stderr)
                    result = {"error": str(exc)}

            # ── Fallback: model called an action name instead of tool name ─────
            # e.g. model calls type_text(...) instead of open_app({action:"type_text",...})
            elif resolved := _resolve_action(called_name, tool_map):
                tool_name, action_name = resolved
                resolved_tool_name = tool_name
                on_status(f"  → resolved '{called_name}' to '{tool_name}' action")
                merged_args = {"action": action_name, **arguments}

                if _needs_confirm(tool_name, merged_args):
                    confirm_fn = _confirm_var.get()
                    allowed = await confirm_fn(tool_name, action_name, merged_args)
                    if not allowed:
                        on_status(f"  [skipped] user declined {tool_name}:{action_name}")
                        result = {"error": f"User declined action '{action_name}'."}
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(result),
                        })
                        continue

                try:
                    result = await tool_map[tool_name]["run"](merged_args)
                except Exception as exc:
                    print(f"[orchestrator] action '{action_name}' raised: {exc}", file=sys.stderr)
                    result = {"error": str(exc)}

            # ── Unknown — surface cleanly so model can recover ────────────────
            else:
                on_status(f"[warning] unknown tool '{called_name}' — skipping")
                result = {"error": f"Tool '{called_name}' is not available."}

            # ── Async tool detection ──────────────────────────────────────────
            if (
                resolved_tool_name
                and tool_map.get(resolved_tool_name, {}).get("is_async")
                and isinstance(result, dict)
                and result.get("task_id")
            ):
                offloaded = {
                    "task_id":    result["task_id"],
                    "poll_path":  tool_map[resolved_tool_name].get("poll_path", ""),
                    "result_key": tool_map[resolved_tool_name].get("result_key", ""),
                }

            result_str = json.dumps(result)
            last_tool_result = result_str
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_str,
                }
            )
    else:
        on_status(f"[warning] reached iteration cap ({MAX_ITERATIONS})")

    text = last_text or last_tool_result or ""
    return {
        "text":       text,
        "task_id":    offloaded.get("task_id") if offloaded else None,
        "poll_path":  offloaded.get("poll_path", "") if offloaded else "",
        "result_key": offloaded.get("result_key", "") if offloaded else "",
    }
