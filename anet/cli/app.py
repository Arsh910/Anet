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
import contextlib
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
    from prompt_toolkit.completion import Completer, Completion
    _HAS_PT = True
except ImportError:
    _HAS_PT = False

load_dotenv()

_ASSISTANT_NAME = os.getenv("ASSISTANT_NAME", "Anet")

from anet.AnetAgents.agents_config import AGENTS
from anet.core.tool_loader import load_tools
from anet.core.engine import Engine, _manager_client as _engine_manager_client
from anet.core.store import ConversationStore
from anet.core.context import on_status as _status_var, on_token as _token_var, on_confirm as _confirm_var, on_output as _output_var, on_ask as _ask_var, on_cancel as _cancel_var
from anet.core.config_loader import agent_overrides as _agent_overrides, manager_config as _manager_config
from anet.core.ex_loader import load_ex_tools, load_ex_agents, get_extra_for_builtins, get_builtin_attachments
from anet.core.mcp_loader import load_mcp_tools_for_agents

# exanet.config.yaml lives in the workspace (Anet home); resolved at runtime so
# the hot-reload watcher follows the real file, not a stale repo-root path.
def _ex_config_file() -> Path:
    return _anet_paths.exanet_path()

# Tools auto-added to EVERY agent (built-in or externally added) at startup,
# so they never need to be listed per-agent in config. Only injected if the
# tool actually loaded.
_ALWAYS_TOOLS = ["ask_user"]

from anet.core import paths as _anet_paths

# Where user data lives. Default ~/.anet, overridable via the first-run prompt
# or ANET_HOME. These module globals are re-resolved by _setup_anet_home() at
# startup (after the prompt) and read by the session/profile helpers below.
_MEMORY_DIR        = _anet_paths.sessions_dir()        # <home>/sessions/
_SHARED_DB_PATH    = _MEMORY_DIR / "conversations.db"  # one db for all sessions, keyed by thread

# Set by /changepack to force the hot-reload loop to rebuild the engine against
# the newly-selected active pack on its next iteration.
_force_reload: bool = False

# Seeded into <home>/.env on first run; the user fills in only the key(s) they need.
_ENV_TEMPLATE = (
    "# ANet API keys & settings. Fill in only what you use, then restart (or just\n"
    "# re-open with /keys). Edit any time:  /keys\n\n"
    "# ── Model providers — set at least ONE ──────────────────────────────────────\n"
    "OPENROUTER_API_KEY=\n"
    "GOOGLE_API_KEY=\n"
    "OPENAI_API_KEY=\n"
    "ANTHROPIC_API_KEY=\n\n"
    "# ── Vertex AI (optional) — also run: gcloud auth application-default login ───\n"
    "VERTEX_PROJECT_ID=\n"
    "VERTEX_LOCATION=\n\n"
    "# ── Optional ─────────────────────────────────────────────────────────────────\n"
    "# ASSISTANT_NAME=Anet\n"
)

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
    "google":           ("GOOGLE_API_KEY",      "Google AI / Gemini"),
    "openrouter":       ("OPENROUTER_API_KEY",  "OpenRouter"),
    "openai":           ("OPENAI_API_KEY",      "OpenAI"),
    "anthropic":        ("ANTHROPIC_API_KEY",   "Anthropic"),
    "vertex_google":    ("VERTEX_PROJECT_ID",   "Google Vertex AI / Gemini"),
    "vertex_anthropic": ("VERTEX_PROJECT_ID",   "Anthropic on Vertex AI"),
    # Legacy aliases — kept so older configs keep working.
    "claude":           ("ANTHROPIC_API_KEY",   "Anthropic"),
    "vertex_claude":    ("VERTEX_PROJECT_ID",   "Anthropic on Vertex AI"),
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
    missing = []
    for provider in providers_in_use:
        if provider not in _PROVIDER_KEYS:
            continue
        env_key, label = _PROVIDER_KEYS[provider]
        if not os.getenv(env_key):
            missing.append((env_key, label))
    for env_key, label in missing:
        console.print(f"  [yellow]WARNING:[/yellow] {env_key} is not set — needed for {label}.")
    if missing:
        console.print("  [dim]→ run [cyan]/keys[/cyan] to set your API keys.[/dim]")

console = Console()


# ── Live spinner display ──────────────────────────────────────────────────────

_LOG_TAIL = 6   # how many past steps to show above the spinner

_CONTEXT_THRESHOLD = 40   # default: message count that triggers the prompt
_CONTEXT_KEEP      = 20   # default: messages to retain after forget/compress

# Tracks the message count at which we last prompted, so we don't nag on every
# turn after the threshold is crossed. Reset to 0 on /new and after any
# compress/forget action. See _context_check.
_last_context_prompt_n: int = 0


def _context_cfg() -> dict:
    try:
        from anet.core.config_loader import load as _load_cfg
        return _load_cfg().get("context", {}) or {}
    except Exception:
        return {}


def _ctx_enabled()   -> bool: return bool(_context_cfg().get("enabled", True))
def _ctx_threshold() -> int:  return int(_context_cfg().get("threshold", _CONTEXT_THRESHOLD))
def _ctx_keep()      -> int:  return int(_context_cfg().get("keep",      _CONTEXT_KEEP))

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
    """Drop oldest messages, keep the last `keep`."""
    keep = _ctx_keep()
    messages = await store.load(thread_id)
    kept = messages[-keep:]
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
    keep = _ctx_keep()
    messages = await store.load(thread_id)
    to_compress = messages[:-keep]
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

    kept = messages[-keep:]
    new_messages = [{"role": "user", "content": f"[Earlier conversation — summarised]\n{summary}"}] + kept
    await store.replace_all(thread_id, new_messages)
    console.print(
        f"  [dim]Compressed {len(to_compress)} message(s) into a summary. "
        f"{keep} recent messages kept.[/dim]\n"
    )


async def _context_check(store: ConversationStore, thread_id: str) -> None:
    """When the conversation passes the threshold, offer compress / forget / skip.

    Only prompts once per threshold crossing: after the user skips, it stays
    quiet until the conversation grows by another `keep` messages, so it never
    nags on every turn. Choosing compress/forget shrinks the history and resets
    the tracker so the next crossing prompts normally.
    """
    global _last_context_prompt_n

    if not _ctx_enabled():
        return
    try:
        n = await store.message_count(thread_id)
    except Exception:
        return

    threshold = _ctx_threshold()
    keep      = _ctx_keep()
    if n < threshold:
        return
    # Back-off: don't re-prompt until the history has grown another `keep`
    # messages past where we last asked and the user chose to skip.
    if _last_context_prompt_n and n < _last_context_prompt_n + keep:
        return

    console.print()
    console.print(f"  [yellow]Context is getting long ({n} messages).[/yellow]")
    console.print(f"  [dim]  [c] compress — summarise old messages, keep last {keep}[/dim]")
    console.print(f"  [dim]  [f] forget   — drop oldest, keep last {keep}[/dim]")
    console.print(f"  [dim]  [s] skip     — continue as-is[/dim]")

    if _HAS_PT and _pt_session is not None:
        raw = await _pt_session.prompt_async("  > ")
    else:
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(None, input, "  > ")

    raw = raw.strip().lower()
    console.print()

    if raw == "c":
        await _context_compress(store, thread_id)
        _last_context_prompt_n = 0   # history shrank — let the next crossing prompt
    elif raw == "f":
        await _context_forget(store, thread_id)
        _last_context_prompt_n = 0
    else:
        # skipped — remember where we asked so we don't prompt again every turn
        _last_context_prompt_n = n


# ── User profile update (called on any exit — clean, Ctrl+C, or interrupt) ────

