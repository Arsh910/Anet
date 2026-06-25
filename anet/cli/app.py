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
from anet.core.OldEngine.engine import Engine, _manager_client as _engine_manager_client
from anet.core.store import ConversationStore
from anet.core.context import on_status as _status_var, on_token as _token_var, on_confirm as _confirm_var, on_output as _output_var, on_ask as _ask_var, on_cancel as _cancel_var, on_notice as _notice_var
from anet.core.config_loader import agent_overrides as _agent_overrides, manager_config as _manager_config
from anet.core.ex_loader import load_ex_tools, load_ex_agents, get_extra_for_builtins, get_builtin_attachments
from anet.core.mcp_loader import load_mcp_tools_for_agents

# exanet.config.yaml lives in the workspace (Anet home); resolved at runtime so
# the hot-reload watcher follows the real file, not a stale repo-root path.
def _ex_config_file() -> Path:
    return _anet_paths.exanet_path()

# The universal baseline auto-added to every agent now lives in
# anet.AnetTools.toolsets.COMMON (expanded via expand_tools at load time).

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
        console.print("  [dim]→ run [accent]/keys[/accent] to set your API keys.[/dim]")

# Console carries the active theme's named styles ("accent", "assistant"), so all
# [accent]…/border_style="accent" markup recolors when the theme changes. The theme
# is per-pack, so it's (re)applied after the home/pack is resolved and on /changepack.
from anet.cli import theme as _theme
console = Console(theme=_theme.rich_theme())
_theme_applied = False


def _activate_theme_styles(name: str | None = None) -> None:
    """Make `name` (or the active pack's theme) the live console theme. Keeps the
    theme stack at base + 1 so repeated switches don't pile up."""
    global _theme_applied
    try:
        if _theme_applied:
            console.pop_theme()
            _theme_applied = False
        console.push_theme(_theme.rich_theme(name))
        _theme_applied = True
    except Exception:
        pass


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

# Working animation — a left↔right "pulse". Frames + interval live here so they're
# easy to swap / make theme-driven later.
_ANIM_FRAMES   = ["●∙∙", "∙●∙", "∙∙●", "∙●∙"]
_ANIM_INTERVAL = 0.12   # seconds per frame
_ANIM_STYLE    = "accent"  # resolves to the active theme's accent via the console theme


class _Anim:
    """A persistent, self-timing line animation. The frame is derived from the console
    clock on EACH render, so it advances even though the object is reused across
    renders — which is the fix for the frozen indicator (rich re-zeroes a freshly
    constructed Spinner every frame, so the old code never animated)."""

    def __init__(self, frames: list[str], interval: float, style: str = "cyan") -> None:
        self.frames   = frames
        self.interval = interval
        self.style    = style
        self.payload: Text = Text("")

    def __rich_console__(self, console, options):
        i = int(console.get_time() / self.interval) % len(self.frames)
        line = Text("  ")
        line.append(self.frames[i], style=self.style)
        line.append("  ")
        line.append_text(self.payload)
        yield line


class _LiveStatus:
    """Rich renderable: rolling step log + animated indicator + elapsed time."""

    def __init__(self) -> None:
        self._current = "Thinking..."
        self.log: list[str] = []
        self.reply = ""              # streamed synthesis tokens (live preview)
        self._start = time.monotonic()
        # Persisted across renders so the animation actually advances.
        self._anim = _Anim(_ANIM_FRAMES, _ANIM_INTERVAL, _ANIM_STYLE)

    def update(self, msg: str) -> None:
        self._current = msg
        self.log.append(msg)

    def add_token(self, delta: str) -> None:
        self.reply += delta

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

        # ── Current step: animated indicator + elapsed ────────────────────────
        # Build the payload as a Text (NOT markup) so a status line containing
        # brackets — e.g. "2 steps [parallel]" — never breaks rendering.
        payload = Text(self._current or "")
        payload.append(f"   {elapsed_str}", style="dim")
        try:
            from anet.core import tokens as _tok
            _u = _tok.current()
            if _u and _u.total:
                payload.append(f"  ↑ {_tok.fmt(_u.total)} tok", style="dim")
        except Exception:
            pass
        self._anim.payload = payload
        parts.append(self._anim)

        # ── Streaming reply (synthesis tokens arriving live) ──────────────────
        if self.reply:
            parts.append(Text(""))
            tail = self.reply[-600:]   # show the growing tail; full reply lands in the panel after
            parts.append(Text(tail, style="assistant"))

        return Group(*parts)


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


async def _prepare_memory() -> None:
    """Initialise long-term memory under a single clean status line. On first run
    this downloads the local embedding model (~130 MB, one-time); afterwards it's a
    fast cached load. All the underlying library download/warning noise is silenced
    in memory_store, so the user sees only this."""
    import time
    try:
        from anet.core import memory_store
    except Exception:
        return
    t0 = time.time()
    try:
        with console.status("[dim]preparing long-term memory…[/dim]", spinner="dots"):
            ready = await asyncio.to_thread(memory_store.get_memory)
    except Exception:
        return
    # Only say anything if a real (slow) download happened — a cached load is silent.
    if ready is not None and time.time() - t0 > 6:
        tip = "" if os.getenv("HF_TOKEN") else \
            "  [yellow]tip:[/yellow] [dim]set HF_TOKEN for faster model downloads[/dim]"
        console.print(f"  [dim]✓ long-term memory ready[/dim]{tip}\n")


def _print_startup_summary(enabled_agents: list[dict], tool_map: dict) -> None:
    from anet.core.mcp_loader import _connections as _mcp_connections

    regular_tools, _ = _split_tools(tool_map)

    console.print()
    try:
        from anet.cli.banner import show_banner
        _bannered = show_banner(console, _ASSISTANT_NAME.upper(),
                                gradient=_theme.banner_stops())
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
    ("/sessions", "List sessions; /sessions <number> to switch"),
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
    ("/settings", "Edit config — keys, models, engine, tools/agents, a prompt, persona, or theme"),
    ("/theme",    "Pick a color theme"),
    ("/keys",     "Shortcut to set your API keys (also under /settings)"),
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


