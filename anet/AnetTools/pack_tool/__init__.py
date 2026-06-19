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
                "action": {"type": "string", "enum": ["inspect", "export", "import_pack", "create"]},
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


_PACKAGE_LAUNCHERS = {"npx", "uvx", "uv", "pipx", "bunx", "pnpm", "yarn"}
_CODE_EXTS = (".js", ".mjs", ".cjs", ".ts", ".py", ".rb", ".jar", ".sh")
# A cloned/vendored project carries one of these manifests — the deterministic
# signal that an MCP's code should be OBTAINED from source, not shipped in the zip.
_PROJECT_MANIFESTS = {"package.json", "pyproject.toml", "setup.py", "cargo.toml",
                      "go.mod", "requirements.txt", "pom.xml", "build.gradle"}
_README_NAMES = {"readme.md", "readme", "readme.txt", "readme.rst"}


def _find_readme(d: Path) -> Path | None:
    if not d.exists():
        return None
    try:
        for p in d.iterdir():
            if p.is_file() and p.name.lower() in _README_NAMES:
                return p
    except OSError:
        pass
    return None


def _read_text(p: Path | None, limit: int = 2000) -> str:
    if p is None:
        return ""
    try:
        t = p.read_text(encoding="utf-8", errors="replace").strip()
        return t if len(t) <= limit else t[:limit] + "\n… [truncated]"
    except Exception:
        return ""


def _has_project_manifest(d: Path) -> bool:
    """True if a package manifest sits at/just under `d` — i.e. it's a vendored
    repo/clone. Checked shallow (depth ≤ 2) so it stays fast on huge trees."""
    if not d.exists():
        return False
    dirs = [d]
    try:
        dirs += [c for c in d.iterdir() if c.is_dir()]
    except OSError:
        pass
    for base in dirs:
        try:
            for p in base.iterdir():
                if p.is_file() and p.name.lower() in _PROJECT_MANIFESTS:
                    return True
        except OSError:
            pass
    return False


def _looks_like_local_path(token: str) -> bool:
    """True if a command/arg/cwd points at a local file or directory (repo code)
    rather than a published package name."""
    t = (token or "").strip()
    if not t:
        return False
    if t in (".", ".."):
        return True
    if t.startswith(("/", "~", "./", "../", ".\\", "..\\")):
        return True
    if len(t) > 2 and t[1] == ":" and (t[2] in "/\\"):   # Windows drive path C:\...
        return True
    if t.lower().endswith(_CODE_EXTS):                    # an entry script
        return True
    # A path-ish token referencing a build/source dir (e.g. mcps/foo/dist/index.js).
    if ("/" in t or "\\" in t) and any(
        seg in t.lower() for seg in ("dist", "build", "src", "bin", "out", "target", "mcps")
    ):
        return True
    return False


def _mcp_details(pack: Path) -> list[dict]:
    """Per-MCP launch info so PackSmith can tell a package-based server (npx/uvx)
    from a REPO-BACKED one (runs from local code that won't exist on the recipient's
    machine and must be cloned/built)."""
    out: list[dict] = []
    mdir = pack / "mcps"
    if not mdir.exists():
        return out
    for d in sorted(mdir.iterdir()):
        cfg_file = d / "config.yaml"
        if not (d.is_dir() and cfg_file.exists()):
            continue
        cfg = _read_yaml(cfg_file)
        command = str(cfg.get("command") or "")
        args    = [str(a) for a in (cfg.get("args") or [])]
        cwd     = cfg.get("cwd")
        launcher = command.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
        package_based = launcher in _PACKAGE_LAUNCHERS
        path_refs = [t for t in ([command, *args] + ([str(cwd)] if cwd else []))
                     if _looks_like_local_path(t)]
        # Code bundled in this MCP's dir beyond its config + README (a vendored repo).
        bundled_code = any(
            p.name != "config.yaml" and p.name.lower() not in _README_NAMES
            for p in d.iterdir()
        )
        vendored_project = _has_project_manifest(d)
        out.append({
            "name": d.name,
            "command": command,
            "args": args,
            "cwd": cwd,
            "package_based": package_based,
            "local_path_refs": path_refs,
            "bundled_code": bundled_code,
            # A cloned project bundled in the pack — its code is STRIPPED on export
            # (deterministic), so the README must say how to obtain it.
            "vendored_project": vendored_project,
            # Repo-backed = runs from local code, not a package launcher.
            "repo_backed": (bool(path_refs) or bundled_code) and not package_based,
            # The MCP's own README (source/install/entry notes), if the author wrote one.
            "readme": _read_text(_find_readme(d)),
        })
    return out


