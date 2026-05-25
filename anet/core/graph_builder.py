"""
graph_builder.py — builds the LangGraph StateGraph from agents_config.

Graph structure:
  START → planner ─┬─ (simple reply) ──────────────────────────────── END
                   └─ (plan exists) → executor → checker ─┬─ (retry/next) → executor
                                                           └─ (all done)  → synthesizer → END

Nodes
  planner     Gemini 2.5 Pro — analyses request, produces a DAG plan with explicit
              step IDs and depends_on declarations, or returns a direct simple reply.
  executor    Runs all DAG-ready steps concurrently. Detects offloaded (async) steps.
              Injects previous step outputs and checker adjustments as context.
              Uses a per-session result cache to skip repeat (agent, task) pairs.
  checker     Validates the agent result using explicit OS checks or LLM classify.
              Advances step_statuses for the validated steps.
  synthesizer Gemini 2.5 Pro — streams the final reply token-by-token to the terminal.

Adding a new agent: edit agents_config.py only — zero graph code changes.
Persistence:   AsyncSqliteSaver (disk). Pass checkpointer= to build_graph().
"""

import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, StateGraph

from openai import AsyncOpenAI

from anet.core import orchestrator
from anet.core.context import on_status as _status_var, on_token as _token_var
from anet.core.state import AgentState

_MAX_RETRIES    = 3
_ASSISTANT_NAME = os.getenv("ASSISTANT_NAME", "Anet")

# ── Soul (loaded once at import time, injected into manager prompts only) ─────
def _load_soul_once() -> str:
    try:
        from anet.core.config_loader import load_soul
        return load_soul()
    except Exception:
        return ""

_SOUL = _load_soul_once()

# ── User profile (loaded once at import time, injected into planner prompt) ───
_USER_PROFILE_PATH = Path(__file__).parents[2] / "memory" / "USER.md"

def _load_user_profile_once() -> str:
    """Read memory/USER.md. Returns empty string if file is missing or only has headers."""
    try:
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

_USER_PROFILE = _load_user_profile_once()

# ── Manager model config (overridable via anet.config.yaml) ───────────────────

_MANAGER_PROVIDERS = {
    "google":      ("https://generativelanguage.googleapis.com/v1beta/openai/", "GOOGLE_API_KEY"),
    "openrouter":  ("https://openrouter.ai/api/v1",                             "OPENROUTER_API_KEY"),
    "openai":      ("https://api.openai.com/v1",                                "OPENAI_API_KEY"),
}

def _manager_cfg() -> tuple[str, str]:
    """Return (model, provider) for the manager, reading anet.config.yaml if present."""
    try:
        from anet.core.config_loader import manager_config
        cfg = manager_config()
        model    = cfg.get("model")    or "gemini-2.5-pro"
        provider = cfg.get("provider") or "google"
    except Exception:
        model, provider = "gemini-2.5-pro", "google"
    return model, provider


def _manager_client() -> tuple[AsyncOpenAI, str]:
    """Return (AsyncOpenAI client, model_name) for the manager."""
    model, provider = _manager_cfg()

    if provider in ("vertex_google", "vertex_claude"):
        from anet.core.agent_runner import build_vertex_client
        return build_vertex_client(), model

    base_url, env_key = _MANAGER_PROVIDERS.get(provider, _MANAGER_PROVIDERS["google"])
    api_key = os.getenv(env_key)
    if not api_key:
        raise RuntimeError(f"{env_key} not set (needed for manager provider='{provider}')")
    return AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=120), model


# Keep _google_client as a thin alias for any callers that haven't been updated
def _google_client() -> AsyncOpenAI:
    client, _ = _manager_client()
    return client


# ── Helpers ───────────────────────────────────────────────────────────────────

def _notify(msg: str) -> None:
    _status_var.get()(msg)


def _to_api_msgs(messages: list) -> list[dict]:
    """Convert LangChain message objects → OpenAI API dicts."""
    out = []
    for m in messages:
        if isinstance(m, HumanMessage):
            out.append({"role": "user", "content": m.content or ""})
        elif isinstance(m, AIMessage):
            out.append({"role": "assistant", "content": m.content or ""})
        elif isinstance(m, dict):
            out.append(m)
    return out