_CANCEL = object()       # returned by a cancellable prompt when the user presses Esc
_esc_cancels = False     # toggled per-prompt by _read_input(cancellable=True)


def _make_prompt_session():
    """Build a prompt_toolkit session with context-aware ESC + slash autocomplete."""
    kb = KeyBindings()

    @kb.add("escape", eager=True)
    def _esc(event):
        # In a guided sub-prompt (cancellable) Esc aborts and goes back; on the main
        # prompt it just clears the current line.
        if _esc_cancels:
            event.app.exit(result=_CANCEL)
        else:
            event.current_buffer.reset()

    return PromptSession(
        key_bindings=kb,
        completer=_SlashCompleter(),
        complete_while_typing=True,
        enable_open_in_editor=False,
    )


_pt_session: "PromptSession | None" = None


async def _read_input(prompt_text: str, cancellable: bool = False) -> str:
    """Read one line of input. `cancellable=True` makes Esc abort the prompt and
    return "" (used by guided sub-prompts so Esc goes back); otherwise Esc clears the
    line. Paste-safe when prompt_toolkit is available."""
    global _pt_session, _esc_cancels
    if _HAS_PT:
        if _pt_session is None:
            _pt_session = _make_prompt_session()
        _esc_cancels = cancellable
        try:
            result = await _pt_session.prompt_async(prompt_text)
        finally:
            _esc_cancels = False
        return "" if result is _CANCEL else result
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
            console.print(f"  [bold accent]┌─ Permission required[/bold accent]")
            console.print(f"  [accent]│[/accent]  {summary}")
            console.print(f"  [accent]└─[/accent] [dim]{choices}[/dim]")

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


def _print_notice(text: str) -> None:
    """Print a short, persistent note above the spinner (stays after it advances)."""
    console.print(f"  [accent]▶[/accent] [dim]{text}[/dim]")


def _make_ask_fn(live: "Live") -> callable:
    """Returns an ask-user callback that pauses the spinner, shows a clarifying
    question (with optional numbered choices), and returns the user's answer.
    Serialized with a Lock so concurrent agents never interleave prompts."""
    _lock = asyncio.Lock()

    async def _ask(question: str, options: list | None = None) -> str:
        options = options or []
        async with _lock:
            live.stop()
            try:
                # 2+ options → an inline checkbox menu (pick one OR several, e.g. the
                # smiths' "attach to which agents?"). A single option is really a yes/no,
                # and 0 options is free text — both use the plain prompt below (more
                # robust; avoids a one-row checkbox grid over the spinner). Esc cancels.
                if len(options) >= 2:
                    _pause_esc_watcher()
                    try:
                        chosen = await _inline_multiselect(
                            _fit(question, 120), [(o, _fit(o)) for o in options],
                            subtitle="Space toggles · Enter confirms (pick one or several)")
                    finally:
                        _resume_esc_watcher()
                    if chosen is not _SENTINEL:
                        return "" if chosen is None else ", ".join(chosen)
                    # menu unavailable → fall through to the text prompt

                console.print()
                console.print("  [bold yellow]┌─ Anet needs your input[/bold yellow]")
                console.print(f"  [yellow]│[/yellow]  {question}")
                for i, opt in enumerate(options, 1):
                    console.print(f"  [yellow]│[/yellow]    [bold]{i}.[/bold] {opt}")
                hint = "type your answer, or numbers to pick (e.g. 1,3)" if options else "type your answer"
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

                raw = (raw or "").strip()
                # Numbers (single or a "1,3" list) select options.
                if options and raw:
                    picks = [options[int(x) - 1] for x in raw.replace(",", " ").split()
                             if x.isdigit() and 1 <= int(x) <= len(options)]
                    if picks:
                        return ", ".join(picks)
                return raw
            finally:
                console.print()
                live.start()

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

    def __init__(self, cancel_event: "asyncio.Event",
                 open_shell_event: "asyncio.Event | None" = None) -> None:
        from prompt_toolkit.input import create_input
        from prompt_toolkit.keys import Keys
        self._Keys       = Keys
        self._inp        = create_input()
        self._cancel     = cancel_event
        self._open_shell = open_shell_event
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
            elif kp.key == self._Keys.ControlO and self._open_shell is not None:
                # Ctrl+O opens the live shell view — only meaningful while a shell
                # command is actually running, else it's a harmless no-op.
                from anet.core import shell_session
                if shell_session.get_active() is not None:
                    self._open_shell.set()

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


async def _show_shell_view(live: "Live", session) -> None:
    """Pause the spinner and open a live view of the running shell command: stream
    its output, and let the user type a line that is piped to the command's stdin
    (so a prompt like Playwright's 'proceed?' can be answered). `:q` closes the view;
    the command keeps running either way."""
    live.stop()
    session.viewing = True
    console.print()
    console.print(f"  [bold accent]┌─ shell[/bold accent]  [dim]{session.command}[/dim]")
    console.print(
        "  [accent]│[/accent]  [dim]live output below · type input + Enter to send to the "
        "command · [bold]:q[/bold] to close (command keeps running)[/dim]"
    )
    console.print("  [accent]└─[/accent]")

    shown = 0

    async def _stream() -> None:
        nonlocal shown
        notified_done = False
        while True:
            buf = session.snapshot()
            if len(buf) > shown:
                sys.stdout.write(buf[shown:]); sys.stdout.flush()
                shown = len(buf)
            if session.done and not notified_done:
                notified_done = True
                sys.stdout.write(
                    f"\n[command finished — exit {session.exit_code} · press :q or Enter to return]\n"
                )
                sys.stdout.flush()
            await asyncio.sleep(0.12)

    _pause_esc_watcher()
    streamer = asyncio.create_task(_stream())
    try:
        from prompt_toolkit.patch_stdout import patch_stdout
        with patch_stdout():
            while True:
                line = await _pt_session.prompt_async("  shell> ")
                if line is None or session.done:
                    break
                if line.strip() in (":q", ":quit", ":close"):
                    break
                await session.write_input(line)
    except Exception as exc:
        console.print(f"  [red]shell view error: {exc}[/red]")
    finally:
        streamer.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await streamer
        session.viewing = False
        _resume_esc_watcher()
        console.print()
        live.start()


