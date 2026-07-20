"""
bench.py — AdaptOrch vs QweenBee A/B harness.

Runs the fixed task suite (bench_tasks.json) through one or both orchestration
engines using the SAME agents/tools (the frozen-worker premise both engines
share), and reports wall-clock, tokens, and — for qweenbee — the generated
graph's source/consistency/skills_used pulled from its evidence log.

    python -m anet.QweenBee.bench --engine both --split all --limit 0

No MCP servers, no ExAgents/ExTools: deliberately hermetic so both engines see
an identical worker pool, and so a bench run doesn't depend on what MCP
servers happen to be configured. AGENTS is imported straight from
anet.AnetAgents.agents_config, which already applies anet.config.yaml's
per-agent model/provider overrides at import time — the same agents a real
session would use, minus MCP.

No automated reply-quality judgment: replies are logged (reply_chars only in
the summary) for a human to read. An LLM judge is a known future upgrade, not
built here.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


# ── Suite loading ────────────────────────────────────────────────────────────

def load_suite(path=None, split: str = "all", limit: int = 0) -> list[dict]:
    """Read bench_tasks.json (or a custom suite), filter by split, truncate."""
    if split not in ("all", "train", "val"):
        raise ValueError(f"unknown split: {split!r} (expected 'all', 'train', or 'val')")
    p = Path(path) if path else Path(__file__).resolve().parent / "bench_tasks.json"
    with open(p, "r", encoding="utf-8") as f:
        tasks = json.load(f)
    if split != "all":
        tasks = [t for t in tasks if t.get("split") == split]
    if limit and limit > 0:
        tasks = tasks[:limit]
    return tasks


# ── Agent/tool construction (identical worker pool for both engines) ───────

def build_agents_and_tools() -> tuple[list[dict], dict]:
    """The same enabled-agents + resolved-tools setup app.py builds for the
    main engine path, minus MCP/ExAgents/ExTools (kept hermetic on purpose)."""
    from anet.AnetAgents.agents_config import AGENTS
    from anet.core.tool_loader import load_tools
    from anet.AnetTools.toolsets import expand_tools

    tools = load_tools()
    agents = [dict(a) for a in AGENTS if a.get("enabled", False)]
    for agent in agents:
        resolved = expand_tools(agent)
        agent["tools"] = [t for t in resolved if t in tools]
    return agents, tools


def make_engines(names: list[str], agents: list[dict], tools: dict) -> dict[str, object]:
    from anet.core.AdaptOrch.coordinator import AdaptOrchEngine
    from anet.QweenBee.coordinator import QweenBeeEngine
    table = {"adaptorch": AdaptOrchEngine, "qweenbee": QweenBeeEngine}
    return {name: table[name](agents, tools, manager_tools={}) for name in names}


# ── Paths ─────────────────────────────────────────────────────────────────────

def _results_path() -> Path:
    from anet.core import paths
    return paths.home() / "orchestration" / "bench_results.jsonl"


def _qweenbee_evidence_path() -> Path:
    from anet.core import paths
    return paths.home() / "orchestration" / "queenbee_evidence.jsonl"


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path, "r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def _rows_since(path: Path, start_count: int) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        lines = [l for l in f if l.strip()]
    for line in lines[start_count:]:
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def append_result(row: dict, path=None) -> None:
    """Best-effort append — a bad write must not kill an in-progress bench run."""
    try:
        p = Path(path) if path else _results_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass


# ── Running one task through one engine ─────────────────────────────────────

async def run_one(engine_name: str, engine, task: dict, *, thread_prefix: str = "bench") -> dict:
    """Run a single task through a single engine on a fresh temp-db thread.
    Returns one result row; never raises (errors are captured in the row)."""
    from anet.core import tokens
    from anet.core.store import ConversationStore

    evidence_path = _qweenbee_evidence_path()
    start_evidence = _count_lines(evidence_path) if engine_name == "qweenbee" else 0

    db_path = str(Path(tempfile.mkdtemp()) / "bench.db")
    thread_id = f"{thread_prefix}-{task['id']}-{engine_name}"

    tokens.begin()
    t0 = time.monotonic()
    reply, error = "", None
    try:
        async with ConversationStore(db_path) as store:
            result = await engine.run_turn(thread_id, store, task["prompt"])
        reply = result.reply
    except Exception as exc:
        error = str(exc)
    elapsed = time.monotonic() - t0
    usage = tokens.current()

    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "engine": engine_name,
        "task_id": task["id"],
        "split": task.get("split"),
        "ok": error is None,
        "seconds": round(elapsed, 2),
        "tokens": usage.total if usage else None,
        "reply_chars": len(reply),
        "error": error,
        "qweenbee": None,
    }
    if engine_name == "qweenbee":
        new_rows = _rows_since(evidence_path, start_evidence)
        if new_rows:
            r = new_rows[-1]
            out = r.get("outcome") or {}
            row["qweenbee"] = {
                "source": r.get("source"),
                "consistency": out.get("consistency"),
                "skills_used": out.get("skills_used"),
            }
    return row


async def run_suite(engine_name: str, engine, tasks: list[dict], *,
                    results_path=None, quiet: bool = False) -> list[dict]:
    """Run every task through one engine, appending + returning result rows.
    Shared by bench's CLI and evolve.py's val passes."""
    rows = []
    for i, task in enumerate(tasks, 1):
        if not quiet:
            print(f"[{engine_name}] {task['id']} ({i}/{len(tasks)})...", flush=True)
        row = await run_one(engine_name, engine, task)
        append_result(row, results_path)
        rows.append(row)
        if not quiet:
            status = "ok" if row["ok"] else f"ERROR: {row['error']}"
            extra = f"  [{row['qweenbee']['source']}]" if row.get("qweenbee") else ""
            print(f"  -> {status}  {row['seconds']}s  {row['tokens']} tok{extra}")
    return rows


# ── Aggregation (pure — unit-testable without any LLM call) ────────────────

def aggregate_results(rows: list[dict]) -> dict[tuple[str, str], dict]:
    """Latest row per (engine, task_id) wins — a rerun task doesn't double-count."""
    latest: dict[tuple[str, str], dict] = {}
    for r in rows:
        latest[(r["engine"], r["task_id"])] = r
    return latest


