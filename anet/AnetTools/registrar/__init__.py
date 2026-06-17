"""
registrar — the smiths' safe integration tool.

Lets the toolsmith / mcpsmith / agentsmith agents register ExTools and ExAgents
and attach tools / MCP servers to agents by editing **exanet.config.yaml only**.

HARD GUARANTEE: this tool writes to exactly one file — `exanet.config.yaml` in
the user's Anet home (workspace). It can never modify the core `anet/` package or
`anet.config.yaml`. The smiths route every config change through here precisely so
that guarantee holds, even if the model is told otherwise.

Attaching a tool/MCP to a BUILT-IN agent (defined inside `anet/`) is recorded
under an `attach:` section in `exanet.config.yaml`, which the loader merges at
startup — so built-in agents gain capabilities without touching `anet.config.yaml`.
"""
from __future__ import annotations

from pathlib import Path

def _exanet() -> Path:
    """The exanet.config.yaml in the user's workspace (Anet home)."""
    from anet.core import paths as _paths
    return _paths.exanet_path()


def _mcps_root() -> Path:
    from anet.core import paths as _paths
    return _paths.mcps_dir()

# Only these smiths may attach tools/MCP to BUILT-IN (internal) agents. Attaching
# to a built-in agent extends the core anet/ agents, so it's deliberately limited
# to the tool/MCP smiths. External agents (your own ExAgents) have no such limit.
_BUILTIN_ATTACH_ALLOWED = {"toolsmith", "mcpsmith"}

SCHEMA = {
    "type": "function",
    "function": {
        "name": "registrar",
        "description": (
            "Register external tools/agents and attach tools or MCP servers to agents by "
            "safely editing exanet.config.yaml ONLY (never the core anet/ package or "
            "anet.config.yaml). Use the list_* actions to discover what is available, then "
            "register_tool / register_agent / attach to apply changes. Attaching to a "
            "built-in (internal) agent is recorded in exanet.config.yaml's attach: section, "
            "and is allowed ONLY for the toolsmith and mcpsmith."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "list_agents", "list_tools", "list_mcps",
                        "register_tool", "register_agent", "attach",
                    ],
                    "description": "Which operation to run.",
                },
                "name":        {"type": "string", "description": "Tool/agent/server name (register_tool, register_agent)."},
                "path":        {"type": "string", "description": "Folder path for register_tool, e.g. ExTools/my_tool."},
                "model":       {"type": "string", "description": "register_agent: model id."},
                "provider":    {"type": "string", "description": "register_agent: provider key."},
                "prompt_file": {"type": "string", "description": "register_agent: path to the prompt .md, e.g. ExAgents/<name>/prompt.md."},
                "system_prompt": {"type": "string", "description": "register_agent: inline prompt (alternative to prompt_file)."},
                "task_types":  {"type": "array", "items": {"type": "string"}, "description": "register_agent: phrases the planner routes on."},
                "tools":       {"type": "array", "items": {"type": "string"}, "description": "Tool names. register_agent: the agent's tools. attach: tools to add to the targets."},
                "mcp":         {"type": "array", "items": {"type": "string"}, "description": "MCP server names. register_agent: the agent's mcp. attach: servers to add to the targets."},
                "targets":     {"type": "array", "items": {"type": "string"}, "description": "attach: agent names (built-in or external) to attach tools/mcp to. Multiple allowed."},
                "enabled":     {"type": "boolean", "description": "register_agent: whether the agent is active (default true)."},
            },
            "required": ["action"],
        },
    },
}


# ── YAML round-trip (prefers ruamel to preserve comments) ─────────────────────

def _yaml():
    try:
        from ruamel.yaml import YAML
        y = YAML()
        y.preserve_quotes = True
        y.indent(mapping=2, sequence=4, offset=2)
        return y
    except Exception:
        return None


def _load():
    y = _yaml()
    if not _exanet().exists():
        return {}, y
    text = _exanet().read_text(encoding="utf-8")
    if y is not None:
        return (y.load(text) or {}), y
    import yaml as pyyaml
    return (pyyaml.safe_load(text) or {}), None


def _save(data, y) -> None:
    if y is not None:
        with open(_exanet(), "w", encoding="utf-8") as f:
            y.dump(data, f)
    else:
        import yaml as pyyaml
        _exanet().write_text(pyyaml.safe_dump(data, sort_keys=False), encoding="utf-8")


# ── Discovery helpers ─────────────────────────────────────────────────────────

def _builtin_agents() -> list[str]:
    try:
        from anet.AnetAgents.agents_config import AGENTS
        return [a["name"] for a in AGENTS if a.get("enabled", True)]
    except Exception:
        return []


def _builtin_tools() -> list[str]:
    try:
        from anet.AnetTools.tools_config import TOOLS
        return [t["name"] for t in TOOLS if t.get("enabled", True) and t["name"] != "registrar"]
    except Exception:
        return []