async def _shell_view_loop(live: "Live", open_shell: "asyncio.Event") -> None:
    """While a turn runs, open the shell view each time Ctrl+O is pressed."""
    while True:
        await open_shell.wait()
        open_shell.clear()
        from anet.core import shell_session
        session = shell_session.get_active()
        if session is not None:
            with contextlib.suppress(Exception):
                await _show_shell_view(live, session)


async def _run_turn_with_esc(engine, thread_id, store, user_input, cancel_event, live=None):
    """Run one turn while watching for ESC. Returns (result, stopped: bool).

    Two-tier stop: ESC sets `cancel_event` (cooperative — the engine/orchestrator
    stop at their next safe checkpoint, so any in-flight tool finishes first); if the
    turn doesn't wind down within a short grace period, it is hard-cancelled.

    While running, Ctrl+O opens a live view of the active shell command (if any).
    """
    global _active_esc_watcher
    run_task = asyncio.create_task(engine.run_turn(thread_id, store, user_input))

    open_shell = asyncio.Event()
    watcher = None
    if _HAS_PT:
        try:
            watcher = _EscWatcher(cancel_event, open_shell)
            watcher.start()
            _active_esc_watcher = watcher
        except Exception:
            watcher = None
            _active_esc_watcher = None

    if watcher is None:
        # No ESC support (prompt_toolkit unavailable) — Ctrl+C still interrupts.
        return (await run_task, False)

    # Background task that opens the shell view on Ctrl+O; only active if we have a
    # live spinner to hand the screen back and forth with.
    shell_loop = (
        asyncio.create_task(_shell_view_loop(live, open_shell)) if live is not None else None
    )

    esc_wait = asyncio.create_task(cancel_event.wait())
    try:
        done, _ = await asyncio.wait({run_task, esc_wait}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        _active_esc_watcher = None
        watcher.stop()
        if shell_loop is not None:
            shell_loop.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await shell_loop

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

  [bold accent]/new[/bold accent]                  Start a fresh session (clears history)
  [bold accent]/sessions[/bold accent] [dim]<number?>[/dim]   List saved sessions; with a number, switch to it
  [bold accent]/agents[/bold accent]               Show loaded agents and their tools
  [bold accent]/tools[/bold accent]                Show loaded tools
  [bold accent]/mcps[/bold accent]                 Show connected MCP servers and their tools
  [bold accent]/forget[/bold accent]               Drop oldest messages, keep last 20
  [bold accent]/compress[/bold accent]             Summarise old messages into one block
  [bold accent]/profile[/bold accent]              Show what Anet remembers about you
  [bold accent]/skills[/bold accent]               List all saved skills
  [bold accent]/newtool[/bold accent] [dim]<path>[/dim]       ToolSmith: scaffold + register an ExTool from code
  [bold accent]/newagent[/bold accent] [dim]<desc>[/dim]      AgentSmith: design + register a new agent
  [bold accent]/addmcp[/bold accent] [dim]<path>[/dim]        MCPSmith: draft + register an MCP server from its docs
  [bold accent]/mcptest[/bold accent] [dim]<name>[/dim]       Connect-test an MCP server and list its tools
  [bold accent]/changepack[/bold accent] [dim]<name>[/dim]    Switch the active pack (workspace) — lists packs if no name
  [bold accent]/settings[/bold accent]             Edit config — keys · models · engine · tools/agents · a prompt · persona · theme (arrow-key menu)
  [bold accent]/theme[/bold accent]                Pick a color theme (arrow-key menu)
  [bold accent]/keys[/bold accent]                 Shortcut straight to your API keys (also under /settings)
  [bold accent]/packsmith new[/bold accent] [dim]<name>[/dim]     Create a blank pack in yourpacks/ and switch to it
  [bold accent]/packsmith share[/bold accent] [dim]<path?>[/dim]  Bundle a pack into a shareable zip (secrets stripped)
  [bold accent]/packsmith add[/bold accent] [dim]<zip>[/dim]      Install a received pack into shared_packs/
  [bold accent]/clear[/bold accent]                Clear the screen
  [bold accent]/help[/bold accent]                 Show this message

  [bold accent]Ctrl+O[/bold accent]                View the running shell command live and answer any prompt
  [bold accent]ESC[/bold accent]                   Stop the running task and return to the prompt
  [bold accent]exit[/bold accent] [dim]or[/dim] [bold accent]quit[/bold accent]           End the session
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
    console.print(Panel(t, title="[bold]Loaded Agents[/bold]", border_style="assistant"))
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

    # Resolve the target: an explicit arg (name or number), else an inline picker.
    target = None
    choice = arg.strip()
    if choice:
        if choice.isdigit() and 1 <= int(choice) <= len(packs):
            target = packs[int(choice) - 1]
        elif choice in packs:
            target = choice
        else:
            console.print(f"  [yellow]unknown pack:[/yellow] {choice}\n")
            return
    else:
        options = [
            (p, f"{p}   ({_anet_paths.pack_kind(p)}){'   ← active' if p == active else ''}")
            for p in packs
        ]
        target = await _select_menu("Switch pack", options, current=active,
                                    subtitle="Select the workspace to use.")

    if not target:
        console.print("  [dim]no change[/dim]\n")
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
    # Adopt the new pack's theme so the colors switch with the pack.
    _activate_theme_styles()
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
                      f"so it isn't editable here. Create your own with [accent]/newagent[/accent].\n")
    else:
        console.print(f"\n  [yellow]No editable prompt found for '{name}'[/yellow] at {prompt_file}.\n"
                      f"  [dim]It must be a pack ExAgent with a prompt_file. See /agents.[/dim]\n")


