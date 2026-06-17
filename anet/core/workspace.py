"""
workspace.py — first-run seeding of the user's Anet workspace.

The user's editable extension area (config files, ExTools, ExAgents, mcps,
skills) lives under the Anet home (~/.anet by default), never inside the
installed core. On first run we seed that home from the bundled default pack
(`anet_pack`) so a fresh `pip`/`pipx` install has working examples, and we
migrate an existing clone's content if ANet is being run from a source checkout.

Seeding is idempotent: it only copies items that are MISSING from the home, so
upgrading the package never clobbers the user's edits. `force=True` (anet init
--reset) re-copies the bundled default pack, overwriting.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from anet.core import paths as _paths

# Items that make up a workspace, copied (file or dir) into the home if missing.
_ITEMS = [
    "anet.config.yaml",
    "exanet.config.yaml",
    "SOUL.md",
    "ExTools",
    "ExAgents",
    "mcps",
    "skills",
]


def _pack_dir() -> Path | None:
    """Locate the bundled default pack (`anet_pack`), works installed or in a checkout."""
    try:
        import importlib.resources as ir
        p = Path(str(ir.files("anet_pack")))
        if p.exists():
            return p
    except Exception:
        pass
    # Source-checkout fallback: top-level anet_pack/ beside the anet/ package.
    p = Path(__file__).resolve().parents[2] / "anet_pack"
    return p if p.exists() else None


def _repo_root_if_clone() -> Path | None:
    """If running from a source checkout, return the repo root (it holds the
    user's existing workspace to migrate). Detected by a marker at the package
    parent (main.py / pyproject.toml / an existing anet.config.yaml)."""
    repo = Path(__file__).resolve().parents[2]
    markers = ("main.py", "pyproject.toml", "anet.config.yaml", "exanet.config.yaml")
    return repo if any((repo / m).exists() for m in markers) else None


def _copy(src: Path, dest: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.env", ".env"))
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def ensure_workspace(force: bool = False) -> list[str]:
    """Seed the Anet home with workspace items that are missing.

    For each item, prefer the user's existing clone content (migration) over the
    bundled default pack, so running from a checkout preserves their current setup.
    Returns the list of items that were newly created (empty when nothing seeded).
    """
    # Always seed the DEFAULT pack (not whatever pack is currently active).
    ws = _paths.default_pack_root()
    ws.mkdir(parents=True, exist_ok=True)
    _paths.anet_files_dir().mkdir(parents=True, exist_ok=True)

    # Legacy migration: older versions kept SOUL.md at the home root; it now lives
    # in the pack. Move an existing one in so the user's persona isn't lost.
    legacy_soul = _paths.home() / "SOUL.md"
    pack_soul = ws / "SOUL.md"
    if legacy_soul.exists() and not pack_soul.exists():
        try:
            shutil.move(str(legacy_soul), str(pack_soul))
        except Exception:
            pass

    repo = _repo_root_if_clone()
    pack = _pack_dir()
    seeded: list[str] = []

    for item in _ITEMS:
        dest = ws / item
        if dest.exists() and not force:
            continue
        # Migration source (the user's clone) wins; the default pack is the fallback.
        src: Path | None = None
        if not force and repo is not None and (repo / item).exists():
            src = repo / item
        elif pack is not None and (pack / item).exists():
            src = pack / item
        if src is None:
            continue
        try:
            _copy(src, dest)
            seeded.append(item)
        except Exception as exc:
            print(f"[workspace] could not seed '{item}': {exc}", file=sys.stderr)

    return seeded
