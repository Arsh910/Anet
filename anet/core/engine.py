"""
engine.py — Pure-Python replacement for graph_builder.py + LangGraph StateGraph.

Same planner → executor → checker → synthesizer pipeline, implemented as
async methods on the Engine class. No LangGraph, no LangChain.
Messages are plain dicts {"role": "user"|"assistant", "content": str}.
"""

import asyncio
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from openai import AsyncOpenAI

from anet.core import orchestrator
from anet.core.context import on_status as _status_var, on_token as _token_var, is_cancelled as _is_cancelled

_MAX_RETRIES    = 3
_ASSISTANT_NAME = os.getenv("ASSISTANT_NAME", "Anet")

# ── Soul (loaded once at import, injected into manager prompts) ───────────────

def _load_soul_once() -> str:
    try:
        from anet.core.config_loader import load_soul
        return load_soul()
    except Exception:
        return ""

_SOUL = _load_soul_once()

# ── User profile (loaded once at import, re-read before incremental saves) ────

def _load_user_profile() -> str:
    try:
        from anet.core.paths import user_profile_path
        _USER_PROFILE_PATH = user_profile_path()
        if not _USER_PROFILE_PATH.exists():
            return ""
        content = _USER_PROFILE_PATH.read_text(encoding="utf-8").strip()
        substantive = [
            ln for ln in content.splitlines()
            if ln.strip() and not ln.startswith("#") and not ln.startswith("<!--")
        ]
        return content if substantive else ""
    except Exception:
        return ""

_USER_PROFILE = _load_user_profile()

# ── Manager model config ──────────────────────────────────────────────────────

_MANAGER_PROVIDERS = {
    "google":     ("https://generativelanguage.googleapis.com/v1beta/openai/", "GOOGLE_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1",                             "OPENROUTER_API_KEY"),
    "openai":     ("https://api.openai.com/v1",                                "OPENAI_API_KEY"),
}

def _manager_cfg() -> tuple[str, str]:
    try:
        from anet.core.config_loader import manager_config
        cfg = manager_config()
        return (cfg.get("model") or "gemini-2.5-pro"), (cfg.get("provider") or "google")
    except Exception:
        return "gemini-2.5-pro", "google"


def _manager_client() -> tuple[AsyncOpenAI, str]:
    model, provider = _manager_cfg()
    if provider in ("vertex_google", "vertex_claude"):
        from anet.core.agent_runner import build_vertex_client
        return build_vertex_client(), model
    base_url, env_key = _MANAGER_PROVIDERS.get(provider, _MANAGER_PROVIDERS["google"])
    api_key = os.getenv(env_key)
    if not api_key:
        raise RuntimeError(f"{env_key} not set (needed for manager provider='{provider}')")
    return AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=120), model


# ── Helpers ───────────────────────────────────────────────────────────────────