async def _open_keys() -> None:
    env_file = _ensure_home_env()
    if _open_in_editor(env_file):
        try:
            from dotenv import load_dotenv as _ldenv
            _ldenv(env_file, override=True)   # picked up on the next model call
            console.print("  [green]keys reloaded[/green] — take effect on your next message.\n")
        except Exception:
            console.print("  [dim]saved — restart to apply.[/dim]\n")


async def _select_menu(title: str, options: list[tuple[str, str]],
                       current: str | None = None, subtitle: str = "") -> str | None:
    """An INLINE selection menu (not a full-screen dialog): renders in the normal
    scroll flow below the prompt, like the slash-command suggestions — a ▶ cursor,
    ↑/↓ to move, Enter to confirm, Esc/q to cancel, clickable rows, and it scrolls
    when the list is long. `options` is [(value, label)]. Returns the chosen value or
    None. Falls back to a numbered prompt if prompt_toolkit isn't usable."""
    sel = await _inline_select(title, options, current=current, subtitle=subtitle)
    if sel is not _SENTINEL:
        return sel
    # Fallback: numbered prompt
    console.print(f"\n  [bold]{title}[/bold]")
    for i, (_v, label) in enumerate(options, 1):
        console.print(f"  [accent]{i}[/accent]  {label}")
    try:
        raw = (await _read_input("  > ", cancellable=True)).strip()
    except (EOFError, KeyboardInterrupt):
        return None
    return options[int(raw) - 1][0] if raw.isdigit() and 1 <= int(raw) <= len(options) else None


_SENTINEL = object()   # distinguishes "inline menu unavailable" from "user cancelled"


def _fit(s: str, n: int = 100) -> str:
    """One-line, length-capped label for a menu row — so a stray huge string (e.g. a
    base64 data: URI an agent passes as an option) can't blow up / garble the menu."""
    s = " ".join(str(s or "").split())   # collapse newlines/runs of whitespace
    return s if len(s) <= n else s[: n - 1] + "…"


async def _inline_select(title, options, current=None, subtitle=""):
    """Inline arrow-key/click list selector via a non-fullscreen prompt_toolkit app.
    Returns the selected value, None if cancelled, or _SENTINEL if it can't run (so
    the caller falls back)."""
    if not _HAS_PT:
        return _SENTINEL
    try:
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout
        from prompt_toolkit.layout.containers import Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.layout.dimension import Dimension
        from prompt_toolkit.data_structures import Point
        from prompt_toolkit.mouse_events import MouseEventType
    except Exception:
        return _SENTINEL

    sel = [0]
    for i, (v, _l) in enumerate(options):
        if v == current:
            sel[0] = i
            break
    header_lines = 2 + (1 if subtitle else 0)   # title (+subtitle) + blank line

    def render():
        frags = [("bold", f"  {_fit(title)}\n")]
        if subtitle:
            frags.append(("class:muted", f"  {_fit(subtitle)}\n"))
        frags.append(("", "\n"))
        for i, (_v, label) in enumerate(options):
            def _click(mouse_event, i=i):
                if mouse_event.event_type == MouseEventType.MOUSE_UP:
                    sel[0] = i
                    app.exit(result=options[i][0])
            if i == sel[0]:
                frags.append(("class:sel", f"  ▶ {_fit(label)}\n", _click))
            else:
                frags.append(("", f"    {_fit(label)}\n", _click))
        frags.append(("", "\n"))
        frags.append(("class:muted", "  Enter to confirm · Esc to cancel"))
        return frags

    control = FormattedTextControl(
        render, focusable=True, show_cursor=False,
        get_cursor_position=lambda: Point(x=0, y=header_lines + sel[0]),
    )
    total = header_lines + len(options) + 2
    window = Window(control, height=Dimension(min=1, preferred=total, max=16),
                    always_hide_cursor=True)

    kb = KeyBindings()
    @kb.add("up")
    def _(e): sel[0] = (sel[0] - 1) % len(options)
    @kb.add("down")
    def _(e): sel[0] = (sel[0] + 1) % len(options)
    @kb.add("enter")
    def _(e): e.app.exit(result=options[sel[0]][0])
    @kb.add("escape")
    @kb.add("c-c")
    @kb.add("q")
    def _(e): e.app.exit(result=None)

    try:
        from prompt_toolkit.styles import Style
        acc = _theme.pt_accent()
        style = Style.from_dict({
            "sel":   (f"bold {acc}" if acc else "bold reverse"),  # selected row → theme accent
            "muted": "#888888",
        })
        app = Application(layout=Layout(window), key_bindings=kb, style=style,
                          full_screen=False, mouse_support=True, erase_when_done=True)
        return await app.run_async()
    except Exception:
        # Any construction/run failure (e.g. no real console) → caller falls back.
        return _SENTINEL


