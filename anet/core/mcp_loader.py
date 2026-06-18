"""
mcp_loader.py — persistent MCP server connections for ANet.

Discovers MCP servers in mcps/<name>/config.yaml, launches them as subprocesses,
and wraps their tools as standard ANet tool_map entries (async callables + OpenAI schema).

Each server runs as a persistent background asyncio.Task for the lifetime of the process.
Tool calls are dispatched through an asyncio.Queue so the connection stays alive and
concurrent calls are serialised per-server (MCP sessions are not thread-safe).

Requires:  pip install mcp
"""
from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from typing import Any

def _mcp_dir() -> Path:
    """Directory of MCP server configs (mcps/<name>/config.yaml) in the workspace."""
    from anet.core import paths as _paths
    return _paths.mcps_dir()

# Registry of live connections: server_name → _MCPConnection
_connections: dict[str, "_MCPConnection"] = {}


# ── Connection class ──────────────────────────────────────────────────────────

class _MCPConnection:
    """Manages a single persistent MCP server connection via stdio."""

    def __init__(self, name: str, cfg: dict) -> None:
        self.name    = name
        self.cfg     = cfg
        self.tools:  list[Any]           = []   # mcp.Tool objects after connect
        self._queue: asyncio.Queue       = asyncio.Queue()
        self._ready: asyncio.Event       = asyncio.Event()
        self._task:  asyncio.Task | None = None
        self.error:  str                 = ""   # non-empty if startup failed

    async def start(self) -> bool:
        """Launch background task and wait up to 30 s for server to become ready."""
        self._task = asyncio.create_task(self._run(), name=f"mcp:{self.name}")
        try:
            await asyncio.wait_for(asyncio.shield(self._ready.wait()), timeout=30)
        except asyncio.TimeoutError:
            self.error = "timeout — server did not become ready within 30 s"
        return not self.error

    async def _run(self) -> None:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            self.error = "'mcp' package not installed — run: pip install mcp"
            self._ready.set()
            return

        command = self.cfg.get("command", "")
        args    = list(self.cfg.get("args") or [])
        env     = self.cfg.get("env") or None

        if not command:
            self.error = "no 'command' in config.yaml"
            self._ready.set()
            return

        # Check the binary exists before trying to spawn it
        resolved_cmd = shutil.which(command)
        if resolved_cmd is None:
            self.error = f"command '{command}' not found in PATH — is it installed?"
            self._ready.set()
            return

        # Working directory for the server subprocess. Default: a per-server data
        # dir under the Anet home (<home>/mcp/<name>/) so any folders the server
        # creates (e.g. .code-review-graph, .playwright-mcp) don't litter the
        # launch directory. A `cwd:` key in config.yaml overrides this (relative
        # paths resolve against the dir ANet was launched from).
        cwd = self._resolve_cwd()

        try:
            params = StdioServerParameters(command=command, args=args, env=env, cwd=cwd)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result     = await session.list_tools()
                    self.tools = result.tools
                    self._ready.set()

                    # Dispatch tool calls from queue
                    while True:
                        tool_name, arguments, fut = await self._queue.get()
                        try:
                            r      = await session.call_tool(tool_name, arguments)
                            parts  = []
                            for block in r.content:
                                parts.append(getattr(block, "text", str(block)))
                            fut.set_result({"result": "\n".join(parts)})
                        except Exception as exc:
                            if not fut.done():
                                fut.set_result({"error": str(exc)})

        except Exception as exc:
            # Unwrap ExceptionGroup (Python 3.11+) raised by asyncio.TaskGroup inside stdio_client
            if hasattr(exc, "exceptions"):
                self.error = "; ".join(str(e) for e in exc.exceptions)
            else:
                self.error = str(exc)
            self._ready.set()

    def _resolve_cwd(self) -> str:
        """Resolve the subprocess working directory and ensure it exists.

        A `cwd:` override resolves relative paths against the directory the user
        launched ANet from (their project), so e.g. `cwd: "."` makes codegraph
        index the current project. With no override, a per-server data dir under
        the Anet home is used, keeping the launch dir clean.
        """
        override = self.cfg.get("cwd")
        if override:
            p = Path(override).expanduser()
            if not p.is_absolute():
                p = (Path.cwd() / p).resolve()
        else:
            from anet.core import paths as _paths
            p = _paths.mcp_data_dir(self.name)
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return str(p)

    async def call(self, tool_name: str, arguments: dict) -> dict:
        if self.error:
            return {"error": f"MCP server '{self.name}' unavailable: {self.error}"}
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        await self._queue.put((tool_name, arguments, fut))
        return await fut

    async def close(self) -> None:
        """Shut down the server subprocess by cancelling its background task."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except BaseException:
                pass


# ── Config reader ─────────────────────────────────────────────────────────────

def _read_server_config(server_name: str) -> dict | None:
    config_file = _mcp_dir() / server_name / "config.yaml"
    if not config_file.exists():
        print(f"[mcp_loader] no config.yaml found at {config_file}", file=sys.stderr)
        return None
    try:
        import yaml
        return yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        print(f"[mcp_loader] could not read config for '{server_name}': {exc}", file=sys.stderr)
        return None


# ── Schema builder ────────────────────────────────────────────────────────────

def _mcp_schema(tool: Any) -> dict:
    """Convert an mcp.Tool to an OpenAI-compatible function schema dict."""
    params: dict = {}
    schema = getattr(tool, "inputSchema", None)
    if schema is not None:
        params = schema.model_dump() if hasattr(schema, "model_dump") else dict(schema)
    return {
        "type": "function",
        "function": {
            "name":        tool.name,
            "description": tool.description or "",
            "parameters":  params,
        },
    }


# ── Public API ────────────────────────────────────────────────────────────────

async def connect_servers(server_names: list[str]) -> dict[str, _MCPConnection]:
    """
    Connect to MCP servers by name (reads mcps/<name>/config.yaml).
    Servers already connected are reused from the module-level cache.
    Returns {name: connection} for successfully started servers.
    """
    result: dict[str, _MCPConnection] = {}
    for name in server_names:
        if name in _connections:
            result[name] = _connections[name]
            continue

        cfg = _read_server_config(name)
        if cfg is None:
            continue

        conn = _MCPConnection(name, cfg)
        ok   = await conn.start()
        if ok:
            _connections[name] = conn
            result[name]       = conn
        else:
            print(f"[mcp_loader] failed to start '{name}': {conn.error}", file=sys.stderr)

    return result


def build_tool_map(connections: dict[str, _MCPConnection]) -> dict[str, dict]:
    """
    Build an ANet tool_map from a dict of live connections.
    Each MCP tool becomes one entry: { tool_name: {"run": async_fn, "schema": dict} }
    """
    tool_map: dict[str, dict] = {}
    for conn in connections.values():
        for tool in conn.tools:
            _c, _n = conn, tool.name   # capture for closure

            async def _run(arguments: dict, c=_c, n=_n) -> dict:
                return await c.call(n, arguments)

            tool_map[tool.name] = {"run": _run, "schema": _mcp_schema(tool)}
    return tool_map


async def load_mcp_tools_for_agents(agents: list[dict]) -> dict[str, dict]:
    """
    Collect all unique MCP server names declared across all agents' mcp: lists,
    connect to them (deduplicated), and return a merged tool_map.

    Also injects the loaded tool names back into each agent's tools list
    so agent_runner picks them up.
    """
    # Gather unique server names
    needed: set[str] = set()
    for agent in agents:
        for srv in agent.get("mcp") or []:
            needed.add(srv)

    # Disconnect servers the (now-active) pack no longer declares, so a pack
    # switch doesn't leave stale MCP connections alive in the global registry.
    for name in list(_connections):
        if name not in needed:
            try:
                await _connections[name].close()
            except Exception:
                pass
            del _connections[name]

    if not needed:
        return {}

    connections = await connect_servers(list(needed))
    tool_map    = build_tool_map(connections)

    # Inject tool names into each agent that declared the server
    for agent in agents:
        for srv in agent.get("mcp") or []:
            conn = connections.get(srv)
            if conn is None:
                continue
            mcp_tool_names = [t.name for t in conn.tools]
            existing       = set(agent.get("tools") or [])
            agent["tools"] = list(agent.get("tools") or []) + [
                n for n in mcp_tool_names if n not in existing
            ]

    return tool_map


def list_available_servers() -> list[str]:
    """Return names of all MCP servers that have a config.yaml in mcps/."""
    if not _mcp_dir().exists():
        return []
    return [
        d.name for d in _mcp_dir().iterdir()
        if d.is_dir() and (d / "config.yaml").exists()
    ]
