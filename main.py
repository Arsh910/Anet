"""
main.py — CLI entry point for the config-driven multi-agent system.

Startup sequence:
  1. Load .env
  2. Validate required API keys
  3. Filter enabled agents from agents_config
  4. Load enabled tools via tool_loader
  5. Open ConversationStore (SQLite — persists conversation across restarts)
  6. Build Engine (pure-Python planner/executor/checker/synthesizer)
  7. Start background VIGA notifier task (polls registry.json every 30 s)
  8. Async readline loop → Live spinner while working → thinking panel + response
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.key_binding import KeyBindings
    _HAS_PT = True
except ImportError:
    _HAS_PT = False

load_dotenv()

_ASSISTANT_NAME = os.getenv("ASSISTANT_NAME", "Anet")

from anet.AnetAgents.agents_config import AGENTS
from anet.core.tool_loader import load_tools
from anet.core.engine import Engine, _manager_client as _engine_manager_client
from anet.core.store import ConversationStore
from anet.core.context import on_status as _status_var, on_token as _token_var, on_confirm as _confirm_var, on_output as _output_var
from anet.core.config_loader import agent_overrides as _agent_overrides, manager_config as _manager_config
from anet.core.ex_loader import load_ex_tools, load_ex_agents, get_extra_for_builtins
from anet.core.mcp_loader import load_mcp_tools_for_agents

_EX_CONFIG = Path(__file__).parent / "exanet.config.yaml"

_MEMORY_DIR         = Path(__file__).parent / "memory"
_USER_PROFILE_PATH  = _MEMORY_DIR / "USER.md"

# ── Memory nudge ──────────────────────────────────────────────────────────────
_session_turn_count: int = 0   # increments on every real user message; reset on /new

_NUDGE_TEXT = (
    "[MEMORY CHECK — system instruction, not from user]\n"
    "Before processing the request below: review the last 10 conversation turns. "
    "If the agent handling this task has memory_tool available, save any genuinely "
    "new and useful facts to memory — user preferences, project details, decisions "
    "made, or lessons learned. Be selective: only save facts not already stored that "
    "would meaningfully help future sessions. Do not save temporary task details. "
    "After saving (or if nothing worth saving), proceed with the user's request.\n"
    "[END MEMORY CHECK]\n\n"
)

# ── API key validation ────────────────────────────────────────────────────────
# Only warn for providers actually referenced by the current config.

_PROVIDER_KEYS = {
    "google":        ("GOOGLE_API_KEY",      "Google AI / Gemini"),
    "openrouter":    ("OPENROUTER_API_KEY",  "OpenRouter"),
    "openai":        ("OPENAI_API_KEY",      "OpenAI"),
    "claude":        ("ANTHROPIC_API_KEY",   "Anthropic / Claude"),
    "vertex_google": ("VERTEX_PROJECT_ID",   "Google Vertex AI / Gemini"),
    "vertex_claude": ("VERTEX_PROJECT_ID",   "Google Vertex AI / Claude"),
}

def _check_api_keys() -> None:
    overrides = _agent_overrides()
    providers_in_use: set[str] = set()
    # Manager (yaml override wins)
    providers_in_use.add(_manager_config().get("provider") or "google")
    # Agents — apply yaml overrides so we warn about what's actually used
    for agent in AGENTS:
        if not agent.get("enabled"):
            continue
        ov = overrides.get(agent["name"], {})
        provider = ov.get("provider") or agent.get("provider") or "openrouter"
        providers_in_use.add(provider)
    # Warn for missing keys
    for provider in providers_in_use:
        if provider not in _PROVIDER_KEYS:
            continue
        env_key, label = _PROVIDER_KEYS[provider]
        if not os.getenv(env_key):
            print(f"WARNING: {env_key} is not set — needed for {label}.")

_check_api_keys()

console = Console()


# ── Live spinner display ──────────────────────────────────────────────────────

_LOG_TAIL = 6   # how many past steps to show above the spinner

_CONTEXT_THRESHOLD = 40   # message count that triggers the compression prompt
_CONTEXT_KEEP      = 20   # messages to retain after forget

class _LiveStatus:
    """Rich renderable: rolling step log + animated spinner + elapsed time."""

    def __init__(self) -> None:
        self._current = "Thinking..."
        self.log: list[str] = []
        self._start = time.monotonic()

    def update(self, msg: str) -> None:
        self._current = msg
        self.log.append(msg)

    def __rich__(self):
        elapsed = time.monotonic() - self._start
        if elapsed < 60:
            elapsed_str = f"{elapsed:.0f}s"
        else:
            m, s = divmod(int(elapsed), 60)
            elapsed_str = f"{m}m {s:02d}s"

        parts = []

        # ── Todo checklist (if the agent wrote one) ───────────────────────────
        try:
            from anet.core.todo_state import get_todos
            todos = get_todos()
        except Exception:
            todos = []

        if todos:
            _icon = {"pending": "☐", "in_progress": "●", "completed": "✓", "failed": "✗"}
            _style = {
                "pending":     "dim",
                "in_progress": "bold cyan",
                "completed":   "dim green",
                "failed":      "red",
            }
            done = sum(1 for t in todos if t.get("status") == "completed")
            parts.append(Text.from_markup(f"  [dim]Tasks {done}/{len(todos)}[/dim]"))
            for t in todos:
                st   = t.get("status", "pending")
                icon = _icon.get(st, "?")
                sty  = _style.get(st, "dim")
                parts.append(Text.from_markup(f"  [{sty}]{icon} {t['content']}[/{sty}]"))
            parts.append(Text(""))   # blank line separator

        # ── Rolling step log ──────────────────────────────────────────────────
        past = self.log[:-1][-_LOG_TAIL:] if len(self.log) > 1 else []
        for step in past:
            parts.append(Text.from_markup(f"  [dim]├─ {step}[/dim]"))

        # ── Current step: spinner + elapsed ───────────────────────────────────
        spinner = Spinner("dots", text=f"  {self._current}  [dim]{elapsed_str}[/dim]")
        parts.append(spinner)

        return Group(*parts) if len(parts) > 1 else spinner


# ── Thinking panel (collapsed block shown after work completes) ───────────────

def _thinking_panel(steps: list[str]) -> Panel:
    """Compact dim panel showing what Anet did — like a collapsed tool-use block."""
    body = Text()
    for i, step in enumerate(steps):
        connector = "└─ " if i == len(steps) - 1 else "├─ "
        body.append(f"  {connector}{step}\n", style="dim")
    return Panel(
        body,
        title="[dim]▸ Thinking[/dim]",
        border_style="dim",
        expand=False,
        padding=(0, 1),
    )


# ── Context compression ───────────────────────────────────────────────────────

async def _context_forget(store: ConversationStore, thread_id: str) -> None:
    """Drop oldest messages, keep the last _CONTEXT_KEEP."""
    messages = await store.load(thread_id)
    kept = messages[-_CONTEXT_KEEP:]
    if len(kept) == len(messages):
        console.print("  [dim]Nothing old enough to forget.[/dim]\n")
        return
    await store.replace_all(thread_id, kept)
    console.print(
        f"  [dim]Dropped {len(messages) - len(kept)} old message(s). "
        f"{len(kept)} most recent kept.[/dim]\n"
    )


async def _context_compress(store: ConversationStore, thread_id: str) -> None:
    """Summarize old messages into one block, replace them in store."""
    messages = await store.load(thread_id)
    to_compress = messages[:-_CONTEXT_KEEP]
    if not to_compress:
        console.print("  [dim]Nothing old enough to compress.[/dim]\n")
        return

    console.print("  [dim]Compressing old context...[/dim]")

    history_text = "\n".join(
        f"{m['role'].upper()}: {(m.get('content') or '').strip()}"
        for m in to_compress
        if (m.get("content") or "").strip()
    )

    try:
        client, model = _engine_manager_client()
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a concise conversation summarizer."},
                {
                    "role": "user",
                    "content": (
                        "Summarize the conversation below into a compact block that preserves "
                        "all important facts, decisions, file paths, code changes, and outcomes. "
                        "Write in past tense. Be thorough but concise.\n\n"
                        + history_text
                    ),
                },
            ],
            max_tokens=1200,
        )
        summary = resp.choices[0].message.content.strip()
    except Exception as exc:
        console.print(f"  [red]Compression failed: {exc}[/red]\n")
        return

    kept = messages[-_CONTEXT_KEEP:]
    new_messages = [{"role": "user", "content": f"[Earlier conversation — summarised]\n{summary}"}] + kept
    await store.replace_all(thread_id, new_messages)
    console.print(
        f"  [dim]Compressed {len(to_compress)} message(s) into a summary. "
        f"{_CONTEXT_KEEP} recent messages kept.[/dim]\n"
    )


async def _context_check(store: ConversationStore, thread_id: str) -> None:
    """If message count exceeds threshold, offer forget / compress to the user."""
    try:
        n = await store.message_count(thread_id)
    except Exception:
        return

    if n < _CONTEXT_THRESHOLD:
        return

    console.print()
    console.print(f"  [yellow]Context is getting long ({n} messages).[/yellow]")
    console.print(f"  [dim]  [f] forget   — drop oldest, keep last {_CONTEXT_KEEP}[/dim]")
    console.print(f"  [dim]  [c] compress — summarise old messages into one block[/dim]")
    console.print(f"  [dim]  [s] skip     — continue as-is[/dim]")

    if _HAS_PT and _pt_session is not None:
        raw = await _pt_session.prompt_async("  > ")
    else:
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(None, input, "  > ")

    raw = raw.strip().lower()
    console.print()

    if raw == "f":
        await _context_forget(store, thread_id)
    elif raw == "c":
        await _context_compress(store, thread_id)


# ── User profile update (called on any exit — clean, Ctrl+C, or interrupt) ────

async def _update_user_profile(store: ConversationStore, thread_id: str) -> None:
    """Send this session's history to the manager model and update memory/USER.md."""
    from anet.core.config_loader import load as _load_cfg
    if not _load_cfg().get("memory", {}).get("user_profile_enabled", True):
        return

    try:
        messages = await store.load(thread_id)
    except Exception:
        return

    if not messages:
        return

    history_text = "\n".join(
        f"{m['role'].upper()}: {(m.get('content') or '').strip()}"
        for m in messages[-40:]
        if (m.get("content") or "").strip()
    )
    if not history_text.strip():
        return

    current = _USER_PROFILE_PATH.read_text(encoding="utf-8").strip() if _USER_PROFILE_PATH.exists() else ""

    try:
        client, model = _engine_manager_client()
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=1000,
            messages=[
                {"role": "system", "content": "You update a user profile file for an AI assistant."},
                {
                    "role": "user",
                    "content": (
                        f"Current USER.md:\n{current}\n\n"
                        f"Session conversation (last 40 messages):\n{history_text}\n\n"
                        "Update USER.md with any new facts learned about the user — preferences, "
                        "tech stack, working style, project context. Only add genuinely new information "
                        "not already present. Never remove existing entries. Keep entries concise "
                        "(one line each). Return the complete updated USER.md content, nothing else."
                    ),
                },
            ],
        )
        updated = (resp.choices[0].message.content or "").strip()
        if updated:
            _USER_PROFILE_PATH.write_text(updated, encoding="utf-8")
            console.print("[dim]  ✓ User profile updated.[/dim]")
    except Exception as exc:
        console.print(f"[dim]  Profile update skipped: {exc}[/dim]")