async def _inline_multiselect(title, options, subtitle=""):
    """Inline CHECKBOX selector: Space toggles, Enter confirms, Esc cancels; clickable
    + scrollable. Returns a list of selected values, None if cancelled, or _SENTINEL if
    it can't run. Used where the user may pick SEVERAL (e.g. the smiths' attach step)."""
    if not _HAS_PT:
        return _SENTINEL
    try:
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout
        from prompt_toolkit.layout.containers import Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.layout.dimension import Dimension
        from prompt_toolkit.data_structures import Point
        from prompt_toolkit.mouse_events import MouseEventType
        from prompt_toolkit.styles import Style
    except Exception:
        return _SENTINEL

    sel = [0]
    checked: set = set()
    header_lines = 2 + (1 if subtitle else 0)

    def render():
        frags = [("bold", f"  {_fit(title)}\n")]
        if subtitle:
            frags.append(("class:muted", f"  {_fit(subtitle)}\n"))
        frags.append(("", "\n"))
        for i, (v, label) in enumerate(options):
            def _click(mouse_event, i=i):
                if mouse_event.event_type == MouseEventType.MOUSE_UP:
                    sel[0] = i
                    val = options[i][0]
                    checked.discard(val) if val in checked else checked.add(val)
            box = "[x]" if v in checked else "[ ]"
            cur = "▶ " if i == sel[0] else "  "
            cls = "class:sel" if i == sel[0] else ("class:on" if v in checked else "")
            frags.append((cls, f"  {cur}{box} {_fit(label)}\n", _click))
        frags.append(("", "\n"))
        frags.append(("class:muted", "  Space toggles · Enter confirms · Esc cancels"))
        return frags

    control = FormattedTextControl(
        render, focusable=True, show_cursor=False,
        get_cursor_position=lambda: Point(x=0, y=header_lines + sel[0]),
    )
    total = header_lines + len(options) + 2
    window = Window(control, height=Dimension(min=1, preferred=total, max=16),
                    always_hide_cursor=True)

    kb = KeyBindings()
    @kb.add("up")
    def _(e): sel[0] = (sel[0] - 1) % len(options)
    @kb.add("down")
    def _(e): sel[0] = (sel[0] + 1) % len(options)
    @kb.add("space")
    def _(e):
        v = options[sel[0]][0]
        checked.discard(v) if v in checked else checked.add(v)
    @kb.add("enter")
    def _(e): e.app.exit(result=[v for v, _l in options if v in checked])
    @kb.add("escape")
    @kb.add("c-c")
    def _(e): e.app.exit(result=None)

    try:
        acc = _theme.pt_accent()
        style = Style.from_dict({
            "sel": (f"bold {acc}" if acc else "bold reverse"),
            "on":  (acc or "bold"),
            "muted": "#888888",
        })
        app = Application(layout=Layout(window), key_bindings=kb, style=style,
                          full_screen=False, mouse_support=True, erase_when_done=True)
        return await app.run_async()
    except Exception:
        return _SENTINEL


def _apply_theme(name: str) -> None:
    """Save the theme into THIS pack's anet.config.yaml and live-apply it. Accent/
    borders/animation recolor on the next render; the banner recolors on next launch
    (or /clear redraws the summary)."""
    if name not in _theme.PRESETS:
        console.print(f"  [yellow]Unknown theme '{name}'.[/yellow]\n"); return
    saved = _theme.set_active(name)            # writes the pack's anet.config.yaml
    _activate_theme_styles(name)               # swap style resolution live
    where = "saved to this pack" if saved else "applied for this session only (couldn't write the pack config)"
    console.print(f"\n  [accent]●[/accent] theme set to [bold accent]{name}[/bold accent] "
                  f"[dim]— {where}; restart or /clear to recolor the banner.[/dim]\n")


async def _cmd_theme(arg: str) -> None:
    """Pick a color theme from an arrow-key menu (or /theme <name> directly)."""
    name = (arg or "").strip().lower()
    if name in _theme.PRESETS:
        _apply_theme(name); return
    current = _theme.active_name()
    options = [
        (n, f"{n}{'   ← current' if n == current else ''}")
        for n in _theme.NAMES
    ]
    chosen = await _select_menu("Theme & colors", options, current=current,
                                subtitle="Select a theme to apply.")
    if chosen:
        _apply_theme(chosen)
    else:
        console.print("  [dim]cancelled.[/dim]\n")


def _current_engine_mode() -> str:
    try:
        from anet.core.config_loader import load as _cfgload
        return ((_cfgload().get("orchestration") or {}).get("mode") or "legacy").lower()
    except Exception:
        return "legacy"


def _set_engine_mode(mode: str) -> bool:
    """Persist orchestration.mode into the active pack's anet.config.yaml,
    preserving comments (ruamel). Returns False if the config can't be written."""
    if mode not in ("adaptorch", "legacy"):
        return False
    p = _anet_paths.config_path()
    if p is None or not p.exists():
        return False
    try:
        import io
        from ruamel.yaml import YAML
        y = YAML()
        y.preserve_quotes = True
        data = y.load(p.read_text(encoding="utf-8")) or {}
        orch = data.get("orchestration")
        if not isinstance(orch, dict):
            orch = {}
            data["orchestration"] = orch
        orch["mode"] = mode
        buf = io.StringIO()
        y.dump(data, buf)
        p.write_text(buf.getvalue(), encoding="utf-8")
        return True
    except Exception:
        return False


async def _cmd_engine(arg: str) -> None:
    """Pick the orchestration engine — AdaptOrch (task-adaptive topology) or the
    legacy planner pipeline — and persist it to the pack's anet.config.yaml."""
    current = _current_engine_mode()
    options = [
        ("adaptorch", f"AdaptOrch — task-adaptive topology (decompose → route → execute → synthesize)"
                      f"{'   ← current' if current == 'adaptorch' else ''}"),
        ("legacy",    f"Legacy — single planner → executor → checker → synthesizer"
                      f"{'   ← current' if current == 'legacy' else ''}"),
    ]
    name = (arg or "").strip().lower()
    if name not in ("adaptorch", "legacy"):
        name = await _select_menu("Orchestration engine", options, current=current,
                                  subtitle="Which orchestration engine should Anet use?")
    if not name:
        console.print("  [dim]cancelled.[/dim]\n"); return
    if name == current:
        console.print(f"\n  [dim]Already using[/dim] [bold accent]{name}[/bold accent].\n"); return
    if _set_engine_mode(name):
        _apply_config_change()
        console.print(f"\n  [accent]●[/accent] engine set to [bold accent]{name}[/bold accent] "
                      f"[dim]— rebuilds on your next message.[/dim]\n")
    else:
        console.print("  [yellow]Couldn't write the pack config.[/yellow]\n")


