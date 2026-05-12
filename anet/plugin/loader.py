"""
loader.py — dynamically loads registered agents into ANet-compatible format.

load_all_agents() returns (agent_configs, tool_map) ready to merge with
the built-in AGENTS list and tool_map in main.py.

Tool output is normalized so plugin authors don't have to be perfect:
  correct dict     → pass through
  plain string     → {success:True, result:str, outputs:{}, error:None}
  exception raised → {success:False, result:"", outputs:{}, error:str(e)}
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

from anet.plugin.registry import list_agents
from anet.plugin.schema import RegistryEntry


# ── Output normalization ──────────────────────────────────────────────────────

def _normalize(result) -> dict:
    if isinstance(result, dict):
        if "error" in result or "result" in result:
            return {
                "success": "error" not in result,
                "result":  result.get("result", ""),
                "outputs": result.get("outputs", {}),
                "error":   result.get("error"),
            }
        return result                            # unknown shape — pass through
    if isinstance(result, str):
        return {"success": True, "result": result, "outputs": {}, "error": None}
    return {"success": True, "result": str(result), "outputs": {}, "error": None}


def _wrap(raw_fn: Callable) -> Callable:
    """Wrap a tool's run() to normalize output and absorb exceptions."""
    if asyncio.iscoroutinefunction(raw_fn):
        async def _runner(params: dict) -> dict:
            try:
                return _normalize(await raw_fn(params))
            except Exception as exc:
                return {"success": False, "result": "", "outputs": {}, "error": str(exc)}
    else:
        async def _runner(params: dict) -> dict:
            try:
                return _normalize(raw_fn(params))
            except Exception as exc:
                return {"success": False, "result": "", "outputs": {}, "error": str(exc)}
    return _runner


# ── Module loader ─────────────────────────────────────────────────────────────

def _load_module(file_path: Path, module_id: str):
    spec = importlib.util.spec_from_file_location(module_id, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {file_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_id] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Agent runtime loader ──────────────────────────────────────────────────────

def _load_tools_for(entry: RegistryEntry) -> dict:
    """Load and wrap all tool files from a registry entry. Returns tool_map."""
    agent_path = Path(entry.path)
    name       = entry.manifest.name
    tool_map: dict = {}

    for tool_spec in entry.manifest.tools:
        tool_path = agent_path / tool_spec.file
        if not tool_path.exists():
            print(f"[loader] warning: tool file not found: {tool_path}")
            continue
        try:
            mod    = _load_module(tool_path, f"anet_plugin.{name}.{tool_spec.name}")
            run_fn = getattr(mod, "run", None)
            if run_fn is None:
                print(f"[loader] warning: no run() in {tool_spec.file}")
                continue
            schema = getattr(mod, "SCHEMA", {})
            tool_map[tool_spec.name] = {
                "run":       _wrap(run_fn),
                "schema":    schema,
                "is_async":  tool_spec.async_tool,
                "poll_path": str(agent_path / tool_spec.poll_path) if tool_spec.poll_path else "",
                "result_key": tool_spec.result_key,
            }
        except Exception as exc:
            print(f"[loader] failed to load tool '{tool_spec.name}': {exc}")

    return tool_map


def _load_one(entry: RegistryEntry) -> tuple[dict, dict] | None:
    """Load a full agent plugin. Returns (agent_config, tool_map) or None on failure."""
    agent_path = Path(entry.path)
    manifest   = entry.manifest
    name       = manifest.name

    # Load plugin-local .env (isolated credentials per plugin)
    plugin_env = agent_path / ".env"
    if plugin_env.exists():
        load_dotenv(plugin_env, override=True)

    # Resolve system prompt
    system_prompt = ""
    if manifest.prompt and "file" in manifest.prompt:
        sp_path = agent_path / manifest.prompt["file"]
        if sp_path.exists():
            system_prompt = sp_path.read_text(encoding="utf-8").strip()
    if not system_prompt and manifest.prompt:
        system_prompt = manifest.prompt.get("inline", "")
    if not system_prompt:
        system_prompt = f"You are {name}, a specialist agent in the ANet network."

    tool_map   = _load_tools_for(entry)
    tool_names = list(tool_map.keys())

    agent_config = {
        "name":          name,
        "model":         manifest.model.name,
        "provider":      manifest.model.provider,
        "system_prompt": system_prompt,
        "task_types":    manifest.capabilities.task_types,
        "tools":         tool_names,
        "enabled":       entry.status != "disabled",
        "can_be_parallelized":   manifest.behavior.can_be_parallelized,
        "requires_confirmation": manifest.behavior.requires_confirmation,
        "execution":     manifest.behavior.execution,
        "_plugin":       True,
        "_agent_id":     entry.agent_id,
    }
    return agent_config, tool_map


def _load_tool_extension(entry: RegistryEntry) -> tuple[dict[str, list[str]], dict] | None:
    """Load a tool-extension plugin. Returns (attach_map, tool_map) or None on failure."""
    agent_path = Path(entry.path)
    manifest   = entry.manifest

    # Load plugin-local .env
    plugin_env = agent_path / ".env"
    if plugin_env.exists():
        load_dotenv(plugin_env, override=True)

    tool_map = _load_tools_for(entry)
    if not tool_map:
        return None

    # attach_map: { target_agent_name -> [tool_names] }
    attach_map: dict[str, list[str]] = {
        target: list(tool_map.keys())
        for target in manifest.attach_to
    }
    return attach_map, tool_map


def load_all_agents() -> tuple[list[dict], dict, dict[str, list[str]]]:
    """Return (agent_configs, tool_map, attach_map) for all enabled registered plugins.

    attach_map: { target_agent_name -> [tool_names] } for tool-extension plugins.
    'manager' is a valid target — caller handles injecting into the planner.
    """
    all_configs:    list[dict]             = []
    all_tools:      dict                   = {}
    all_attach_map: dict[str, list[str]]  = {}

    for entry in list_agents():
        if entry.status == "disabled":
            continue

        if entry.manifest.is_tool_extension:
            result = _load_tool_extension(entry)
            if result:
                attach_map, tools = result
                all_tools.update(tools)
                for target, tool_names in attach_map.items():
                    all_attach_map.setdefault(target, []).extend(tool_names)
        else:
            result = _load_one(entry)
            if result:
                cfg, tools = result
                all_configs.append(cfg)
                all_tools.update(tools)

    return all_configs, all_tools, all_attach_map
