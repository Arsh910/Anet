"""
anet CLI — manage ANet plugin agents.

Usage (from project root):
    python cli.py init  <name>          scaffold a new agent folder
    python cli.py validate              check agent.yaml in current directory
    python cli.py connect               register agent in current directory
    python cli.py disconnect [name]     remove an agent from the registry
    python cli.py status                list all connected agents
    python cli.py list-tools            list all tools across connected agents
"""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Force UTF-8 output so checkmark symbols render on Windows terminals
import io as _io
import sys as _sys
_console_file = _io.TextIOWrapper(_sys.stdout.buffer, encoding="utf-8", errors="replace") \
    if hasattr(_sys.stdout, "buffer") else _sys.stdout

# Ensure project root is importable when running `python cli.py ...`
_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from anet.plugin.registry import (
    ANET_DIR, REGISTRY_FILE,
    add_agent, get_agent, list_agents, remove_agent,
)
from anet.plugin.schema import (
    AgentIdentity, AgentManifest, BehaviorConfig, CapabilityConfig,
    ModelConfig, RegistryEntry, ToolDefinition,
)
from anet.plugin.validator import validate_agent
from anet.cli.graph import graph_group

console = Console(file=_console_file)


# ── Templates ─────────────────────────────────────────────────────────────────

def _tmpl_agent_yaml(name: str) -> str:
    return f"""\
# ANet Plugin Manifest
# Run `anet validate` to check this file, then `anet connect` to go live.
# ─────────────────────────────────────────────────────────────────────────

identity:
  name: {name}                  # unique agent name (lowercase + underscores)
  version: 1.0.0
  description: A brief description of what this agent does
  author: ""                    # optional

# The LLM that powers this agent
model:
  name: gemini-2.5-flash        # gemini-2.5-flash | gpt-4o | claude-3-5-sonnet
  provider: google              # google | openai | anthropic | openrouter
  temperature: 0.2
  max_tokens: 2048

# How the planner routes tasks to this agent.
# Be specific — these are matched semantically against user requests.
capabilities:
  task_types:
    - describe task type one
    - describe task type two

# Tool definitions — one entry per tool file.
# Each tool file must export: SCHEMA (dict) and async def run(params: dict) -> dict
tools:
  - name: {name}_tool
    file: tools/{name}_tool.py
    description: What this tool does

# System prompt — choose one of these two options:
prompt:
  file: prompts/system.txt
  # inline: "You are a specialist agent..."

# Optional behavior settings
behavior:
  timeout: 30                   # seconds before task is considered timed out
  can_be_parallelized: true     # can ANet run this alongside other agents?
  requires_confirmation: false  # pause and ask the user before executing?
  execution: sync               # sync = waits for result | async = returns task_id immediately
"""


def _tmpl_tool_py(name: str) -> str:
    return f'''\
# {name}_tool.py — tool implementation for the {name} agent
#
# ANet Tool Contract
# ──────────────────
# SCHEMA  : OpenAI function-calling format (dict). Tells the model what the tool does.
# run()   : async def run(params: dict) -> dict
#           Return  {{"result": ...}}          on success
#           Return  {{"error": "..."}}         on failure
#           ANet normalises the output automatically, so partial compliance is fine.

SCHEMA = {{
    "type": "function",
    "function": {{
        "name": "{name}_tool",
        "description": "TODO: describe what this tool does",
        "parameters": {{
            "type": "object",
            "properties": {{
                "action": {{
                    "type": "string",
                    "enum": ["action_one", "action_two"],
                    "description": "The operation to perform",
                }},
                "input": {{
                    "type": "string",
                    "description": "Primary input for the operation",
                }},
            }},
            "required": ["action"],
        }},
    }},
}}


async def run(params: dict) -> dict:
    action     = params.get("action", "")
    user_input = params.get("input", "")

    if action == "action_one":
        # TODO: implement action_one
        return {{"result": f"action_one received: {{user_input}}"}}

    if action == "action_two":
        # TODO: implement action_two
        return {{"result": f"action_two received: {{user_input}}"}}

    return {{"error": f"Unknown action: {{action}}"}}
'''


