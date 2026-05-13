"""
main.py — CLI entry point for the config-driven multi-agent system.

Startup sequence:
  1. Load .env
  2. Validate required API keys
  3. Filter enabled agents from agents_config
  4. Load enabled tools via tool_loader
  5. Open AsyncSqliteSaver (disk checkpointer — persists conversation across restarts)
  6. Build LangGraph
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
from langchain_core.messages import HumanMessage
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
from anet.core.graph_builder import build_graph
from anet.core.context import on_status as _status_var, on_token as _token_var, on_confirm as _confirm_var
from anet.core.config_loader import agent_overrides as _agent_overrides, manager_config as _manager_config
from anet.plugin.loader import load_all_agents as _load_plugin_agents
from anet.plugin.registry import REGISTRY_FILE as _ANP_REGISTRY

_MEMORY_DIR = Path(__file__).parent / "memory"

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
    providers_in_use: set[str] = set()
    # Manager
    providers_in_use.add(_manager_config().get("provider") or "google")
    # Agents
    for agent in AGENTS:
        if agent.get("enabled"):
            providers_in_use.add(agent.get("provider") or "openrouter")
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
        preview = ", ".join(a.get("task_types", [])[:3])
        if len(a.get("task_types", [])) > 3:
            preview += ", …"
        at.add_row(a["name"], a["model"], ", ".join(a.get("tools", [])) or "—", preview)
    console.print(Panel(at, title="[bold]Loaded Agents[/bold]", border_style="green"))

    tt = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    tt.add_column("Tool",   style="bold")
    tt.add_column("Status")
    for name in tool_map:
        tt.add_row(name, "[green]ready[/green]")
    console.print(Panel(tt, title="[bold]Loaded Tools[/bold]", border_style="blue"))

    console.print(
        f"[dim]Type your message and press Enter. "
        f"Type [bold]exit[/bold] or [bold]quit[/bold] to stop.[/dim]"
    )
    console.print()


# ── Background async task notifier ───────────────────────────────────────────

async def _async_notifier(graph_box: list, config: dict, interval: int = 30) -> None:
    """
    Generic async-task notifier.

    Polls each task's poll_path (a JSON registry file) every `interval` seconds.
    When a task transitions to "done", stores the result in async_results and
    re-invokes the graph with __anet_async_resume__ so blocked pending_steps run.
    When a task transitions to "failed" or "stopped", prints an error notification.
    """
    last_state: dict[str, str] = {}

    while True:
        await asyncio.sleep(interval)
        graph = graph_box[0]

        try:
            snapshot = await graph.aget_state(config)
            state    = snapshot.values if snapshot else {}
        except Exception:
            continue

        offloaded_tasks: dict = state.get("offloaded_tasks", {})
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
                # Store result in async_results
                new_async_results = dict(state.get("async_results", {}))
                if result_key:
                    new_async_results[result_key] = out
                else:
                    new_async_results[task_id] = out

                await graph.aupdate_state(
                    config,
                    {"async_results": new_async_results},
                )

                # Resume blocked steps if any
                pending_steps = state.get("pending_steps", [])
                if pending_steps:
                    await graph.ainvoke(
                        {"messages": [HumanMessage(content="__anet_async_resume__")]},
                        config=config,
                    )

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
  [bold cyan]/status[/bold cyan]               Show connected plugin agents
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


def _cmd_status() -> None:
    from anet.plugin.registry import list_agents
    agents = list_agents()
    console.print()
    if not agents:
        console.print("  [dim]No plugin agents connected.[/dim]")
        console.print(
            "  [dim]Connect one with:  python cli.py connect --path plugins/<agent>[/dim]"
        )
        console.print()
        return
    t = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    t.add_column("Agent",     style="bold")
    t.add_column("Status")
    t.add_column("Connected", style="dim")
    t.add_column("Path",      style="dim")
    for e in agents:
        color = {"idle": "green", "running": "yellow", "disabled": "dim"}.get(e.status, "white")
        t.add_row(
            e.manifest.name,
            f"[{color}]{e.status}[/{color}]",
            str(e.registered_at)[:16].replace("T", "  "),
            e.path,
        )
    console.print(Panel(t, title="[bold]Plugin Agents[/bold]", border_style="blue"))
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
    raw: str, config: dict, enabled_agents: list[dict], tool_map: dict
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

    elif command == "/status":
        _cmd_status()

    else:
        console.print(f"\n  [yellow]Unknown command:[/yellow] {command}  "
                      f"[dim](type /help for a list)[/dim]\n")

    return False


# ── Chat turn (single request/response cycle) ─────────────────────────────────

async def _chat_turn(
    graph, config: dict, enabled_agents: list[dict], tool_map: dict
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
        return await _handle_slash(user_input, config, enabled_agents, tool_map)

    # Save first-message title for session listing
    session_dir = _MEMORY_DIR / config["configurable"]["thread_id"]
    _save_session_title(session_dir, user_input)

    live_status = _LiveStatus()

    def on_status(msg: str) -> None:
        live_status.update(msg)

    try:
        with Live(live_status, console=console, refresh_per_second=12, transient=True) as live:
            status_tk  = _status_var.set(on_status)
            token_tk   = _token_var.set(lambda _: None)
            confirm_tk = _confirm_var.set(_make_confirm_fn(live))
            try:
                state = await graph.ainvoke(
                    {"messages": [HumanMessage(content=user_input)]},
                    config=config,
                )
            finally:
                _status_var.reset(status_tk)
                _token_var.reset(token_tk)
                _confirm_var.reset(confirm_tk)

    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted. Type 'exit' to quit.[/dim]")
        return False
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        return False

    response = state.get("final_reply") or "Done."

    if state.get("step_results") and live_status.log:
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

    enabled_agents = [a for a in AGENTS if a.get("enabled", False)]
    if not enabled_agents:
        console.print("[red]No enabled agents found in agents_config.py. Exiting.[/red]")
        sys.exit(1)

    tool_map = load_tools()
    _check_optional_deps()
    _print_startup_summary(enabled_agents, tool_map)

    # ── Resolve session before opening checkpointer ───────────────────────────
    thread_id, session_label = _resolve_session(args)
    _save_last_session(thread_id)

    # Each session gets its own subfolder: memory/<session_id>/
    session_dir = _MEMORY_DIR / thread_id
    session_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(session_dir / "checkpoint.db")

    try:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        checkpointer_ctx = AsyncSqliteSaver.from_conn_string(db_path)
    except ImportError:
        console.print(
            "[yellow]langgraph-checkpoint-sqlite not installed — "
            "using in-memory checkpointer.[/yellow]"
        )
        checkpointer_ctx = None

    async def _run(checkpointer) -> None:
        config = {"configurable": {"thread_id": thread_id}}
        console.print(f"  {session_label}")
        console.print(f"  [dim]--resume to continue · /sessions to list · exit to quit[/dim]")
        console.print()

        def _merge_plugins() -> tuple[list[dict], dict, dict, int]:
            """Load plugin agents/extensions and merge with built-ins.
            Returns (agents, tools, manager_tools, plugin_count)."""
            plugin_agents, plugin_tools, attach_map = _load_plugin_agents()

            # Inject attached tools into target built-in agents
            merged_agents = [dict(a) for a in enabled_agents]
            for agent in merged_agents:
                extra = attach_map.get(agent["name"], [])
                if extra:
                    agent["tools"] = list(agent.get("tools", [])) + extra

            # Manager tools are handled separately by the graph builder
            manager_tools = {
                name: plugin_tools[name]
                for name in attach_map.get("manager", [])
                if name in plugin_tools
            }

            return (
                merged_agents + plugin_agents,
                {**tool_map, **plugin_tools},
                manager_tools,
                len(plugin_agents),
            )

        # Initial build
        all_agents, all_tools, manager_tools, n_plugins = _merge_plugins()
        graph = build_graph(all_agents, all_tools, checkpointer=checkpointer, manager_tools=manager_tools)
        if n_plugins:
            console.print(f"[dim]  + {n_plugins} plugin agent(s) loaded from registry[/dim]\n")

        try:
            mtime = _ANP_REGISTRY.stat().st_mtime if _ANP_REGISTRY.exists() else 0.0
        except OSError:
            mtime = 0.0

        # Pass graph as a mutable container so notifier and hot-reload both see updates
        graph_box = [graph]
        notifier = asyncio.create_task(_async_notifier(graph_box, config))
        try:

            async def _loop_with_hotreload() -> None:
                nonlocal mtime
                while True:
                    # Check if registry changed (anet connect / disconnect in another terminal)
                    try:
                        new_mtime = _ANP_REGISTRY.stat().st_mtime if _ANP_REGISTRY.exists() else 0.0
                    except OSError:
                        new_mtime = 0.0
                    if new_mtime != mtime:
                        mtime = new_mtime
                        all_agents2, all_tools2, manager_tools2, n2 = _merge_plugins()
                        graph_box[0] = build_graph(all_agents2, all_tools2, checkpointer=checkpointer, manager_tools=manager_tools2)
                        console.print(f"[dim]  ✓ registry updated — {n2} plugin agent(s) active[/dim]")

                    # Run one chat turn
                    done = await _chat_turn(graph_box[0], config, all_agents, all_tools)
                    if done:
                        break

            await _loop_with_hotreload()
        finally:
            notifier.cancel()
            try:
                await notifier
            except asyncio.CancelledError:
                pass

    if checkpointer_ctx is not None:
        async with checkpointer_ctx as checkpointer:
            await _run(checkpointer)
    else:
        await _run(None)


if __name__ == "__main__":
    asyncio.run(main())
