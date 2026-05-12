"""
registry.py — manages ~/.anet/registry.json

Stores all agents connected via `anet connect`. ANet reads this at startup
and watches its mtime so new agents are discovered without a restart.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from anet.plugin.schema import RegistryEntry

ANET_DIR      = Path.home() / ".anet"
REGISTRY_FILE = ANET_DIR / "registry.json"


def _ensure() -> None:
    ANET_DIR.mkdir(parents=True, exist_ok=True)


def _load_raw() -> list[dict]:
    _ensure()
    if not REGISTRY_FILE.exists():
        return []
    try:
        return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_raw(entries: list[dict]) -> None:
    _ensure()
    REGISTRY_FILE.write_text(
        json.dumps(entries, indent=2, default=str), encoding="utf-8"
    )


def _entry_name(raw: dict) -> str:
    return raw.get("manifest", {}).get("identity", {}).get("name", "")


def list_agents() -> list[RegistryEntry]:
    out = []
    for raw in _load_raw():
        try:
            out.append(RegistryEntry(**raw))
        except Exception as exc:
            print(f"[registry] skipping malformed entry: {exc}")
    return out


def get_agent(name: str) -> Optional[RegistryEntry]:
    for entry in list_agents():
        if entry.manifest.name == name:
            return entry
    return None


def add_agent(entry: RegistryEntry) -> None:
    raw = _load_raw()
    raw = [r for r in raw if _entry_name(r) != entry.manifest.name]
    raw.append(entry.model_dump())
    _save_raw(raw)


def remove_agent(name: str) -> bool:
    raw = _load_raw()
    before = len(raw)
    raw = [r for r in raw if _entry_name(r) != name]
    if len(raw) < before:
        _save_raw(raw)
        return True
    return False


def update_status(name: str, status: str) -> None:
    raw = _load_raw()
    for r in raw:
        if _entry_name(r) == name:
            r["status"] = status
            break
    _save_raw(raw)
