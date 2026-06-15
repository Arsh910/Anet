"""
paths.py — resolves where Anet stores user data (sessions, USER.md, SOUL.md).

Layout under the chosen home directory (default ~/.anet):

    <home>/
    ├── sessions/
    │   ├── <session_id>/checkpoint.db + title.txt
    │   └── last_session.txt
    ├── USER.md
    └── SOUL.md

Resolution order for the home directory:
  1. ANET_HOME environment variable
  2. "home" key in ~/.anet/settings.json  (saved by the first-run prompt)
  3. None → not configured yet; main.py runs the one-time first-run prompt

The bootstrap dir ~/.anet always exists (memory_tool already keeps memory.json
there) and holds settings.json, which points at the (possibly relocated) home.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_BOOTSTRAP = Path.home() / ".anet"
_SETTINGS = _BOOTSTRAP / "settings.json"

# Default home is the bootstrap dir itself, so a default setup keeps everything
# (memory.json, sessions/, USER.md, SOUL.md, settings.json) under ~/.anet.
DEFAULT_HOME = _BOOTSTRAP


def _read_settings() -> dict:
    if _SETTINGS.exists():
        try:
            data = json.loads(_SETTINGS.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def configured_home() -> Path | None:
    """Return the configured home, or None if the user hasn't chosen one yet."""
    env = os.environ.get("ANET_HOME")
    if env:
        return Path(env).expanduser()
    home = _read_settings().get("home")
    if home:
        return Path(home).expanduser()
    return None


def save_home(path: Path) -> None:
    """Persist the chosen home dir to ~/.anet/settings.json."""
    _BOOTSTRAP.mkdir(parents=True, exist_ok=True)
    data = _read_settings()
    data["home"] = str(path)
    _SETTINGS.write_text(json.dumps(data, indent=2), encoding="utf-8")


def home() -> Path:
    """The resolved home dir, falling back to the default if unconfigured."""
    return configured_home() or DEFAULT_HOME


def sessions_dir() -> Path:
    return home() / "sessions"


def user_profile_path() -> Path:
    return home() / "USER.md"


def soul_path() -> Path:
    return home() / "SOUL.md"