async def _cmd_settings(arg: str) -> None:
    """One entry point for configuration — an arrow-key menu. Folds in the old /keys,
    /editpack, /editagent commands plus theme selection."""
    options = [
        ("keys",   "API keys              (~/.anet/.env)"),
        ("models", "Models & providers    (anet.config.yaml)"),
        ("engine", "Orchestration engine  (AdaptOrch / legacy)"),
        ("exanet", "Tools & agents        (exanet.config.yaml)"),
        ("agent",  "An agent's prompt     (ExAgents/<name>/prompt.md)"),
        ("soul",   "Persona               (SOUL.md)"),
        ("theme",  "Theme & colors"),
    ]
    valid = {v for v, _ in options}
    choice = (arg or "").strip().lower()
    if choice not in valid:
        choice = await _select_menu("Settings", options,
                                    subtitle="Select what to edit.")
    if not choice:
        console.print("  [dim]cancelled.[/dim]\n"); return

    if choice == "keys":
        await _open_keys()
    elif choice == "models":
        _edit_yaml(_anet_paths.config_path(), "anet.config.yaml (models/providers)")
    elif choice == "engine":
        await _cmd_engine("")
    elif choice == "exanet":
        _edit_yaml(_anet_paths.exanet_path(), "exanet.config.yaml (tools/agents)")
    elif choice == "agent":
        await _pick_and_edit_agent()
    elif choice == "soul":
        soul = _anet_paths.soul_path()
        if not soul.exists():
            console.print(f"  [yellow]SOUL.md not found:[/yellow] {soul}\n"); return
        if _open_in_editor(soul):
            _apply_config_change()
            console.print("  [green]SOUL.md updated[/green] — applied on your next message.\n")
    elif choice == "theme":
        await _cmd_theme("")


async def _pick_and_edit_agent() -> None:
    """List the pack's editable agent prompts and open the chosen one."""
    adir = _anet_paths.exagents_dir()
    agents = sorted(
        d.name for d in adir.iterdir()
        if d.is_dir() and (d / "prompt.md").exists()
    ) if adir.exists() else []
    if not agents:
        console.print("  [dim]No editable agent prompts in this pack. Create one with "
                      "[accent]/newagent[/accent].[/dim]\n")
        return
    console.print("\n  [bold]Which agent's prompt?[/bold]")
    for i, a in enumerate(agents, 1):
        console.print(f"  [accent]{i}[/accent]  {a}")
    try:
        sel = (await _read_input("  > ", cancellable=True)).strip()
    except (EOFError, KeyboardInterrupt):
        console.print(); return
    if sel.isdigit() and 1 <= int(sel) <= len(agents):
        _cmd_editagent(agents[int(sel) - 1])
    else:
        console.print("  [dim]cancelled.[/dim]\n")


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
        f"[accent]/newtool[/accent] · [accent]/newagent[/accent] · [accent]/addmcp[/accent]. "
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


def _ordered_session_ids() -> list[str]:
    """Session ids newest-first — the same order `_print_sessions` numbers them in,
    so `/sessions <number>` and the printed list always agree."""
    return [d.name for d in _list_session_dirs()]


def _resolve_session_arg(arg: str) -> str | None:
    """Resolve a `/sessions <arg>` value (a list number, or an exact session id) to an
    EXISTING session id. Returns None if it matches no saved session — callers must
    NOT silently create one (that's what `/new` is for)."""
    ids = _ordered_session_ids()
    arg = (arg or "").strip()
    if arg.isdigit():
        i = int(arg) - 1
        return ids[i] if 0 <= i < len(ids) else None
    return arg if arg in ids else None


def _print_sessions(current: str | None = None) -> None:
    dirs = _list_session_dirs()
    last = _load_last_session()
    console.print()
    if not dirs:
        console.print("  [dim]No sessions saved yet — just start typing to begin one.[/dim]\n")
        return
    console.print("  [bold]Saved sessions[/bold]  [dim]— /sessions <number> to switch[/dim]")
    for i, d in enumerate(dirs, 1):
        sid    = d.name
        count  = _session_msg_count(sid)
        size   = f"{count} msg" if count else "empty"
        title  = _session_title(d)
        label  = title if title else "(no title yet)"
        marker = ""
        if current and sid == current:
            marker = "  [green]← active[/green]"
        elif sid == last and not current:
            marker = "  [green]← last[/green]"
        console.print(f"  [accent]{i:>2}[/accent]  {label}  [dim]{sid} ({size})[/dim]{marker}")
    console.print()
    console.print("  [dim]/sessions <number>  switch · /new  fresh session[/dim]")
    console.print()


