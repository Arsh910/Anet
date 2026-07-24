"""
orchestrator.py — the agentic loop.

Drives an agent through model ↔ tool-call iterations until the model
returns a plain-text reply (natural termination), a cycle is detected
(same tool+args repeated _CYCLE_REPEAT times), or the safety cap fires.

Rules:
  - Never references a specific agent name or tool name.
  - All status updates go through the on_status callback; nothing is printed directly.
  - The messages list is the complete shared state; every assistant turn and
    every tool result is appended before the next model call.
"""

import asyncio
import hashlib
import json
import sys
from datetime import datetime
from typing import Callable

from anet.core import agent_runner
from anet.core.context import on_confirm as _confirm_var, on_output as _output_var, is_cancelled as _is_cancelled

# Safety valve — only fires if the model never stops calling tools.
# Normal tasks end naturally when the model returns a plain-text reply.
# Per-agent cap: agent["max_steps"] overrides this when set.
_SAFETY_CAP = 80

# Cycle detection — if the same (tool, args) signature appears this many
# times in the last _CYCLE_WINDOW calls, the model is stuck in a loop.
# Only WRITE operations are tracked — reads are legitimate verification steps.
_CYCLE_REPEAT = 3
_CYCLE_WINDOW = 10

# Read-only operations excluded from cycle detection (reading the same file
# multiple times to verify a change is normal, not a loop).
_READ_ONLY_OPS: set[str] = {
    "glob_tool", "grep_tool", "web_search", "todo_tool",
}
_READ_ONLY_ACTIONS: set[str] = {
    "read_file", "read_lines", "list_directory", "search_files",
    "get_file_info", "parse_csv", "parse_json",
    "show", "find", "deps", "summary",
    "read", "check_window", "check_process", "check_path",
    "list_windows", "read_window_text", "take_screenshot",
    "list",                                          # conflict_tool
    "diagnostics", "hover", "definition",            # lsp_tool read-only actions
    "references", "symbols", "status",               # lsp_tool read-only actions
}


def _is_read_only(tool_name: str, arguments: dict) -> bool:
    if tool_name in _READ_ONLY_OPS:
        return True
    action = arguments.get("action", "")
    return action in _READ_ONLY_ACTIONS

# ── Confirmation policy ───────────────────────────────────────────────────────
# Maps tool name → set of actions that require user approval.
# None means ALL actions for that tool require approval.