async def _update_user_profile(store: ConversationStore, thread_id: str) -> None:
    """On exit, hand this session's history to mem0, which extracts the salient
    facts and folds them into long-term memory (de-duplicating against what it
    already knows). This is the session-end pass that complements the periodic
    in-session extraction; both go through the same mem0 store."""
    from anet.core.config_loader import load as _load_cfg
    if not _load_cfg().get("memory", {}).get("user_profile_enabled", True):
        return

    try:
        messages = await store.load(thread_id)
    except Exception:
        return
    if not messages:
        return

    try:
        from anet.core import memory_store
        if not memory_store.is_available():
            return
        res = await asyncio.to_thread(
            memory_store.add_conversation, messages[-40:], run_id=thread_id
        )
        if res.get("results"):
            console.print(f"[dim]  ✓ Memory updated ({len(res['results'])} change(s)).[/dim]")
    except Exception as exc:
        console.print(f"[dim]  Memory update skipped: {exc}[/dim]")


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


def _split_tools(tool_map: dict) -> tuple[dict, set[str]]:
    """Return (regular_tools, mcp_tool_names) — MCP-backed tools split out."""
    from anet.core.mcp_loader import _connections as _mcp_connections
    mcp_tool_names: set[str] = set()
    for conn in _mcp_connections.values():
        for t in conn.tools:
            mcp_tool_names.add(t.name)
    regular_tools = {k: v for k, v in tool_map.items() if k not in mcp_tool_names}
    return regular_tools, mcp_tool_names


def _print_startup_summary(enabled_agents: list[dict], tool_map: dict) -> None:
    from anet.core.mcp_loader import _connections as _mcp_connections

    regular_tools, _ = _split_tools(tool_map)

    console.print()
    try:
        from anet.cli.banner import show_banner
        _bannered = show_banner(console, _ASSISTANT_NAME.upper())
    except Exception:
        _bannered = False
    if not _bannered:
        console.rule(f"[bold green]{_ASSISTANT_NAME}[/bold green]")
    _mcfg     = _manager_config()
    _m_model  = _mcfg.get("model") or "gemini-2.5-pro"
    _m_prov   = _mcfg.get("provider") or "google"
    _m_label  = f"{_m_model}" + (f" [{_m_prov}]" if _m_prov != "google" else "")
    console.print(f"[dim]Manager: {_m_label} — plans and coordinates all requests[/dim]")
    console.print()

    # Compact counts — full menus available via /agents, /tools, /mcps.
    # Total = built-in agents (enabled+disabled) + any external agents loaded.
    _builtin_names = {a["name"] for a in AGENTS}
    n_external = sum(1 for a in enabled_agents if a["name"] not in _builtin_names)
    agents_loaded = len(enabled_agents)
    agents_total  = len(AGENTS) + n_external
    tools_loaded  = len(regular_tools)
    mcp_ready     = sum(1 for c in _mcp_connections.values() if not c.error)
    mcp_total     = len(_mcp_connections)

    s = Table(show_header=False, box=None, padding=(0, 2))
    s.add_column(style="bold")
    s.add_column()
    s.add_column(style="dim")
    s.add_row("Agents", f"[green]{agents_loaded}[/green]/{agents_total} loaded", "/agents to view")
    s.add_row("Tools",  f"[green]{tools_loaded}[/green]/{tools_loaded} ready",   "/tools to view")
    s.add_row("MCP",    f"[green]{mcp_ready}[/green]/{mcp_total} connected",      "/mcps to view")
    console.print(s)
    console.print()

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

# Slash commands offered as autocomplete suggestions (command, description).
_SLASH_COMMANDS: list[tuple[str, str]] = [
    ("/new",      "Start a fresh session"),
    ("/session",  "Switch to a named session <name>"),
    ("/sessions", "List all saved sessions"),
    ("/agents",   "Show loaded agents and their tools"),
    ("/tools",    "Show loaded tools"),
    ("/mcps",     "Show connected MCP servers"),
    ("/skills",   "List saved skills"),
    ("/profile",  "Show what Anet remembers about you"),
    ("/forget",   "Drop oldest messages, keep last 20"),
    ("/compress", "Summarise old messages into one block"),
    ("/newtool",  "ToolSmith — scaffold + register an ExTool from code <path>"),
    ("/newagent", "AgentSmith — design + register a new agent <description>"),
    ("/addmcp",   "MCPSmith — draft + register an MCP server <path>"),
    ("/mcptest",  "Connect-test an MCP server <name>"),
    ("/changepack", "Switch the active pack (workspace) <name?>"),
    ("/keys",     "Set your API keys (opens ~/.anet/.env in an editor)"),
    ("/settings", "Edit models/providers (opens anet.config.yaml)"),
    ("/editpack", "Edit the pack's tools/agents (opens exanet.config.yaml)"),
    ("/editagent", "Edit an agent's prompt <name>"),
    ("/packsmith", "Packs: new <name> | share <path?> | add <zip>"),
    ("/clear",    "Clear screen and redraw the startup view"),
    ("/help",     "Show the slash command list"),
]


if _HAS_PT:
    class _SlashCompleter(Completer):
        """Suggest slash commands as soon as the line starts with '/'."""
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            # Only complete the command token itself: starts with '/', no space yet.
            if not text.startswith("/") or " " in text:
                return
            for cmd, desc in _SLASH_COMMANDS:
                if cmd.startswith(text):
                    yield Completion(
                        cmd,
                        start_position=-len(text),
                        display=cmd,
                        display_meta=desc,
                    )


def _make_prompt_session():
    """Build a prompt_toolkit session with ESC-to-clear and slash-command autocomplete."""
    kb = KeyBindings()

    @kb.add("escape", eager=True)
    def _esc(event):
        # Clear the buffer; prompt_toolkit will redraw the empty prompt automatically.
        event.current_buffer.reset()

    return PromptSession(
        key_bindings=kb,
        completer=_SlashCompleter(),
        complete_while_typing=True,
        enable_open_in_editor=False,
    )


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
    if tool == "download_file":
        url = args.get("url", "?")
        fn  = args.get("filename")
        tail = f"\n  [dim]│[/dim]  [dim]save as: {fn}[/dim]" if fn else ""
        return f"download: [bold]{url}[/bold]{tail}"
    if tool == "memory_tool":
        if action == "clear":
            return "memory: [bold red]CLEAR ALL memories[/bold red]"
        if action == "delete":
            return f"memory: delete [bold]{args.get('id', '?')}[/bold]"
        return f"memory: [bold]{action}[/bold]"
    if tool == "open_app":
        target = args.get("app_name") or args.get("window_title") or "?"
        return f"{action}: [bold]{target}[/bold]"
    return f"{tool}: {action}"


def _dest_key(tool: str, args: dict) -> str | None:
    """Return the argument key holding the destination path for a redirectable
    create/place action, or None if this action has no path the user can redirect.
    Used to offer the 'p = choose a different path' confirmation option."""
    action = args.get("action", "")
    if tool == "edit_tool":
        return "path"
    if tool == "file_tool":
        return {
            "write_file":    "path",
            "create_folder": "path",
            "copy_file":     "dst",
            "move_file":     "dst",
            "zip_files":     "output_zip",
            "unzip_file":    "extract_to",
        }.get(action)
    return None