# ── Startup helpers ───────────────────────────────────────────────────────────

def _check_optional_deps() -> None:
    missing = [p for p in ("pyautogui", "pywinauto") if not _can_import(p)]
    if missing:
        console.print(
            f"[yellow]WARNING: desktop automation packages not installed: "
            f"{', '.join(missing)}\n"
            f"Run: pip install {' '.join(missing)} Pillow[/yellow]"
        )


def _can_import(pkg: str) -> bool:
    try:
        __import__(pkg)
        return True
    except ImportError:
        return False


def _print_startup_summary(enabled_agents: list[dict], tool_map: dict) -> None:
    from anet.core.mcp_loader import _connections as _mcp_connections

    # Split tool_map into MCP-backed vs regular AnetTools
    mcp_tool_names: set[str] = set()
    for conn in _mcp_connections.values():
        for t in conn.tools:
            mcp_tool_names.add(t.name)
    regular_tools = {k: v for k, v in tool_map.items() if k not in mcp_tool_names}

    console.print()
    console.rule(f"[bold green]{_ASSISTANT_NAME}[/bold green]")
    _mcfg     = _manager_config()
    _m_model  = _mcfg.get("model") or "gemini-2.5-pro"
    _m_prov   = _mcfg.get("provider") or "google"
    _m_label  = f"{_m_model}" + (f" [{_m_prov}]" if _m_prov != "google" else "")
    console.print(f"[dim]Manager: {_m_label} — plans and coordinates all requests[/dim]")
    console.print()

    at = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    at.add_column("Agent",   style="bold")
    at.add_column("Model",   style="dim")
    at.add_column("Tools")
    at.add_column("Handles")
    for a in enabled_agents:
        # Show only non-MCP tools in the agent row (MCP tools shown separately below)
        agent_tools = [t for t in a.get("tools", []) if t not in mcp_tool_names]
        preview = ", ".join(a.get("task_types", [])[:3])
        if len(a.get("task_types", [])) > 3:
            preview += ", …"
        at.add_row(a["name"], a["model"], ", ".join(agent_tools) or "—", preview)
    console.print(Panel(at, title="[bold]Loaded Agents[/bold]", border_style="green"))

    tt = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    tt.add_column("Tool",   style="bold")
    tt.add_column("Status")
    for name in regular_tools:
        tt.add_row(name, "[green]ready[/green]")
    console.print(Panel(tt, title="[bold]Loaded Tools[/bold]", border_style="blue"))

    if _mcp_connections:
        mt = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
        mt.add_column("Server",  style="bold")
        mt.add_column("Tools",   style="dim")
        mt.add_column("Status")
        for srv_name, conn in _mcp_connections.items():
            tool_names = ", ".join(t.name for t in conn.tools) or "—"
            status = "[red]error[/red]" if conn.error else "[green]ready[/green]"
            mt.add_row(srv_name, tool_names, status)
        console.print(Panel(mt, title="[bold]MCP Servers[/bold]", border_style="magenta"))

    console.print(
        f"[dim]Type your message and press Enter. "
        f"Type [bold]exit[/bold] or [bold]quit[/bold] to stop.[/dim]"
    )
    console.print()