def _last_user_msg(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return m.get("content", "")
    return ""


def _extract_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{[\s\S]+\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"No JSON found in: {text!r}")


def _memory_context(user_msg: str) -> str:
    try:
        from anet.AnetTools.memory_tool import search_memories
        results = search_memories(user_msg, max_results=5)
        if not results:
            return ""
        lines = "\n".join(
            f"  [{m['id']}] {m['content']}"
            + (f"  (project: {m['project_path']})" if m.get("project_path") else "")
            for m in results
        )
        return (
            "RELEVANT MEMORIES FROM PAST SESSIONS (use these to inform your plan):\n"
            + lines
        )
    except Exception:
        return ""


def _dedup_reply(text: str) -> str:
    n = len(text)
    if n < 100:
        return text
    start = text[:80]
    idx = text.find(start, n // 3)
    if idx > 0:
        return text[:idx].rstrip()
    return text


def _keyword_fallback(user_msg: str, agents: list[dict]) -> dict:
    plannable = [a for a in agents if a["name"] != "checker_agent"]
    lower = user_msg.lower()
    best, best_score = plannable[0], 0
    for a in plannable:
        score = sum(
            1 for tt in a.get("task_types", [])
            for w in tt.lower().split() if w in lower
        )
        if score > best_score:
            best_score, best = score, a
    print(f"[engine] keyword fallback → '{best['name']}'", file=sys.stderr)
    return {"type": "plan", "steps": [{
        "id": 1, "agent": best["name"], "task": user_msg,
        "success_criteria": "Task completed without errors.", "check": None,
        "depends_on": [], "wait_for_async": False,
    }]}


def _has_ready_steps(plan: list[dict], step_statuses: dict) -> bool:
    for step in plan:
        sid = str(step.get("id", step.get("agent", "")))
        if step_statuses.get(sid) in ("completed", "offloaded", "failed"):
            continue
        deps       = step.get("depends_on", [])
        wait_async = step.get("wait_for_async", False)
        blocked = any(
            step_statuses.get(str(d), "pending") == "offloaded" and wait_async
            for d in deps
        )
        pending_deps = any(
            step_statuses.get(str(d), "pending") in ("pending", "running")
            for d in deps
        )
        if not blocked and not pending_deps:
            return True
    return False


# ── Explicit OS check ─────────────────────────────────────────────────────────

async def _run_explicit_check(checker_tool: dict, check: dict, notify) -> tuple[str, str]:
    action = check.get("action", "")
    params = ", ".join(f"{k}={v!r}" for k, v in check.items() if k != "action")
    notify(f"checker: {action}({params})...")

    r = await checker_tool["run"](check)
    if "error" in r:
        return "unknown", r["error"]

    if action == "check_window":
        return (
            ("success", f"Window '{r.get('title')}' is open.")
            if r.get("exists")
            else ("failure", f"Window '{check.get('title')}' not found on screen.")
        )
    if action == "check_process":
        return (
            ("success", f"Process '{check.get('process_name')}' is running (PID {r.get('pid')}).")
            if r.get("running")
            else ("failure", f"Process '{check.get('process_name')}' is not running.")
        )
    if action == "check_path":
        return (
            ("success", f"Path exists ({r.get('type')}).")
            if r.get("exists")
            else ("failure", f"Path '{check.get('path')}' does not exist.")
        )
    return "unknown", f"Unknown check action '{action}'"


# ── Prompts ───────────────────────────────────────────────────────────────────

def _plan_system_prompt(agents: list[dict], has_direct_tools: bool = False, memory_ctx: str = "") -> str:
    plannable = [a for a in agents if a["name"] != "checker_agent"]
    agent_lines = "\n".join(
        f"  - {a['name']}: {', '.join(a['task_types'])}" for a in plannable
    )
    agent_names = [a["name"] for a in plannable]

    now = datetime.now().strftime("%A, %B %d, %Y  %H:%M")
    memory_section  = f"\n\n{memory_ctx}" if memory_ctx else ""
    soul_section    = f"\n\n{_SOUL}" if _SOUL else ""
    profile_section = f"\n\n## What I know about you\n{_USER_PROFILE}" if _USER_PROFILE else ""
    return f"""You are {_ASSISTANT_NAME}, an AI assistant. Analyse the user request and decide how to fulfil it.
Current date and time: {now} (local).
If asked about your identity, name, or who you are, answer as {_ASSISTANT_NAME} — never mention the underlying model or "Google".{soul_section}{profile_section}{memory_section}


AVAILABLE AGENTS:
{agent_lines}

{("You also have direct tools you can call yourself for simple one-step tasks — use them instead of delegating to an agent when appropriate." if has_direct_tools else "")}
OUTPUT — ONLY a JSON object, no prose (unless calling a direct tool):

Simple (greetings, trivial facts):
  {{"type": "simple", "reply": "<answer>"}}

One or more agents needed:
  {{"type": "plan", "steps": [
    {{"id": 1, "agent": "<name>", "task": "<instruction>",
      "success_criteria": "<verifiable outcome>",
      "check": {{"action": "check_window"|"check_process"|"check_path", "<param>": "<value>"}} | null,
      "depends_on": [],
      "wait_for_async": false
    }}
  ]}}

RULES:
- You MUST ALWAYS respond with a valid JSON object. Never return plain prose. Even for follow-up
  questions like "explain it" or "what does it say", output {{"type":"simple","reply":"..."}} — never raw text.
- agent must be one of: {agent_names}
- Each step MUST have a unique integer "id" field starting from 1.
- depends_on: [] means run immediately in parallel with other free steps. Steps sharing no deps run concurrently.
- depends_on: [1, 2] means wait for steps with id 1 and 2 to complete first.
- wait_for_async: true means block until the dependency's background async task fully completes (e.g. 3D render).
- check: use the SPECIFIC name/title being acted on, not generic terms.
  Opening "ikarus" folder  → check_window title="ikarus"
  Opening Notepad          → check_window title="Notepad"
  Research / file / data tasks → null
- NEVER use check_path for file search or file read tasks — the exact path is unknown until the
  agent runs. Use check: null and let the LLM classifier verify the result.
- System auto-injects each step's output into the next step.
- TASK SPECIFICATION — when writing a task for an agent, include ALL context it needs to act
  without asking follow-up questions:
  * Always include the FULL absolute path of any project/folder/file referenced.
  * For "fix it" / "not working" / "blank page" / "error" requests: scan recent messages for
    the project path, include it verbatim, and describe the symptom (e.g. "blank page on
    http://localhost:5173" or "npm run dev fails with error X"). Never write a vague task like
    "fix the issue" — say exactly: fix WHAT, at WHAT PATH, with WHAT observed symptom.
  * If the user says "it" / "the app" / "that" without specifying — infer the target from the
    most recent project/file path mentioned in conversation and include it explicitly.
- CONTEXT: Use type:"simple" ONLY for pure information requests where no action is needed:
  greetings, factual questions, "explain it", "what does it say", "summarise what you found" —
  but ONLY when the answer already exists in conversation context and no agent work is required.
  NEVER use type:"simple" when the user wants you to DO or BUILD something.
- CORRECTION RULE (HIGHEST PRIORITY — overrides everything else): If the user indicates the
  previous result was wrong, incomplete, or not what they wanted — ANY phrasing like "you didn't
  do X", "you didn't make X", "that's not what I asked", "you only did Y not Z", "you missed X",
  "do it properly", "complete it", "try again", "still not working", "I asked for X" — you MUST
  generate a new plan and dispatch to the appropriate agent. NEVER respond with type:"simple" for
  corrections. The user is demanding action, not an explanation.

ROUTING — match each sub-task to the most specific agent (works for ANY agent, including ones added later):
- Each agent advertises what it handles via its task_types (listed above). Break the request into
  sub-tasks and route EACH sub-task to the agent whose task_types most specifically match it.
- A general-purpose agent (code_agent) must NOT absorb a sub-task that clearly matches a more
  specialized agent's task_types. Delivering / sending / notifying / posting via an external service —
  or any action another listed agent advertises — MUST be its OWN separate step routed to that agent,
  with depends_on set to the step that produced the content.
- NEVER have one agent improvise another agent's job, write code to do that job, or ask the user for
  credentials that a specialized agent already holds.

CRITICAL — agent routing for code vs desktop:
- code_agent handles ALL programming and command-line work: writing/editing code, running npm/pip/
  python/git commands, scaffolding projects, creating files with content, running tests.
  It runs shell commands internally via shell_tool — it NEVER needs a terminal window.
- computer_agent handles ONLY GUI desktop actions: opening a named app (Notepad, Chrome, VS Code),
  clicking buttons, typing into an open window, taking screenshots.
- NEVER route to computer_agent to run shell/terminal commands. NEVER ask computer_agent to open
  "Terminal" or "PowerShell" to execute npm/pip/python/git. Use code_agent for all of that.

CRITICAL — agent routing for file management vs code editing:
- file_agent handles ONLY raw file-system operations on NON-CODE files: copying, moving, deleting,
  renaming, zipping/unzipping, listing directories, reading plain text/data files when no code
  modification is needed. file_agent does NOT have edit_tool — it CANNOT make surgical code edits.
- code_agent handles ALL tasks that involve source code or project files — even if the task is
  described as "write to a file", "create a file", "update a file", or "fix something in a file".
  Any task inside a software project (React, Python, Node, HTML/CSS/JS/TS, config files, etc.)
  must go to code_agent — never file_agent.
- When in doubt between file_agent and code_agent → ALWAYS choose code_agent.
- BUT "code_agent does everything" covers programming/file work ONLY — it does NOT mean code_agent
  should perform an action another agent advertises (e.g. sending a message/notification via an
  external service). Split those into a separate step for the matching agent, even inside a coding pipeline.
- NEVER route to file_agent: editing UI, fixing layouts, adding components, modifying source code,
  changing HTML/CSS/JS/Python/TypeScript files, or ANY task inside a software project folder.

APPROVAL GATE — 3D rendering:
- If the user asks for a 3D model WITHOUT providing an image path, plan ONLY the
  image search + download step (research_agent). Stop there. Do NOT include
  viga_agent in the same plan. The manager will show the downloaded image to the
  user and ask for approval before the next turn.
- Only plan viga_agent when: (a) the user provides an explicit local image path, OR
  (b) the user has already approved an image in a previous message ("yes", "use it",
  "looks good", "proceed", "go ahead", "make the model", etc.).
- NEVER chain research_agent image download and viga_agent start in a single plan.

PATH INJECTION — when planning viga_agent:
- Scan ALL assistant messages in conversation history for lines starting with "Downloaded:".
- Extract the file path from the most recent such line.
- Include that path verbatim in the task instruction for viga_agent, e.g.:
  "task": "Generate a 3D model of a water bottle. Use target_image=C:\\...\\bottle.jpg"
- This way viga_agent has the path in its task and does NOT need to ask the user for it.

IMAGE DOWNLOAD TASKS — how to write the task for research_agent:
- ALWAYS tell research_agent to find a DIRECT image URL (ending in .jpg, .png, or .webp).
  Example task: "Find a direct downloadable .jpg image of <topic>. Search Wikimedia Commons first
  (site:upload.wikimedia.org), then try filetype:jpg queries. Download it once a direct URL is found."
- success_criteria: "Result contains 'Downloaded:' line with an absolute file path"
- If the image download succeeds AND the user wants it sent to Telegram, chain a tele_agent step:
  depends_on the research step, task: "Send the downloaded image to Telegram. The file path is
  in the previous step result — look for the 'Downloaded: <path>' line and pass that path."

TELEGRAM TASKS — success criteria:
- Whenever you plan a tele_agent step, ALWAYS set success_criteria to:
  "Result must contain 'message_id' confirming Telegram delivery"
- This ensures the checker verifies a real delivery ID, not just the agent's claim.

EXAMPLES:
"open notepad"
→ {{"type":"plan","steps":[{{"id":1,"agent":"computer_agent","task":"Open Notepad","success_criteria":"Notepad is open","check":{{"action":"check_window","title":"Notepad"}},"depends_on":[],"wait_for_async":false}}]}}

"find latest AI news and type in notepad"
→ {{"type":"plan","steps":[
  {{"id":1,"agent":"research_agent","task":"Find latest AI news headlines and summaries","success_criteria":"3+ news items returned","check":null,"depends_on":[],"wait_for_async":false}},
  {{"id":2,"agent":"computer_agent","task":"Open Notepad and type the AI news","success_criteria":"Notepad open with news typed","check":{{"action":"check_window","title":"Notepad"}},"depends_on":[1],"wait_for_async":false}}
]}}

"create a react vite app in c:\\projects\\myapp"
→ {{"type":"plan","steps":[
  {{"id":1,"agent":"code_agent","task":"Scaffold a new Vite React app in c:\\projects\\myapp using npx create-vite@latest, then npm install","success_criteria":"package.json exists and node_modules installed","check":null,"depends_on":[],"wait_for_async":false}}
]}}

"you didn't make the travel website, you just made the vite project"
→ {{"type":"plan","steps":[{{"id":1,"agent":"code_agent","task":"Build the full travel website UI inside the existing Vite project — create React components (Header, Hero, Destinations, Footer), add routing with react-router-dom, apply Tailwind CSS styling so it looks like a real travel website","success_criteria":"Travel website with multiple pages and styled components is visible when running npm run dev","check":null,"depends_on":[],"wait_for_async":false}}]}}

"that's not what I asked, do it again"
→ Replan with the correct agent based on what the original request was.

OUTPUT ONLY JSON."""


def _synthesis_system_prompt(interim: bool = False) -> str:
    soul_prefix = f"{_SOUL}\n\n" if _SOUL else ""
    base = (
        f"{soul_prefix}You are {_ASSISTANT_NAME}, an AI assistant. One or more agents just completed the user's request. "
        "Your job is to present the results to the user.\n\n"
        "Rules:\n"
        "- If the agent returned information, facts, lists, quotes, code, or any content the user asked for: "
        "present it fully and clearly. Do NOT summarise it away — include the actual content.\n"
        "- If the agent performed an action (opened an app, downloaded a file, wrote a file, ran code): "
        "give a brief 1-2 sentence confirmation.\n"
        "- Never say 'I found some results' or 'Here are the results' and then skip the results. "
        "Always include the actual content.\n"
        "- If a step failed after retries, mention it briefly.\n"
        "- IMPORTANT: if any agent output contains a line starting with 'Downloaded:', "
        "copy that exact line verbatim into your reply.\n"
        "- If any agent output starts with '[INCOMPLETE', the agent ran out of steps mid-task. "
        "Tell the user clearly what was completed so far and that they can ask you to continue.\n"
        "- NEVER invent capability limitations. If a task failed, say WHAT failed (e.g. "
        "'research_agent could not find a direct downloadable image URL') — do NOT claim "
        "the system 'cannot' do something (like 'cannot send images to Telegram') unless "
        "the agent explicitly returned that error. Stick to what the agent results actually say.\n\n"
        "TRUTHFULNESS — the agent outputs below are your ONLY source of truth:\n"
        "- Report ONLY what the agent outputs actually contain. The 'Original request' tells you "
        "what was ASKED, NOT what was done — never treat the request as evidence of completion.\n"
        "- If the request asked for an action that has NO matching agent output (a file written, a "
        "test run, code executed, a message sent), you MUST NOT claim it happened. Say plainly that "
        "that part was not completed, and which agent would have done it.\n"
        "- NEVER fabricate file paths, code, test results, numbers/statistics, or delivery "
        "confirmations (e.g. 'sent to Telegram', 'tests passed') that are not present in the agent "
        "outputs below.\n"
        "- If only some steps ran, report exactly those and clearly list which parts of the request "
        "were NOT done."
    )
    if interim:
        base += (
            "\n- NOTE: Some tasks are running in the background. "
            "Tell the user what started and that they'll be notified when complete."
        )
    return base


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class EngineResult:
    reply: str
    step_results: list[dict] = field(default_factory=list)


# ── Engine ────────────────────────────────────────────────────────────────────

class Engine:
    """
    Drop-in replacement for LangGraph's compiled StateGraph.

    API surface used by main.py:
      result = await engine.run_turn(thread_id, store, user_input)
      engine.get_offloaded_tasks(thread_id)   — for async notifier
      engine.get_pending_steps(thread_id)     — for async notifier
      engine.set_async_result(thread_id, key, value)  — for async notifier
    """

    def __init__(
        self,
        agents:        list[dict],
        tools:         dict,
        manager_tools: dict | None = None,
    ) -> None:
        self._agents       = agents
        self._agent_map    = {a["name"]: a for a in agents}
        self._tools        = tools
        self._manager_tools = manager_tools or {}
        self._result_cache: dict[tuple[str, str], str] = {}
        # Per-thread async task state (survives across turns in same session)
        self._async_state: dict[str, dict] = {}
        # Incremental memory: turn counters per thread
        self._turn_counts: dict[str, int] = {}

    # ── Async state accessors (used by _async_notifier in main.py) ────────────

    def get_offloaded_tasks(self, thread_id: str) -> dict:
        return self._async_state.get(thread_id, {}).get("offloaded_tasks", {})

    def get_pending_steps(self, thread_id: str) -> list:
        return self._async_state.get(thread_id, {}).get("pending_steps", [])

    def get_async_results(self, thread_id: str) -> dict:
        return self._async_state.get(thread_id, {}).get("async_results", {})

    def set_async_result(self, thread_id: str, result_key: str, value: str) -> None:
        state = self._async_state.setdefault(thread_id, {
            "offloaded_tasks": {}, "async_results": {}, "pending_steps": [],
        })
        state["async_results"][result_key] = value

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _notify(self, msg: str) -> None:
        _status_var.get()(msg)

    def _cache_key(self, agent_name: str, task: str) -> tuple[str, str]:
        return (agent_name, hashlib.sha256(task.encode()).hexdigest()[:16])

    # ── Main entry point ──────────────────────────────────────────────────────

    async def run_turn(self, thread_id: str, store, user_input: str) -> "EngineResult":
        is_async_resume = user_input.startswith("__anet_async_resume__")

        messages = await store.load(thread_id)

        # For planner context, include the new user message (unless internal resume)
        messages_for_planner = messages if is_async_resume else (
            messages + [{"role": "user", "content": user_input}]
        )

        # Get (or init) per-thread async state
        async_state = self._async_state.setdefault(thread_id, {
            "offloaded_tasks": {}, "async_results": {}, "pending_steps": [],
        })
        pending_steps   = list(async_state.get("pending_steps", []))
        offloaded_tasks = dict(async_state.get("offloaded_tasks", {}))
        async_results   = dict(async_state.get("async_results", {}))

        # ── Plan ──────────────────────────────────────────────────────────────
        plan: list[dict]       = []
        step_statuses: dict    = {}

        if is_async_resume and pending_steps:
            self._notify("manager: resuming blocked steps after async completion...")
            plan = pending_steps
            step_statuses = {str(s.get("id", s.get("agent", ""))): "pending" for s in plan}
            async_state["pending_steps"] = []
        else:
            plan_result = await self._plan(messages_for_planner)
            ptype = plan_result.get("type")

            if ptype in ("simple", "tool_call"):
                reply = plan_result.get("reply", "")
                await self._persist(store, thread_id, user_input, reply, is_async_resume)
                return EngineResult(reply=reply)

            plan = plan_result.get("steps", [])
            if not plan:
                reply = "I couldn't determine how to handle that."
                await self._persist(store, thread_id, user_input, reply, is_async_resume)
                return EngineResult(reply=reply)

            step_statuses = {str(s.get("id", i + 1)): "pending" for i, s in enumerate(plan)}

        # ── Execute → Check loop ──────────────────────────────────────────────
        step_results:       list[dict] = []
        last_result:        str        = ""
        last_check:         dict       = {}
        attempts:           int        = 0
        pending_steps_after: list      = []

        while True:
            if _is_cancelled():
                break
            exec_r = await self._execute(
                plan, step_statuses, step_results,
                offloaded_tasks, async_results,
                attempts, last_check, messages_for_planner,
            )

            step_statuses   = exec_r["step_statuses"]
            offloaded_tasks.update(exec_r.get("new_offloaded", {}))
            step_results         = exec_r["step_results"]
            last_result          = exec_r["last_result"]
            active_step_ids      = exec_r["active_step_ids"]
            last_step_count      = exec_r["last_step_count"]
            pending_steps_after  = exec_r.get("pending_steps", [])

            if last_step_count == 0:
                break  # nothing ran (all offloaded or no ready steps)

            check_r = await self._check(
                plan, active_step_ids, last_step_count,
                attempts, last_result, step_results, step_statuses,
            )

            if check_r:
                step_statuses = check_r.get("step_statuses", step_statuses)
                step_results  = check_r.get("step_results", step_results)
                last_check    = check_r.get("last_check", {})
                if check_r.get("retry"):
                    attempts = check_r["attempts"]
                    continue
                attempts = 0

            if not _has_ready_steps(plan, step_statuses):
                break

        # ── Stopped by user (ESC) — skip synthesis/persist, return cleanly ──────
        if _is_cancelled():
            return EngineResult(reply="", step_results=step_results)

        # ── Save async state ──────────────────────────────────────────────────
        async_state["offloaded_tasks"] = offloaded_tasks
        async_state["pending_steps"]   = pending_steps_after

        # ── Synthesize ────────────────────────────────────────────────────────
        user_msg_synth = user_input if not is_async_resume else (
            messages[-1]["content"] if messages else ""
        )
        reply = await self._synthesize(step_results, user_msg_synth, bool(pending_steps_after))

        # ── Persist ───────────────────────────────────────────────────────────
        await self._persist(store, thread_id, user_input, reply, is_async_resume)

        # ── Incremental memory (fire-and-forget every N turns) ────────────────
        if not is_async_resume:
            n = self._turn_counts.get(thread_id, 0) + 1
            self._turn_counts[thread_id] = n
            try:
                from anet.core.config_loader import load as _cfg
                interval = int(_cfg().get("memory", {}).get("incremental_interval", 5))
            except Exception:
                interval = 5
            if interval > 0 and n % interval == 0:
                from anet.core.memory_agent import run_memory_review
                messages_snap = await store.load(thread_id)
                asyncio.create_task(run_memory_review(messages_snap, thread_id))

        return EngineResult(reply=reply, step_results=step_results)

    # ── Planner ───────────────────────────────────────────────────────────────

    async def _plan(self, messages: list[dict]) -> dict:
        self._notify("manager: planning...")
        client, manager_model = _manager_client()

        user_msg = _last_user_msg(messages)
        mem_ctx  = await asyncio.to_thread(_memory_context, user_msg)

        api_msgs = messages[-8:]
        msgs = [
            {"role": "system", "content": _plan_system_prompt(
                self._agents, bool(self._manager_tools), memory_ctx=mem_ctx
            )},
            *api_msgs,
        ]

        tools_param = (
            [t["schema"] for t in self._manager_tools.values() if t.get("schema")]
            if self._manager_tools else None
        )

        try:
            kwargs: dict = {"model": manager_model, "messages": msgs, "temperature": 0}
            if tools_param:
                kwargs["tools"] = tools_param

            resp = await client.chat.completions.create(**kwargs)
            msg  = resp.choices[0].message

            # Manager direct tool call
            if getattr(msg, "tool_calls", None):
                results = []
                for tc in msg.tool_calls:
                    tool_name = tc.function.name
                    try:
                        arguments = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    if tool_name in self._manager_tools:
                        self._notify(f"manager: calling {tool_name}...")
                        r = await self._manager_tools[tool_name]["run"](arguments)
                        results.append(f"[{tool_name}]: {json.dumps(r)}")
                    else:
                        results.append(f"[{tool_name}]: tool not found")
                return {"type": "tool_call", "reply": "\n".join(results)}

            raw = (msg.content or "").strip()
            try:
                plan = _extract_json(raw)
            except ValueError:
                if raw:
                    self._notify("manager: prose reply — treating as simple answer")
                    plan = {"type": "simple", "reply": raw}
                else:
                    raise ValueError("empty response")

        except Exception as exc:
            self._notify(f"manager: planning failed ({exc}) — keyword fallback")
            plan = _keyword_fallback(user_msg, self._agents)

        if plan.get("type") == "simple":
            return {"type": "simple", "reply": _dedup_reply(
                plan.get("reply") or "I'm not sure how to help with that."
            )}

        steps = plan.get("steps") or []
        for i, s in enumerate(steps):
            if "id" not in s:
                s["id"] = i + 1
        return {"type": "plan", "steps": steps}

    # ── Executor ──────────────────────────────────────────────────────────────

    async def _run_one(
        self, step: dict, full_task: str, attempts: int = 0, history=None
    ) -> tuple[dict, str, dict | None]:
        agent_name = step.get("agent", "")
        agent      = self._agent_map.get(agent_name)
        if agent is None:
            return step, f"Error: unknown agent '{agent_name}'", None

        key = self._cache_key(agent_name, full_task)
        if key in self._result_cache and attempts == 0:
            self._notify(f"  [cache] reusing result for {agent_name}")
            return step, self._result_cache[key], None

        raw = await orchestrator.run(
            agent=agent,
            tool_map=self._tools,
            user_message=full_task,
            history=history or [],
            on_status=self._notify,
        )
        text = raw["text"]
        offload = None
        if raw.get("task_id"):
            offload = {
                "task_id":    raw["task_id"],
                "poll_path":  raw.get("poll_path", ""),
                "result_key": raw.get("result_key", ""),
            }
        self._result_cache[key] = text
        return step, text, offload

    async def _execute(
        self,
        plan:            list[dict],
        step_statuses:   dict,
        step_results:    list[dict],
        offloaded_tasks: dict,
        async_results:   dict,
        attempts:        int,
        last_check:      dict,
        messages:        list[dict],
    ) -> dict:
        if not plan:
            return {
                "last_result": "", "last_step_count": 0, "active_step_ids": [],
                "step_statuses": step_statuses, "step_results": step_results,
                "new_offloaded": {}, "pending_steps": [],
            }

        # Build context from completed + async results
        parts = []
        for r in step_results:
            if r.get("status") in ("success", "failure", "partial", "offloaded"):
                parts.append(f"=== Output from {r['agent']} ===\n{r['result']}")
        for k, v in async_results.items():
            parts.append(f"=== Async result [{k}] ===\n{v}")
        context_block = ("\n\n" + "\n\n".join(parts)) if parts else ""

        # File path injection (avoid agents having to parse context for download paths)
        downloaded_paths: list[str] = []
        for r in step_results:
            for line in (r.get("result") or "").splitlines():
                if line.strip().startswith("Downloaded:"):
                    downloaded_paths.append(line.strip())
        file_injection = (
            "\n\nFiles downloaded in previous steps — use these EXACT paths:\n"
            + "\n".join(downloaded_paths)
        ) if downloaded_paths else ""

        adj_block = ""
        if attempts > 0:
            adj = last_check.get("adjustment", "")
            if adj:
                adj_block = f"\n\nAdjustment: {adj}"

        # Assign IDs if missing
        for i, s in enumerate(plan):
            if "id" not in s:
                s["id"] = i + 1

        # DAG: find ready steps
        ready: list[dict] = []
        blocked: list[dict] = []
        for step in plan:
            sid = str(step["id"])
            if step_statuses.get(sid) in ("completed", "offloaded", "failed"):
                continue

            deps       = step.get("depends_on", [])
            wait_async = step.get("wait_for_async", False)
            is_ready   = True
            is_blocked = False

            for dep_id in deps:
                dep_status = step_statuses.get(str(dep_id), "pending")
                if dep_status in ("pending", "running"):
                    is_ready = False
                    break
                if dep_status == "offloaded" and wait_async:
                    is_ready   = False
                    is_blocked = True
                    break
                if dep_status == "failed":
                    step_statuses[sid] = "failed"
                    is_ready = False
                    break

            if is_ready:
                ready.append(step)
            elif is_blocked:
                blocked.append(step)

        if not ready:
            return {
                "last_result": "", "last_step_count": 0, "active_step_ids": [],
                "step_statuses": step_statuses, "step_results": step_results,
                "new_offloaded": {}, "pending_steps": blocked,
            }

        names  = " + ".join(s.get("agent", "?") for s in ready)
        suffix = f" (attempt {attempts + 1}/{_MAX_RETRIES})" if attempts > 0 else ""
        label  = f"{len(ready)} steps [parallel]" if len(ready) > 1 else "step"
        self._notify(f"manager: {label} → {names}{suffix}")

        # Recent conv history for agents (for context without full task re-description)
        conv_history: list[dict] = []
        for m in messages[-6:]:
            role    = m.get("role", "")
            content = (m.get("content") or "")[:600]
            if role in ("user", "assistant"):
                conv_history.append({"role": role, "content": content})

        # Run all ready steps concurrently
        coros = [
            self._run_one(
                s,
                s.get("task", "") + context_block + adj_block + file_injection,
                attempts,
                history=conv_history,
            )
            for s in ready
        ]
        results = await asyncio.gather(*coros)

        sync_pairs:   list[tuple[dict, str]] = []
        new_offloaded: dict                  = {}

        for step, text, offload in results:
            sid = str(step["id"])
            if offload and offload.get("task_id"):
                task_id = offload["task_id"]
                step_statuses[sid] = "offloaded"
                new_offloaded[task_id] = {
                    "step_id":    sid,
                    "agent":      step.get("agent"),
                    "poll_path":  offload["poll_path"],
                    "result_key": offload["result_key"],
                }
                step_results.append({
                    "agent":   step.get("agent"),
                    "step_id": sid,
                    "result":  text,
                    "status":  "offloaded",
                })
                self._notify(f"  {step.get('agent')}: offloaded → task {task_id[:8]}")
            else:
                sync_pairs.append((step, text))

        last_result = "\n\n".join(f"[{s.get('agent')}]: {t}" for s, t in sync_pairs)
        sync_ids    = [str(s["id"]) for s, _ in sync_pairs]

        return {
            "last_result":     last_result,
            "last_step_count": len(sync_pairs),
            "active_step_ids": sync_ids,
            "step_statuses":   step_statuses,
            "step_results":    step_results,
            "new_offloaded":   new_offloaded,
            "pending_steps":   blocked,
        }

    # ── Checker ───────────────────────────────────────────────────────────────

    async def _check(
        self,
        plan:            list[dict],
        active_step_ids: list[str],
        last_step_count: int,
        attempts:        int,
        last_result:     str,
        step_results:    list[dict],
        step_statuses:   dict,
    ) -> dict:
        if not active_step_ids or last_step_count == 0:
            return {}

        active_steps = [
            s for s in plan
            if str(s.get("id", s.get("agent", ""))) in active_step_ids
        ]
        if not active_steps:
            return {}

        step             = active_steps[0]
        check            = step.get("check") or None
        success_criteria = step.get("success_criteria", "Task completed without errors.")
        checker_tool     = self._tools.get("checker")

        self._notify("checker: validating...")

        status, reason = "unknown", ""
        if check and checker_tool:
            status, reason = await _run_explicit_check(checker_tool, check, self._notify)

        if status == "unknown":
            if checker_tool:
                cl = await checker_tool["run"]({
                    "action":           "classify",
                    "task":             step["task"],
                    "result":           last_result,
                    "success_criteria": success_criteria,
                })
                status = cl.get("status", "failure")
                reason = cl.get("reason", "")
            else:
                status, reason = "success", "No checker."

        self._notify(f"checker: {status} — {reason}")

        adjustment = ""
        if status != "success" and checker_tool:
            diag = await checker_tool["run"]({
                "action":         "diagnose",
                "task":           step["task"],
                "result":         last_result,
                "failure_reason": reason,
                "attempt_number": attempts + 1,
            })
            adjustment = diag.get("adjustment", "")
            if adjustment:
                self._notify(f"checker: adjustment → {adjustment}")

        advance = status == "success" or attempts >= _MAX_RETRIES - 1

        if advance:
            if attempts >= _MAX_RETRIES - 1 and status != "success":
                self._notify(f"checker: max retries — proceeding with {status}")
            for sid in active_step_ids:
                step_statuses[sid] = "completed" if status == "success" else "failed"
            new_entries = [
                {
                    "agent":   s.get("agent"),
                    "step_id": str(s.get("id", s.get("agent", ""))),
                    "result":  last_result,
                    "status":  status,
                }
                for s in active_steps
            ]
            return {
                "last_check":    {"status": status, "reason": reason, "adjustment": ""},
                "step_results":  step_results + new_entries,
                "step_statuses": step_statuses,
                "attempts":      0,
                "retry":         False,
            }
        else:
            return {
                "last_check":    {"status": status, "reason": reason, "adjustment": adjustment},
                "step_results":  step_results,
                "step_statuses": step_statuses,
                "attempts":      attempts + 1,
                "retry":         True,
            }

    # ── Synthesizer ───────────────────────────────────────────────────────────

    async def _synthesize(
        self, step_results: list[dict], user_msg: str, is_interim: bool = False
    ) -> str:
        self._notify("manager: synthesising final response...")
        client, manager_model = _manager_client()
        emit_token = _token_var.get()

        results_text = "\n\n".join(
            f"[{r['agent']} — {r['status']}]:\n{r['result']}"
            for r in step_results
        )

        try:
            stream = await client.chat.completions.create(
                model=manager_model,
                messages=[
                    {"role": "system", "content": _synthesis_system_prompt(interim=is_interim)},
                    {"role": "user", "content": f"Original request: {user_msg}\n\nAgent outputs:\n{results_text}"},
                ],
                temperature=0.3,
                stream=True,
            )
            reply = ""
            async for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    reply += delta
                    emit_token(delta)
            reply = _dedup_reply(reply)
        except Exception as exc:
            print(f"[engine] synthesis failed ({exc})", file=sys.stderr)
            reply = results_text

        return reply

    # ── Persistence helper ────────────────────────────────────────────────────

    @staticmethod
    async def _persist(store, thread_id: str, user_input: str, reply: str, is_async_resume: bool) -> None:
        if not is_async_resume:
            await store.append(thread_id, "user", user_input)
        await store.append(thread_id, "assistant", reply)

