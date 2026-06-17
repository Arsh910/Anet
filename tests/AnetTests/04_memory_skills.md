# Tier 04 — Memory, Profile & Self-Improving Skills

Goal: demonstrate ANet's persistence layer — cross-session memory, the auto-built
user profile, the periodic memory nudge, and skills that get written then re-used.

Prereqs: API key. Some effects are **background/periodic**, so this tier spans
multiple turns and (for the profile) a session restart.

---

## Memory

### M1 — Save a fact explicitly
**Prompt:** `remember as a preference that every Python function you generate must have its name start with "anet_" — for example anet_average`
**Exercises:** `memory_tool` save (tagged `preference` so it auto-applies to coding tasks)
**Pass if:** confirms a memory was saved (with an id), tagged `preference`.
**Why this preference:** it's **distinctive** — the model would never prefix function
names on its own — so M3 can *observably* prove memory influenced the output. (A
preference like "use 4-space indents" is the model's default and proves nothing.)

### M2 — Recall it
**Prompt:** `what coding preferences have you saved about me?`
**Exercises:** `memory_tool` search (keyword) + planner memory-context injection
**Pass if:** the indent/type-hint preference is recalled.

### M3 — Memory influences behavior
**Prompt:** `write a small function that averages a list of numbers`
**Exercises:** the engine injects the M1 `preference` into `code_agent`'s prompt even
though it shares no keywords with the task (Phase B preference channel), without
code_agent calling memory_tool.
**Pass if:** the generated function is named **`anet_average`** (or `anet_…`) — the
distinctive marker from M1. `def average(...)` means the preference did **not** reach
the model.
**Note:** unrelated tasks and memory-free agents (e.g. research) get **no** memory
block injected — only relevant facts + applicable preferences. Tip: requires a
**restart** after code changes (ANet is long-running; injection loads at startup).

### M4 — Delete / list
**Prompt:** `list everything you remember about me, then delete the indentation preference`
**Pass if:** list shown, target memory deleted, confirmed.

## User profile (USER.md)

### M5 — Trigger the background profile builder
Run ~5+ substantive turns (M1–M4 plus any 1–2 more) in one session. The
incremental memory agent fires every `incremental_interval` (default 5) turns.
**Then:** `/profile`
**Exercises:** background `memory_agent`, `memory/USER.md`
**Pass if:** `USER.md` now contains structured facts about you (prefs/stack/style).

### M6 — Profile persists across sessions
Exit cleanly (`exit`) — triggers the session-end USER.md pass. Restart:
`python main.py --resume` then ask: `what do you already know about me?`
**Pass if:** the assistant recalls profile facts from the prior session.

## Skills

### M7 — Provoke a skill creation
Give a task that takes **≥6 tool calls and a self-correction** (Tier 02 A4, or:
`Search the web for the top 5 most starred Python repositories on GitHub right now. Write a Python script that fetches their README (first 500 chars) via the GitHub API and saves a report.json with name, stars, and readme snippet for each. Write a test that validates the JSON structure and that all 5 entries exist. Run it, fix any failures, re-run until clean. Then send the final report.json contents as a formatted message to a Telegram bot.`).
**Exercises:** background `skill_manager` writing a `skills/*.md` procedure
**Then:** `/skills`
**Pass if:** a new skill file appears describing the procedure, with a usage count.

### M8 — Skill injection on a similar later task
Start a new task similar to M8 (`build scratch/calc2.py with the same four ops and a test`).
**Watch for:** "Relevant Skills from Past Experience" injected into the agent prompt.
**Pass if:** the agent follows the saved procedure (visibly faster / fewer missteps).

### M9 — Curator (needs ≥5 skill files)
Once `skills/` has ≥5 files, restart ANet. A background Curator merges duplicates
and improves skills used ≥3×, archiving originals to `skills/archived/`.
**Pass if:** after restart, `skills/archived/` exists and duplicates are merged.

---

**Tier pass:** facts survive within and across sessions, the profile builds
itself, and at least one skill is created then re-injected.