# ── Background async task notifier ───────────────────────────────────────────

async def _async_notifier(
    engine_box: list, thread_id: str, store: ConversationStore, interval: int = 30
) -> None:
    """
    Background async-task notifier.

    Polls each task's poll_path (a JSON registry file) every `interval` seconds.
    When a task transitions to "done", stores the result via engine.set_async_result
    and calls engine.run_turn with __anet_async_resume__ so blocked pending_steps run.
    """
    last_state: dict[str, str] = {}

    while True:
        await asyncio.sleep(interval)
        engine = engine_box[0]

        offloaded_tasks = engine.get_offloaded_tasks(thread_id)
        if not offloaded_tasks:
            continue

        for task_id, task_info in offloaded_tasks.items():
            poll_path  = task_info.get("poll_path", "")
            result_key = task_info.get("result_key", "")
            agent_name = task_info.get("agent", "task")
            short      = task_id[:8]

            if not poll_path:
                continue

            poll_file = Path(poll_path)
            if not poll_file.exists():
                continue

            try:
                registry = json.loads(poll_file.read_text(encoding="utf-8"))
            except Exception:
                continue

            info   = registry.get(task_id, {})
            status = info.get("status", "")
            prev   = last_state.get(task_id)
            last_state[task_id] = status

            if status == prev or prev not in (None, "running"):
                continue

            if status == "done":
                out = info.get("output_file") or info.get("result") or "no output"
                console.print(
                    f"\n[bold green]{agent_name}[/bold green]: task {short}… complete → {out}"
                )
                engine.set_async_result(thread_id, result_key or task_id, out)

                if engine.get_pending_steps(thread_id):
                    await engine.run_turn(thread_id, store, "__anet_async_resume__")

            elif status in ("failed", "stopped"):
                console.print(
                    f"\n[bold red]{agent_name}[/bold red]: task {short}… {status}"
                )


