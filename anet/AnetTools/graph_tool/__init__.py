"""
graph_tool — reads and builds the project code graph for the code_agent.

The graph is stored at: <project_root>/.anet/graph.json

Actions:
  build   — scan a project and build/update its code graph (LLM-powered)
  show    — compact project overview (~500 tokens), call this first on every task
  find    — search for a file by name (partial match)
  deps    — show what a file imports and what imports it
  summary — one-file detail: summary + exports + imports
"""
from __future__ import annotations

import json
from pathlib import Path


SCHEMA = {
    "type": "function",
    "function": {
        "name": "graph_tool",
        "description": (
            "Build or query the project code graph. "
            "Use action='build' to index a project. "
            "Use action='show' at the start of every coding task to understand the codebase."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["build", "show", "find", "deps", "summary"],
                    "description": (
                        "build=scan project and create/update graph, "
                        "show=full project overview, "
                        "find=search file by partial name, "
                        "deps=dependency chain for a file, "
                        "summary=detail for one file"
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "File name or partial path for find/deps/summary actions.",
                },
                "project_path": {
                    "type": "string",
                    "description": "Project root directory. Default: current working directory.",
                },
            },
            "required": ["action"],
        },
    },
}


# ── Graph loading ─────────────────────────────────────────────────────────────

def _find_graph(start: str | None) -> Path | None:
    """Walk up from start to find .anet/graph.json."""
    root = Path(start).resolve() if start else Path.cwd()
    for _ in range(8):
        candidate = root / ".anet" / "graph.json"
        if candidate.exists():
            return candidate
        if root.parent == root:
            break
        root = root.parent
    return None


def _load(project_path: str | None) -> dict | None:
    p = _find_graph(project_path)
    if p is None:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


# ── Formatters ────────────────────────────────────────────────────────────────

def _fmt_show(graph: dict) -> str:
    meta  = graph.get("meta", {})
    nodes = graph.get("nodes", {})

    lines = [
        f"PROJECT: {meta.get('project', '?')}  "
        f"({meta.get('total_files', len(nodes))} files, "
        f"graph built {str(meta.get('built_at', '?'))[:16]})",
        "",
    ]

    # Group by top-level directory
    groups: dict[str, list[tuple[str, dict]]] = {}
    for rel, node in sorted(nodes.items()):
        parts = rel.split("/")
        grp   = parts[0] + "/" if len(parts) > 1 else "(root)"
        groups.setdefault(grp, []).append((rel, node))

    for grp, items in sorted(groups.items()):
        lines.append(grp.upper())
        for rel, node in items:
            name    = Path(rel).name
            summary = node.get("summary", "—")
            exps    = node.get("exports", [])
            exp_str = f"  [{', '.join(exps[:4])}{'…' if len(exps) > 4 else ''}]" if exps else ""
            lines.append(f"  {name:<32} {summary}{exp_str}")
        lines.append("")

    edges = graph.get("edges", [])
    if edges:
        lines.append("KEY DEPENDENCIES")
        seen: set[str] = set()
        for e in edges:
            pair = f"{Path(e['from']).name} → {Path(e['to']).name}"
            if pair not in seen:
                lines.append(f"  {pair}")
                seen.add(pair)
            if len(seen) >= 12:
                remaining = len(edges) - len(seen)
                if remaining > 0:
                    lines.append(f"  … ({remaining} more)")
                break

    return "\n".join(lines)


def _fmt_file(rel: str, node: dict) -> str:
    lines = [
        f"FILE: {rel}",
        f"  Summary : {node.get('summary', '—')}",
        f"  Language: {node.get('language', '?')}",
        f"  Modified: {node.get('last_modified', '?')}",
        f"  Size    : {node.get('size_bytes', 0):,} bytes",
    ]
    if node.get("exports"):
        lines.append(f"  Exports : {', '.join(node['exports'])}")
    if node.get("imports"):
        lines.append(f"  Imports : {', '.join(node['imports'][:10])}")
    return "\n".join(lines)


def _fmt_deps(rel: str, node: dict, graph: dict) -> str:
    edges  = graph.get("edges", [])
    import_edges  = [e["to"]   for e in edges if e["from"] == rel]
    used_by_edges = [e["from"] for e in edges if e["to"]   == rel]
    lines = [
        f"DEPS: {rel}",
        f"  Summary : {node.get('summary', '—')}",
    ]
    if import_edges:
        lines.append("  Imports :")
        for t in import_edges:
            lines.append(f"    → {t}")
    if used_by_edges:
        lines.append("  Used by :")
        for f in used_by_edges:
            lines.append(f"    ← {f}")
    if not import_edges and not used_by_edges:
        lines.append("  No tracked dependencies.")
    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

async def run(params: dict) -> dict:
    action       = params.get("action", "show")
    query        = params.get("query", "")
    project_path = params.get("project_path")

    # ── build ─────────────────────────────────────────────────────────────────
    if action == "build":
        import os
        from dotenv import load_dotenv
        from anet.cli.graph import _build, save_graph, find_project_root, DEFAULT_INCLUDE

        start = Path(project_path).resolve() if project_path else Path.cwd()
        if not start.exists():
            return {"error": f"Path not found: {project_path}"}

        root = find_project_root(start)
        load_dotenv(root / ".env")
        if not os.getenv("GOOGLE_API_KEY"):
            return {"error": "GOOGLE_API_KEY not set — cannot build graph."}

        try:
            built = await _build(root, DEFAULT_INCLUDE)
            save_graph(root, built)
            return {
                "result": (
                    f"Graph built: {len(built['nodes'])} files indexed. "
                    f"Project: {root.name}  Root: {root}"
                )
            }
        except Exception as exc:
            return {"error": f"Graph build failed: {exc}"}

    graph = _load(project_path)
    if graph is None:
        return {
            "result": "NO_GRAPH: No project graph found. Proceed with the task without graph context."
        }

    nodes = graph.get("nodes", {})

    # ── show ──────────────────────────────────────────────────────────────────
    if action == "show":
        return {"result": _fmt_show(graph)}

    # ── find ──────────────────────────────────────────────────────────────────
    if action == "find":
        if not query:
            return {"error": "Provide a query for action='find'"}
        matches = {k: v for k, v in nodes.items() if query.lower() in k.lower()}
        if not matches:
            return {"error": f"No files matching '{query}' in graph."}
        lines = []
        for rel, node in list(matches.items())[:10]:
            lines.append(f"  {rel}")
            lines.append(f"    {node.get('summary', '—')}")
        return {"result": "\n".join(lines)}

    # ── summary ───────────────────────────────────────────────────────────────
    if action == "summary":
        if not query:
            return {"error": "Provide a query for action='summary'"}
        matches = {k: v for k, v in nodes.items() if query.lower() in k.lower()}
        if not matches:
            return {"error": f"No files matching '{query}' in graph."}
        rel, node = next(iter(matches.items()))
        return {"result": _fmt_file(rel, node)}

    # ── deps ──────────────────────────────────────────────────────────────────
    if action == "deps":
        if not query:
            return {"error": "Provide a query for action='deps'"}
        matches = {k: v for k, v in nodes.items() if query.lower() in k.lower()}
        if not matches:
            return {"error": f"No files matching '{query}' in graph."}
        rel, node = next(iter(matches.items()))
        return {"result": _fmt_deps(rel, node, graph)}

    return {"error": f"Unknown action '{action}'. Use: show, find, deps, summary"}