def _make_confirm_fn(live: "Live") -> callable:
    """Returns a confirmation callback that pauses the spinner and asks the user.
    Uses an asyncio.Lock so concurrent tool calls queue up — never interleave."""
    _allow_all      = [False]
    _allow_download = [False]   # set after the first approved download — ask once per turn
    _lock = asyncio.Lock()

    async def _confirm(tool: str, action: str, args: dict) -> bool:
        if _allow_all[0] or (tool == "download_file" and _allow_download[0]):
            return True

        async with _lock:
            # Another confirm may have set allow_all / allow_download while we waited
            if _allow_all[0] or (tool == "download_file" and _allow_download[0]):
                return True

            summary  = _confirm_summary(tool, action, args)
            dest_key = _dest_key(tool, args)
            choices  = "y = yes · n = no · a = allow all remaining"
            if dest_key:
                choices += " · p = choose a different path"

            live.stop()
            console.print()
            console.print(f"  [bold cyan]┌─ Permission required[/bold cyan]")
            console.print(f"  [cyan]│[/cyan]  {summary}")
            console.print(f"  [cyan]└─[/cyan] [dim]{choices}[/dim]")

            async def _read(prompt: str) -> str:
                _pause_esc_watcher()   # release stdin so this prompt can read it
                try:
                    if _HAS_PT and _pt_session is not None:
                        return await _pt_session.prompt_async(prompt)
                    loop = asyncio.get_event_loop()
                    return await loop.run_in_executor(None, input, prompt)
                finally:
                    _resume_esc_watcher()

            raw = (await _read("  > ")).strip().lower()

            # ── p = redirect the destination path, then proceed ───────────────
            if dest_key and raw in ("p", "path"):
                newp = (await _read("  new path > ")).strip().strip('"').strip("'")
                console.print()
                live.start()
                if not newp:
                    console.print("  [dim]no path given — cancelled[/dim]\n")
                    return False
                orig = str(args.get(dest_key) or "")
                # A directory target keeps the original filename.
                if orig and (newp.endswith(("/", "\\")) or os.path.isdir(newp)):
                    newp = os.path.join(newp, os.path.basename(orig.rstrip("/\\")))
                args[dest_key] = newp
                console.print(f"  [green]→ using path:[/green] {newp}\n")
                return True

            console.print()
            live.start()

            if raw == "a":
                _allow_all[0] = True
                return True
            approved = raw in ("y", "yes", "")
            # First approved download covers the rest of this request (incl. retries).
            if approved and tool == "download_file":
                _allow_download[0] = True
            return approved

    return _confirm


def _make_ask_fn(live: "Live") -> callable:
    """Returns an ask-user callback that pauses the spinner, shows a clarifying
    question (with optional numbered choices), and returns the user's answer.
    Serialized with a Lock so concurrent agents never interleave prompts."""
    _lock = asyncio.Lock()

    async def _ask(question: str, options: list | None = None) -> str:
        options = options or []
        async with _lock:
            live.stop()
            console.print()
            console.print("  [bold yellow]┌─ Anet needs your input[/bold yellow]")
            console.print(f"  [yellow]│[/yellow]  {question}")
            for i, opt in enumerate(options, 1):
                console.print(f"  [yellow]│[/yellow]    [bold]{i}.[/bold] {opt}")
            hint = "type your answer, or a number to pick" if options else "type your answer"
            console.print(f"  [yellow]└─[/yellow] [dim]{hint}[/dim]")

            _pause_esc_watcher()   # release stdin so this prompt can read it
            try:
                if _HAS_PT and _pt_session is not None:
                    raw = await _pt_session.prompt_async("  > ")
                else:
                    loop = asyncio.get_event_loop()
                    raw = await loop.run_in_executor(None, input, "  > ")
            finally:
                _resume_esc_watcher()

            console.print()
            live.start()

            raw = (raw or "").strip()
            # A bare number selects the matching option.
            if options and raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(options):
                    return options[idx]
            return raw

    return _ask


# ── ESC-to-stop (cross-platform via prompt_toolkit input) ─────────────────────

_active_esc_watcher = None   # current watcher — confirm/ask prompts pause it via the helpers below


class _EscWatcher:
    """Watch for the ESC key while a task runs and set a cancel event.

    Uses prompt_toolkit's input layer, which works on Windows/macOS/Linux with one
    code path and hooks into the asyncio loop (no polling thread, no manual termios,
    and it disambiguates a lone ESC from an escape sequence). Must be PAUSED while a
    confirm/ask prompt reads stdin — a tty allows only one reader at a time.
    """

    def __init__(self, cancel_event: "asyncio.Event") -> None:
        from prompt_toolkit.input import create_input
        from prompt_toolkit.keys import Keys
        self._Keys   = Keys
        self._inp    = create_input()
        self._cancel = cancel_event
        self._raw    = None
        self._attach = None

    def _on_keys(self) -> None:
        try:
            keys = list(self._inp.read_keys()) + list(self._inp.flush_keys())
        except Exception:
            return
        for kp in keys:
            if kp.key == self._Keys.Escape:
                self._cancel.set()

    def start(self) -> None:
        self._raw = self._inp.raw_mode(); self._raw.__enter__()
        self._attach = self._inp.attach(self._on_keys); self._attach.__enter__()

    def pause(self) -> None:
        """Release stdin so a confirm/ask prompt can read it."""
        if self._attach is not None:
            with contextlib.suppress(Exception):
                self._attach.__exit__(None, None, None)
            self._attach = None
        if self._raw is not None:
            with contextlib.suppress(Exception):
                self._raw.__exit__(None, None, None)
            self._raw = None

    def resume(self) -> None:
        if self._raw is None:
            self._raw = self._inp.raw_mode(); self._raw.__enter__()
        if self._attach is None:
            self._attach = self._inp.attach(self._on_keys); self._attach.__enter__()

    def stop(self) -> None:
        self.pause()
        with contextlib.suppress(Exception):
            self._inp.close()


def _pause_esc_watcher() -> None:
    if _active_esc_watcher is not None:
        with contextlib.suppress(Exception):
            _active_esc_watcher.pause()


def _resume_esc_watcher() -> None:
    if _active_esc_watcher is not None:
        with contextlib.suppress(Exception):
            _active_esc_watcher.resume()