def _last_user_msg(messages: list) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return m.content or ""
        if isinstance(m, dict) and m.get("role") == "user":
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


# ── Prompts ───────────────────────────────────────────────────────────────────

def _memory_context(user_msg: str) -> str:
    """Search stored memories for keywords in the user message and return an injection block."""
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

"find ikarus folder and open in File Explorer"
→ {{"type":"plan","steps":[{{"id":1,"agent":"computer_agent","task":"Find folder named ikarus on C drive and open it","success_criteria":"File Explorer open showing ikarus folder","check":{{"action":"check_window","title":"ikarus"}},"depends_on":[],"wait_for_async":false}}]}}

"find latest AI news and type in notepad"
→ {{"type":"plan","steps":[
  {{"id":1,"agent":"research_agent","task":"Find latest AI news headlines and summaries","success_criteria":"3+ news items returned","check":null,"depends_on":[],"wait_for_async":false}},
  {{"id":2,"agent":"computer_agent","task":"Open Notepad and type the AI news","success_criteria":"Notepad open with news typed","check":{{"action":"check_window","title":"Notepad"}},"depends_on":[1],"wait_for_async":false}}
]}}

"create a react vite app in c:\\projects\\myapp"
→ {{"type":"plan","steps":[
  {{"id":1,"agent":"code_agent","task":"Scaffold a new Vite React app in c:\\projects\\myapp using npx create-vite@latest, then npm install","success_criteria":"package.json exists and node_modules installed","check":null,"depends_on":[],"wait_for_async":false}}
]}}