# ── Input helper ──────────────────────────────────────────────────────────────

def _make_prompt_session():
    """Build a prompt_toolkit session with ESC-to-clear binding."""
    kb = KeyBindings()

    @kb.add("escape", eager=True)
    def _esc(event):
        # Clear the buffer; prompt_toolkit will redraw the empty prompt automatically.
        event.current_buffer.reset()

    return PromptSession(key_bindings=kb, enable_open_in_editor=False)


_pt_session: "PromptSession | None" = None


async def _read_input(prompt_text: str) -> str:
    """Read one line of input. Paste-safe and ESC-to-clear when prompt_toolkit is available."""
    global _pt_session
    if _HAS_PT:
        if _pt_session is None:
            _pt_session = _make_prompt_session()
        return await _pt_session.prompt_async(prompt_text)
    # fallback: plain input() in executor
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, input, prompt_text)


# ── Confirmation UI ───────────────────────────────────────────────────────────

def _confirm_summary(tool: str, action: str, args: dict) -> str:
    """Build a human-readable description of what the agent wants to do."""
    if tool == "shell_tool":
        cmd = args.get("command", "?")
        cwd = args.get("cwd", "")
        lines = [f"run: [bold]{cmd}[/bold]"]
        if cwd:
            lines.append(f"  [dim]cwd: {cwd}[/dim]")
        return "\n  [dim]│[/dim]  ".join(lines)
    if tool == "edit_tool":
        path = args.get("path", "?")
        old  = (args.get("old_string") or "")[:60].replace("\n", "↵")
        new  = (args.get("new_string") or "")[:60].replace("\n", "↵")
        return f"edit: [bold]{path}[/bold]\n  [dim]│[/dim]  [red]- {old}[/red]\n  [dim]│[/dim]  [green]+ {new}[/green]"
    if tool == "file_tool":
        path = args.get("path") or args.get("src") or args.get("output_zip") or "?"
        return f"{action}: [bold]{path}[/bold]"
    if tool == "open_app":
        target = args.get("app_name") or args.get("window_title") or "?"
        return f"{action}: [bold]{target}[/bold]"
    return f"{tool}: {action}"


def _make_confirm_fn(live: "Live") -> callable:
    """Returns a confirmation callback that pauses the spinner and asks the user.
    Uses an asyncio.Lock so concurrent tool calls queue up — never interleave."""
    _allow_all = [False]
    _lock = asyncio.Lock()

    async def _confirm(tool: str, action: str, args: dict) -> bool:
        if _allow_all[0]:
            return True

        async with _lock:
            # Another confirm may have set allow_all while we were waiting
            if _allow_all[0]:
                return True

            summary = _confirm_summary(tool, action, args)

            live.stop()
            console.print()
            console.print(f"  [bold cyan]┌─ Permission required[/bold cyan]")
            console.print(f"  [cyan]│[/cyan]  {summary}")
            console.print(f"  [cyan]└─[/cyan] [dim]y = yes · n = no · a = allow all remaining[/dim]")

            if _HAS_PT and _pt_session is not None:
                raw = await _pt_session.prompt_async("  > ")
            else:
                loop = asyncio.get_event_loop()
                raw = await loop.run_in_executor(None, input, "  > ")

            raw = raw.strip().lower()
            console.print()
            live.start()

            if raw == "a":
                _allow_all[0] = True
                return True
            return raw in ("y", "yes", "")

    return _confirm


