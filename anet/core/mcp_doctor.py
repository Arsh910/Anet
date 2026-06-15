"""
mcp_doctor.py — verify an MCP server actually connects in ANet, and list its tools.

This is the MCP analog of extool_validator: for MCP there is no code/schema to write
(ANet auto-introspects each server's tools via list_tools), so the missing piece is
*verification* — does the server launch over stdio and expose tools? The doctor runs
the EXACT path mcp_loader uses at runtime, so a PASS here means it'll work for agents.

Usage:
    python -m anet.core.mcp_doctor <server_name>

Exit 0 = connects, 1 = failed. Reasons are printed either way.
"""
from __future__ import annotations

import asyncio
import shutil
import sys

from anet.core import mcp_loader


async def diagnose(name: str) -> dict:
    """Return {name, ok, tools, messages}. Tests config -> PATH -> live connection."""
    out: dict = {"name": name, "ok": False, "tools": [], "messages": []}
    m: list[str] = out["messages"]

    # 1. mcp package present
    try:
        import mcp  # noqa: F401
        m.append("OK: 'mcp' package importable")
    except ImportError:
        m.append("FAIL: 'mcp' package not installed - run: pip install mcp")
        return out

    # 2. config.yaml readable
    cfg = mcp_loader._read_server_config(name)
    if cfg is None:
        m.append(f"FAIL: no readable config at mcps/{name}/config.yaml")
        return out
    command = cfg.get("command", "")
    args    = list(cfg.get("args") or [])
    if not command:
        m.append("FAIL: config.yaml has no 'command' field")
        return out
    m.append(f"OK: config.yaml found (command='{command}', {len(args)} arg(s))")

    # 3. command resolvable on PATH (mcp_loader rejects it otherwise)
    if shutil.which(command) is None:
        m.append(
            f"FAIL: command '{command}' not found on PATH - install it "
            f"(node/npx, uv/uvx, python, ...) or use an absolute path"
        )
        return out
    m.append(f"OK: '{command}' resolved on PATH")

    # 4. real connection over stdio (HTTP/SSE-only servers fail here - stdio only)
    m.append("...  launching server and listing tools (up to 30s)")
    try:
        conns = await mcp_loader.connect_servers([name])
    except Exception as exc:  # noqa: BLE001
        m.append(f"FAIL: connection raised {type(exc).__name__}: {exc}")
        return out
    conn = conns.get(name)
    if conn is None or conn.error:
        err = conn.error if conn else "server did not start"
        m.append(f"FAIL: connection failed - {err}")
        m.append("      (stdio transport only; remote HTTP/SSE MCP servers are not supported)")
        return out

    tool_names = [t.name for t in conn.tools]
    out["tools"] = tool_names
    out["ok"]    = True
    m.append(f"OK: connected. {len(tool_names)} tool(s): {', '.join(tool_names) or '(none)'}")
    return out


def _main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m anet.core.mcp_doctor <server_name>")
        return 2
    name = argv[0]
    res = asyncio.run(diagnose(name))
    for line in res["messages"]:
        print("  " + line)
    print()
    if res["ok"]:
        print(f"PASS: MCP '{name}' connects. To use it, add it to an agent in anet.config.yaml")
        print("      and RESTART ANet (anet.config.yaml is not hot-reloaded):")
        print("  agents:")
        print("    code_agent:")
        print(f"      mcp: [{name}]")
        return 0
    print(f"INVALID: MCP '{name}' did not connect (see FAIL lines above).")
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
