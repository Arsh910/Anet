# SWE-bench harness for Anet

Two separate steps: **generate** patches with an Anet agent (this directory),
then **score** them with the official `swebench` package (Docker required).
This directory only does step 1 — nothing here has been run yet.

## Setup

```
pip install datasets     # already installed in this env
pip install swebench     # only needed for step 2 (scoring)
```

Step 2 also needs **Docker running** — the official harness builds one image
per repo/environment and executes the real test suite inside a container per
instance. Step 1 needs `git` on PATH and network access to GitHub.

## Step 1 — generate predictions

```
python tests/SWE_bench/generate_predictions.py --dataset princeton-nlp/SWE-bench_Lite --limit 5
```

What it does, per instance:
1. Clones the instance's repo (cached bare-clone under `.swebench_cache/`,
   reused across instances that share a repo) and checks out `base_commit`
   into a fresh dir under `.swebench_work/<instance_id>/`.
2. Runs `code_agent` **standalone** (no manager/decomposer/planner — a
   SWE-bench task is a single-repo code fix, already atomic; routing it
   through AdaptOrch or QweenBee's multi-agent pipeline would decompose
   something that doesn't need decomposing) with the issue text as its task,
   `cwd` scoped to that checkout.
3. `git add -A && git diff --cached` in the checkout captures the agent's
   edits (including new files) as a unified diff.
4. Appends `{"instance_id", "model_name_or_path", "model_patch"}` to
   `predictions.jsonl` — the exact format `swebench` expects.

Useful flags:

| Flag | Default | Purpose |
|---|---|---|
| `--dataset` | `princeton-nlp/SWE-bench_Lite` | try `princeton-nlp/SWE-bench_Verified` once this works |
| `--limit` | `0` (no limit) | cap instance count — start small |
| `--instance_ids` | (all) | comma-separated list to re-run specific instances |
| `--agent` | `code_agent` | any name from `agents_config.AGENTS` |
| `--out` | `tests/SWE_bench/predictions.jsonl` | resumable — already-recorded instance_ids are skipped on rerun |

Runs are **sequential**, not parallel — the script `os.chdir`s into each
checkout, which is process-global state. Docker-bound scoring (step 2) is
the actual bottleneck anyway, so this isn't the place to add concurrency.

Errors on individual instances are logged to `<out>.errors.jsonl` and don't
stop the run; a rerun with the same `--out` skips everything already done.

⚠️ **No sandboxing beyond the working directory.** Anet's filesystem/shell
tools have no root jail, and this script runs headless with `on_confirm`
defaulting to auto-approve (no one is there to click "yes"). The agent gets
real shell access to whatever it's pointed at. Run this inside a VM or
container you're OK with an LLM having shell access in — the same posture
the official evaluation step already assumes (it runs everything in Docker).

## Step 2 — score with the official harness

```
python -m swebench.harness.run_evaluation \
  --predictions_path tests/SWE_bench/predictions.jsonl \
  --dataset_name princeton-nlp/SWE-bench_Lite \
  --run_id my_run \
  --max_workers 4
```

This applies each patch inside a built container, runs the instance's
`FAIL_TO_PASS`/`PASS_TO_PASS` tests, and writes a report
(`my_run.<model_name_or_path>.json`) with resolved/unresolved counts — that
resolve rate is the number comparable to the SWE-bench leaderboard.

## Comparing engines

To compare `code_agent` alone against routing through AdaptOrch or QweenBee,
run this script twice with different `--agent`/`--model_name` values into
separate `--out` files, then score each `predictions.jsonl` separately in
step 2. (Routing a SWE-bench task through the full multi-agent pipeline isn't
wired up here — `--agent` only supports single, standalone agents. If you
want that comparison, say so and it can be added.)