# ── Slash commands ────────────────────────────────────────────────────────────

_HELP_TEXT = """
[bold]Slash commands[/bold]

  [bold cyan]/new[/bold cyan]                  Start a fresh session (clears history)
  [bold cyan]/session[/bold cyan] [dim]<name>[/dim]        Switch to a named session (creates if new)
  [bold cyan]/sessions[/bold cyan]             List all saved sessions
  [bold cyan]/agents[/bold cyan]               Show loaded agents and their tools
  [bold cyan]/forget[/bold cyan]               Drop oldest messages, keep last 20
  [bold cyan]/compress[/bold cyan]             Summarise old messages into one block
  [bold cyan]/profile[/bold cyan]              Show the current user profile (USER.md)
  [bold cyan]/skills[/bold cyan]               List all saved skills
  [bold cyan]/clear[/bold cyan]                Clear the screen
  [bold cyan]/help[/bold cyan]                 Show this message

  [bold cyan]exit[/bold cyan] [dim]or[/dim] [bold cyan]quit[/bold cyan]           End the session
"""


def _cmd_agents(enabled_agents: list[dict], tool_map: dict) -> None:
    t = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    t.add_column("Agent",    style="bold")
    t.add_column("Model",    style="dim")
    t.add_column("Tools")
    t.add_column("Task types", style="dim")
    for a in enabled_agents:
        tools   = ", ".join(a.get("tools", [])) or "—"
        preview = ", ".join(a.get("task_types", [])[:3])
        if len(a.get("task_types", [])) > 3:
            preview += ", …"
        t.add_row(a["name"], a.get("model", "?"), tools, preview)
    console.print()
    console.print(Panel(t, title="[bold]Loaded Agents[/bold]", border_style="green"))
    console.print()



def _list_session_dirs() -> list[Path]:
    """Return session dirs sorted newest first (by mtime)."""
    if not _MEMORY_DIR.exists():
        return []
    dirs = [d for d in _MEMORY_DIR.iterdir()
            if d.is_dir() and (d / "checkpoint.db").exists()]
    return sorted(dirs, key=lambda d: d.stat().st_mtime, reverse=True)


def _session_title(session_dir: Path) -> str:
    """Return the saved session title, or empty string if none."""
    title_file = session_dir / "title.txt"
    if title_file.exists():
        return title_file.read_text(encoding="utf-8").strip()
    return ""


def _save_session_title(session_dir: Path, user_input: str) -> None:
    """Save first-message title once (never overwrites)."""
    title_file = session_dir / "title.txt"
    if title_file.exists():
        return
    words = user_input.split()[:20]
    title = " ".join(words)
    if len(user_input.split()) > 20:
        title += "…"
    title_file.write_text(title, encoding="utf-8")


def _print_sessions(current: str | None = None) -> None:
    dirs = _list_session_dirs()
    last = _load_last_session()
    console.print()
    if not dirs:
        console.print("  [dim]No sessions saved yet.[/dim]\n")
        return
    console.print("  [bold]Saved sessions[/bold]")
    for d in dirs:
        sid    = d.name
        db     = d / "checkpoint.db"
        size   = f"{db.stat().st_size / 1024:.0f} KB" if db.exists() else "—"
        title  = _session_title(d)
        label  = f"[dim]{title}[/dim]" if title else ""
        marker = ""
        if current and sid == current:
            marker = "  [green]← active[/green]"
        elif sid == last and not current:
            marker = "  [green]← last[/green]"
        console.print(f"  [dim]•[/dim] {sid}  [dim]({size})[/dim]  {label}{marker}")
    console.print()
    console.print("  [dim]/session <name>  to switch · /new  for a fresh one[/dim]")
    console.print()


def _list_sessions_cmd() -> None:
    _print_sessions()
    console.print("[dim]Resume with:  python main.py --session <name>[/dim]")
    console.print("[dim]              python main.py --resume[/dim]\n")


def _cmd_sessions(current_thread_id: str | None = None) -> None:
    _print_sessions(current_thread_id)