async def _run_turn_with_esc(engine, thread_id, store, user_input, cancel_event):
    """Run one turn while watching for ESC. Returns (result, stopped: bool).

    Two-tier stop: ESC sets `cancel_event` (cooperative — the engine/orchestrator
    stop at their next safe checkpoint, so any in-flight tool finishes first); if the
    turn doesn't wind down within a short grace period, it is hard-cancelled.
    """
    global _active_esc_watcher
    run_task = asyncio.create_task(engine.run_turn(thread_id, store, user_input))

    watcher = None
    if _HAS_PT:
        try:
            watcher = _EscWatcher(cancel_event)
            watcher.start()
            _active_esc_watcher = watcher
        except Exception:
            watcher = None
            _active_esc_watcher = None

    if watcher is None:
        # No ESC support (prompt_toolkit unavailable) — Ctrl+C still interrupts.
        return (await run_task, False)

    esc_wait = asyncio.create_task(cancel_event.wait())
    try:
        done, _ = await asyncio.wait({run_task, esc_wait}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        _active_esc_watcher = None
        watcher.stop()

    if run_task in done:
        esc_wait.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await esc_wait
        return (run_task.result(), False)

    # ESC pressed → cooperative cancel already set. Give the turn a moment to stop
    # at its next checkpoint; hard-cancel if it doesn't.
    try:
        result = await asyncio.wait_for(asyncio.shield(run_task), timeout=2.0)
        return (result, True)
    except asyncio.TimeoutError:
        run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await run_task
        return (None, True)


# ── Slash commands ────────────────────────────────────────────────────────────

_HELP_TEXT = """
[bold]Slash commands[/bold]

  [bold cyan]/new[/bold cyan]                  Start a fresh session (clears history)
  [bold cyan]/session[/bold cyan] [dim]<name>[/dim]        Switch to a named session (creates if new)
  [bold cyan]/sessions[/bold cyan]             List all saved sessions
  [bold cyan]/agents[/bold cyan]               Show loaded agents and their tools
  [bold cyan]/tools[/bold cyan]                Show loaded tools
  [bold cyan]/mcps[/bold cyan]                 Show connected MCP servers and their tools
  [bold cyan]/forget[/bold cyan]               Drop oldest messages, keep last 20
  [bold cyan]/compress[/bold cyan]             Summarise old messages into one block
  [bold cyan]/profile[/bold cyan]              Show what Anet remembers about you
  [bold cyan]/skills[/bold cyan]               List all saved skills
  [bold cyan]/newtool[/bold cyan] [dim]<path>[/dim]       ToolSmith: scaffold + register an ExTool from code
  [bold cyan]/newagent[/bold cyan] [dim]<desc>[/dim]      AgentSmith: design + register a new agent
  [bold cyan]/addmcp[/bold cyan] [dim]<path>[/dim]        MCPSmith: draft + register an MCP server from its docs
  [bold cyan]/mcptest[/bold cyan] [dim]<name>[/dim]       Connect-test an MCP server and list its tools
  [bold cyan]/changepack[/bold cyan] [dim]<name>[/dim]    Switch the active pack (workspace) — lists packs if no name
  [bold cyan]/keys[/bold cyan]                 Set your API keys (opens ~/.anet/.env in an editor)
  [bold cyan]/settings[/bold cyan]             Edit models/providers (opens anet.config.yaml)
  [bold cyan]/editpack[/bold cyan]             Edit the pack's tools/agents (opens exanet.config.yaml)
  [bold cyan]/editagent[/bold cyan] [dim]<name>[/dim]   Edit one of your agents' prompt
  [bold cyan]/packsmith new[/bold cyan] [dim]<name>[/dim]     Create a blank pack in yourpacks/ and switch to it
  [bold cyan]/packsmith share[/bold cyan] [dim]<path?>[/dim]  Bundle a pack into a shareable zip (secrets stripped)
  [bold cyan]/packsmith add[/bold cyan] [dim]<zip>[/dim]      Install a received pack into shared_packs/
  [bold cyan]/clear[/bold cyan]                Clear the screen
  [bold cyan]/help[/bold cyan]                 Show this message

  [bold cyan]ESC[/bold cyan]                   Stop the running task and return to the prompt
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


def _cmd_tools(tool_map: dict) -> None:
    regular_tools, _ = _split_tools(tool_map)
    t = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    t.add_column("Tool",   style="bold")
    t.add_column("Status")
    for name in regular_tools:
        t.add_row(name, "[green]ready[/green]")
    console.print()
    console.print(Panel(t, title="[bold]Loaded Tools[/bold]", border_style="blue"))
    console.print()


def _cmd_mcps() -> None:
    from anet.core.mcp_loader import _connections as _mcp_connections
    if not _mcp_connections:
        console.print("\n  [dim]No MCP servers configured.[/dim]\n")
        return
    t = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    t.add_column("Server",  style="bold")
    t.add_column("Tools",   style="dim")
    t.add_column("Status")
    for srv_name, conn in _mcp_connections.items():
        tool_names = ", ".join(tl.name for tl in conn.tools) or "—"
        status = "[red]error[/red]" if conn.error else "[green]ready[/green]"
        t.add_row(srv_name, tool_names, status)
    console.print()
    console.print(Panel(t, title="[bold]MCP Servers[/bold]", border_style="magenta"))
    console.print()


async def _cmd_changepack(arg: str = "") -> None:
    """List available packs and switch the active one (the workspace ANet reads).
    Triggers an engine rebuild so the new pack's tools/agents take effect."""
    global _force_reload
    packs  = _anet_paths.list_packs()
    active = _anet_paths.active_pack()

    # Resolve the target: an explicit arg (name or number), else show a picker.
    target = None
    choice = arg.strip()
    if not choice:
        console.print("\n  [bold]Available packs[/bold]")
        for i, p in enumerate(packs, 1):
            marker = "  [green]← active[/green]" if p == active else ""
            kind = f"[dim]({_anet_paths.pack_kind(p)})[/dim]"
            console.print(f"   [cyan]{i}[/cyan]. {p} {kind}{marker}")
        console.print()
        choice = (await _read_input("  pick a pack (number or name, blank to cancel): ")).strip()

    if not choice:
        console.print("  [dim]no change[/dim]\n")
        return
    if choice.isdigit() and 1 <= int(choice) <= len(packs):
        target = packs[int(choice) - 1]
    elif choice in packs:
        target = choice
    else:
        console.print(f"  [yellow]unknown pack:[/yellow] {choice}  [dim](see the list above)[/dim]\n")
        return

    if target == active:
        console.print(f"  [dim]already on '{target}'[/dim]\n")
        return

    _switch_pack(target)
    console.print(f"\n  [green]switched to pack:[/green] [bold]{target}[/bold] — reloading on next message…\n")


def _switch_pack(name: str) -> None:
    """Set the active pack, drop the config cache, and flag an engine rebuild."""
    global _force_reload
    _anet_paths.set_active_pack(name)
    try:
        from anet.core.config_loader import reset_cache
        reset_cache()
    except Exception:
        pass
    _force_reload = True


def _cmd_editagent(arg: str) -> None:
    """Open an ExAgent's prompt.md in the editor (the /editagent command)."""
    name = arg.split()[0] if arg.strip() else ""
    if not name:
        console.print("\n  [yellow]Usage:[/yellow] /editagent <agent-name>  [dim](one of your pack's agents)[/dim]\n")
        return
    prompt_file = _anet_paths.exagents_dir() / name / "prompt.md"
    if prompt_file.exists():
        if _open_in_editor(prompt_file):
            _apply_config_change()
            console.print(f"  [green]{name} prompt updated[/green] — applied on your next message.\n")
        return
    # Helpful diagnosis if not found.
    if any(a.get("name") == name for a in AGENTS):
        console.print(f"\n  [yellow]'{name}' is a built-in agent[/yellow] — its prompt lives in the read-only core, "
                      f"so it isn't editable here. Create your own with [cyan]/newagent[/cyan].\n")
    else:
        console.print(f"\n  [yellow]No editable prompt found for '{name}'[/yellow] at {prompt_file}.\n"
                      f"  [dim]It must be a pack ExAgent with a prompt_file. See /agents.[/dim]\n")


async def _cmd_packsmith_new(name: str) -> None:
    """Create a blank pack in yourpacks/<name> and switch to it (the /packsmith new
    command). Build it up afterward with /newtool, /newagent, /addmcp."""
    import anet.AnetTools.pack_tool as _pack_tool
    r = await _pack_tool.run({"action": "create", "name": name})
    if "error" in r:
        console.print(f"\n  [red]{r['error']}[/red]\n")
        return
    created = r["result"]["name"]
    path    = r["result"]["path"]
    console.print(f"\n  [green]created pack:[/green] [bold]{created}[/bold]  [dim]{path}[/dim]")
    _switch_pack(created)
    console.print(
        f"  [green]switched to[/green] [bold]{created}[/bold] — build it with "
        f"[cyan]/newtool[/cyan] · [cyan]/newagent[/cyan] · [cyan]/addmcp[/cyan]. "
        f"Reloading on next message…\n"
    )



def _list_session_dirs() -> list[Path]:
    """Return session dirs sorted newest first (by mtime)."""
    if not _MEMORY_DIR.exists():
        return []
    dirs = [d for d in _MEMORY_DIR.iterdir() if d.is_dir()]
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
        count  = _session_msg_count(sid)
        size   = f"{count} msg" if count else "empty"
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
        _print_startup_summary(enabled_agents, tool_map)

    elif command == "/changepack":
        await _cmd_changepack(arg)

    elif command == "/keys":
        env_file = _ensure_home_env()
        if _open_in_editor(env_file):
            try:
                from dotenv import load_dotenv as _ldenv
                _ldenv(env_file, override=True)   # picked up on the next model call
                console.print("  [green]keys reloaded[/green] — take effect on your next message.\n")
            except Exception:
                console.print("  [dim]saved — restart to apply.[/dim]\n")

    elif command == "/settings":
        _edit_yaml(_anet_paths.config_path(), "anet.config.yaml (models/providers)")

    elif command == "/editpack":
        _edit_yaml(_anet_paths.exanet_path(), "exanet.config.yaml (tools/agents)")

    elif command == "/editagent":
        _cmd_editagent(arg)

    elif command == "/new":
        global _session_turn_count, _last_context_prompt_n
        _session_turn_count = 0
        _last_context_prompt_n = 0
        new_id = _new_session_id()
        _save_last_session(new_id)
        (_MEMORY_DIR / new_id).mkdir(parents=True, exist_ok=True)
        config["configurable"]["thread_id"] = new_id
        console.print(f"\n  [dim]New session:[/dim] [bold]{new_id}[/bold]\n")

    elif command == "/session":
        if not arg:
            current = config["configurable"]["thread_id"]
            console.print(f"\n  [dim]Current session:[/dim] [bold]{current}[/bold]")
            console.print("  [dim]Usage: /session <name>[/dim]\n")
        else:
            _save_last_session(arg)
            (_MEMORY_DIR / arg).mkdir(parents=True, exist_ok=True)
            config["configurable"]["thread_id"] = arg
            console.print(f"\n  [dim]Switched to session:[/dim] [bold]{arg}[/bold]\n")

    elif command == "/sessions":
        if arg:
            # /sessions <name> is an alias for /session <name>
            _save_last_session(arg)
            (_MEMORY_DIR / arg).mkdir(parents=True, exist_ok=True)
            config["configurable"]["thread_id"] = arg
            console.print(f"\n  [dim]Switched to session:[/dim] [bold]{arg}[/bold]\n")
        else:
            _cmd_sessions(config["configurable"].get("thread_id"))

    elif command == "/agents":
        _cmd_agents(enabled_agents, tool_map)

    elif command == "/tools":
        _cmd_tools(tool_map)

    elif command == "/mcps":
        _cmd_mcps()

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
            # /skills pin <name> | /skills unpin <name> — protect a skill from the curator
            parts = arg.split(maxsplit=1) if arg else []
            if parts and parts[0] in ("pin", "unpin"):
                if len(parts) < 2:
                    console.print(f"\n  [dim]Usage: /skills {parts[0]} <skill-name>[/dim]\n")
                else:
                    target = parts[1].strip().removesuffix(".md")
                    ok = _sm.set_pinned(target, parts[0] == "pin")
                    if ok:
                        verb = "pinned" if parts[0] == "pin" else "unpinned"
                        console.print(f"\n  [green]Skill '{target}' {verb}.[/green]\n")
                    else:
                        console.print(f"\n  [yellow]No skill named '{target}'.[/yellow]\n")
                return False
            sdir = _sm._skills_dir()
            skill_files = sorted(sdir.glob("*.md")) if (sdir and sdir.exists()) else []
            if not skill_files:
                console.print("\n  [dim]No skills saved yet — Anet writes them after complex tasks.[/dim]\n")
            else:
                t = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
                t.add_column("Skill",      style="bold")
                t.add_column("Applies to", style="dim")
                t.add_column("Used",       justify="right", style="dim")
                t.add_column("",           style="yellow")  # pin indicator
                for f in skill_files:
                    name, applies_to, used = _sm.read_skill_header(f)
                    pin = "📌" if _sm.is_pinned(f.stem) else ""
                    t.add_row(name, applies_to or "—", str(used), pin)
                console.print()
                console.print(Panel(t, title="[bold]Skills[/bold]", border_style="cyan"))
                console.print("  [dim]/skills pin <name> to protect a skill from the curator[/dim]\n")
        except Exception as exc:
            console.print(f"\n  [red]Error: {exc}[/red]\n")

    elif command == "/profile":
        try:
            from anet.core import memory_store
            if not memory_store.is_available():
                console.print("\n  [dim]Long-term memory is unavailable (mem0 not initialised).[/dim]\n")
            else:
                mems = await asyncio.to_thread(memory_store.get_all, None, 200)
                if not mems:
                    console.print("\n  [dim]Nothing remembered yet — Anet builds this as you work.[/dim]\n")
                else:
                    prefs = [m for m in mems if m.get("always_inject")]
                    facts = [m for m in mems if m not in prefs]
                    facts.sort(key=lambda m: m.get("created_at", ""), reverse=True)
                    t = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False)
                    t.add_column("What Anet remembers")
                    t.add_column("", style="dim")
                    for m in prefs:
                        t.add_row(m.get("content", ""), m.get("category") or "standing")
                    for m in facts:
                        t.add_row(m.get("content", ""), m.get("category") or m.get("project_path") or "")
                    console.print()
                    console.print(Panel(t, title=f"[bold]Memory[/bold] ({len(mems)})", border_style="cyan"))
                    console.print("  [dim]/forget <id> via the assistant, or ask it to update what it knows[/dim]\n")
        except Exception as exc:
            console.print(f"\n  [red]Error: {exc}[/red]\n")

    elif command == "/newtool":
        if not arg:
            console.print(
                "\n  [yellow]Usage:[/yellow] /newtool <path-to-tool-source>\n"
                "  [dim]Generates ExTools/<name>/__init__.py from existing code, validates it,[/dim]\n"
                "  [dim]and prints the registration stanza. Example: /newtool ExTools/myzip/myzip_repo[/dim]\n"
            )
        else:
            await _run_toolsmith(arg, tool_map)

    elif command == "/newagent":
        if not arg:
            console.print(
                "\n  [yellow]Usage:[/yellow] /newagent <describe the agent you want>\n"
                "  [dim]Designs an ExAgent: writes its prompt, lets you pick tools/MCP, and[/dim]\n"
                "  [dim]registers it. Example: /newagent an agent that summarises PDFs and emails them[/dim]\n"
            )
        else:
            await _run_agentsmith(arg, tool_map)

    elif command == "/addmcp":
        if not arg:
            console.print(
                "\n  [yellow]Usage:[/yellow] /addmcp <path-to-mcp-repo-or-readme>\n"
                "  [dim]Drafts mcps/<name>/config.yaml from the server's docs, verifies it[/dim]\n"
                "  [dim]connects, attaches it to agents you pick, and confirms. Example: /addmcp ../some-mcp-server[/dim]\n"
            )
        else:
            await _run_mcpsmith(arg, tool_map)

    elif command == "/mcptest":
        if not arg:
            console.print(
                "\n  [yellow]Usage:[/yellow] /mcptest <server-name>\n"
                "  [dim]Connect-tests an existing mcps/<name>/config.yaml and lists its tools.[/dim]\n"
            )
        else:
            await _run_mcp_doctor(arg.split()[0])

    elif command == "/packsmith":
        sub_parts = arg.split(None, 1)
        sub  = sub_parts[0].lower() if sub_parts else ""
        rest = sub_parts[1].strip() if len(sub_parts) > 1 else ""
        if sub == "new":
            if not rest:
                console.print("\n  [yellow]Usage:[/yellow] /packsmith new <name>\n")
            else:
                await _cmd_packsmith_new(rest.split()[0])
        elif sub == "share":
            await _run_packsmith("share", rest, tool_map)
        elif sub == "add":
            if not rest:
                console.print("\n  [yellow]Usage:[/yellow] /packsmith add <path-to-pack.zip>\n")
            else:
                await _run_packsmith("add", rest, tool_map)
        else:
            console.print(
                "\n  [yellow]Usage:[/yellow] /packsmith new [dim]<name>[/dim]\n"
                "          /packsmith share [dim]<pack path — blank = active pack>[/dim]\n"
                "          /packsmith add [dim]<path-to-pack.zip>[/dim]\n"
                "  [dim]new:   create a blank pack in yourpacks/ and switch to it — build it with the smiths.[/dim]\n"
                "  [dim]share: bundle a pack into a shareable zip (secrets stripped, README added).[/dim]\n"
                "  [dim]add:   install a received pack zip into shared_packs/, then /changepack to it.[/dim]\n"
            )

    else:
        console.print(f"\n  [yellow]Unknown command:[/yellow] {command}  "
                      f"[dim](type /help for a list)[/dim]\n")

    return False