def summarize(rows: list[dict]) -> dict[str, dict]:
    """Per-engine means over the latest-row-per-task set."""
    latest = list(aggregate_results(rows).values())
    by_engine: dict[str, list[dict]] = {}
    for r in latest:
        by_engine.setdefault(r["engine"], []).append(r)

    summary: dict[str, dict] = {}
    for engine, rs in by_engine.items():
        n = len(rs)
        ok = sum(1 for r in rs if r.get("ok"))
        seconds = [r["seconds"] for r in rs if r.get("seconds") is not None]
        tok = [r["tokens"] for r in rs if r.get("tokens") is not None]
        qb_rows = [r["qweenbee"] for r in rs if r.get("qweenbee")]
        cons = [q["consistency"] for q in qb_rows if q.get("consistency") is not None]
        fallbacks = sum(1 for q in qb_rows if q.get("source") == "fallback")
        summary[engine] = {
            "n": n,
            "ok": ok,
            "mean_seconds": round(sum(seconds) / len(seconds), 2) if seconds else None,
            "mean_tokens": round(sum(tok) / len(tok), 1) if tok else None,
            "mean_consistency": round(sum(cons) / len(cons), 3) if cons else None,
            "fallback_rate": round(fallbacks / len(qb_rows), 3) if qb_rows else None,
        }
    return summary


def _fmt(v) -> str:
    return "-" if v is None else str(v)


def print_summary(summary: dict[str, dict]) -> None:
    print(f"\n{'engine':<10} {'n':>3} {'ok':>3} {'mean_s':>8} {'mean_tok':>10} "
         f"{'mean_cs':>8} {'fallback%':>10}")
    for engine, s in summary.items():
        fb = f"{s['fallback_rate'] * 100:.0f}" if s["fallback_rate"] is not None else "-"
        print(f"{engine:<10} {s['n']:>3} {s['ok']:>3} {_fmt(s['mean_seconds']):>8} "
             f"{_fmt(s['mean_tokens']):>10} {_fmt(s['mean_consistency']):>8} {fb:>10}")


# ── CLI ───────────────────────────────────────────────────────────────────────

async def main_async(args: argparse.Namespace) -> None:
    agents, tools = build_agents_and_tools()
    names = ["adaptorch", "qweenbee"] if args.engine == "both" else [args.engine]
    engines = make_engines(names, agents, tools)
    tasks = load_suite(args.suite, args.split, args.limit)
    print(f"Bench: {len(tasks)} task(s), engine(s)={names}\n")

    all_rows: list[dict] = []
    for name in names:
        rows = await run_suite(name, engines[name], tasks)
        all_rows.extend(rows)

    print_summary(summarize(all_rows))


def main() -> None:
    parser = argparse.ArgumentParser(description="QweenBee vs AdaptOrch bench")
    parser.add_argument("--engine", choices=["both", "adaptorch", "qweenbee"], default="both")
    parser.add_argument("--split", choices=["all", "train", "val"], default="all")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--suite", default=None)
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
