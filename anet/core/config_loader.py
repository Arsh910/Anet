"""
config_loader.py — reads anet.config.yaml from the project root.

Provides agent and manager overrides that are merged on top of the
hardcoded defaults in agents_config.py at startup.

The file is optional — if absent, all defaults are used as-is.
"""
from __future__ import annotations

from pathlib import Path

_cache: dict | None = None
_soul_cache: str | None = None


def reset_cache() -> None:
    """Drop cached config + soul so the next load re-reads from the (possibly
    switched) active pack. Called by /changepack."""
    global _cache, _soul_cache
    _cache = None
    _soul_cache = None


def load() -> dict:
    """Load and cache anet.config.yaml from the Anet home. Returns {} if absent/invalid."""
    global _cache
    if _cache is not None:
        return _cache

    from anet.core import paths as _paths
    cfg_file = _paths.config_path()

    if not cfg_file.exists():
        _cache = {}
        return _cache

    try:
        import yaml
        with cfg_file.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        _cache = data
    except Exception as exc:
        print(f"[config_loader] WARNING: could not read anet.config.yaml — {exc}")
        _cache = {}

    return _cache


def load_soul() -> str:
    """Load and cache SOUL.md from the repo root (or a custom path from config).
    Returns empty string if persona is disabled or file is missing."""
    global _soul_cache
    if _soul_cache is not None:
        return _soul_cache

    cfg     = load().get("persona") or {}
    enabled = cfg.get("enabled", True)
    if not enabled:
        _soul_cache = ""
        return _soul_cache

    # SOUL.md lives in the Anet home (seeded from the default pack on first run);
    # fall back to the bundled default pack if the home copy isn't there yet.
    try:
        from anet.core.paths import soul_path as _home_soul_path
        home_soul = _home_soul_path()
    except Exception:
        home_soul = None
    if home_soul and home_soul.exists():
        soul_path = home_soul
    else:
        try:
            import importlib.resources as _ir
            soul_path = Path(str(_ir.files("anet_pack"))) / "SOUL.md"
        except Exception:
            soul_path = Path(__file__).resolve().parents[2] / "anet_pack" / "SOUL.md"
    if soul_path.exists():
        try:
            _soul_cache = soul_path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            print(f"[config_loader] WARNING: could not read {soul_path} — {exc}")
            _soul_cache = ""
    else:
        _soul_cache = ""

    return _soul_cache


def agent_overrides() -> dict[str, dict]:
    """Return {agent_name: {key: value}} overrides from the 'agents' section."""
    return load().get("agents") or {}


def manager_config() -> dict:
    """Return manager model/provider config, or {} to use defaults."""
    return load().get("manager") or {}
