"""
pack_tool — deterministic packaging for shareable ANet packs.

A "pack" is a self-contained workspace folder (config + ExTools/ExAgents/mcps/
skills + SOUL.md). This tool does the exact, safe mechanics the PackSmith agent
relies on, so the model never has to hand-roll file ops:

  inspect      — scan a pack → manifest (tools/agents/mcps/skills + required env vars)
  export       — copy a pack → a sanitized shareable .zip (SECRETS + heavy/runtime
                 junk stripped), optionally embedding a README
  import_pack  — extract a received .zip into <home>/shared_packs/<name>/

It NEVER bundles secrets: every `.env` is stripped on export (`.env.example` is
kept). Activation is a separate, explicit step (/changepack) — importing never
runs the pack's code.
"""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

SCHEMA = {
    "type": "function",
    "function": {
        "name": "pack_tool",
        "description": (
            "Package shareable ANet packs. Actions: 'inspect' (scan a pack folder → "
            "manifest of tools/agents/mcps/skills + required env vars), 'export' (copy a "
            "pack to a sanitized .zip with all secrets/.env and heavy junk stripped, "
            "optionally embedding a README), 'import_pack' (extract a received .zip into "
            "<home>/shared_packs/<name>). Never bundles secrets; never runs pack code."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["inspect", "export", "import_pack"]},
                "path":   {"type": "string", "description": "inspect/export: path to the pack folder. Blank on export = the active pack."},
                "out":    {"type": "string", "description": "export: output .zip path (default: <home>/anet_files/<name>.zip)."},
                "readme": {"type": "string", "description": "export: README.md text to embed in the zip (the usage guide you wrote)."},
                "zip":    {"type": "string", "description": "import_pack: path to the .zip to import."},
                "name":   {"type": "string", "description": "import_pack: override the imported pack's folder name."},
            },
            "required": ["action"],
        },
    },
}

# Never copied into a shared pack (secrets, runtime artifacts, heavy/VCS junk).
_STRIP_DIRS = {"__pycache__", "node_modules", ".git", "archived"}
_STRIP_EXACT = {".usage.json"}


def _is_secret_env(name: str) -> bool:
    # Strip `.env` and `*.env`, but keep `.env.example`.
    return name == ".env" or (name.endswith(".env") and name != ".env.example")


def _ignore(_dir, names):
    drop = set()
    for n in names:
        if n in _STRIP_DIRS or n in _STRIP_EXACT or n.endswith(".pyc") or _is_secret_env(n):
            drop.add(n)
    return drop


def _read_yaml(p: Path) -> dict:
    try:
        import yaml
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _env_keys(example: Path) -> list[str]:
    keys = []
    try:
        for line in example.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                keys.append(line.split("=", 1)[0].strip())
    except Exception:
        pass
    return keys


def _inspect(pack: Path) -> dict:
    exanet = _read_yaml(pack / "exanet.config.yaml")
    tools  = [t.get("name") for t in (exanet.get("tools") or []) if isinstance(t, dict) and t.get("name")]
    agents = [a.get("name") for a in (exanet.get("agents") or []) if isinstance(a, dict) and a.get("name")]

    extool_dirs = sorted(d.name for d in (pack / "ExTools").iterdir()
                         if d.is_dir() and (d / "__init__.py").exists()) if (pack / "ExTools").exists() else []
    mcps = sorted(d.name for d in (pack / "mcps").iterdir()
                  if d.is_dir() and (d / "config.yaml").exists()) if (pack / "mcps").exists() else []
    skills = sorted(f.stem for f in (pack / "skills").glob("*.md")
                    if f.stem.lower() != "readme") if (pack / "skills").exists() else []

    env_vars: dict[str, list[str]] = {}
    for ex in pack.rglob("*.env.example"):
        rel = ex.relative_to(pack).as_posix()
        env_vars[rel.replace(".example", "")] = _env_keys(ex)

    return {
        "name": pack.name,
        "tools": tools,
        "extool_dirs": extool_dirs,
        "agents": agents,
        "mcps": mcps,
        "skills": skills,
        "env_files": env_vars,                 # {relative .env path: [KEY, ...]}
        "has_soul": (pack / "SOUL.md").exists(),
        "has_readme": (pack / "README.md").exists(),
    }


def _safe_name(name: str) -> str:
    keep = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in name).strip("-")
    return keep or "pack"


async def run(params: dict) -> dict:
    from anet.core import paths as _paths
    action = (params.get("action") or "").strip()

    if action == "inspect":
        path = (params.get("path") or "").strip()
        pack = Path(path).expanduser() if path else _paths.workspace_root()
        if not pack.exists():
            return {"error": f"pack not found: {pack}"}
        return {"result": _inspect(pack)}

    if action == "export":
        path = (params.get("path") or "").strip()
        pack = Path(path).expanduser() if path else _paths.workspace_root()
        if not pack.exists():
            return {"error": f"pack not found: {pack}"}
        name = _safe_name(pack.name)
        manifest = _inspect(pack)

        out = params.get("out", "").strip()
        out_path = Path(out).expanduser() if out else (_paths.anet_files_dir() / f"{name}.zip")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        stage_parent = Path(tempfile.mkdtemp())
        stage = stage_parent / name
        try:
            shutil.copytree(pack, stage, ignore=_ignore)
            readme = params.get("readme")
            if readme:
                (stage / "README.md").write_text(readme, encoding="utf-8")
            # Zip with the pack name as the single top-level folder.
            if out_path.exists():
                out_path.unlink()
            with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in stage.rglob("*"):
                    if f.is_file():
                        zf.write(f, arcname=f"{name}/{f.relative_to(stage).as_posix()}")
        finally:
            shutil.rmtree(stage_parent, ignore_errors=True)

        return {"result": {"zip": str(out_path), "manifest": manifest,
                            "note": "secrets (.env) and node_modules/.git/__pycache__ were stripped"}}

    if action == "import_pack":
        zip_path = Path((params.get("zip") or "").strip()).expanduser()
        if not zip_path.exists():
            return {"error": f"zip not found: {zip_path}"}

        tmp = Path(tempfile.mkdtemp())
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(tmp)
            # The pack is the single top-level dir (or tmp itself if flat).
            tops = [d for d in tmp.iterdir() if d.is_dir()]
            extracted = tops[0] if len(tops) == 1 else tmp
            name = _safe_name(params.get("name", "").strip() or extracted.name or zip_path.stem)

            dest_root = _paths.shared_packs_dir()
            dest_root.mkdir(parents=True, exist_ok=True)
            dest = dest_root / name
            i = 2
            while dest.exists():
                dest = dest_root / f"{name}-{i}"
                i += 1
            shutil.copytree(extracted, dest)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        manifest = _inspect(dest)
        readme = ""
        rp = dest / "README.md"
        if rp.exists():
            readme = rp.read_text(encoding="utf-8")
        return {"result": {"path": str(dest), "name": dest.name,
                            "manifest": manifest, "readme": readme,
                            "env_files_needed": list(manifest.get("env_files", {}).keys())}}

    return {"error": f"unknown action '{action}'"}
