# Tier 01 — Simple / Smoke

Goal: prove the basics work before anything complex. These should resolve on the
planner's **direct-reply** or **single-tool** path — no multi-step DAG, no checker.

Prereqs: API key only.

---

### S1 — Plain greeting (direct reply, no agents)
**Prompt:** `hi, who are you and what can you do?`
**Exercises:** planner `simple` path, SOUL.md persona injection
**Watch for:** an immediate text reply with Anet's persona/name — **no** "manager: planning steps", no agent status lines.
**Pass if:** you get a coherent intro and zero tools fire.

### S2 — Trivial factual question (direct reply)
**Prompt:** `what is the capital of Japan?`
**Exercises:** planner `simple` path
**Watch for:** instant answer, no `research_agent` spin-up.
**Pass if:** answered directly without a web search.

### S3 — One-shot web lookup (single agent, single tool)
**Prompt:** `search the web for the latest stable Python version and cite the source`
**Exercises:** planner → `research_agent` → `web_search`
**Watch for:** `research_agent` status, one web_search call, a cited URL in the reply.
**Pass if:** answer includes a version number **and** a source URL.

### S4 — Read a file (single tool, read-only, no confirmation)
**Prompt:** `read the first 20 lines of README.md and summarize what ANet is`
**Exercises:** `file_agent` → `file_tool` read (read-only ⇒ no y/n prompt)
**Watch for:** file read happens **without** a confirmation prompt.
**Pass if:** a correct 1–2 sentence summary, no approval was asked.

### S5 — Glob listing (single tool)
**Prompt:** `list all Python files under the anet/core folder`
**Exercises:** `glob_tool`
**Watch for:** a list including engine.py, orchestrator.py, store.py, etc.
**Pass if:** the core modules appear, sorted/listed cleanly.

### S6 — Todo surfacing on a tiny task
**Prompt:** `make a 3-item checklist for adding a new tool to ANet, then mark step 1 done`
**Exercises:** `todo_tool` live render in the spinner
**Watch for:** the checklist appearing in the status area with item 1 checked.
**Pass if:** a 3-item list renders and reflects the completed step.

---

**Tier pass:** S1–S6 all behave as described. If S1/S2 trigger agents, the
planner's simple-path classification is mis-tuned — note it in RESULTS.md.