async def _handle_slash(
    raw: str, config: dict, enabled_agents: list[dict], tool_map: dict,
    engine=None, store: ConversationStore | None = None,
) -> bool:
    """
    Handle a slash command. Returns True if the main loop should exit.
    Mutates config in-place for session-switching commands.
    """
    parts   = raw.strip().split(None, 1)
    command = parts[0].lower()
    arg     = parts[1].strip() if len(parts) > 1 else ""

    if command == "/help":
        console.print(_HELP_TEXT)

    elif command == "/clear":
        console.clear()

    elif command == "/new":
        global _session_turn_count
        _session_turn_count = 0
        new_id = _new_session_id()
        _save_last_session(new_id)
        config["configurable"]["thread_id"] = new_id
        console.print(f"\n  [dim]New session:[/dim] [bold]{new_id}[/bold]\n")

    elif command == "/session":
        if not arg:
            current = config["configurable"]["thread_id"]
            console.print(f"\n  [dim]Current session:[/dim] [bold]{current}[/bold]")
            console.print("  [dim]Usage: /session <name>[/dim]\n")
        else:
            _save_last_session(arg)
            config["configurable"]["thread_id"] = arg
            console.print(f"\n  [dim]Switched to session:[/dim] [bold]{arg}[/bold]\n")

    elif command == "/sessions":
        if arg:
            # /sessions <name> is an alias for /session <name>
            _save_last_session(arg)
            config["configurable"]["thread_id"] = arg
            console.print(f"\n  [dim]Switched to session:[/dim] [bold]{arg}[/bold]\n")
        else:
            _cmd_sessions(config["configurable"].get("thread_id"))

    elif command == "/agents":
        _cmd_agents(enabled_agents, tool_map)

    elif command == "/forget":
        if store is None:
            console.print("  [yellow]No active store.[/yellow]\n")
        else:
            thread_id = config["configurable"]["thread_id"]
            await _context_forget(store, thread_id)

    elif command == "/compress":
        if store is None:
            console.print("  [yellow]No active store.[/yellow]\n")
        else:
            thread_id = config["configurable"]["thread_id"]
            await _context_compress(store, thread_id)

    elif command == "/skills":
        try:
            from anet.core import skill_manager as _sm
            sdir = _sm._skills_dir()
            skill_files = sorted(sdir.glob("*.md")) if (sdir and sdir.exists()) else []
            if not skill_files:
                console.print("\n  [dim]No skills saved yet — Anet writes them after complex tasks.[/dim]\n")
            else:
                t = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
                t.add_column("Skill",      style="bold")
                t.add_column("Applies to", style="dim")
                t.add_column("Used",       justify="right", style="dim")
                for f in skill_files:
                    name, applies_to, used = _sm.read_skill_header(f)
                    t.add_row(name, applies_to or "—", str(used))
                console.print()
                console.print(Panel(t, title="[bold]Skills[/bold]", border_style="cyan"))
                console.print()
        except Exception as exc:
            console.print(f"\n  [red]Error: {exc}[/red]\n")

    elif command == "/profile":
        if _USER_PROFILE_PATH.exists():
            content = _USER_PROFILE_PATH.read_text(encoding="utf-8").strip()
            substantive = [
                ln for ln in content.splitlines()
                if ln.strip() and not ln.startswith("#") and not ln.startswith("<!--")
            ]
            if substantive:
                console.print()
                console.print(Markdown(content))
                console.print()
            else:
                console.print("\n  [dim]User profile is empty — Anet will build it after this session.[/dim]\n")
        else:
            console.print("\n  [dim]No user profile yet.[/dim]\n")

    else:
        console.print(f"\n  [yellow]Unknown command:[/yellow] {command}  "
                      f"[dim](type /help for a list)[/dim]\n")

    return False


# ── Chat turn (single request/response cycle) ─────────────────────────────────

async def _chat_turn(
    engine: Engine, store: ConversationStore, config: dict,
    enabled_agents: list[dict], tool_map: dict,
) -> bool:
    """Run one input → response cycle. Returns True when the user wants to exit."""
    try:
        user_input = await _read_input("You: ")
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]Goodbye![/dim]")
        return True

    user_input = user_input.strip()
    if not user_input:
        return False
    if user_input.lower() in ("exit", "quit"):
        console.print("[dim]Goodbye![/dim]")
        return True
    if user_input.startswith("/"):
        return await _handle_slash(user_input, config, enabled_agents, tool_map, engine, store)

    # Real user message — increment turn counter and check memory nudge
    global _session_turn_count
    _session_turn_count += 1

    effective_input = user_input
    if len(user_input) > 20:   # skip trivial/very short inputs
        try:
            from anet.core.config_loader import load as _cfg_load
            _mem_cfg = _cfg_load().get("memory", {})
            _nudge_enabled  = _mem_cfg.get("nudge_enabled", True)
            _nudge_interval = int(_mem_cfg.get("nudge_interval", 10))
        except Exception:
            _nudge_enabled, _nudge_interval = True, 10

        if _nudge_enabled and _nudge_interval > 0 and _session_turn_count % _nudge_interval == 0:
            print(
                f"[memory nudge] turn {_session_turn_count} — prompting memory reflection",
                file=sys.stderr,
            )
            effective_input = _NUDGE_TEXT + user_input

    # Offer forget/compress if context is getting long
    thread_id = config["configurable"]["thread_id"]
    await _context_check(store, thread_id)

    # Save first-message title for session listing
    session_dir = _MEMORY_DIR / config["configurable"]["thread_id"]
    _save_session_title(session_dir, user_input)

    # Clear any leftover todos from the previous task — the checklist is per-task,
    # not persistent. Agents that forget to call todo_tool(clear) at the end would
    # otherwise bleed their old list into the spinner for the next turn, and the
    # new code_agent would see stale incomplete items and try to finish them.
    try:
        from anet.core.todo_state import clear_todos
        clear_todos()
    except Exception:
        pass

    live_status = _LiveStatus()

    def on_status(msg: str) -> None:
        live_status.update(msg)

    def _render_diff(text: str) -> None:
        """Print a colored unified diff above the live spinner."""
        # Split summary line from diff body
        lines   = text.splitlines()
        summary = lines[0] if lines else ""
        diff    = Text()
        in_diff = False
        for line in lines[1:]:
            if line.startswith("---") or line.startswith("+++"):
                in_diff = True
                diff.append(line + "\n", style="bold")
            elif line.startswith("@@"):
                in_diff = True
                diff.append(line + "\n", style="cyan")
            elif line.startswith("+"):
                diff.append(line + "\n", style="green")
            elif line.startswith("-"):
                diff.append(line + "\n", style="red")
            elif in_diff:
                diff.append(line + "\n", style="dim")
        console.print(Panel(
            diff,
            title=f"[dim]{summary}[/dim]",
            border_style="dim",
            expand=False,
            padding=(0, 1),
        ))

    try:
        with Live(live_status, console=console, refresh_per_second=12, transient=True) as live:
            status_tk  = _status_var.set(on_status)
            token_tk   = _token_var.set(lambda _: None)
            confirm_tk = _confirm_var.set(_make_confirm_fn(live))
            output_tk  = _output_var.set(_render_diff)
            try:
                result = await engine.run_turn(thread_id, store, effective_input)
            finally:
                _status_var.reset(status_tk)
                _token_var.reset(token_tk)
                _confirm_var.reset(confirm_tk)
                _output_var.reset(output_tk)

    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted. Type 'exit' to quit.[/dim]")
        return False
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        return False

    response = result.reply or "Done."

    if result.step_results and live_status.log:
        console.print(Padding(_thinking_panel(live_status.log), (0, 0, 1, 0)))

    console.print(Panel(
        Markdown(response),
        title=f"[bold]{_ASSISTANT_NAME}[/bold]",
        border_style="green",
        padding=(1, 2),
    ))
    console.print()
    return False