def _list_sessions_cmd() -> None:
    _print_sessions()
    console.print("[dim]Resume with:  python main.py --session <name>[/dim]")
    console.print("[dim]              python main.py --resume[/dim]\n")


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
        os.system("cls" if os.name == "nt" else "clear")
        _print_startup_summary(enabled_agents, tool_map)

    elif command == "/changepack":
        await _cmd_changepack(arg)

    elif command == "/settings":
        await _cmd_settings(arg)

    elif command == "/theme":
        await _cmd_theme(arg)

    elif command == "/keys":
        # Kept as a quick shortcut to the most common task (it's also under /settings).
        await _open_keys()

    elif command == "/new":
        global _session_turn_count, _last_context_prompt_n
        _session_turn_count = 0
        _last_context_prompt_n = 0
        new_id = _new_session_id()
        _save_last_session(new_id)
        (_MEMORY_DIR / new_id).mkdir(parents=True, exist_ok=True)
        config["configurable"]["thread_id"] = new_id
        console.print(f"\n  [dim]New session:[/dim] [bold]{new_id}[/bold]\n")

    elif command == "/sessions":
        current = config["configurable"].get("thread_id")
        sid = None
        if arg:
            # Explicit: /sessions <number-or-id> switches directly.
            sid = _resolve_session_arg(arg)
            if sid is None:
                # Don't silently create an empty session — that was the old bug that
                # looked like "switched but it forgot everything".
                console.print(f"\n  [yellow]No saved session matching '{arg}'.[/yellow] "
                              "Run /sessions to pick one, or [bold]/new[/bold] for a fresh one.\n")
        else:
            ids = _ordered_session_ids()
            if not ids:
                console.print("\n  [dim]No sessions saved yet.[/dim]\n")
            else:
                options = []
                for s in ids:
                    title = _session_title(_MEMORY_DIR / s) or "(untitled)"
                    n = _session_msg_count(s)
                    mark = "   ← active" if s == current else ""
                    options.append((s, f"{title}   [{n} msg]{mark}"))
                sid = await _select_menu("Sessions", options, current=current,
                                         subtitle="Select a session to switch to.")

        if sid and sid != current:
            # (declared global by the /new branch above)
            _session_turn_count = 0
            _last_context_prompt_n = 0
            _save_last_session(sid)
            config["configurable"]["thread_id"] = sid
            n = _session_msg_count(sid)
            console.print(f"\n  [green]Switched to[/green] [bold]{sid}[/bold] "
                          f"[dim]({n} msg — history loaded)[/dim]\n")
        elif sid and sid == current:
            console.print(f"\n  [dim]Already in [bold]{sid}[/bold].[/dim]\n")

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
                console.print(Panel(t, title="[bold]Skills[/bold]", border_style="accent"))
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
                    console.print(Panel(t, title=f"[bold]Memory[/bold] ({len(mems)})", border_style="accent"))
                    console.print("  [dim]/forget <id> via the assistant, or ask it to update what it knows[/dim]\n")
        except Exception as exc:
            console.print(f"\n  [red]Error: {exc}[/red]\n")

    elif command == "/newtool":
        path = arg.strip()
        if not path:
            console.print("\n  [dim]ToolSmith — wrap existing code into an ExTool "
                          "(scaffold · validate · register).[/dim]")
            path = (await _read_input("  path to the tool's source (file or folder): ", cancellable=True)).strip()
        if path:
            await _run_toolsmith(path, tool_map)
        else:
            console.print("  [dim]cancelled.[/dim]\n")

    elif command == "/newagent":
        desc = arg.strip()
        if not desc:
            console.print("\n  [dim]AgentSmith — design a new agent from a description "
                          "(writes its prompt · picks tools/MCP · registers).[/dim]")
            desc = (await _read_input("  describe the agent you want: ", cancellable=True)).strip()
        if desc:
            await _run_agentsmith(desc, tool_map)
        else:
            console.print("  [dim]cancelled.[/dim]\n")

    elif command == "/addmcp":
        src = arg.strip()
        if not src:
            console.print("\n  [dim]MCPSmith — add an MCP server from its repo/README "
                          "(drafts the config · connect-tests · attaches it).[/dim]")
            src = (await _read_input("  path to the MCP repo/README (or package name): ", cancellable=True)).strip()
        if src:
            await _run_mcpsmith(src, tool_map)
        else:
            console.print("  [dim]cancelled.[/dim]\n")

    elif command == "/mcptest":
        name = arg.split()[0] if arg.strip() else ""
        if not name:
            servers = []
            try:
                from anet.core import mcp_loader
                servers = mcp_loader.list_available_servers()
            except Exception:
                pass
            if not servers:
                console.print("\n  [dim]No MCP servers configured in this pack (mcps/).[/dim]\n")
            else:
                name = await _select_menu("Test an MCP server",
                                          [(s, s) for s in servers],
                                          subtitle="Select a server to connect-test.")
        if name:
            await _run_mcp_doctor(name)

    elif command == "/packsmith":
        sub_parts = arg.split(None, 1)
        sub  = sub_parts[0].lower() if sub_parts else ""
        rest = sub_parts[1].strip() if len(sub_parts) > 1 else ""
        if sub not in ("new", "share", "add"):
            sub = await _select_menu("PackSmith", [
                ("new",   "new     — create a blank pack and switch to it"),
                ("share", "share   — bundle a pack into a shareable .zip"),
                ("add",   "add     — install a received pack .zip"),
            ], subtitle="What do you want to do?")
        if sub == "new":
            name = rest.split()[0] if rest else (await _read_input("  pack name: ", cancellable=True)).strip()
            if name:
                await _cmd_packsmith_new(name)
            else:
                console.print("  [dim]cancelled.[/dim]\n")
        elif sub == "share":
            await _run_packsmith("share", rest, tool_map)
        elif sub == "add":
            path = rest or (await _read_input("  path to pack .zip: ", cancellable=True)).strip()
            if path:
                await _run_packsmith("add", path, tool_map)
            else:
                console.print("  [dim]cancelled.[/dim]\n")
        # sub is None → the menu was cancelled; do nothing.

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
    from anet.core.OldEngine import orchestrator

    agent = dict(agent_def)
    name  = agent.get("name", "agent")
    # COMMON baseline + toolset bundles + explicit tools, filtered to what loaded.
    from anet.AnetTools.toolsets import expand_tools as _expand_tools
    agent["tools"] = [t for t in _expand_tools(agent) if t in tool_map]

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
    console.print(f"  [bold accent]{name}[/bold accent] [dim]{banner}[/dim]\n")

    live_status = _LiveStatus()

    def on_status(msg: str) -> None:
        live_status.update(msg)

    global _active_esc_watcher
    cancel_event = asyncio.Event()
    open_shell   = asyncio.Event()
    try:
        with Live(live_status, console=console, refresh_per_second=12, transient=True) as live:
            s_tk = _status_var.set(on_status)
            t_tk = _token_var.set(lambda _: None)
            c_tk = _confirm_var.set(_make_confirm_fn(live))
            o_tk = _output_var.set(_render_diff_panel)
            a_tk = _ask_var.set(_make_ask_fn(live))
            cancel_tk = _cancel_var.set(cancel_event)
            n_tk = _notice_var.set(_print_notice)

            # Ctrl+O shell view (and ESC-to-stop) during a smith run too — this is
            # exactly where a long install that prompts for input tends to happen.
            watcher = shell_loop = None
            if _HAS_PT:
                try:
                    watcher = _EscWatcher(cancel_event, open_shell)
                    watcher.start()
                    _active_esc_watcher = watcher
                    shell_loop = asyncio.create_task(_shell_view_loop(live, open_shell))
                except Exception:
                    watcher = shell_loop = None
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
                _cancel_var.reset(cancel_tk)
                _notice_var.reset(n_tk)
                if watcher is not None:
                    _active_esc_watcher = None
                    watcher.stop()
                if shell_loop is not None:
                    shell_loop.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await shell_loop
    except Exception as exc:
        console.print(f"  [red]{name} error: {exc}[/red]\n")
        return

    text = (result or {}).get("text") or "Done."
    console.print(Panel(Markdown(text), title=f"[bold]{name}[/bold]",
                        border_style="assistant", padding=(1, 2)))
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
    console.print(f"  [bold accent]mcp doctor[/bold accent] [dim]testing[/dim] {name}\n")
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
    from anet.core import tokens as _tokens
    _usage = _tokens.begin()          # fresh token accounting for this turn
    try:
        with Live(live_status, console=console, refresh_per_second=12, transient=True) as live:
            status_tk  = _status_var.set(on_status)
            token_tk   = _token_var.set(live_status.add_token)   # stream synthesis live
            confirm_tk = _confirm_var.set(_make_confirm_fn(live))
            output_tk  = _output_var.set(_render_diff)
            ask_tk     = _ask_var.set(_make_ask_fn(live))
            cancel_tk  = _cancel_var.set(cancel_event)
            notice_tk  = _notice_var.set(_print_notice)
            try:
                result, stopped = await _run_turn_with_esc(
                    engine, thread_id, store, effective_input, cancel_event, live
                )
            finally:
                _status_var.reset(status_tk)
                _token_var.reset(token_tk)
                _confirm_var.reset(confirm_tk)
                _output_var.reset(output_tk)
                _ask_var.reset(ask_tk)
                _cancel_var.reset(cancel_tk)
                _notice_var.reset(notice_tk)

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
        border_style="assistant",
        padding=(1, 2),
    ))
    # Per-turn token usage footer.
    if _usage and _usage.total:
        console.print(
            f"  [dim]Tokens: {_tokens.fmt(_usage.total)} "
            f"(in {_tokens.fmt(_usage.prompt)} · out {_tokens.fmt(_usage.completion)} · "
            f"{_usage.calls} calls)[/dim]"
        )
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