"write a python script that sorts a csv and save it to c:\\data\\sort.py"
→ {{"type":"plan","steps":[
  {{"id":1,"agent":"code_agent","task":"Write a Python script that sorts a CSV file and save it to c:\\data\\sort.py","success_criteria":"File c:\\data\\sort.py exists with correct code","check":null,"depends_on":[],"wait_for_async":false}}
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
        "the agent explicitly returned that error. Stick to what the agent results actually say."
    )
    if interim:
        base += (
            "\n- NOTE: Some tasks are running in the background. "
            "Tell the user what started and that they'll be notified when complete."
        )
    return base


# ── Reply deduplication (Gemini thinking tokens can double the content) ───────

def _dedup_reply(text: str) -> str:
    """
    Remove duplicated content that Gemini 2.5 Pro sometimes returns when its
    thinking tokens bleed into the main content field.

    Heuristic: if the string length > 100 and the second half starts with the
    same sentence as the first half, trim at the repeat point.
    """
    n = len(text)
    if n < 100:
        return text
    # Search for the start of text appearing again after the first 40% of chars
    start = text[:80]  # first 80 chars as a fingerprint
    idx = text.find(start, n // 3)
    if idx > 0:
        return text[:idx].rstrip()
    return text


# ── Routing guard ─────────────────────────────────────────────────────────────

# Signals in a task description that indicate it needs code_agent, not file_agent.
# file_agent has no edit_tool — routing code work there causes "tool not found".
_CODE_SIGNALS: frozenset[str] = frozenset({
    # file extensions
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".scss",
    ".json", ".yaml", ".yml", ".toml", ".vue", ".svelte",
    # code concepts
    "react", "component", "tailwind", "bootstrap",
    "python", "typescript", "javascript", "npm", "vite", "webpack",
    "import", "export", "function", "class", "const ", "let ", "def ",
    # action verbs that imply editing
    "fix", "edit ", "update ", "refactor", "implement", "add feature",
    "change the", "modify ", "rewrite", "rename ", "remove ",
    # ui/project
    "layout", "styling", "frontend", "backend", " ui ", "interface",
    "bug", "error", " code", "source", "project",
})

_FILE_ONLY_SIGNALS: frozenset[str] = frozenset({
    "copy", "move", "delete", "zip", "unzip", "compress", "extract",
    "rename folder", "create folder", "list folder",
})


def _coerce_routing(steps: list[dict]) -> list[dict]:
    """
    Guard against weak planner models routing code tasks to file_agent.
    If a step assigned to file_agent contains code-task signals, reroute
    it to code_agent (which has edit_tool, shell_tool, glob_tool, etc.).
    Only fires when the task looks like code work AND not a pure file-op.
    """
    for step in steps:
        if step.get("agent") != "file_agent":
            continue
        task_lower = step.get("task", "").lower()
        has_code   = any(sig in task_lower for sig in _CODE_SIGNALS)
        has_file_only = any(sig in task_lower for sig in _FILE_ONLY_SIGNALS)
        if has_code and not has_file_only:
            print(f"[graph] routing guard: file_agent → code_agent for task: {step.get('task','')[:80]!r}", file=sys.stderr)
            step["agent"] = "code_agent"
    return steps


# ── Keyword fallback ──────────────────────────────────────────────────────────

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
    print(f"[graph] keyword fallback → '{best['name']}'", file=sys.stderr)
    return {"type": "plan", "steps": [{
        "id": 1, "agent": best["name"], "task": user_msg,
        "success_criteria": "Task completed without errors.", "check": None,
        "depends_on": [], "wait_for_async": False,
    }]}


# ── Explicit OS check ─────────────────────────────────────────────────────────

async def _run_explicit_check(
    checker_tool: dict, check: dict
) -> tuple[str, str]:
    """Run a structured OS check from the plan. Returns (status, reason)."""
    action = check.get("action", "")
    params = ", ".join(f"{k}={v!r}" for k, v in check.items() if k != "action")
    _notify(f"checker: {action}({params})...")

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


# ── Node factories ────────────────────────────────────────────────────────────

def make_planner_node(enabled_agents: list[dict], manager_tools: dict | None = None) -> Callable:
    _mtools = manager_tools or {}

    async def planner(state: AgentState) -> dict:
        # ── Async resume: skip re-planning, just re-queue pending steps ───────
        messages = state.get("messages", [])
        last_content = (getattr(messages[-1], "content", "") if messages else "") or ""
        pending_steps = state.get("pending_steps", [])
        if last_content.startswith("__anet_async_resume__") and pending_steps:
            _notify("manager: resuming blocked steps after async completion...")
            return {
                "plan":          pending_steps,
                "step_statuses": {str(s.get("id", s.get("agent", ""))): "pending" for s in pending_steps},
                "pending_steps": [],
                "active_step_ids": [],
                "attempts":      0,
                "last_result":   "",
                "last_check":    {},
                "final_reply":   "",
            }

        _notify("manager: planning...")
        client, manager_model = _manager_client()

        # Search memory for context relevant to this request.
        # Run in a thread so the sync file I/O does not block the event loop
        # (blocking the event loop stalls Rich's spinner animation).
        user_msg_for_memory = _last_user_msg(state.get("messages", []))
        mem_ctx = await asyncio.to_thread(_memory_context, user_msg_for_memory)

        # Use last 8 messages for context
        api_msgs = _to_api_msgs(state.get("messages", [])[-8:])
        msgs = [
            {"role": "system", "content": _plan_system_prompt(enabled_agents, bool(_mtools), memory_ctx=mem_ctx)},
            *api_msgs,
        ]

        # Build tool schemas for manager if any are attached
        tools_param = (
            [t["schema"] for t in _mtools.values() if t.get("schema")]
            if _mtools else None
        )

        try:
            kwargs: dict = {"model": manager_model, "messages": msgs, "temperature": 0}
            if tools_param:
                kwargs["tools"] = tools_param

            resp = await client.chat.completions.create(**kwargs)
            msg  = resp.choices[0].message

            # ── Manager tool call: execute directly, return simple reply ──────
            if getattr(msg, "tool_calls", None):
                results = []
                for tc in msg.tool_calls:
                    tool_name = tc.function.name
                    try:
                        arguments = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    if tool_name in _mtools:
                        _notify(f"manager: calling {tool_name}...")
                        r = await _mtools[tool_name]["run"](arguments)
                        results.append(f"[{tool_name}]: {json.dumps(r)}")
                    else:
                        results.append(f"[{tool_name}]: tool not found")
                reply = "\n".join(results)
                return {
                    "final_reply":     reply,
                    "plan":            [],
                    "step_statuses":   {},
                    "active_step_ids": [],
                    "attempts":        0,
                    "step_results":    [],
                    "last_result":     "",
                    "last_check":      {},
                    "offloaded_tasks": {},
                    "async_results":   {},
                    "pending_steps":   [],
                    "messages":        [AIMessage(content=reply)],
                }

            raw = (msg.content or "").strip()
            try:
                plan = _extract_json(raw)
            except ValueError:
                if raw:
                    _notify("manager: prose reply — treating as simple answer")
                    plan = {"type": "simple", "reply": raw}
                else:
                    raise ValueError("empty response")
        except Exception as exc:
            _notify(f"manager: planning failed ({exc}) — keyword fallback")
            user_msg = _last_user_msg(state.get("messages", []))
            plan = _keyword_fallback(user_msg, enabled_agents)
            plan["steps"] = _coerce_routing(plan.get("steps", []))

        # Simple direct reply — no agents needed
        if plan.get("type") == "simple":
            reply = _dedup_reply(plan.get("reply") or "I'm not sure how to help with that.")
            return {
                "final_reply":     reply,
                "plan":            [],
                "step_statuses":   {},
                "active_step_ids": [],
                "attempts":        0,
                "step_results":    [],
                "last_result":     "",
                "last_check":      {},
                "offloaded_tasks": {},
                "async_results":   {},
                "pending_steps":   [],
                "messages":        [AIMessage(content=reply)],
            }

        steps = plan.get("steps") or []
        if not steps:
            reply = "I couldn't determine how to handle that."
            return {
                "final_reply":     reply,
                "plan":            [],
                "step_statuses":   {},
                "active_step_ids": [],
                "attempts":        0,
                "step_results":    [],
                "last_result":     "",
                "last_check":      {},
                "offloaded_tasks": {},
                "async_results":   {},
                "pending_steps":   [],
                "messages":        [AIMessage(content=reply)],
            }

        # Assign IDs if missing (backward compat with simple plans)
        for i, s in enumerate(steps):
            if "id" not in s:
                s["id"] = i + 1

        # Guard: reroute any code tasks misassigned to file_agent
        steps = _coerce_routing(steps)

        return {
            "plan":            steps,
            "step_statuses":   {str(s.get("id", i + 1)): "pending" for i, s in enumerate(steps)},
            "active_step_ids": [],
            "attempts":        0,
            "step_results":    [],
            "last_result":     "",
            "last_check":      {},
            "offloaded_tasks": {},
            "async_results":   {},
            "pending_steps":   [],
            "final_reply":     "",
        }

    return planner


def make_executor_node(
    enabled_agents: list[dict],
    tool_map: dict,
    result_cache: dict,
) -> Callable:
    agent_map = {a["name"]: a for a in enabled_agents}

    def _cache_key(agent_name: str, task: str) -> tuple[str, str]:
        return (agent_name, hashlib.sha256(task.encode()).hexdigest()[:16])

    async def _run_one(
        step: dict, full_task: str, attempts: int = 0, history: list | None = None
    ) -> tuple[dict, str, dict | None]:
        """Run a single plan step. Returns (step, result_text, offload_info | None)."""
        agent_name = step.get("agent", "")
        agent      = agent_map.get(agent_name)
        if agent is None:
            return step, f"Error: unknown agent '{agent_name}'", None

        key = _cache_key(agent_name, full_task)
        if key in result_cache and attempts == 0:
            _notify(f"  [cache] reusing result for {agent_name}")
            return step, result_cache[key], None

        raw = await orchestrator.run(
            agent=agent,
            tool_map=tool_map,
            user_message=full_task,
            history=history or [],
            on_status=_notify,
        )
        text = raw["text"]
        offload = None
        if raw.get("task_id"):
            offload = {
                "task_id":    raw["task_id"],
                "poll_path":  raw.get("poll_path", ""),
                "result_key": raw.get("result_key", ""),
            }
        result_cache[key] = text
        return step, text, offload

    async def executor(state: AgentState) -> dict:
        plan          = state.get("plan", [])
        step_statuses = dict(state.get("step_statuses", {}))
        async_results = state.get("async_results", {})
        step_results  = list(state.get("step_results", []))
        attempts      = state.get("attempts", 0)
        offloaded_tasks = dict(state.get("offloaded_tasks", {}))

        if not plan:
            return {"last_result": "", "last_step_count": 0, "active_step_ids": []}

        # Build context from completed + async results
        parts = []
        for r in step_results:
            if r.get("status") in ("success", "failure", "partial", "offloaded"):
                parts.append(f"=== Output from {r['agent']} ===\n{r['result']}")
        for k, v in async_results.items():
            parts.append(f"=== Async result [{k}] ===\n{v}")
        context_block = ("\n\n" + "\n\n".join(parts)) if parts else ""

        # Explicitly extract "Downloaded: <path>" lines from completed steps and
        # re-inject them so agents like tele_agent don't have to parse the full
        # context to find the file path — free models often miss embedded paths.
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
            adj = state.get("last_check", {}).get("adjustment", "")
            if adj:
                adj_block = f"\n\nAdjustment: {adj}"

        # Assign step IDs if missing (backward compat)
        for i, s in enumerate(plan):
            if "id" not in s:
                s["id"] = i + 1

        # DAG: find ready steps
        ready, blocked = [], []
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
                "last_result":     "",
                "last_step_count": 0,
                "active_step_ids": [],
                "step_statuses":   step_statuses,
                "pending_steps":   blocked,
            }

        # Notify
        names  = " + ".join(s.get("agent", "?") for s in ready)
        suffix = f" (attempt {attempts + 1}/{_MAX_RETRIES})" if attempts > 0 else ""
        label  = f"{len(ready)} steps [parallel]" if len(ready) > 1 else "step"
        _notify(f"manager: {label} → {names}{suffix}")

        # Build recent conversation history for agents (so they have context about
        # what was previously done without relying on vague task descriptions).
        # Truncate each message to avoid blowing out the agent's context window.
        conv_history: list[dict] = []
        for m in state.get("messages", [])[-6:]:
            if isinstance(m, HumanMessage):
                conv_history.append({"role": "user", "content": (m.content or "")[:600]})
            elif isinstance(m, AIMessage):
                conv_history.append({"role": "assistant", "content": (m.content or "")[:600]})
            elif isinstance(m, dict) and m.get("role") in ("user", "assistant"):
                conv_history.append({
                    "role":    m["role"],
                    "content": (m.get("content") or "")[:600],
                })

        # Run all ready steps concurrently
        coros   = [_run_one(s, s.get("task", "") + context_block + adj_block + file_injection, attempts, history=conv_history) for s in ready]
        results = await asyncio.gather(*coros)

        # Process results
        sync_pairs   = []    # (step, text) for sync steps
        new_offloaded: dict = {}

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
                _notify(f"  {step.get('agent')}: offloaded → task {task_id[:8]}")
            else:
                sync_pairs.append((step, text))

        last_result = "\n\n".join(f"[{s.get('agent')}]: {t}" for s, t in sync_pairs)
        sync_ids    = [str(s["id"]) for s, _ in sync_pairs]

        return {
            "last_result":     last_result,
            "last_step_count": len(sync_pairs),
            "active_step_ids": sync_ids,
            "step_statuses":   step_statuses,
            "offloaded_tasks": {**offloaded_tasks, **new_offloaded},
            "step_results":    step_results,
            "pending_steps":   blocked,
        }

    return executor


def make_checker_node(tool_map: dict) -> Callable:
    async def checker(state: AgentState) -> dict:
        plan            = state.get("plan", [])
        active_step_ids = state.get("active_step_ids", [])
        attempts        = state.get("attempts", 0)
        last_result     = state.get("last_result", "")
        step_results    = list(state.get("step_results", []))
        step_statuses   = dict(state.get("step_statuses", {}))
        last_step_count = state.get("last_step_count", 0)

        if not active_step_ids or last_step_count == 0:
            return {}  # nothing to validate (all offloaded or nothing ran)

        active_steps = [
            s for s in plan
            if str(s.get("id", s.get("agent", ""))) in active_step_ids
        ]
        if not active_steps:
            return {}

        step             = active_steps[0]
        check            = step.get("check") or None
        success_criteria = step.get("success_criteria", "Task completed without errors.")
        checker_tool     = tool_map.get("checker")

        _notify("checker: validating...")

        # ── 1. Explicit OS check ──────────────────────────────────────────────
        status, reason = "unknown", ""
        if check and checker_tool:
            status, reason = await _run_explicit_check(checker_tool, check)

        # ── 2. LLM classify fallback ──────────────────────────────────────────
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

        _notify(f"checker: {status} — {reason}")

        # ── 3. On failure: diagnose for next attempt ──────────────────────────
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
                _notify(f"checker: adjustment → {adjustment}")

        # ── 4. Advance or retry ───────────────────────────────────────────────
        advance = status == "success" or attempts >= _MAX_RETRIES - 1

        if advance:
            if attempts >= _MAX_RETRIES - 1 and status != "success":
                _notify(f"checker: max retries — proceeding with {status}")
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
            }
        else:
            return {
                "last_check": {"status": status, "reason": reason, "adjustment": adjustment},
                "attempts":   attempts + 1,
            }

    return checker


def make_synthesizer_node() -> Callable:
    async def synthesizer(state: AgentState) -> dict:
        _notify("manager: synthesising final response...")
        client, manager_model = _manager_client()
        step_results  = state.get("step_results", [])
        user_msg      = _last_user_msg(state.get("messages", []))
        emit_token    = _token_var.get()
        pending_steps = state.get("pending_steps", [])
        is_interim    = bool(pending_steps)

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
            print(f"[graph] synthesis failed ({exc})", file=sys.stderr)
            reply = results_text

        return {
            "final_reply": reply,
            "messages":    [AIMessage(content=reply)],
        }

    return synthesizer


# ── Routing ───────────────────────────────────────────────────────────────────

def _route_from_planner(state: AgentState) -> str:
    """After planner: go to executor if there's a plan, else end (simple reply)."""
    if state.get("final_reply"):
        return END
    return "executor"


def _route_from_checker(state: AgentState) -> str:
    """After checker: retry/next step → executor, all done → synthesizer."""
    plan          = state.get("plan", [])
    step_statuses = state.get("step_statuses", {})

    for step in plan:
        sid = str(step.get("id", step.get("agent", "")))
        if step_statuses.get(sid) in ("completed", "offloaded", "failed"):
            continue
        # Check if this step is ready (not blocked on async)
        deps       = step.get("depends_on", [])
        wait_async = step.get("wait_for_async", False)
        blocked    = any(
            step_statuses.get(str(d), "pending") == "offloaded" and wait_async
            for d in deps
        )
        pending_deps = any(
            step_statuses.get(str(d), "pending") in ("pending", "running")
            for d in deps
        )
        if not blocked and not pending_deps:
            return "executor"

    return "synthesizer"


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_graph(enabled_agents: list[dict], tool_map: dict, checkpointer=None, manager_tools: dict | None = None):
    """
    Build and compile the agent graph.

    Pass a checkpointer from outside (e.g. AsyncSqliteSaver) for disk persistence.
    Falls back to an in-process MemorySaver if none is provided.
    """
    if checkpointer is None:
        from langgraph.checkpoint.memory import MemorySaver
        checkpointer = MemorySaver()

    # Per-session agent result cache: (agent_name, task_hash) → result string.
    # Shared across all executor invocations within this graph instance.
    _result_cache: dict[tuple[str, str], str] = {}

    graph = StateGraph(AgentState)

    # ── Register nodes ────────────────────────────────────────────────────────
    graph.add_node("planner",     make_planner_node(enabled_agents, manager_tools))
    graph.add_node("executor",    make_executor_node(enabled_agents, tool_map, _result_cache))
    graph.add_node("checker",     make_checker_node(tool_map))
    graph.add_node("synthesizer", make_synthesizer_node())

    # ── Wire edges ────────────────────────────────────────────────────────────
    graph.set_entry_point("planner")

    graph.add_conditional_edges(
        "planner",
        _route_from_planner,
        {"executor": "executor", END: END},
    )
    graph.add_edge("executor", "checker")
    graph.add_conditional_edges(
        "checker",
        _route_from_checker,
        {"executor": "executor", "synthesizer": "synthesizer"},
    )
    graph.add_edge("synthesizer", END)

    return graph.compile(checkpointer=checkpointer)