# ── Tool generator (/newtool) ─────────────────────────────────────────────────

def _render_diff_panel(text: str) -> None:
    """Print a colored unified diff (module-level so /newtool can reuse it)."""
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
    console.print(Panel(diff, title=f"[dim]{summary}[/dim]", border_style="dim",
                        expand=False, padding=(0, 1)))


async def _run_standalone_agent(agent_def: dict, user_message: str, tool_map: dict,
                                banner: str) -> None:
    """Run a standalone (non-manager) agent in a direct loop — shared by /newtool
    and /addmcp. Bypasses the planner entirely.
    """
    from anet.core import orchestrator

    agent = dict(agent_def)
    name  = agent.get("name", "agent")
    agent["tools"] = list(agent.get("tools") or [])
    for t in _ALWAYS_TOOLS:                       # ask_user — needed for the confirm step
        if t in tool_map and t not in agent["tools"]:
            agent["tools"].append(t)

    # Resolve model/provider: explicit agents.<name> override > manager model.
    try:
        from anet.core.config_loader import load as _cfg_load
        override = (_cfg_load().get("agents") or {}).get(name) or {}
    except Exception:
        override = {}
    try:
        mcfg = _manager_config()
    except Exception:
        mcfg = {}
    agent["model"]    = override.get("model")    or mcfg.get("model")    or "gemini-2.5-pro"
    agent["provider"] = override.get("provider") or mcfg.get("provider") or "google"
    if override.get("max_steps"):
        agent["max_steps"] = int(override["max_steps"])

    missing = [t for t in agent["tools"] if t not in tool_map]
    if missing:
        console.print(f"  [yellow]{name}: tools not loaded, skipping:[/yellow] {', '.join(missing)}")

    console.print()
    console.print(f"  [bold cyan]{name}[/bold cyan] [dim]{banner}[/dim]\n")

    live_status = _LiveStatus()

    def on_status(msg: str) -> None:
        live_status.update(msg)

    try:
        with Live(live_status, console=console, refresh_per_second=12, transient=True) as live:
            s_tk = _status_var.set(on_status)
            t_tk = _token_var.set(lambda _: None)
            c_tk = _confirm_var.set(_make_confirm_fn(live))
            o_tk = _output_var.set(_render_diff_panel)
            a_tk = _ask_var.set(_make_ask_fn(live))
            try:
                result = await orchestrator.run(
                    agent=agent, tool_map=tool_map,
                    user_message=user_message, history=[], on_status=on_status,
                )
            finally:
                _status_var.reset(s_tk)
                _token_var.reset(t_tk)
                _confirm_var.reset(c_tk)
                _output_var.reset(o_tk)
                _ask_var.reset(a_tk)
    except Exception as exc:
        console.print(f"  [red]{name} error: {exc}[/red]\n")
        return

    text = (result or {}).get("text") or "Done."
    console.print(Panel(Markdown(text), title=f"[bold]{name}[/bold]",
                        border_style="green", padding=(1, 2)))
    console.print()