# ── Editor-based config editing (the /settings hub, and the /keys shortcut) ───

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
    console.print(f"  [dim]opening[/dim] [accent]{path}[/accent] [dim]in {editor[0]} — save & close to continue…[/dim]")
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

    # Now that the active pack is resolved, apply ITS theme (per-pack colors) before
    # the banner / startup view render.
    _activate_theme_styles()


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

            # ── 7.5 Resolve each agent's tools: COMMON baseline + toolset
            # bundles + explicit tools (see anet.AnetTools.toolsets). Every agent —
            # built-in or user-added — gets the common baseline so none is helpless;
            # co-used tools travel together via bundles. Filtered to what actually
            # loaded so a bundle naming an unavailable tool is harmless.
            from anet.AnetTools.toolsets import expand_tools as _expand_tools
            for agent in all_agents:
                resolved = _expand_tools(agent)
                agent["tools"] = [t for t in resolved if t in combined_tools]

            # ── Configure spawn_tool with live agents + tools ─────────────────
            try:
                from anet.AnetTools.spawn_tool import configure as _cfg_spawn
                _cfg_spawn(combined_tools, all_agents)
            except Exception:
                pass

            # The manager gets NO direct tools. Memory is handled conversationally:
            # facts the user mentions are captured by background extraction, and recall
            # comes from the profile/memories injected into the planner prompt — so the
            # planner only ever returns a plan or a simple reply, never a (mis)fired
            # tool call. (Giving it memory_tool made it spuriously tool-call non-memory
            # requests and collapse to "Done.".)
            manager_tools: dict = {}
            return all_agents, combined_tools, manager_tools, len(ex_agents)

        def _make_engine(agents, tools, manager_tools):
            """Pick the orchestration engine by `orchestration.mode` (default legacy).
            'adaptorch' → the task-adaptive AdaptOrch coordinator; else the OldEngine."""
            try:
                from anet.core.config_loader import load as _cfgload
                mode = ((_cfgload().get("orchestration") or {}).get("mode") or "legacy").lower()
            except Exception:
                mode = "legacy"
            if mode == "adaptorch":
                from anet.core.orchestration.coordinator import AdaptOrchEngine
                return AdaptOrchEngine(agents, tools, manager_tools=manager_tools)
            return Engine(agents, tools, manager_tools=manager_tools)

        # Initial build
        all_agents, all_tools, manager_tools, n_external = await _merge_all()
        engine = _make_engine(all_agents, all_tools, manager_tools)
        # Reprint summary now that MCP tools have been injected into agent tool lists
        _print_startup_summary(all_agents, all_tools)
        if n_external:
            console.print(f"[dim]  + {n_external} external agent(s) loaded[/dim]\n")

        # Prepare long-term memory up front, under a clean spinner. The first run
        # builds the Chroma store and downloads the fastembed embedding model
        # (~130 MB) — doing it here keeps that one-time work (and its library noise,
        # already silenced in memory_store) off the prompt. Cached after first run.
        await _prepare_memory()

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
                        engine_box[0] = _make_engine(cur_agents, cur_tools, mgr_tools2)
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
            # Tear down MCP subprocesses cleanly BEFORE the loop closes, so their
            # stdio pipes don't get GC'd post-close (proactor errors on Windows).
            try:
                from anet.core import mcp_loader
                await mcp_loader.disconnect_all()
            except Exception:
                pass


def run_cli() -> None:
    """Synchronous console entry point (used by the `anet` command and the root
    main.py dev shim). Wraps the async main() in asyncio.run."""
    asyncio.run(main())


if __name__ == "__main__":
    run_cli()
