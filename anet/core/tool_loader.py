"""
tool_loader.py — dynamically imports every enabled tool at startup.

The rest of the system receives a plain dict:
    { tool_name: { "run": async_fn, "schema": dict } }

Adding a new tool requires only:
  1. A new folder under /tools/ with __init__.py exporting `run` and `SCHEMA`
  2. A new entry in tools/tools_config.py

Nothing in this file ever needs to change.
"""

import importlib
import sys
from anet.AnetTools.tools_config import TOOLS


def load_tools() -> dict[str, dict]:
    """Read tools_config, import enabled tool modules, return the tool map."""
    tool_map: dict[str, dict] = {}

    for cfg in TOOLS:
        if not cfg.get("enabled", False):
            continue

        name: str = cfg["name"]
        path: str = cfg["path"]

        try:
            module = importlib.import_module(path)
        except ImportError as exc:
            print(
                f"[tool_loader] WARNING: could not import tool '{name}' "
                f"from '{path}': {exc}",
                file=sys.stderr,
            )
            continue

        run_fn = getattr(module, "run", None)
        schema = getattr(module, "SCHEMA", None)

        if run_fn is None or schema is None:
            print(
                f"[tool_loader] WARNING: tool '{name}' at '{path}' is missing "
                f"'run' or 'SCHEMA' — skipping.",
                file=sys.stderr,
            )
            continue

        tool_map[name] = {"run": run_fn, "schema": schema}

    return tool_map
