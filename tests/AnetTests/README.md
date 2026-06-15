# AnetTests — End-to-End Prompt Suite

> **What this is:** a curated catalog of prompts you type into the live ANet CLI
> (`python main.py`) to exercise **every system** in the project, ordered from
> simplest to most complex. Unlike `tests/AnetToolTests/` (offline unit tests for
> individual tool `run()` functions), this suite drives the *whole* stack —
> planner, multi-agent DAG, tools, memory, skills, MCP, safety, sessions.
>
> It doubles as a **demo script**: run a tier, record the screen, drop the clip
> next to the matching `.md` file. Each prompt lists what it exercises, what to
> watch for, and a pass criterion so a viewer can verify the behavior.

---

## How to run

```bash
cd C:\thinkbig\Anet\Anet
python main.py                 # start a fresh interactive session
# ...or...
python main.py --session demo  # named session, so recordings are reproducible
```

Then paste prompts from the tier files below, one at a time, and observe.

To re-baseline the **tool layer** (offline, deterministic) at any time:

```bash
python tests/AnetToolTests/run_all.py
```

---

## Tiers

| File | Tier | Systems exercised |
|---|---|---|
| [01_simple.md](01_simple.md) | Smoke | Planner direct-reply path, single tool, no DAG |
| [02_agents.md](02_agents.md) | Per-agent | research / code / file / computer / checker in isolation |
| [03_tools.md](03_tools.md) | Per-tool | edit, file, grep, glob, shell, process, diagnose, conflict, lsp, web_search, download |
| [04_memory_skills.md](04_memory_skills.md) | Memory + skills | memory_tool, USER.md profile, 10-turn nudge, skill creation/injection/curator |
| [05_orchestration_spawn.md](05_orchestration_spawn.md) | Multi-agent | planner DAG, parallel steps, spawn_tool, checker retry, todo_tool |
| [06_mcp_external.md](06_mcp_external.md) | MCP + external | codegraph, playwright, tele_agent, ExTools |
| [07_safety_context_sessions.md](07_safety_context_sessions.md) | Safety + state | confirmation policy, cycle detection, /forget, /compress, sessions, --resume |
| [08_complex_workflows.md](08_complex_workflows.md) | Full-stack | end-to-end prompts that chain research → code → check → notify |
| [RESULTS.md](RESULTS.md) | Tracker | fill-in pass/fail table to publish with the repo |

### Feature runbooks (step-by-step, with ready subjects)

| File | Verifies | Setup shipped |
|---|---|---|
| [add_tool_test.md](add_tool_test.md) | `/newtool` ExTool generator + `extool_validator` | `ExTools/wordcount/wordcount_repo/` |
| [mcp_doctor_test.md](mcp_doctor_test.md) | `/mcptest` doctor + `/addmcp` integrator | `samples/everything_mcp/` |

---

## Prerequisites per tier

Most tiers need only `OPENROUTER_API_KEY` (or whatever provider you configured in
`anet.config.yaml`). A few need extras — each prompt is flagged inline:

- **`computer_agent` / open_app** — Windows only (you are on Windows ✓).
- **codegraph / playwright MCP** — Node.js installed and the servers wired into an
  agent's `mcp:` list in `anet.config.yaml` (codegraph→code_agent, playwright→file_agent).
- **tele_agent** — `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` in `ExAgents/tele_agent/.env`.
- **wifi_pumpkin** — pentest tool; **only run against networks you own / are
  authorized to test.** Skip in public demo recordings.

---

## Reading a prompt entry

Every entry follows the same shape:

```
### S1 — Title
**Prompt:** `the exact text to type`
**Exercises:** which subsystem(s)
**Watch for:** what should visibly happen (status lines, confirmations, tools)
**Pass if:** the concrete success condition
```

Use the IDs (S1, A3, T7, …) in `RESULTS.md` and in your recording filenames
(e.g. `clips/A3_code_agent_refactor.mp4`).

---

## Baseline status (last verified 2026-06-15)

- `tests/AnetToolTests/run_all.py` → **20/20 tool test files passed.**
- Live prompt tiers below are **manual / recorded** — track outcomes in `RESULTS.md`.