def _inspect(pack: Path) -> dict:
    exanet = _read_yaml(pack / "exanet.config.yaml")
    tools  = [t.get("name") for t in (exanet.get("tools") or []) if isinstance(t, dict) and t.get("name")]
    agents = [a.get("name") for a in (exanet.get("agents") or []) if isinstance(a, dict) and a.get("name")]

    extool_dirs = sorted(d.name for d in (pack / "ExTools").iterdir()
                         if d.is_dir() and (d / "__init__.py").exists()) if (pack / "ExTools").exists() else []
    mcps = sorted(d.name for d in (pack / "mcps").iterdir()
                  if d.is_dir() and (d / "config.yaml").exists()) if (pack / "mcps").exists() else []
    mcp_details = _mcp_details(pack)
    skills = sorted(f.stem for f in (pack / "skills").glob("*.md")
                    if f.stem.lower() != "readme") if (pack / "skills").exists() else []

    env_vars: dict[str, list[str]] = {}
    for ex in pack.rglob("*.env.example"):
        rel = ex.relative_to(pack).as_posix()
        env_vars[rel.replace(".example", "")] = _env_keys(ex)

    # Per-component READMEs (the authoring-rule docs) PackSmith should read to write
    # accurate setup steps. ExTools/ExAgents readmes are pointed to; MCP readmes are
    # inlined in mcp_details above.
    component_readmes: list[str] = []
    for sub in ("ExTools", "ExAgents"):
        base = pack / sub
        if base.exists():
            for comp in sorted(base.iterdir()):
                rd = _find_readme(comp) if comp.is_dir() else None
                if rd:
                    component_readmes.append(rd.relative_to(pack).as_posix())

    return {
        "name": pack.name,
        "tools": tools,
        "extool_dirs": extool_dirs,
        "agents": agents,
        "mcps": mcps,
        "mcp_details": mcp_details,            # per-MCP launch info + repo_backed + readme
        "component_readmes": component_readmes,  # ExTools/ExAgents README paths to read
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

            # Deterministic strip of VENDORED MCP repos: an MCP that bundles a cloned
            # project (has a package manifest) ships only its config.yaml + README —
            # the code itself is obtained from source by the recipient (per its README).
            # This is filesystem logic, so a weak authoring model can't produce a
            # broken/bloated zip.
            stripped: list[str] = []
            for m in manifest.get("mcp_details", []):
                if not m.get("vendored_project"):
                    continue
                mdir = stage / "mcps" / m["name"]
                if not mdir.exists():
                    continue
                for item in list(mdir.iterdir()):
                    if item.name == "config.yaml" or item.name.lower() in _README_NAMES:
                        continue
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)
                stripped.append(m["name"])

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

        note = "secrets (.env) and node_modules/.git/__pycache__ were stripped"
        if stripped:
            note += (f"; vendored MCP code stripped (obtain from source per README): "
                     f"{', '.join(stripped)}")
        return {"result": {"zip": str(out_path), "manifest": manifest,
                            "stripped_mcp_code": stripped, "note": note}}

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

    if action == "create":
        name = _safe_name((params.get("name") or "").strip())
        if not name:
            return {"error": "create requires a 'name'"}
        dest = _paths.yourpacks_dir() / name
        if dest.exists():
            return {"error": f"a pack named '{name}' already exists at {dest}"}

        default = _paths.default_pack_root()
        dest.mkdir(parents=True)
        for sub in ("ExTools", "ExAgents", "mcps", "skills"):
            (dest / sub).mkdir()

        # Base config + persona: copy the default pack's, so the new pack is a valid,
        # working workspace from the start. The user edits/builds it from here.
        for fname in ("anet.config.yaml", "SOUL.md"):
            src = default / fname
            if src.exists():
                shutil.copy2(src, dest / fname)

        # Start with an EMPTY extension registry — the smiths fill it in.
        (dest / "exanet.config.yaml").write_text(
            "# exanet.config.yaml — external tools/agents for this pack.\n"
            "# Empty to start. Build it up with /newtool, /newagent, /addmcp\n"
            "# (or by hand). See anet_pack's guides for the format.\n\n"
            "tools: []\n\n"
            "agents: []\n\n"
            "# attach extra tools/MCP to built-in agents (code_agent, research_agent, …)\n"
            "# attach:\n"
            "#   code_agent:\n"
            "#     tools: [my_tool]\n",
            encoding="utf-8",
        )
        return {"result": {"name": name, "path": str(dest)}}

    return {"error": f"unknown action '{action}'"}