async def _run_toolsmith(repo_path: str, tool_map: dict) -> None:
    """Scaffold an ExTool from existing code (the /newtool command)."""
    from anet.AnetAgents.toolsmith import TOOLSMITH_AGENT
    user_message = (
        "Generate an ANet ExTool for the code at this path:\n"
        f"{repo_path}\n\n"
        "Follow your workflow exactly: explore the source, confirm the tool name and "
        "capability with the user via ask_user, write ExTools/<tool_name>/__init__.py, "
        "validate it with `python -m anet.core.extool_validator` until PASS, then REGISTER "
        "and ATTACH it using the registrar tool (which edits exanet.config.yaml only — never "
        "anet.config.yaml or the anet/ package): ask the user which agents should get the "
        "tool and attach to their choices."
    )
    await _run_standalone_agent(
        TOOLSMITH_AGENT, user_message, tool_map,
        banner=f"scaffolding an ExTool from {repo_path}",
    )


async def _run_mcpsmith(source: str, tool_map: dict) -> None:
    """Draft + verify an MCP server config from its docs/repo (the /addmcp command)."""
    from anet.AnetAgents.mcpsmith import MCPSMITH_AGENT
    user_message = (
        "Integrate the MCP server documented at this path:\n"
        f"{source}\n\n"
        "Follow your workflow: read the docs/repo, confirm the server name and launch "
        "command with the user via ask_user, write mcps/<name>/config.yaml, verify it "
        "with `python -m anet.core.mcp_doctor <name>` until PASS, then ATTACH it using the "
        "registrar tool (which edits exanet.config.yaml only — never anet.config.yaml or the "
        "anet/ package): ask the user which agents should get the server and attach to them."
    )
    await _run_standalone_agent(
        MCPSMITH_AGENT, user_message, tool_map,
        banner=f"integrating an MCP server from {source}",
    )


async def _run_agentsmith(description: str, tool_map: dict) -> None:
    """Design + register a new ExAgent from a description (the /newagent command)."""
    from anet.AnetAgents.agentsmith import AGENTSMITH_AGENT
    user_message = (
        "Design and register a new ANet ExAgent from this description:\n"
        f"{description}\n\n"
        "Follow your workflow: decide the agent name and task_types; use the registrar tool "
        "(list_tools / list_mcps) to show the user the available tools and MCP servers and let "
        "them pick which to add; confirm; write ExAgents/<name>/prompt.md; then register it with "
        "registrar action='register_agent'. The registrar edits exanet.config.yaml only — never "
        "anet.config.yaml or the anet/ package."
    )
    await _run_standalone_agent(
        AGENTSMITH_AGENT, user_message, tool_map,
        banner="designing a new ExAgent",
    )


async def _run_packsmith(mode: str, arg: str, tool_map: dict) -> None:
    """Share a pack as a zip, or install a received pack zip (the /packsmith command)."""
    from anet.AnetAgents.packsmith import PACKSMITH_AGENT
    if mode == "share":
        target = arg.strip() or "the active pack"
        user_message = (
            "SHARE a pack as a distributable .zip.\n"
            f"Pack to share: {target}.\n\n"
            "Follow your SHARE workflow: inspect the pack with pack_tool, write a clear "
            "recipient README (what it does, required env vars + where, prerequisites, how to "
            "/packsmith add + /changepack, a trust note), then pack_tool action='export' with "
            "that README (secrets are stripped automatically). Report the final .zip path."
        )
        banner = f"packaging {target} for sharing"
    else:  # add
        user_message = (
            "ADD (install) a shared pack from this zip:\n"
            f"{arg.strip()}\n\n"
            "Follow your ADD workflow: import it with pack_tool (it extracts to shared_packs/ "
            "and never runs pack code), read its README, summarise what's inside + the trust "
            "implication, collect any required secrets via ask_user and write the .env files, "
            "run only the setup the README documents (via shell_tool, user-approved), then tell "
            "the user to /changepack to activate it."
        )
        banner = f"installing pack from {arg.strip()}"
    await _run_standalone_agent(
        PACKSMITH_AGENT, user_message, tool_map, banner=banner,
    )


