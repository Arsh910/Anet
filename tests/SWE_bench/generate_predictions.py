"""
generate_predictions.py — run an Anet agent over SWE-bench instances and emit
a predictions file in the official format the swebench harness expects:

    {"instance_id": ..., "model_name_or_path": ..., "model_patch": "<unified diff>"}

This script only PRODUCES patches — it does not score them. Scoring needs
Docker and the separate `swebench` package; see README.md in this directory
for the full two-step workflow.

    python tests/SWE_bench/generate_predictions.py --dataset princeton-nlp/SWE-bench_Lite --limit 5

Design notes:
  - Runs ONE agent (code_agent by default) standalone against each instance's
    checked-out repo — bypassing the manager/decomposer/planner entirely.
    SWE-bench instances are single-repo code-fix tasks; routing one through
    AdaptOrch's or QweenBee's multi-agent pipeline would decompose a task
    that's already atomic and burn tokens for no benefit.
  - No sandboxing beyond a working-directory scope (os.chdir + an explicit
    instruction in the prompt): Anet's filesystem/shell tools have no root
    jail, and on_confirm defaults to auto-approve for a headless run. Run
    this inside a VM/container you're OK with an LLM having shell access in
    — the same posture the official evaluation step already requires (it
    runs everything in Docker).
  - Sequential, not concurrent: os.chdir is process-global state, so
    parallelizing instances would race. Docker-bound evaluation (the slow
    part) already happens in a separate, later step, so this isn't the
    bottleneck.
  - Resumable: instance_ids already present in --out are skipped, so an
    interrupted run can just be restarted with the same --out path.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Repo root (tests/SWE_bench/../.. == the anet package's parent).
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

# This script is standalone (imports orchestrator/agent_runner directly, not
# anet.cli.app), so — unlike a normal `anet` session — nothing loads .env for
# it automatically. Load the repo-root .env explicitly, then fall back to
# Anet's canonical <home>/.env for anything not covered there (override=False
# by default, so a real OS env var always wins over either file).
from dotenv import load_dotenv
load_dotenv(_REPO_ROOT / ".env")
try:
    from anet.core import paths as _anet_paths
    load_dotenv(_anet_paths.env_path())
except Exception:
    pass


# ── Dataset ──────────────────────────────────────────────────────────────────

def load_instances(dataset: str, split: str, limit: int = 0,
                   instance_ids: list[str] | None = None) -> list[dict]:
    """Pull SWE-bench instances via the datasets library. Each row has (at
    minimum) instance_id, repo, base_commit, problem_statement."""
    from datasets import load_dataset
    ds = load_dataset(dataset, split=split)
    rows = [dict(r) for r in ds]
    if instance_ids:
        wanted = set(instance_ids)
        rows = [r for r in rows if r["instance_id"] in wanted]
    if limit and limit > 0:
        rows = rows[:limit]
    return rows


# ── Repo checkout (cached bare clone -> fast local clone per instance) ─────

def _run_git(args: list[str], cwd: str | Path | None = None) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _cache_repo(repo: str, cache_dir: Path) -> Path:
    """Bare-clone `repo` (SWE-bench's "owner/name" form) once into cache_dir,
    reused by every instance that shares this repo (SWE-bench repeats repos
    heavily — django/django alone backs 100+ Lite instances)."""
    slug = repo.replace("/", "__")
    bare = cache_dir / f"{slug}.git"
    if not bare.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        _run_git(["clone", "--bare", f"https://github.com/{repo}.git", str(bare)])
    return bare


def _rmtree_force(path: Path) -> None:
    """shutil.rmtree that survives Windows' git checkouts: git marks
    .git/objects/pack/*.idx and *.pack read-only, and Windows refuses to
    delete a read-only file until the attribute is cleared (unlike Linux,
    where deletion only depends on the containing directory's permissions)."""
    import shutil
    import stat

    def _on_error(func, p, exc_info):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    shutil.rmtree(path, onerror=_on_error)


def prepare_repo(instance: dict, work_dir: Path, cache_dir: Path) -> Path:
    """Return a checked-out working copy of the instance's repo at base_commit,
    fresh under work_dir/<instance_id> (removed and recreated if it exists)."""
    bare = _cache_repo(instance["repo"], cache_dir)
    inst_dir = work_dir / instance["instance_id"]
    if inst_dir.exists():
        _rmtree_force(inst_dir)
    inst_dir.parent.mkdir(parents=True, exist_ok=True)
    _run_git(["clone", str(bare), str(inst_dir)])
    _run_git(["checkout", instance["base_commit"]], cwd=inst_dir)
    return inst_dir


# ── Prompt + agent run ──────────────────────────────────────────────────────

_TASK_TEMPLATE = """You are fixing a real GitHub issue in a checked-out repository.

Repository working directory (all your file/shell operations happen here):
{repo_path}

## Issue
{problem_statement}

## Instructions
- Locate the relevant code and make the minimal change that resolves the issue.
- Don't touch tests or unrelated files.
- You may run the project's existing tests to sanity-check your fix, but don't
  spend excessive turns exploring — this repo is large; be targeted (grep/glob
  for the failing area first).
- When you believe the fix is complete, stop. Do not commit or create a PR —
  the diff is captured for you.
"""


def build_task_prompt(instance: dict, repo_path: Path) -> str:
    return _TASK_TEMPLATE.format(
        repo_path=str(repo_path),
        problem_statement=instance.get("problem_statement", "").strip(),
    )


def build_agent(agent_name: str) -> tuple[dict, dict]:
    """Resolve one agent (by name, from anet.AnetAgents.agents_config.AGENTS —
    which already applies anet.config.yaml's per-agent model/provider override
    at import time) plus the live tool map, exactly as the CLI would build it,
    minus MCP/ExAgents (kept hermetic for a batch run)."""
    from anet.AnetAgents.agents_config import AGENTS
    from anet.core.tool_loader import load_tools
    from anet.AnetTools.toolsets import expand_tools

    match = next((a for a in AGENTS if a.get("name") == agent_name), None)
    if match is None:
        names = [a.get("name") for a in AGENTS]
        raise ValueError(f"no agent named {agent_name!r} in agents_config.AGENTS (have: {names})")

    tools = load_tools()
    agent = dict(match)
    resolved = expand_tools(agent)
    agent["tools"] = [t for t in resolved if t in tools]
    return agent, tools


async def run_agent_on_repo(agent: dict, tool_map: dict, prompt: str, repo_path: Path) -> str:
    """chdir into repo_path, run the agent loop once, restore cwd. Returns the
    agent's final text (not used for scoring — the git diff is what matters)."""
    from anet.core import orchestrator

    def on_status(msg: str) -> None:
        print(f"    [{msg}]", flush=True)

    prev_cwd = os.getcwd()
    os.chdir(repo_path)
    try:
        result = await orchestrator.run(agent, tool_map, prompt, [], on_status)
    finally:
        os.chdir(prev_cwd)
    return (result or {}).get("text", "")


# ── Patch capture ────────────────────────────────────────────────────────────

def capture_patch(repo_path: Path) -> str:
    """Stage everything (so new files are included) and diff against the
    checked-out base_commit — the standard way to turn an agent's edits into
    a SWE-bench-format unified diff."""
    _run_git(["add", "-A"], cwd=repo_path)
    diff = _run_git(["diff", "--cached"], cwd=repo_path)
    return diff


# ── Predictions I/O (resumable) ──────────────────────────────────────────────

def load_done_ids(out_path: Path) -> set[str]:
    if not out_path.exists():
        return set()
    done = set()
    with open(out_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)["instance_id"])
            except Exception:
                continue
    return done


def append_prediction(out_path: Path, record: dict) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def append_error(err_path: Path, instance_id: str, error: str) -> None:
    err_path.parent.mkdir(parents=True, exist_ok=True)
    with open(err_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"instance_id": instance_id, "error": error}) + "\n")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main_async(args: argparse.Namespace) -> None:
    work_dir = Path(args.work_dir)
    cache_dir = Path(args.cache_dir)
    out_path = Path(args.out)
    err_path = out_path.with_name(out_path.stem + ".errors.jsonl")

    agent, tool_map = build_agent(args.agent)
    instance_ids = [s.strip() for s in args.instance_ids.split(",")] if args.instance_ids else None
    instances = load_instances(args.dataset, args.split, args.limit, instance_ids)

    done = load_done_ids(out_path)
    todo = [i for i in instances if i["instance_id"] not in done]
    print(f"{len(instances)} instance(s) selected, {len(done)} already done, "
         f"{len(todo)} to run (agent={args.agent}).")

    for n, instance in enumerate(todo, 1):
        iid = instance["instance_id"]
        print(f"\n[{n}/{len(todo)}] {iid}  ({instance.get('repo')})")
        t0 = time.monotonic()
        try:
            repo_path = prepare_repo(instance, work_dir, cache_dir)
            prompt = build_task_prompt(instance, repo_path)
            await run_agent_on_repo(agent, tool_map, prompt, repo_path)
            patch = capture_patch(repo_path)
            append_prediction(out_path, {
                "instance_id": iid,
                "model_name_or_path": args.model_name,
                "model_patch": patch,
            })
            status = "ok" if patch.strip() else "ok (empty diff)"
        except Exception as exc:
            append_error(err_path, iid, str(exc))
            status = f"ERROR: {exc}"
        print(f"  -> {status}  ({time.monotonic() - t0:.1f}s)")

    print(f"\nPredictions: {out_path}")
    if err_path.exists():
        print(f"Errors:      {err_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate SWE-bench predictions with an Anet agent")
    parser.add_argument("--dataset", default="princeton-nlp/SWE-bench_Lite",
                        help="HF dataset name (e.g. princeton-nlp/SWE-bench_Verified)")
    parser.add_argument("--split", default="test")
    parser.add_argument("--limit", type=int, default=0, help="0 = no limit")
    parser.add_argument("--instance_ids", default="", help="comma-separated list to run only these")
    parser.add_argument("--agent", default="code_agent", help="agent name from agents_config.AGENTS")
    parser.add_argument("--model_name", default="anet-code_agent",
                        help="model_name_or_path recorded in the predictions file")
    parser.add_argument("--work_dir", default=str(_REPO_ROOT / ".swebench_work"),
                        help="scratch dir for per-instance checkouts")
    parser.add_argument("--cache_dir", default=str(_REPO_ROOT / ".swebench_cache"),
                        help="bare-clone cache dir, reused across instances of the same repo")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "predictions.jsonl"))
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