_CONFIRM_TOOLS: dict[str, set[str] | None] = {
    "shell_tool": None,   # every command needs approval
    "code_execution": None,   # runs arbitrary Python — every run needs approval
    "download_file": None,    # writes a file to disk — approve before downloading
    "file_tool": {        # only write/destructive actions
        "write_file", "create_folder", "delete_file",
        "copy_file", "move_file", "rename_file",
        "zip_files", "unzip_file",
    },
    "edit_tool": None,    # every edit needs approval (modifies files)
    "open_app": {         # only actions that change desktop state
        "launch_and_type", "type_text", "click_element", "keyboard_shortcut",
    },
    "memory_tool": {      # destructive memory ops — never wipe/forget silently
        "clear", "delete",
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


# ── Memory injection (Phase A retrieval + Phase B preferences) ────────────────

def _memory_block(agent: dict, task: str) -> str:
    """Build a 'relevant memory' block for an agent's prompt: task-relevant facts
    (keyword + optional semantic) plus standing preferences scoped to this agent.

    Returns '' when nothing is relevant, so memory-free tasks stay clean and small
    models aren't handed noise. This is how memory reaches EVERY agent without
    giving them the memory_tool or dumping the whole store.
    """
    try:
        from anet.AnetTools.memory_tool import search_memories, preference_memories
    except Exception:
        return ""

    agent_name = (agent.get("name") or "").lower()

    # Standing memories (always_inject categories like preferences/identity), already
    # scoped to this agent by the LLM-assigned `applies_to` — no tag parsing here.
    try:
        prefs = preference_memories(agent_name)
    except Exception:
        prefs = []

    # Task-relevant facts. min_score drops weak single-generic-word matches so a
    # task like "write a function" doesn't pull in every memory mentioning code.
    try:
        facts = search_memories(task, max_results=4, min_score=0.34)
    except Exception:
        facts = []

    seen: set[str] = set()
    picked: list[dict] = []
    for m in prefs + facts:          # preferences first
        if m["id"] in seen:
            continue
        seen.add(m["id"])
        picked.append(m)
    if not picked:
        return ""

    def _snip(text: str, limit: int = 240) -> str:
        # Collapse whitespace and cap length — a single huge memory must never
        # dump hundreds of lines into an agent's prompt.
        text = " ".join((text or "").split())
        return text if len(text) <= limit else text[:limit].rstrip() + "…"

    lines = []
    for m in picked:
        tag  = f" ({m['category']})" if m.get("always_inject") and m.get("category") else ""
        proj = f"  [project: {m['project_path']}]" if m.get("project_path") else ""
        lines.append(f"  • {_snip(m['content'])}{tag}{proj}")
    return (
        "RELEVANT MEMORY (apply where it makes sense; ignore if not relevant):\n"
        + "\n".join(lines)
    )


# ── Graceful budget exhaustion ──────────────────────────────────────────────────
# When an agent hits its step cap or gets stuck, don't dump raw trajectory JSON as
# the result. Make ONE tools-disabled call so the model turns what it has into a
# coherent best-effort answer — structured for partial work, honest about gaps.

_FINAL_ANSWER_INSTRUCTION = (
    "You have reached your step budget and can no longer call tools. Do NOT attempt any "
    "tool call. Using ONLY the work already done above, give your best final answer now.\n\n"
    "If the task is essentially complete, state the answer/result directly.\n"
    "If it is partial, structure your reply:\n"
    "- Done: what was actually accomplished (concrete — files, commands, results).\n"
    "- Result: the answer or deliverable produced so far.\n"
    "- Incomplete: what still remains, and the single most useful next step.\n\n"
    "Report ONLY what the work above actually supports. Never invent results, success, "
    "commands, or output that is not present above."
)


def _seal_dangling_tool_calls(msgs: list[dict]) -> list[dict]:
    """Stub any tool_calls in the trailing assistant message that never got a
    result (the stuck case breaks mid-loop), so appending a user message doesn't
    violate provider role-alternation."""
    out = list(msgs)
    last_asst = next(
        (i for i in range(len(out) - 1, -1, -1)
         if out[i].get("role") == "assistant" and out[i].get("tool_calls")),
        None,
    )
    if last_asst is None:
        return out
    answered = {m.get("tool_call_id") for m in out[last_asst + 1:] if m.get("role") == "tool"}
    for tc in out[last_asst]["tool_calls"]:
        tcid = tc.get("id")
        if tcid and tcid not in answered:
            out.append({"role": "tool", "tool_call_id": tcid,
                        "content": json.dumps({"error": "skipped — step limit reached"})})
    return out


async def _final_answer(agent: dict, messages: list[dict], on_status: Callable) -> str:
    """One tools-disabled model call to salvage a coherent answer from an exhausted
    or stuck loop. Empty tool_map → no schemas offered → the model must reply in text."""
    on_status(f"{agent.get('name', 'agent')}: wrapping up...")
    msgs = _seal_dangling_tool_calls(messages) + [
        {"role": "user", "content": _FINAL_ANSWER_INSTRUCTION}
    ]
    try:
        resp = await agent_runner.run(agent, {}, msgs)
        return (resp.content or "").strip()
    except Exception as exc:
        print(f"[orchestrator] final-answer call failed: {exc}", file=sys.stderr)
        return ""


# ── Cheap trajectory prune (no LLM) ──────────────────────────────────────────────
# A bulky tool result is resent to the model on EVERY later step. This replaces
# old, large tool-result content with a short placeholder — no model call (unlike
# diet.py's reflect, which is why this can be always-on). Self-limiting: a no-op
# until a task accumulates more than the protected tail of tool messages.
# ponytail: heuristic tail-protect; if it ever prunes something still needed, the
# placeholder tells the model to re-run the tool, and it can be config-gated later.
_PRUNE_MIN_CHARS = 800      # only bulky results are worth pruning
_PRUNE_PROTECT_TAIL = 6     # keep this many most-recent tool results intact
_PRUNE_PLACEHOLDER = json.dumps(
    {"pruned": "old tool output cleared to save context — re-run the tool if you still need it"}
)


def prune_old_tool_results(messages: list[dict], base_len: int) -> int:
    """Replace bulky OLD tool-result content (past the base, before the protected
    tail) with a placeholder. Returns chars saved. Never touches the base
    (system + history + task) or the most-recent tool results still in use."""
    tool_idxs = [i for i in range(base_len, len(messages))
                 if messages[i].get("role") == "tool"]
    if len(tool_idxs) <= _PRUNE_PROTECT_TAIL:
        return 0
    saved = 0
    for i in tool_idxs[:-_PRUNE_PROTECT_TAIL]:
        content = messages[i].get("content") or ""
        if len(content) <= _PRUNE_MIN_CHARS or content == _PRUNE_PLACEHOLDER:
            continue
        saved += len(content) - len(_PRUNE_PLACEHOLDER)
        messages[i]["content"] = _PRUNE_PLACEHOLDER
    return saved


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
    # Date only — no clock time. This string opens the system prompt, which is
    # the head of the cached prefix, so anything that changes here invalidates
    # the whole cached block (system prompt + tool schemas) for that request.
    # With HH:MM in it the prefix changed every 60 seconds, so in a real
    # multi-turn session most turns started from a cold cache. Day granularity
    # keeps the prefix stable for a whole session while still telling the model
    # what today is; an agent that needs the actual time can call a tool.
    date_ctx = f"Current date: {datetime.now().strftime('%A, %B %d, %Y')} (local)."

    # Inject relevant skills into the system prompt (fast file-based search, no model call)
    try:
        from anet.core import skill_manager as _sm
        _skill_block = _sm.find_relevant_skills(user_message)
    except Exception:
        _skill_block = ""

    # Cached-vs-ephemeral split (Hermes lesson): keep the system prompt BYTE-STABLE
    # across a session so the provider-side cache_control'd system block keeps
    # hitting. Per-turn relevance (skills, memory) depends on the current message,
    # so it rides on the USER message instead of mutating the cached system prompt
    # every turn. (date_ctx is day-granular, so the system prompt is stable per day.)
    _sys_content = f"{date_ctx}\n\n{agent['system_prompt']}"

    # Relevant memory (facts + preferences) reaches agents that don't carry the
    # memory_tool; scoped to relevance so memory-free tasks stay clean.
    _mem_block = _memory_block(agent, user_message)

    _turn_ctx = ""
    if _skill_block:
        _turn_ctx += f"{_skill_block}\n\n"
    if _mem_block:
        _turn_ctx += f"{_mem_block}\n\n"

    messages: list[dict] = [{"role": "system", "content": _sys_content}]
    messages.extend(history)
    messages.append({"role": "user", "content": _turn_ctx + user_message})

    # Trajectory reduction (AgentDiet). Everything up to here — system prompt,
    # history, the task itself — is the fixed base and is never reduced; only
    # the tool results the loop appends below are eligible. Off unless a
    # `diet:` block enables it in anet.config.yaml.
    from anet.core import diet as _diet
    _diet_cfg = _diet.config()
    _diet_base_len = len(messages)

    last_text: str = ""
    last_tool_result: str = ""
    offloaded: dict | None = None
    _cycle_window: list[str] = []   # sliding window of recent call signatures
    _stuck = False
    _cancelled = False

    # ── Skill creation tracking ───────────────────────────────────────────────
    _total_tool_calls = 0
    _had_retry        = False
    _prev_tool: str | None      = None
    _prev_args_hash: str | None = None
    _prev_failed                = False

    agent_name = agent.get("name", "agent")
    cap = int(agent.get("max_steps") or _SAFETY_CAP)

    for iteration in range(1, cap + 1):
        if _is_cancelled():
            _cancelled = True
            break
        step_label = f"step {iteration}" if iteration > 1 else "thinking"
        on_status(f"{agent_name}: {step_label}...")
        try:
            response_message = await agent_runner.run(agent, tool_map, messages)
        except Exception as exc:
            on_status(f"[error] model call failed: {exc}")
            text = last_text or f"An error occurred while contacting the model: {exc}"
            return {"text": text, "task_id": None, "poll_path": "", "result_key": ""}

        # ── Plain-text response → done (natural termination) ──────────────────
        if not response_message.tool_calls:
            last_text = response_message.content or last_tool_result or ""
            break

        # ── Tool-call response → execute each tool, then loop ─────────────────
        last_text = response_message.content or ""

        messages.append(_message_to_dict(response_message))

        for tool_call in response_message.tool_calls:
            if _is_cancelled():
                _cancelled = True
                break
            called_name = tool_call.function.name

            try:
                arguments = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                arguments = {}

            # Inject agent name for tools that need to know who is calling (e.g. for storage paths)
            arguments["_agent_name"] = agent_name

            # ── Skill creation: track total calls and retries ─────────────────
            _total_tool_calls += 1
            _curr_hash = hashlib.md5(json.dumps(arguments, sort_keys=True).encode()).hexdigest()[:8]
            # Same tool with different args → self-correction
            if _prev_tool == called_name and _prev_args_hash != _curr_hash:
                _had_retry = True
            # shell_tool after a failed shell_tool → correction
            if called_name == "shell_tool" and _prev_tool == "shell_tool" and _prev_failed:
                _had_retry = True
            _prev_tool      = called_name
            _prev_args_hash = _curr_hash

            # ── Cycle detection (writes only) ─────────────────────────────────
            # Only track mutating operations — reads are legitimate verification.
            if not _is_read_only(called_name, arguments):
                sig = f"{called_name}:{hashlib.md5(json.dumps(arguments, sort_keys=True).encode()).hexdigest()[:8]}"
                _cycle_window.append(sig)
                if len(_cycle_window) > _CYCLE_WINDOW:
                    _cycle_window.pop(0)
                if _cycle_window.count(sig) >= _CYCLE_REPEAT:
                    on_status(f"[warning] cycle detected — '{called_name}' write repeating with same args, stopping")
                    _stuck = True
                    break

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

            # Emit file diffs to the terminal so the user can see what changed.
            # Only fires for edit_tool when the result contains a unified diff.
            effective = resolved_tool_name or called_name
            if effective == "edit_tool" and isinstance(result, dict):
                res_text = result.get("result", "")
                if res_text and ("---" in res_text and "+++" in res_text):
                    _output_var.get()(res_text)

            _prev_failed = isinstance(result, dict) and "error" in result
            result_str = json.dumps(result)
            last_tool_result = result_str
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_str,
                }
            )

        if _stuck or _cancelled:
            break

        # ── Trajectory reduction ──────────────────────────────────────────
        # Cheap no-LLM pass first: clear stale bulky tool outputs so they stop
        # being resent every step. Always on, self-limiting, protects the tail.
        prune_old_tool_results(messages, _diet_base_len)
        # Then the LLM reflect (diet) rewrites one EARLIER step for a smarter
        # takeaway. Best-effort: no-op if disabled or if anything goes wrong.
        if _diet_cfg["enabled"]:
            await _diet.maybe_reduce(
                messages, _diet_base_len, user_message, on_status, _diet_cfg
            )
    else:
        # Safety cap hit — model never stopped calling tools. Salvage a coherent
        # answer instead of dumping raw trajectory.
        on_status(f"[warning] safety cap reached ({cap} steps) — wrapping up")
        final = await _final_answer(agent, messages, on_status)
        text = final or (
            f"[INCOMPLETE — safety cap of {cap} steps reached]\n\n"
            + (last_tool_result or last_text or "")
        )
        return {
            "text":       text,
            "task_id":    offloaded.get("task_id") if offloaded else None,
            "poll_path":  offloaded.get("poll_path", "") if offloaded else "",
            "result_key": offloaded.get("result_key", "") if offloaded else "",
        }

    if _stuck:
        final = await _final_answer(agent, messages, on_status)
        text = final or (
            f"[INCOMPLETE — stuck in loop, same tool called {_CYCLE_REPEAT}× with identical args]\n\n"
            + (last_tool_result or last_text or "")
        )
        return {
            "text":       text,
            "task_id":    offloaded.get("task_id") if offloaded else None,
            "poll_path":  offloaded.get("poll_path", "") if offloaded else "",
            "result_key": offloaded.get("result_key", "") if offloaded else "",
        }

    if _cancelled:
        return {
            "text":       last_text or last_tool_result or "[stopped by user]",
            "task_id":    offloaded.get("task_id") if offloaded else None,
            "poll_path":  offloaded.get("poll_path", "") if offloaded else "",
            "result_key": offloaded.get("result_key", "") if offloaded else "",
        }

    # ── Skill creation trigger (background, non-blocking) ────────────────────
    # The counter is only a cheap PRE-FILTER to avoid reviewing trivial tasks.
    # The real decision — did the task succeed, and is there a durable lesson —
    # is made by the model inside create_skill_background, which can decline.
    # _prev_failed (the last tool result) is passed as the outcome signal so a
    # task that ended in failure never produces a skill.
    try:
        from anet.core import skill_manager as _sm
        _threshold = _sm._creation_threshold()
        if _total_tool_calls >= _threshold:
            _history_text = "\n".join(
                f"{m['role'].upper()}: {(m.get('content') or '')[:300]}"
                for m in messages
                if m.get("content") and m["role"] in ("user", "assistant", "tool")
            )
            asyncio.create_task(
                _sm.create_skill_background(
                    _history_text,
                    agent_name,
                    outcome_failed=_prev_failed,
                    had_retry=_had_retry,
                )
            )
    except Exception:
        pass

    text = last_text or last_tool_result or ""
    return {
        "text":       text,
        "task_id":    offloaded.get("task_id") if offloaded else None,
        "poll_path":  offloaded.get("poll_path", "") if offloaded else "",
        "result_key": offloaded.get("result_key", "") if offloaded else "",
    }