def _tmpl_system_txt(name: str) -> str:
    return f"""\
You are {name}, a specialist agent in the ANet network.
Use the {name}_tool to complete tasks.
Always return clear, structured responses.
Never explain what you are going to do — just call the tool and return the result.
"""


def _tmpl_readme(name: str) -> str:
    return f"""\
# {name}

An ANet plugin agent.

## Quick start

```
cd {name}
anet validate
anet connect
```

## Structure

```
{name}/
├── agent.yaml           ← manifest (edit this first)
├── tools/
│   └── {name}_tool.py   ← your tool: SCHEMA + async def run(params) -> dict
├── prompts/
│   └── system.txt       ← agent system prompt
└── README.md
```

## Disconnecting

```
anet disconnect {name}
```
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _print_validation(result) -> None:
    for check in result.checks:
        icon  = "[green]✓[/green]" if check.passed else "[red]✗[/red]"
        label = check.name
        msg   = f"  [dim]{check.message}[/dim]" if check.message else ""
        console.print(f"  {icon}  {label}{msg}")
    if result.warnings:
        console.print()
        for w in result.warnings:
            console.print(f"  [yellow]⚠  {w}[/yellow]")
    if result.errors:
        console.print()
        for e in result.errors:
            console.print(f"  [red]→  {e}[/red]")


def _make_agent_id(name: str) -> str:
    h = hashlib.sha256(name.encode()).hexdigest()[:8]
    return f"did:anet:{h}::{name}"


# ── CLI group ─────────────────────────────────────────────────────────────────

@click.group()
def cli() -> None:
    """ANet — connect agents to the network."""


cli.add_command(graph_group)


# ── anet init ─────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("name")
def init(name: str) -> None:
    """Scaffold a new agent folder."""
    target = Path.cwd() / name
    if target.exists():
        console.print(f"[red]Error: '{name}' already exists.[/red]")
        sys.exit(1)

    (target / "tools").mkdir(parents=True)
    (target / "prompts").mkdir()

    (target / "agent.yaml").write_text(_tmpl_agent_yaml(name), encoding="utf-8")
    (target / "tools" / f"{name}_tool.py").write_text(_tmpl_tool_py(name), encoding="utf-8")
    (target / "prompts" / "system.txt").write_text(_tmpl_system_txt(name), encoding="utf-8")
    (target / "README.md").write_text(_tmpl_readme(name), encoding="utf-8")

    console.print()
    console.rule(f"[bold green]{name}[/bold green] scaffolded")
    console.print()
    console.print(f"  [bold]{target}[/bold]")
    console.print(f"  ├── agent.yaml")
    console.print(f"  ├── tools/{name}_tool.py")
    console.print(f"  ├── prompts/system.txt")
    console.print(f"  └── README.md")
    console.print()
    console.print("  Next steps:")
    console.print(f"  [dim]1.[/dim]  cd {name}")
    console.print(f"  [dim]2.[/dim]  Edit [bold]agent.yaml[/bold] — fill in task_types and description")
    console.print(f"  [dim]3.[/dim]  Edit [bold]tools/{name}_tool.py[/bold] — implement your logic")
    console.print(f"  [dim]4.[/dim]  [bold]anet validate[/bold]")
    console.print(f"  [dim]5.[/dim]  [bold]anet connect[/bold]")
    console.print()


# ── anet validate ─────────────────────────────────────────────────────────────

@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True),
              help="Path to agent folder (default: current directory)")
def validate(path: str) -> None:
    """Check agent.yaml and tool files for errors."""
    console.print()
    console.print("[bold]Validating agent.yaml...[/bold]")
    console.print()
    result = validate_agent(path)
    _print_validation(result)
    console.print()
    if result.passed:
        console.print("[bold green]✓ Validation passed[/bold green]")
    else:
        console.print("[bold red]✗ Validation failed[/bold red]")
    console.print()
    sys.exit(0 if result.passed else 1)


# ── anet connect ──────────────────────────────────────────────────────────────

@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True),
              help="Path to agent folder (default: current directory)")
def connect(path: str) -> None:
    """Register this agent with ANet."""
    agent_path = Path(path).resolve()

    console.print()
    console.print("[bold]Validating agent.yaml...[/bold]")
    console.print()
    result = validate_agent(str(agent_path))
    _print_validation(result)
    console.print()

    if not result.passed:
        console.print("[bold red]Connection failed. Fix the above errors and retry.[/bold red]")
        console.print()
        sys.exit(1)

    manifest_raw = yaml.safe_load((agent_path / "agent.yaml").read_text(encoding="utf-8"))
    manifest     = AgentManifest(**manifest_raw)

    entry = RegistryEntry(
        agent_id     = _make_agent_id(manifest.name),
        registered_at= datetime.now(timezone.utc).isoformat(),
        status       = "idle",
        path         = str(agent_path),
        manifest     = manifest,
    )
    add_agent(entry)

    tool_names = [t.name for t in manifest.tools]
    task_preview = ", ".join(manifest.capabilities.task_types[:4])
    if len(manifest.capabilities.task_types) > 4:
        task_preview += ", …"

    console.print(f"[bold green]✓ {manifest.name} connected to ANet[/bold green]")
    console.print(f"  [dim]ID:      {entry.agent_id}[/dim]")
    console.print(f"  [dim]Tools:   {', '.join(tool_names)}[/dim]")
    console.print(f"  [dim]Tasks:   {task_preview}[/dim]")
    console.print(f"  [dim]Registry:{REGISTRY_FILE}[/dim]")
    console.print()
    console.print("  [dim]ANet will discover this agent on the next request.[/dim]")
    console.print()


# ── anet disconnect ───────────────────────────────────────────────────────────

@cli.command()
@click.argument("name", required=False)
def disconnect(name: str | None) -> None:
    """Remove an agent from ANet. Reads agent.yaml if no name given."""
    if not name:
        local = Path.cwd() / "agent.yaml"
        if local.exists():
            try:
                m = yaml.safe_load(local.read_text(encoding="utf-8"))
                name = m.get("identity", {}).get("name")
            except Exception:
                pass
    if not name:
        console.print("[red]Error: provide a name or run from an agent folder.[/red]")
        sys.exit(1)

    if remove_agent(name):
        console.print(f"[green]✓ '{name}' disconnected from ANet.[/green]")
    else:
        console.print(f"[yellow]'{name}' was not connected.[/yellow]")


# ── anet status ───────────────────────────────────────────────────────────────

@cli.command()
def status() -> None:
    """Show all connected agents."""
    agents = list_agents()
    console.print()

    if not agents:
        console.print(
            "  [dim]No agents connected.\n"
            "  Run [bold]anet connect[/bold] from an agent folder to add one.[/dim]"
        )
        console.print()
        return

    t = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    t.add_column("Agent",      style="bold")
    t.add_column("Status")
    t.add_column("Task Types", style="dim")
    t.add_column("Connected",  style="dim")
    t.add_column("Path",       style="dim")

    for entry in agents:
        status_str = entry.status
        color = {"idle": "green", "running": "yellow", "disabled": "dim"}.get(status_str, "white")
        task_types = entry.manifest.capabilities.task_types
        preview    = ", ".join(task_types[:3]) + (", …" if len(task_types) > 3 else "")
        connected  = entry.registered_at[:16].replace("T", "  ")
        t.add_row(
            entry.manifest.name,
            f"[{color}]{status_str}[/{color}]",
            preview,
            connected,
            entry.path,
        )

    console.print(Panel(t, title="[bold]Connected Agents[/bold]", border_style="green"))
    console.print(f"  [dim]Registry: {REGISTRY_FILE}[/dim]")
    console.print()


# ── anet list-tools ───────────────────────────────────────────────────────────

@cli.command("list-tools")
def list_tools() -> None:
    """List all tools across connected agents."""
    agents = list_agents()
    console.print()

    if not agents:
        console.print("  [dim]No agents connected.[/dim]")
        console.print()
        return

    for entry in agents:
        name  = entry.manifest.name
        tools = entry.manifest.tools
        if not tools:
            continue
        console.print(f"  [bold]{name}[/bold]")
        for tool in tools:
            desc = f"  — {tool.description}" if tool.description else ""
            console.print(f"    [dim]├─ {tool.name}  ({tool.file}){desc}[/dim]")
        console.print()