async def _run_mcp_doctor(name: str) -> None:
    """Connect-test an existing MCP server config (the /mcptest command)."""
    from anet.core.mcp_doctor import diagnose
    console.print()
    console.print(f"  [bold cyan]mcp doctor[/bold cyan] [dim]testing[/dim] {name}\n")
    try:
        res = await diagnose(name)
    except Exception as exc:
        console.print(f"  [red]mcp doctor error: {exc}[/red]\n")
        return
    for line in res["messages"]:
        style = "red" if line.startswith("FAIL") else ("green" if line.startswith("OK") else "dim")
        console.print(f"  [{style}]{line}[/{style}]")
    console.print()
    if res["ok"]:
        console.print(f"  [green]PASS[/green] — '{name}' connects with {len(res['tools'])} tool(s).")
        console.print("  [dim]Add it to an agent's mcp: list in anet.config.yaml and restart ANet.[/dim]\n")
    else:
        console.print(f"  [red]INVALID[/red] — '{name}' did not connect (see FAIL lines).\n")


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

    cancel_event = asyncio.Event()
    stopped = False
    try:
        with Live(live_status, console=console, refresh_per_second=12, transient=True) as live:
            status_tk  = _status_var.set(on_status)
            token_tk   = _token_var.set(lambda _: None)
            confirm_tk = _confirm_var.set(_make_confirm_fn(live))
            output_tk  = _output_var.set(_render_diff)
            ask_tk     = _ask_var.set(_make_ask_fn(live))
            cancel_tk  = _cancel_var.set(cancel_event)
            try:
                result, stopped = await _run_turn_with_esc(
                    engine, thread_id, store, effective_input, cancel_event
                )
            finally:
                _status_var.reset(status_tk)
                _token_var.reset(token_tk)
                _confirm_var.reset(confirm_tk)
                _output_var.reset(output_tk)
                _ask_var.reset(ask_tk)
                _cancel_var.reset(cancel_tk)

    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted. Type 'exit' to quit.[/dim]")
        return False
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        return False

    if stopped:
        console.print("\n  [yellow]⏹ Stopped.[/yellow] [dim]Back to the prompt.[/dim]\n")
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

_LAST_SESSION_FILE = _anet_paths.sessions_dir() / "last_session.txt"


def _new_session_id() -> str:
    return datetime.now().strftime("session_%Y%m%d_%H%M%S")


def _save_last_session(sid: str) -> None:
    _LAST_SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LAST_SESSION_FILE.write_text(sid, encoding="utf-8")


def _load_last_session() -> str | None:
    if _LAST_SESSION_FILE.exists():
        return _LAST_SESSION_FILE.read_text(encoding="utf-8").strip() or None
    return None


def _session_msg_count(thread_id: str) -> int:
    """Message count for a thread in the shared db (sync read, 0 if none/missing)."""
    if not _SHARED_DB_PATH.exists():
        return 0
    import sqlite3
    try:
        con = sqlite3.connect(str(_SHARED_DB_PATH))
        try:
            cur = con.execute(
                "SELECT COUNT(*) FROM messages WHERE thread = ?", (thread_id,)
            )
            row = cur.fetchone()
            return row[0] if row else 0
        finally:
            con.close()
    except sqlite3.Error:
        return 0


def _migrate_per_session_dbs() -> None:
    """One-time fold of legacy per-session <id>/checkpoint.db files into the
    shared conversations.db. Rows are copied verbatim (thread, role, content),
    which also repairs any rows misfiled by the old session-switch bug, since
    the shared db keys purely by the thread column. Each migrated file is
    renamed to checkpoint.db.migrated so it is never reprocessed."""
    if not _MEMORY_DIR.exists():
        return
    import sqlite3

    legacy = [
        d / "checkpoint.db"
        for d in _MEMORY_DIR.iterdir()
        if d.is_dir() and (d / "checkpoint.db").exists()
    ]
    # Oldest first, so a conversation the old switch-bug split across two db
    # files is reassembled in chronological order under its thread.
    legacy.sort(key=lambda p: p.stat().st_mtime)
    if not legacy:
        return

    try:
        dst = sqlite3.connect(str(_SHARED_DB_PATH))
    except sqlite3.Error:
        return
    try:
        dst.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                thread  TEXT    NOT NULL,
                role    TEXT    NOT NULL,
                content TEXT    NOT NULL
            )
        """)
        for db_file in legacy:
            try:
                src = sqlite3.connect(str(db_file))
                try:
                    rows = src.execute(
                        "SELECT thread, role, content FROM messages ORDER BY id"
                    ).fetchall()
                finally:
                    src.close()
                if rows:
                    dst.executemany(
                        "INSERT INTO messages (thread, role, content) VALUES (?, ?, ?)",
                        rows,
                    )
                    dst.commit()
                db_file.rename(db_file.with_suffix(".db.migrated"))
            except (sqlite3.Error, OSError):
                continue
    finally:
        dst.close()


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


# ── Anet home setup (first-run prompt + migration) ────────────────────────────

def _prompt_for_home() -> Path:
    """One-time prompt: ask where to store sessions, USER.md and SOUL.md."""
    default = _anet_paths.DEFAULT_HOME
    console.print()
    console.print("  [bold]First-time setup[/bold]")
    console.print(
        "  [dim]Where should Anet store your sessions, USER.md and SOUL.md?[/dim]"
    )
    console.print(f"  [dim]Press Enter for the default:[/dim] [bold]{default}[/bold]")
    try:
        raw = input("  Path: ").strip()
    except (EOFError, KeyboardInterrupt):
        raw = ""
    chosen = Path(raw).expanduser() if raw else default
    try:
        chosen.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        console.print(f"  [red]Could not create {chosen}: {exc} — using {default}[/red]")
        chosen = default
        chosen.mkdir(parents=True, exist_ok=True)
    _anet_paths.save_home(chosen)
    console.print(f"  [green]Saved.[/green] Anet will store data in [bold]{chosen}[/bold]\n")
    return chosen


def _seed_and_migrate(home: Path, repo: Path | None = None) -> None:
    """On first run: remove the old in-repo sessions (the user opted to drop them).
    SOUL.md is part of the pack and is handled by workspace.ensure_workspace();
    long-term memory now lives in mem0 (no USER.md file to seed)."""
    import shutil
    # Legacy in-repo migration only applies to a source checkout; resolve the repo
    # root via the dev marker (None when pip-installed, where there's nothing to migrate).
    repo = repo or _anet_paths._dev_repo_root()
    if repo is None:
        return
    old_mem = repo / "memory"

    # Remove old in-repo sessions — the user asked to drop them, not migrate.
    if old_mem.exists():
        old_sessions = [
            d for d in old_mem.iterdir()
            if d.is_dir() and (d / "checkpoint.db").exists()
        ]
        if old_sessions:
            console.print(
                f"  [dim]Removing {len(old_sessions)} old session(s) from the repo...[/dim]"
            )
            for d in old_sessions:
                shutil.rmtree(d, ignore_errors=True)
        # Clean up legacy loose files now that USER.md is migrated.
        for leftover in ("last_session.txt", "USER.md"):
            p = old_mem / leftover
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        try:
            if not any(old_mem.iterdir()):
                old_mem.rmdir()
        except OSError:
            pass


# ── Editor-based config editing (/keys, /settings, /editpack, /editagent) ─────

def _resolve_editor() -> list[str]:
    """Pick an editor: $VISUAL/$EDITOR, else notepad on Windows, else nano/vi."""
    import shutil
    for var in ("VISUAL", "EDITOR"):
        val = os.environ.get(var)
        if val:
            return val.split()
    if sys.platform == "win32":
        return ["notepad"]
    for cand in ("nano", "vim", "vi"):
        if shutil.which(cand):
            return [cand]
    return ["vi"]


def _open_in_editor(path: Path) -> bool:
    """Open a file in the user's editor (blocking). Returns True if it launched."""
    import subprocess
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")
    editor = _resolve_editor()
    console.print(f"  [dim]opening[/dim] [cyan]{path}[/cyan] [dim]in {editor[0]} — save & close to continue…[/dim]")
    try:
        subprocess.run([*editor, str(path)])
        return True
    except Exception as exc:
        console.print(f"  [red]couldn't launch an editor ({exc}). Edit this file by hand:[/red] {path}")
        return False