# ── Session helpers ───────────────────────────────────────────────────────────

_LAST_SESSION_FILE = Path(__file__).parent / "memory" / "last_session.txt"


def _new_session_id() -> str:
    return datetime.now().strftime("session_%Y%m%d_%H%M%S")


def _save_last_session(sid: str) -> None:
    _LAST_SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LAST_SESSION_FILE.write_text(sid, encoding="utf-8")


def _load_last_session() -> str | None:
    if _LAST_SESSION_FILE.exists():
        return _LAST_SESSION_FILE.read_text(encoding="utf-8").strip() or None
    return None


def _resolve_session(args: argparse.Namespace) -> tuple[str, str]:
    """Return (thread_id, label) based on CLI args."""
    if args.session:
        return args.session, f"[dim]resuming:[/dim] [bold]{args.session}[/bold]"
    if args.resume:
        last = _load_last_session()
        if last:
            return last, f"[dim]resuming:[/dim] [bold]{last}[/bold]"
        console.print("[yellow]No previous session found — starting a new one.[/yellow]")
    sid = _new_session_id()
    return sid, f"[dim]new session:[/dim] [bold]{sid}[/bold]"


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    parser = argparse.ArgumentParser(description="ANet multi-agent assistant", add_help=False)
    parser.add_argument("--session", metavar="NAME",
                        help="Resume or create a named session")
    parser.add_argument("--resume",  action="store_true",
                        help="Resume the last session")
    parser.add_argument("--list-sessions", action="store_true",
                        help="List available saved sessions and exit")
    args, _ = parser.parse_known_args()

    # ── --list-sessions ───────────────────────────────────────────────────────
    if args.list_sessions:
        _list_sessions_cmd()
        return

    # Create USER.md with initial structure on first run
    if not _USER_PROFILE_PATH.exists():
        _MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        _USER_PROFILE_PATH.write_text(
            "## User Profile\n"
            "<!-- Anet builds this automatically. Do not edit manually. -->\n\n"
            "### Preferences\n\n"
            "### Tech Stack\n\n"
            "### Working Style\n\n"
            "### Project Context\n",
            encoding="utf-8",
        )

    enabled_agents = [a for a in AGENTS if a.get("enabled", False)]
    if not enabled_agents:
        console.print("[red]No enabled agents found in agents_config.py. Exiting.[/red]")
        sys.exit(1)

    tool_map = load_tools()
    _check_optional_deps()

    # ── Resolve session ───────────────────────────────────────────────────────
    thread_id, session_label = _resolve_session(args)
    _save_last_session(thread_id)

    # Each session gets its own subfolder: memory/<session_id>/
    session_dir = _MEMORY_DIR / thread_id
    session_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(session_dir / "checkpoint.db")

    async with ConversationStore(db_path) as store:
        config = {"configurable": {"thread_id": thread_id}}
        console.print(f"  {session_label}")
        console.print(f"  [dim]--resume to continue · /sessions to list · exit to quit[/dim]")
        console.print()

        async def _merge_all() -> tuple[list[dict], dict, dict, int]:
            """Load all agents and tools: built-ins + ExTools/ExAgents + MCP.
            Returns (agents, tools, manager_tools, external_count)."""

            # ── 1. ExTools from exanet.config.yaml ───────────────────────────
            ex_tools  = load_ex_tools()

            # ── 2. ExAgents from exanet.config.yaml ──────────────────────────
            ex_agents = load_ex_agents()

            # ── 3. Load .env files for ExAgents that have one ─────────────────
            _repo_root = Path(__file__).parent
            for agent in ex_agents:
                env_file = _repo_root / "ExAgents" / agent["name"] / ".env"
                if env_file.exists():
                    from dotenv import load_dotenv as _ldenv
                    _ldenv(env_file, override=True)

            # ── 4. Merge all tools ────────────────────────────────────────────
            combined_tools = {**tool_map, **ex_tools}

            # ── 5. Apply extra_tools/mcp from anet.config.yaml to built-ins ──
            extra_map       = get_extra_for_builtins()
            merged_builtins = [dict(a) for a in enabled_agents]
            for agent in merged_builtins:
                extra = extra_map.get(agent["name"], {})
                for t in extra.get("tools", []):
                    if t not in agent["tools"]:
                        agent["tools"] = agent["tools"] + [t]
                if extra.get("mcp"):
                    agent["mcp"] = list(agent.get("mcp") or []) + extra["mcp"]

            # ── 6. All agents combined ────────────────────────────────────────
            all_agents = merged_builtins + ex_agents

            # ── 7. Connect MCP servers for every agent that needs them ────────
            mcp_tools = await load_mcp_tools_for_agents(all_agents)
            combined_tools.update(mcp_tools)

            # spawn_tool is not auto-injected — agents that need it declare it
            # explicitly in their tools list, same as any other tool.

            # ── Configure spawn_tool with live agents + tools ─────────────────
            try:
                from anet.AnetTools.spawn_tool import configure as _cfg_spawn
                _cfg_spawn(combined_tools, all_agents)
            except Exception:
                pass

            manager_tools: dict = {}
            return all_agents, combined_tools, manager_tools, len(ex_agents)

        # Initial build
        all_agents, all_tools, manager_tools, n_external = await _merge_all()
        engine = Engine(all_agents, all_tools, manager_tools=manager_tools)
        # Reprint summary now that MCP tools have been injected into agent tool lists
        _print_startup_summary(all_agents, all_tools)
        if n_external:
            console.print(f"[dim]  + {n_external} external agent(s) loaded[/dim]\n")

        # Run Curator in background if enough skills exist
        try:
            from anet.core import skill_manager as _sm
            _sdir = _sm._skills_dir()
            if _sdir and _sdir.exists():
                _skill_count = len(list(_sdir.glob("*.md")))
                if _skill_count >= _sm._curator_min_skills():
                    console.print(f"[dim]  [curator] reviewing {_skill_count} skills...[/dim]\n")
                    asyncio.create_task(_sm.run_curator())
        except Exception:
            pass

        try:
            mtime = _EX_CONFIG.stat().st_mtime if _EX_CONFIG.exists() else 0.0
        except OSError:
            mtime = 0.0

        # Mutable box so notifier and hot-reload always reference the current Engine
        engine_box = [engine]
        notifier = asyncio.create_task(
            _async_notifier(engine_box, thread_id, store)
        )
        try:
            async def _loop_with_hotreload() -> None:
                nonlocal mtime
                cur_agents = all_agents
                cur_tools  = all_tools
                while True:
                    # Check if registry changed (anet connect / disconnect in another terminal)
                    try:
                        new_mtime = _EX_CONFIG.stat().st_mtime if _EX_CONFIG.exists() else 0.0
                    except OSError:
                        new_mtime = 0.0
                    if new_mtime != mtime:
                        mtime = new_mtime
                        cur_agents, cur_tools, mgr_tools2, n2 = await _merge_all()
                        engine_box[0] = Engine(cur_agents, cur_tools, manager_tools=mgr_tools2)
                        console.print(f"[dim]  ✓ registry updated — {n2} external agent(s) active[/dim]")

                    # Run one chat turn
                    done = await _chat_turn(engine_box[0], store, config, cur_agents, cur_tools)
                    if done:
                        break

            try:
                await _loop_with_hotreload()
            except (KeyboardInterrupt, asyncio.CancelledError):
                console.print("\n[dim]Session interrupted.[/dim]")
            finally:
                # Save user profile on ANY exit — clean, Ctrl+C, or terminal close
                console.print("[dim]  saving user profile...[/dim]")
                try:
                    await asyncio.wait_for(
                        _update_user_profile(store, thread_id),
                        timeout=15.0,
                    )
                except (asyncio.TimeoutError, Exception):
                    pass
        finally:
            notifier.cancel()
            try:
                await notifier
            except asyncio.CancelledError:
                pass


if __name__ == "__main__":
    asyncio.run(main())
