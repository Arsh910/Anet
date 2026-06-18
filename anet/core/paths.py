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


def mcp_data_dir(name: str) -> Path:
    """Per-server working/data directory: <home>/mcp/<name>/.

    MCP servers are launched with this as their cwd so any data folders they
    create (codegraph's `.code-review-graph`, playwright's `.playwright-mcp`,
    etc.) land here instead of polluting the repo root.
    """
    return home() / "mcp" / name


# ── Workspace (the user's editable extension area, seeded from templates) ─────
# Everything the user/agents add lives under the Anet home, NOT inside the
# installed package: config files, ExTools, ExAgents, mcps, skills, anet_files.

def _dev_repo_root() -> Path | None:
    """Repo root if ANet is running from a source checkout, else None.

    Detected by a dev marker (main.py / pyproject.toml / .git) sitting beside the
    `anet/` package, alongside an `anet_pack/` source dir. A pip/pipx install in
    site-packages has none of these markers, so this returns None there.
    """
    repo = Path(__file__).resolve().parents[2]  # anet/core/paths.py → repo root
    markers = ("main.py", "pyproject.toml", ".git")
    if (repo / "anet_pack").is_dir() and any((repo / m).exists() for m in markers):
        return repo
    return None


def default_pack_root() -> Path:
    """The bundled default pack's location: the repo's `anet_pack/` in a source
    checkout, else `<home>/anet_pack/`. This is what first-run seeding targets."""
    dev = _dev_repo_root()
    return (dev / "anet_pack") if dev is not None else (home() / "anet_pack")


def yourpacks_dir() -> Path:
    """Where packs YOU create live: <home>/yourpacks/<name>/."""
    return home() / "yourpacks"


def shared_packs_dir() -> Path:
    """Where imported/received packs live: <home>/shared_packs/<name>/."""
    return home() / "shared_packs"


def _named_pack_path(name: str) -> Path | None:
    """Resolve a (non-default) pack name to its dir, searching yourpacks/ then
    shared_packs/. Returns None if not found."""
    for base in (yourpacks_dir(), shared_packs_dir()):
        p = base / name
        if p.exists():
            return p
    return None


def _active_pack_file() -> Path:
    return home() / "active_pack.txt"


def active_pack() -> str:
    """Name of the currently-selected pack. Default 'anet_pack'."""
    try:
        name = _active_pack_file().read_text(encoding="utf-8").strip()
        return name or "anet_pack"
    except Exception:
        return "anet_pack"


def set_active_pack(name: str) -> None:
    f = _active_pack_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text((name or "anet_pack").strip(), encoding="utf-8")


def list_packs() -> list[str]:
    """All selectable packs: the default 'anet_pack' plus packs under yourpacks/
    and shared_packs/ (deduped, default first)."""
    packs = ["anet_pack"]
    for base in (yourpacks_dir(), shared_packs_dir()):
        if base.exists():
            for d in sorted(base.iterdir()):
                if d.is_dir() and d.name not in packs:
                    packs.append(d.name)
    return packs


def pack_kind(name: str) -> str:
    """'default' | 'yours' | 'shared' | 'missing' — for display in the picker."""
    if name == "anet_pack":
        return "default"
    if (yourpacks_dir() / name).exists():
        return "yours"
    if (shared_packs_dir() / name).exists():
        return "shared"
    return "missing"


def workspace_root() -> Path:
    """The ACTIVE pack — what the loaders read and the smiths write.

    Defaults to the bundled pack (`default_pack_root()`); `/changepack` switches
    it to a shared pack under `<home>/shared_packs/<name>/`. In a source checkout
    the default pack is the repo's `anet_pack/` (edit-and-test instantly); when
    installed it's `<home>/anet_pack/`.

    Holds config + ExTools/ExAgents/mcps/skills + SOUL.md — everything that
    defines a workspace and can be shared. Personal/generated data (USER.md,
    sessions/, anet_files/, mcp/ runtime) lives at the home root, OUTSIDE any pack.
    """
    active = active_pack()
    if active and active != "anet_pack":
        p = _named_pack_path(active)
        if p is not None:
            return p
        # Active pack went missing → fall back to the default.
    return default_pack_root()


def config_path() -> Path:
    return workspace_root() / "anet.config.yaml"


def exanet_path() -> Path:
    return workspace_root() / "exanet.config.yaml"


def extools_dir() -> Path:
    return workspace_root() / "ExTools"


def exagents_dir() -> Path:
    return workspace_root() / "ExAgents"


def mcps_dir() -> Path:
    return workspace_root() / "mcps"


def skills_dir() -> Path:
    return workspace_root() / "skills"


def anet_files_dir() -> Path:
    # Generated output — stays at the home root, outside the shareable pack.
    return home() / "anet_files"


def env_path() -> Path:
    # The user's API keys / env vars, at the home root (shared across all packs).
    return home() / ".env"


def user_profile_path() -> Path:
    return home() / "USER.md"


def soul_path() -> Path:
    # Persona is part of the shareable pack.
    return workspace_root() / "SOUL.md"