def _ensure_home_env() -> Path:
    """Create <home>/.env from the template if missing; return its path."""
    env_file = _anet_paths.env_path()
    if not env_file.exists():
        try:
            env_file.parent.mkdir(parents=True, exist_ok=True)
            env_file.write_text(_ENV_TEMPLATE, encoding="utf-8")
        except OSError:
            pass
    return env_file


def _apply_config_change() -> None:
    """Drop cached config and flag an engine rebuild so edits take effect next turn."""
    global _force_reload
    try:
        from anet.core.config_loader import reset_cache
        reset_cache()
    except Exception:
        pass
    _force_reload = True


def _edit_yaml(path: Path, label: str) -> None:
    """Open a YAML file in the editor, then validate it and trigger a reload."""
    if not path.exists():
        console.print(f"  [yellow]{label} not found:[/yellow] {path}\n")
        return
    if not _open_in_editor(path):
        return
    try:
        import yaml
        yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        console.print(f"  [red]⚠ {path.name} has a YAML error — changes saved but may not load:[/red]\n    {exc}")
        console.print(f"  [dim]re-open to fix it.[/dim]\n")
        return
    _apply_config_change()
    console.print(f"  [green]{label} updated[/green] — applied on your next message.\n")


def _setup_anet_home(interactive: bool = True) -> None:
    """Resolve the home dir (prompting once on first run) and point the session
    and profile globals at it."""
    global _MEMORY_DIR, _SHARED_DB_PATH, _LAST_SESSION_FILE

    home = _anet_paths.configured_home()
    first_run = home is None
    if first_run:
        home = _prompt_for_home() if interactive else _anet_paths.DEFAULT_HOME

    sessions = home / "sessions"
    try:
        sessions.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    _MEMORY_DIR        = sessions
    _SHARED_DB_PATH    = sessions / "conversations.db"
    _LAST_SESSION_FILE = sessions / "last_session.txt"

    # Create <home>/.env (if missing) and load it, so API keys live in one stable
    # place regardless of where `anet` is launched. Shell env still wins (override=False).
    try:
        from dotenv import load_dotenv as _ldenv
        _ldenv(_ensure_home_env(), override=False)
    except Exception:
        pass

    if first_run and interactive:
        _seed_and_migrate(home)

    # Seed the workspace (config + ExTools/ExAgents/mcps/skills) from bundled
    # templates, or migrate an existing clone's content — idempotent, so it only
    # fills in what's missing and never clobbers the user's edits.
    try:
        from anet.core.workspace import ensure_workspace
        seeded = ensure_workspace()
        if seeded:
            console.print(f"  [dim]pack ready in {_anet_paths.workspace_root()} — seeded: {', '.join(seeded)}[/dim]")
    except Exception as exc:
        print(f"[setup] workspace seeding failed: {exc}", file=sys.stderr)

    # Fold any legacy per-session checkpoint.db files into the shared db.
    _migrate_per_session_dbs()


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

    # Resolve where Anet stores user data (prompts once on first run).
    # --list-sessions stays non-interactive: it just reads from the default/
    # configured home without triggering setup.
    _setup_anet_home(interactive=not args.list_sessions)

    # ── --list-sessions ───────────────────────────────────────────────────────
    if args.list_sessions:
        _list_sessions_cmd()
        return


    enabled_agents = [a for a in AGENTS if a.get("enabled", False)]
    if not enabled_agents:
        console.print("[red]No enabled agents found in agents_config.py. Exiting.[/red]")
        sys.exit(1)

    # Warn about missing provider keys now that the home .env is loaded.
    _check_api_keys()

    tool_map = load_tools()
    _check_optional_deps()

    # Warm up long-term memory in the background: the first mem0 call builds the
    # Chroma store and downloads the fastembed model (~130 MB, one time). Doing it
    # off the critical path here means it's ready before the first recall instead
    # of freezing mid-conversation. Fire-and-forget; failures disable memory quietly.
    async def _prewarm_memory() -> None:
        try:
            from anet.core import memory_store
            await asyncio.to_thread(memory_store.get_memory)
        except Exception:
            pass
    asyncio.create_task(_prewarm_memory())

    # ── Resolve session ───────────────────────────────────────────────────────
    thread_id, session_label = _resolve_session(args)
    _save_last_session(thread_id)

    # All sessions share one db, keyed by thread_id. Each session keeps a
    # subfolder (memory/<session_id>/) for metadata only (title.txt).
    session_dir = _MEMORY_DIR / thread_id
    session_dir.mkdir(parents=True, exist_ok=True)

    async with ConversationStore(str(_SHARED_DB_PATH)) as store:
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
            _exagents_dir = _anet_paths.exagents_dir()
            for agent in ex_agents:
                env_file = _exagents_dir / agent["name"] / ".env"
                if env_file.exists():
                    from dotenv import load_dotenv as _ldenv
                    _ldenv(env_file, override=True)

            # ── 4. Merge all tools ────────────────────────────────────────────
            combined_tools = {**tool_map, **ex_tools}

            # ── 5. Apply extra_tools/mcp/task_types to built-ins ──────────────
            # Sources: anet.config.yaml (user-managed) + exanet.config.yaml
            # `attach:` (written by the smiths, which never touch anet.config.yaml).
            extra_map = get_extra_for_builtins()
            for _name, _att in get_builtin_attachments().items():
                _e = extra_map.setdefault(_name, {"tools": [], "mcp": [], "task_types": []})
                _e["tools"] = list(_e.get("tools") or []) + _att.get("tools", [])
                _e["mcp"]   = list(_e.get("mcp") or []) + _att.get("mcp", [])
            merged_builtins = [dict(a) for a in enabled_agents]
            for agent in merged_builtins:
                extra = extra_map.get(agent["name"], {})
                for t in extra.get("tools", []):
                    if t not in agent["tools"]:
                        agent["tools"] = agent["tools"] + [t]
                for tt in extra.get("task_types", []):
                    if tt not in agent.get("task_types", []):
                        agent["task_types"] = agent.get("task_types", []) + [tt]
                if extra.get("mcp"):
                    agent["mcp"] = list(agent.get("mcp") or []) + extra["mcp"]

            # ── 6. All agents combined ────────────────────────────────────────
            all_agents = merged_builtins + ex_agents

            # ── 7. Connect MCP servers for every agent that needs them ────────
            mcp_tools = await load_mcp_tools_for_agents(all_agents)
            combined_tools.update(mcp_tools)

            # ── 7.5 Auto-inject always-on tools into every agent ──────────────
            # ask_user (and anything in _ALWAYS_TOOLS) is useful to every agent,
            # so it's added here rather than listed in each agent's config —
            # newly added agents pick it up automatically.
            for agent in all_agents:
                tools = list(agent.get("tools") or [])
                for t in _ALWAYS_TOOLS:
                    if t in combined_tools and t not in tools:
                        tools.append(t)
                agent["tools"] = tools

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
            mtime = _ex_config_file().stat().st_mtime if _ex_config_file().exists() else 0.0
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
                    # Rebuild when the active pack's exanet.config.yaml changes
                    # (smith edits) OR when /changepack switched the active pack.
                    global _force_reload
                    try:
                        new_mtime = _ex_config_file().stat().st_mtime if _ex_config_file().exists() else 0.0
                    except OSError:
                        new_mtime = 0.0
                    if new_mtime != mtime or _force_reload:
                        mtime = new_mtime
                        _force_reload = False
                        cur_agents, cur_tools, mgr_tools2, n2 = await _merge_all()
                        engine_box[0] = Engine(cur_agents, cur_tools, manager_tools=mgr_tools2)
                        console.print(f"[dim]  ✓ pack loaded — {n2} external agent(s) active[/dim]")

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


def run_cli() -> None:
    """Synchronous console entry point (used by the `anet` command and the root
    main.py dev shim). Wraps the async main() in asyncio.run."""
    asyncio.run(main())


if __name__ == "__main__":
    run_cli()
