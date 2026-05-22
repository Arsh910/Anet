"""
ex_loader.py — loads ExTools and ExAgents from exanet.config.yaml.

ExTools: Python tool modules in ExTools/, same __init__.py structure as AnetTools.
ExAgents: agent definitions declared in exanet.config.yaml (no Python code required
          unless the agent has custom tools in ExTools/).

get_extra_tools_for_builtins() reads anet.config.yaml for extra_tools / mcp entries
on built-in agents so users can bolt external tools onto code_agent etc. without
touching agents_config.py.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT      = Path(__file__).parents[2]
_EX_CONFIG = _ROOT / "exanet.config.yaml"


# ── Config reader ─────────────────────────────────────────────────────────────

def _load_ex_config() -> dict:
    if not _EX_CONFIG.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(_EX_CONFIG.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        print(f"[ex_loader] could not read exanet.config.yaml: {exc}", file=sys.stderr)
        return {}


# ── Module loader (same pattern as plugin/loader.py) ─────────────────────────

def _load_module(path: Path, module_id: str):
    spec = importlib.util.spec_from_file_location(module_id, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_id] = mod
    spec.loader.exec_module(mod)
    return mod


# ── ExTools ───────────────────────────────────────────────────────────────────

def load_ex_tools() -> dict[str, dict]:
    """
    Load ExTools declared under the 'tools:' key in exanet.config.yaml.

    Each entry must have:
      name: my_tool
      path: ExTools/my_tool       ← relative to repo root

    The tool module at ExTools/my_tool/__init__.py must export:
      run(arguments: dict) -> dict   (sync or async)
      SCHEMA: dict                   (OpenAI function schema)

    Returns a standard ANet tool_map: { tool_name: {"run": fn, "schema": dict} }
    """
    cfg      = _load_ex_config()
    specs    = cfg.get("tools") or []
    tool_map: dict[str, dict] = {}

    for spec in specs:
        name = spec.get("name", "").strip()
        path = spec.get("path", "").strip()
        if not name or not path:
            print(f"[ex_loader] skipping tool entry with missing name/path: {spec}", file=sys.stderr)
            continue

        init_file = (_ROOT / path / "__init__.py")
        if not init_file.exists():
            print(f"[ex_loader] ExTool '{name}': __init__.py not found at {init_file}", file=sys.stderr)
            continue

        try:
            mod    = _load_module(init_file, f"ext_tools.{name}")
            run_fn = getattr(mod, "run", None)
            schema = getattr(mod, "SCHEMA", None)
            if run_fn is None or schema is None:
                print(f"[ex_loader] ExTool '{name}': missing run() or SCHEMA — skipping", file=sys.stderr)
                continue
            tool_map[name] = {"run": run_fn, "schema": schema}
        except Exception as exc:
            print(f"[ex_loader] failed to load ExTool '{name}': {exc}", file=sys.stderr)

    return tool_map


# ── ExAgents ──────────────────────────────────────────────────────────────────

def load_ex_agents() -> list[dict]:
    """
    Load ExAgents declared under the 'agents:' key in exanet.config.yaml.

    Each entry supports:
      name, model, provider, enabled, task_types, tools, mcp,
      system_prompt (inline), prompt_file (path relative to repo root)

    Returns a list of agent config dicts ready to merge into enabled_agents.
    The 'mcp' field is preserved so main.py can connect MCP servers for them.
    """
    cfg         = _load_ex_config()
    agent_specs = cfg.get("agents") or []
    agents: list[dict] = []

    for spec in agent_specs:
        name = spec.get("name", "").strip()
        if not name:
            continue
        if not spec.get("enabled", True):
            continue

        # Resolve system prompt: inline > prompt_file > default
        system_prompt = (spec.get("system_prompt") or "").strip()
        prompt_file   = spec.get("prompt_file", "")
        if not system_prompt and prompt_file:
            pf = _ROOT / prompt_file
            if pf.exists():
                system_prompt = pf.read_text(encoding="utf-8").strip()
        if not system_prompt:
            system_prompt = f"You are {name}, a specialist agent."

        agents.append({
            "name":          name,
            "model":         spec.get("model") or "gemini-2.5-flash",
            "provider":      spec.get("provider") or "google",
            "system_prompt": system_prompt,
            "task_types":    list(spec.get("task_types") or []),
            "tools":         list(spec.get("tools") or []),
            "mcp":           list(spec.get("mcp") or []),
            "enabled":       True,
            "_external":     True,
        })

    return agents


# ── Extra tools / MCP for built-in agents (via anet.config.yaml) ─────────────

def get_extra_for_builtins() -> dict[str, dict]:
    """
    Read anet.config.yaml agents section for extra_tools and mcp entries
    so users can extend built-in agents without editing agents_config.py.

    Returns:
      { agent_name: { "tools": [tool_names], "mcp": [server_names] } }
    """
    try:
        from anet.core.config_loader import load as _load_anet
        cfg = _load_anet()
    except Exception:
        return {}

    result: dict[str, dict] = {}
    for agent_name, overrides in (cfg.get("agents") or {}).items():
        extra_tools = list(overrides.get("extra_tools") or [])
        mcp         = list(overrides.get("mcp") or [])
        if extra_tools or mcp:
            result[agent_name] = {"tools": extra_tools, "mcp": mcp}
    return result
