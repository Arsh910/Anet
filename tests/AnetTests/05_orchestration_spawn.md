# Tier 05 — Multi-Agent Orchestration & Spawn

Goal: show the planner building a real DAG — multiple agents, parallel where
possible, sub-agent delegation via spawn_tool, and the checker→retry loop.

Prereqs: API key.

---

### O1 — Two-agent sequence
**Prompt:** `research the current latest Node.js LTS version, then write scratch/node_version.txt containing just that version number`
**Exercises:** planner DAG `research_agent → file_agent`, step dependency
**Watch for:** two distinct steps, the second consuming the first's result.
**Pass if:** the file contains the version the research step found.

### O2 — Parallel steps (independent work)
**Prompt:** `at the same time: (a) find the latest stable Go version, and (b) count how many .py files are in anet/core — give me both`
**Exercises:** planner parallel branch (research_agent ∥ file_agent/glob)
**Watch for:** two steps running concurrently in the status panel.
**Pass if:** both answers returned; panel shows them as parallel, not strictly serial.

### O3 — spawn_tool delegation
**Prompt:** `as the code agent, summarize what anet/core/engine.py does — and if you need web context on the planner→executor pattern, delegate that lookup to the research agent yourself`
**Exercises:** `spawn_tool` (code_agent spawns research_agent mid-task, depth ≤ 2)
**Watch for:** a sub-agent invocation **without** returning to the manager.
**Pass if:** a spawned research sub-task runs and its result folds into the code summary.

### O4 — Checker retry on partial result
**Prompt:** `create scratch/report.md that contains BOTH the latest Python version AND the latest Java LTS — verify both are present before finishing`
**Exercises:** `checker_agent` classifying partial → retry loop
**Watch for:** if the first attempt misses one, a retry fills it in.
**Pass if:** final file has both versions; a retry is visible if the first pass was partial.

### O5 — todo_tool across a multi-step plan
**Prompt:** `plan and execute: make scratch/site/ with index.html, style.css, app.js stubs, track each as a todo, and tick them off as you go`
**Exercises:** `todo_tool` live checklist across a real multi-step build
**Pass if:** the checklist updates live and all three files exist at the end.

---

**Tier pass:** you can point at a recording and say "here the planner ran A and B
in parallel, here an agent spawned a sub-agent, here the checker forced a retry."
