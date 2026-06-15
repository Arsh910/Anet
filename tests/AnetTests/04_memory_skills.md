# Tier 04 — Memory, Profile & Self-Improving Skills

Goal: demonstrate ANet's persistence layer — cross-session memory, the auto-built
user profile, the periodic memory nudge, and skills that get written then re-used.

Prereqs: API key. Some effects are **background/periodic**, so this tier spans
multiple turns and (for the profile) a session restart.

---

## Memory

### M1 — Save a fact explicitly
**Prompt:** `remember that I prefer all generated Python to use 4-space indents and type hints`
**Exercises:** `memory_tool` save
**Pass if:** confirms a memory was saved (with an id).

### M2 — Recall it
**Prompt:** `what coding preferences have you saved about me?`
**Exercises:** `memory_tool` search (keyword) + planner memory-context injection
**Pass if:** the indent/type-hint preference is recalled.

### M3 — Memory influences behavior
**Prompt:** `write a small function that averages a list of numbers`
**Exercises:** memory context steering generation
**Pass if:** output uses 4-space indents and type hints (per M1) without being re-told.

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

### M7 — 10-turn nudge
Keep a single session going for 10+ substantive messages.
**Watch for:** around turn 10 the active agent is prompted to push new facts to memory.
**Pass if:** a memory write happens near the nudge interval without you asking.

## Skills

### M8 — Provoke a skill creation
Give a task that takes **≥6 tool calls and a self-correction** (Tier 02 A4, or:
`build scratch/calc.py with add/sub/mul/div, write a quick test, run it, fix any failure, re-run`).
**Exercises:** background `skill_manager` writing a `skills/*.md` procedure
**Then:** `/skills`
**Pass if:** a new skill file appears describing the procedure, with a usage count.

### M9 — Skill injection on a similar later task
Start a new task similar to M8 (`build scratch/calc2.py with the same four ops and a test`).
**Watch for:** "Relevant Skills from Past Experience" injected into the agent prompt.
**Pass if:** the agent follows the saved procedure (visibly faster / fewer missteps).

### M10 — Curator (needs ≥5 skill files)
Once `skills/` has ≥5 files, restart ANet. A background Curator merges duplicates
and improves skills used ≥3×, archiving originals to `skills/archived/`.
**Pass if:** after restart, `skills/archived/` exists and duplicates are merged.

---

**Tier pass:** facts survive within and across sessions, the profile builds
itself, and at least one skill is created then re-injected.