def _mcp_servers() -> list[str]:
    d = _mcps_root()
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and (p / "config.yaml").exists())


def _find_by_name(seq, name):
    for item in seq or []:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return None


def _merge_into(d: dict, key: str, items: list) -> None:
    if not items:
        return
    cur = d.get(key)
    if cur is None:
        d[key] = list(items)
        return
    for it in items:
        if it not in cur:
            cur.append(it)


# ── Tool entry point ──────────────────────────────────────────────────────────

async def run(params: dict) -> dict:
    action = (params.get("action") or "").strip()

    # ── Read-only discovery ───────────────────────────────────────────────────
    if action == "list_agents":
        data, _ = _load()
        external = [a.get("name") for a in (data.get("agents") or []) if isinstance(a, dict) and a.get("name")]
        return {"result": {"builtin": _builtin_agents(), "external": external}}

    if action == "list_tools":
        data, _ = _load()
        external = [t.get("name") for t in (data.get("tools") or []) if isinstance(t, dict) and t.get("name")]
        return {"result": {"builtin": _builtin_tools(), "external": external}}

    if action == "list_mcps":
        return {"result": {"servers": _mcp_servers()}}

    # ── Mutating actions (exanet.config.yaml only) ────────────────────────────
    if action == "register_tool":
        name = (params.get("name") or "").strip()
        path = (params.get("path") or "").strip()
        if not name or not path:
            return {"error": "register_tool requires 'name' and 'path'"}
        data, y = _load()
        if data.get("tools") is None:
            data["tools"] = []
        entry = _find_by_name(data["tools"], name)
        if entry is not None:
            entry["path"] = path
        else:
            data["tools"].append({"name": name, "path": path})
        _save(data, y)
        return {"result": f"registered tool '{name}' (path: {path}) in exanet.config.yaml"}

    if action == "register_agent":
        name = (params.get("name") or "").strip()
        if not name:
            return {"error": "register_agent requires 'name'"}
        if not params.get("prompt_file") and not params.get("system_prompt"):
            return {"error": "register_agent requires 'prompt_file' or 'system_prompt'"}
        data, y = _load()
        if data.get("agents") is None:
            data["agents"] = []
        spec = _find_by_name(data["agents"], name)
        if spec is None:
            spec = {"name": name}
            data["agents"].append(spec)
        for k in ("model", "provider", "prompt_file", "system_prompt"):
            if params.get(k):
                spec[k] = params[k]
        if params.get("task_types") is not None:
            spec["task_types"] = list(params["task_types"])
        if params.get("tools") is not None:
            spec["tools"] = list(params["tools"])
        if params.get("mcp") is not None:
            spec["mcp"] = list(params["mcp"])
        spec["enabled"] = bool(params.get("enabled", True))
        _save(data, y)
        return {"result": f"registered agent '{name}' in exanet.config.yaml"}

    if action == "attach":
        targets   = params.get("targets") or []
        add_tools = params.get("tools") or []
        add_mcp   = params.get("mcp") or []
        if not targets:
            return {"error": "attach requires 'targets' (one or more agent names)"}
        if not add_tools and not add_mcp:
            return {"error": "attach requires 'tools' and/or 'mcp' to add"}

        caller = (params.get("_agent_name") or "").strip()
        data, y = _load()
        builtins = set(_builtin_agents())
        if data.get("agents") is None:
            data["agents"] = []

        done, skipped, refused = [], [], []
        for tgt in targets:
            ext = _find_by_name(data["agents"], tgt)
            if ext is not None:                     # external agent → edit its block
                _merge_into(ext, "tools", add_tools)
                _merge_into(ext, "mcp", add_mcp)
                done.append(f"{tgt} (external)")
            elif tgt in builtins:                   # built-in → exanet attach: section
                if caller not in _BUILTIN_ATTACH_ALLOWED:
                    refused.append(tgt)
                    continue
                if data.get("attach") is None:
                    data["attach"] = {}
                if data["attach"].get(tgt) is None:
                    data["attach"][tgt] = {}
                _merge_into(data["attach"][tgt], "tools", add_tools)
                _merge_into(data["attach"][tgt], "mcp", add_mcp)
                done.append(f"{tgt} (built-in)")
            else:
                skipped.append(tgt)

        _save(data, y)
        parts = []
        if add_tools:
            parts.append("tools=[" + ", ".join(add_tools) + "]")
        if add_mcp:
            parts.append("mcp=[" + ", ".join(add_mcp) + "]")
        msg = f"attached {' '.join(parts)} → {', '.join(done) or 'nothing'}"
        if refused:
            msg += (f"  (refused built-in agent(s): {', '.join(refused)} — only the tool/MCP "
                    f"smiths may attach to built-in agents)")
        if skipped:
            msg += f"  (unknown agents skipped: {', '.join(skipped)})"
        return {"result": msg}

    return {"error": f"unknown action '{action}'"}
