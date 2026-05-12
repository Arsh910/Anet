"""
graph.py — anet graph subcommand implementation.

Three commands:
  anet graph build   — full scan + LLM summarisation, writes .anet/graph.json
  anet graph update  — hash-check, re-summarises only changed files (fast)
  anet graph show    — prints compact project overview (~500 tokens)

Graph is stored at <project_root>/.anet/graph.json (project-local).
LLM: Gemini Flash (cheap, fast).  Requires GOOGLE_API_KEY in .env.
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

console = Console()

# ── File scanning config ──────────────────────────────────────────────────────

DEFAULT_INCLUDE = {
    "*.py", "*.js", "*.ts", "*.jsx", "*.tsx",
    "*.go", "*.rs", "*.java", "*.yaml", "*.yml",
}

DEFAULT_EXCLUDE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    "dist", "build", ".anet", "VIGA-release", ".pytest_cache",
    "htmlcov", ".mypy_cache", ".ruff_cache", "eggs", "*.egg-info",
}

DEFAULT_EXCLUDE_FILES = {"*.pyc", "*.pyo", "*.pyd", "*.min.js", "*.map"}

MAX_FILE_BYTES  = 80_000   # skip files larger than this
SUMMARY_LINES   = 120      # max lines sent to LLM for summarisation
MIN_LINES_FOR_LLM = 4      # files shorter than this get a trivial summary


# ── Project root detection ────────────────────────────────────────────────────

def find_project_root(start: Path) -> Path:
    """Walk up from start to find the project root (git, pyproject, package.json)."""
    markers = {".git", "pyproject.toml", "package.json", "setup.py", "go.mod"}
    cur = start.resolve()
    for _ in range(10):
        if any((cur / m).exists() for m in markers):
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return start.resolve()


# ── Graph storage ─────────────────────────────────────────────────────────────

def graph_path(root: Path) -> Path:
    return root / ".anet" / "graph.json"


def load_graph(root: Path) -> dict:
    p = graph_path(root)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"meta": {}, "nodes": {}, "edges": []}


def save_graph(root: Path, graph: dict) -> None:
    p = graph_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")


# ── File utilities ────────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    try:
        h.update(path.read_bytes())
    except OSError:
        return ""
    return h.hexdigest()[:16]


def _is_excluded(path: Path, root: Path) -> bool:
    parts = path.relative_to(root).parts
    for part in parts:
        for pat in DEFAULT_EXCLUDE_DIRS:
            if fnmatch(part, pat):
                return True
    for pat in DEFAULT_EXCLUDE_FILES:
        if fnmatch(path.name, pat):
            return True
    return False


def _language(path: Path) -> str:
    return {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".jsx": "jsx", ".tsx": "tsx", ".go": "go", ".rs": "rust",
        ".java": "java", ".yaml": "yaml", ".yml": "yaml",
    }.get(path.suffix.lower(), "text")


def scan_files(root: Path, include: set[str]) -> list[Path]:
    """Return all included, non-excluded files under root."""
    result = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if _is_excluded(p, root):
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            continue
        if any(fnmatch(p.name, pat) for pat in include):
            result.append(p)
    return result


# ── Import / export extraction ────────────────────────────────────────────────

def _extract_python(source: str) -> tuple[list[str], list[str]]:
    imports, exports = [], []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return _extract_generic(source), []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_") and isinstance(node, ast.stmt):
                exports.append(node.name)
    # Top-level only
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_") and node.name not in exports:
                exports.append(node.name)
    return list(dict.fromkeys(imports)), list(dict.fromkeys(exports))


def _extract_generic(source: str) -> list[str]:
    """Regex-based import extraction for non-Python files."""
    patterns = [
        r'import\s+[\'"]([^\'"]+)[\'"]',           # JS/TS import "..."
        r'from\s+[\'"]([^\'"]+)[\'"]',              # JS/TS from "..."
        r'require\([\'"]([^\'"]+)[\'"]\)',           # CommonJS
        r'^\s*import\s+([\w.]+)',                   # Go/Java import
    ]
    found = []
    for pat in patterns:
        for m in re.finditer(pat, source, re.MULTILINE):
            found.append(m.group(1).split("/")[0])
    return list(dict.fromkeys(found))


def extract_file_info(path: Path, source: str) -> tuple[list[str], list[str]]:
    if path.suffix == ".py":
        return _extract_python(source)
    return _extract_generic(source), []


# ── LLM summarisation ─────────────────────────────────────────────────────────

_SUMMARISE_SYSTEM = (
    "You are a code documentation tool. Summarise the given file in exactly 1-2 sentences. "
    "Focus on: what the file does, its main entry point or key exported names, and any critical pattern. "
    "Be concrete and specific — name actual functions or classes. "
    "Under 40 words total. No markdown. No bullet points. Plain text only."
)


async def _summarise(client, rel_path: str, source: str, language: str) -> str:
    lines = source.splitlines()
    if len(lines) < MIN_LINES_FOR_LLM:
        return source.strip()[:120] or f"Short {language} file."
    preview = "\n".join(lines[:SUMMARY_LINES])
    try:
        resp = await client.chat.completions.create(
            model="gemini-2.5-flash",
            messages=[
                {"role": "system", "content": _SUMMARISE_SYSTEM},
                {"role": "user", "content": f"File: {rel_path}\n\n```{language}\n{preview}\n```"},
            ],
            temperature=0,
            max_tokens=80,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        return f"[summary unavailable: {exc}]"


async def _summarise_batch(client, files: list[tuple[str, str, str]], progress, task_id) -> dict[str, str]:
    """Summarise up to 8 files concurrently."""
    semaphore = asyncio.Semaphore(8)
    results: dict[str, str] = {}

    async def _one(rel_path, source, language):
        async with semaphore:
            summary = await _summarise(client, rel_path, source, language)
            results[rel_path] = summary
            progress.advance(task_id)

    await asyncio.gather(*[_one(r, s, l) for r, s, l in files])
    return results


# ── Build & Update ────────────────────────────────────────────────────────────

async def _build(root: Path, include: set[str], force: bool = False) -> dict:
    load_dotenv(root / ".env")
    key = os.getenv("GOOGLE_API_KEY")
    if not key:
        console.print("[red]GOOGLE_API_KEY not set — cannot summarise files.[/red]")
        sys.exit(1)

    from openai import AsyncOpenAI
    client = AsyncOpenAI(
        api_key=key,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    )

    existing = {} if force else load_graph(root).get("nodes", {})
    files    = scan_files(root, include)

    nodes: dict = {}
    edges: list = []
    to_summarise: list[tuple[str, str, str]] = []

    console.print(f"  [dim]Found {len(files)} files[/dim]")

    # First pass: hash check, extract imports/exports
    for path in files:
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel   = str(path.relative_to(root)).replace("\\", "/")
        h     = _sha256(path)
        lang  = _language(path)
        imps, exps = extract_file_info(path, source)

        node = {
            "summary":       existing.get(rel, {}).get("summary", ""),
            "exports":       exps,
            "imports":       imps,
            "last_modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat()[:19],
            "hash":          h,
            "language":      lang,
            "size_bytes":    path.stat().st_size,
        }
        nodes[rel] = node

        old_hash = existing.get(rel, {}).get("hash", "")
        if not node["summary"] or old_hash != h:
            to_summarise.append((rel, source, lang))

    console.print(f"  [dim]{len(to_summarise)} files need summarisation[/dim]")

    if to_summarise:
        with Progress(
            SpinnerColumn(),
            TextColumn("[dim]{task.description}[/dim]"),
            BarColumn(bar_width=30),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task("Summarising...", total=len(to_summarise))
            summaries = await _summarise_batch(client, to_summarise, progress, task_id)

        for rel, summary in summaries.items():
            nodes[rel]["summary"] = summary

    # Build edges from imports (resolve to project-relative paths)
    node_names = set(nodes.keys())
    for rel, node in nodes.items():
        source_dir = str(Path(rel).parent).replace("\\", "/")
        for imp in node["imports"]:
            # Try to match import to a known node
            candidates = [
                n for n in node_names
                if Path(n).stem == imp or Path(n).stem == imp.split(".")[-1]
                or n.endswith(f"/{imp}.py") or n.endswith(f"/{imp}/__init__.py")
            ]
            for target in candidates[:1]:
                if target != rel:
                    edges.append({"from": rel, "to": target, "type": "imports"})

    graph = {
        "meta": {
            "project":     root.name,
            "root":        str(root),
            "built_at":    datetime.now().isoformat()[:19],
            "total_files": len(nodes),
        },
        "nodes": nodes,
        "edges": edges,
    }
    return graph


async def _update(root: Path) -> tuple[dict, int]:
    """Reload graph, re-summarise only changed/new files."""
    existing_graph = load_graph(root)
    existing_nodes = existing_graph.get("nodes", {})

    # Detect changes
    all_files = scan_files(root, DEFAULT_INCLUDE)
    current_hashes = {
        str(p.relative_to(root)).replace("\\", "/"): _sha256(p)
        for p in all_files
    }

    changed = [
        rel for rel, h in current_hashes.items()
        if existing_nodes.get(rel, {}).get("hash") != h
    ]
    deleted = [rel for rel in existing_nodes if rel not in current_hashes]

    if not changed and not deleted:
        return existing_graph, 0

    # Remove deleted
    for rel in deleted:
        existing_nodes.pop(rel, None)

    # Re-summarise changed using same logic as build but for subset
    if changed:
        load_dotenv(root / ".env")
        key = os.getenv("GOOGLE_API_KEY")
        if not key:
            console.print("[red]GOOGLE_API_KEY not set.[/red]")
            sys.exit(1)
        from openai import AsyncOpenAI
        client = AsyncOpenAI(
            api_key=key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        to_summarise = []
        for rel in changed:
            path = root / rel.replace("/", "\\")
            if not path.exists():
                continue
            try:
                source = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            lang = _language(path)
            imps, exps = extract_file_info(path, source)
            existing_nodes[rel] = {
                "summary":       "",
                "exports":       exps,
                "imports":       imps,
                "last_modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat()[:19],
                "hash":          current_hashes[rel],
                "language":      lang,
                "size_bytes":    path.stat().st_size,
            }
            to_summarise.append((rel, source, lang))

        with Progress(SpinnerColumn(), TextColumn("[dim]{task.description}[/dim]"),
                      BarColumn(bar_width=30), TaskProgressColumn(), console=console) as progress:
            task_id = progress.add_task("Updating...", total=len(to_summarise))
            summaries = await _summarise_batch(client, to_summarise, progress, task_id)
        for rel, summary in summaries.items():
            existing_nodes[rel]["summary"] = summary

    existing_graph["nodes"] = existing_nodes
    existing_graph["meta"]["built_at"] = datetime.now().isoformat()[:19]
    existing_graph["meta"]["total_files"] = len(existing_nodes)
    return existing_graph, len(changed) + len(deleted)


# ── Show formatter ────────────────────────────────────────────────────────────

def format_show(graph: dict) -> str:
    meta  = graph.get("meta", {})
    nodes = graph.get("nodes", {})

    if not nodes:
        return "Graph is empty. Run: anet graph build"

    lines = []
    lines.append(f"{'═' * 56}")
    lines.append(f"  PROJECT: {meta.get('project', '?')}  "
                 f"({meta.get('total_files', len(nodes))} files, "
                 f"built {str(meta.get('built_at', '?'))[:16]})")
    lines.append(f"{'═' * 56}")

    # Group by directory
    groups: dict[str, list[tuple[str, dict]]] = {}
    for rel, node in sorted(nodes.items()):
        parts = rel.split("/")
        if len(parts) == 1:
            grp = "(root)"
        elif len(parts) == 2:
            grp = parts[0] + "/"
        else:
            grp = "/".join(parts[:2]) + "/"
        groups.setdefault(grp, []).append((rel, node))

    for grp, items in sorted(groups.items()):
        lines.append(f"\n{grp.upper()}")
        for rel, node in items:
            name    = Path(rel).name
            summary = node.get("summary", "—")
            exps    = node.get("exports", [])
            exp_str = f"  [{', '.join(exps[:4])}{'…' if len(exps) > 4 else ''}]" if exps else ""
            lines.append(f"  {name:<30} {summary}{exp_str}")

    # Edges / dependency summary
    edges = graph.get("edges", [])
    if edges:
        lines.append("\nKEY DEPENDENCIES")
        seen = set()
        for e in edges:
            pair = f"{Path(e['from']).name} → {Path(e['to']).name}"
            if pair not in seen:
                lines.append(f"  {pair}")
                seen.add(pair)
            if len(seen) >= 15:
                lines.append(f"  … ({len(edges) - 15} more)")
                break

    return "\n".join(lines)


# ── Click commands ────────────────────────────────────────────────────────────

@click.group("graph")
def graph_group() -> None:
    """Build and query the project code graph."""


@graph_group.command("build")
@click.option("--path",  default=".", type=click.Path(exists=True),
              help="Project root (default: walk up from cwd)")
@click.option("--force", is_flag=True, help="Re-summarise all files, ignoring cache")
def graph_build(path: str, force: bool) -> None:
    """Scan project, summarise all files, write .anet/graph.json."""
    root = find_project_root(Path(path))
    console.print()
    console.print(f"[bold]Building graph[/bold] for [dim]{root}[/dim]")
    console.print()

    graph = asyncio.run(_build(root, DEFAULT_INCLUDE, force=force))
    save_graph(root, graph)

    console.print()
    console.print(f"[bold green]✓ Graph built[/bold green]  "
                  f"[dim]{len(graph['nodes'])} files → {graph_path(root)}[/dim]")
    console.print()


@graph_group.command("update")
@click.option("--path", default=".", type=click.Path(exists=True),
              help="Project root (default: walk up from cwd)")
def graph_update(path: str) -> None:
    """Check file hashes, re-summarise only changed files."""
    root = find_project_root(Path(path))
    if not graph_path(root).exists():
        console.print(
            "[yellow]No graph found. Run [bold]anet graph build[/bold] first.[/yellow]"
        )
        return

    console.print()
    console.print(f"[bold]Updating graph[/bold] for [dim]{root}[/dim]")
    console.print()

    graph, n_changed = asyncio.run(_update(root))
    save_graph(root, graph)

    console.print()
    if n_changed == 0:
        console.print("[green]✓ Graph is up to date — no changes detected.[/green]")
    else:
        console.print(f"[bold green]✓ Graph updated[/bold green]  "
                      f"[dim]{n_changed} file(s) refreshed[/dim]")
    console.print()


@graph_group.command("show")
@click.option("--path",  default=".", type=click.Path(exists=True),
              help="Project root (default: walk up from cwd)")
@click.option("--json",  "as_json", is_flag=True, help="Output raw JSON instead of formatted text")
@click.option("--file",  "filter_file", default=None,
              help="Show summary for a single file (partial name match)")
def graph_show(path: str, as_json: bool, filter_file: Optional[str]) -> None:
    """Print compact project overview from the graph."""
    root = find_project_root(Path(path))
    p    = graph_path(root)
    if not p.exists():
        console.print(
            "[yellow]No graph found. Run [bold]anet graph build[/bold] first.[/yellow]"
        )
        return

    graph = load_graph(root)

    if as_json:
        console.print_json(json.dumps(graph, indent=2))
        return

    if filter_file:
        nodes = graph.get("nodes", {})
        matches = {k: v for k, v in nodes.items() if filter_file.lower() in k.lower()}
        if not matches:
            console.print(f"[yellow]No file matching '{filter_file}' in graph.[/yellow]")
            return
        for rel, node in matches.items():
            console.print(f"\n[bold]{rel}[/bold]")
            console.print(f"  [dim]Summary:[/dim]  {node.get('summary', '—')}")
            if node.get("exports"):
                console.print(f"  [dim]Exports:[/dim]  {', '.join(node['exports'])}")
            if node.get("imports"):
                console.print(f"  [dim]Imports:[/dim]  {', '.join(node['imports'][:8])}")
        console.print()
        return

    console.print()
    console.print(format_show(graph))
    console.print()
