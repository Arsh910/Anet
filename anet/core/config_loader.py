"""
config_loader.py — reads anet.config.yaml from the project root.

Provides agent and manager overrides that are merged on top of the
hardcoded defaults in agents_config.py at startup.

The file is optional — if absent, all defaults are used as-is.
"""
from __future__ import annotations

from pathlib import Path

_CONFIG_FILE = Path(__file__).parents[2] / "anet.config.yaml"
_cache: dict | None = None


def load() -> dict:
    """Load and cache anet.config.yaml. Returns {} if file is absent or invalid."""
    global _cache
    if _cache is not None:
        return _cache

    if not _CONFIG_FILE.exists():
        _cache = {}
        return _cache

    try:
        import yaml
        with _CONFIG_FILE.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        _cache = data
    except Exception as exc:
        print(f"[config_loader] WARNING: could not read anet.config.yaml — {exc}")
        _cache = {}

    return _cache


def agent_overrides() -> dict[str, dict]:
    """Return {agent_name: {key: value}} overrides from the 'agents' section."""
    return load().get("agents") or {}


def manager_config() -> dict:
    """Return manager model/provider config, or {} to use defaults."""
    return load().get("manager") or {}
