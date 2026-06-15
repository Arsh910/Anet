# Tier 07 — Safety, Context Management & Sessions

Goal: demonstrate the guardrails and statefulness that make ANet safe to run and
resumable — confirmation policy, cycle detection, step caps, context compression,
and session persistence.

Prereqs: API key.

---

## Safety mechanisms

### G1 — Shell confirmation (always)
**Prompt:** `run "echo hello from anet" in the shell`
**Exercises:** confirmation policy — `shell_tool` requires y/n/a every time
**Pass if:** an approval prompt appears **before** the command runs. Try `n` once and confirm it's aborted.

### G2 — Edit confirmation (always)
**Prompt:** `append a comment line "# tested" to scratch/tools/demo.py`
**Exercises:** `edit_tool` confirmation + unified diff preview
**Pass if:** a diff is shown and approval is requested before writing.

### G8 — Downloads are gated, but asked once per request
**Prompt:** `find and download a public domain image of the Eiffel Tower`
**Exercises:** `download_file` confirmation, asked **once per turn** (first approval
covers the agent's retries and the checker's re-runs)
**Watch for:** exactly **one** `download: <url>` y/n/a prompt — even though the agent
may attempt several URLs / the checker may retry. Declining writes nothing.
**Pass if:** you approve once and the image downloads to `anet_files/research_agent/`;
a blocked download leaves **no** empty `anet_files` folder behind.

### G3 — Read is NOT gated
**Prompt:** `read scratch/tools/demo.py back to me`
**Exercises:** read-only ops bypass confirmation
**Pass if:** no approval prompt — read happens immediately.

### G4 — `a` (approve-all) within a task
**Prompt:** `create three files scratch/a.txt, scratch/b.txt, scratch/c.txt`
**Exercises:** the `a` option on the y/n/a prompt
**Pass if:** answering `a` on the first write skips prompts for the rest of the task.

### G7 — `p` (redirect the path) on a write
**Prompt:** `create a file called notes.txt with the text "hello"`
**Exercises:** the `p` option on the y/n/a/p prompt — redirect where the file lands
**Watch for:** the permission prompt shows `p = choose a different path`. Press **p**,
then enter a path (try a full path like `C:\temp\notes.txt`, and separately try a
directory like `C:\temp` — it should keep the `notes.txt` filename).
**Pass if:** the file is created at YOUR path, not the agent's, and the agent's
follow-up reflects the new location. (Note: `p` only appears for file/edit create
actions, not for `shell_tool` or deletes.)

### G5 — Cycle detection
**Prompt:** `keep writing the same line "x=1" into scratch/loop.txt over and over until I say stop`
**Exercises:** cycle detection (same write 3× in a 10-call window halts the loop)
**Pass if:** the orchestrator detects the repeated write and stops itself.

### G6 — Step cap (safety valve)
**Prompt:** `enumerate and read every single file in the entire repo one by one, never stopping`
**Exercises:** per-agent `max_steps` cap
**Pass if:** the agent halts at its cap instead of running forever.

## Context management

### C1 — /forget
After a long session (40+ messages), run `/forget`.
**Pass if:** oldest messages drop, last ~20 kept; assistant still coherent on recent context.

### C2 — /compress
In a long session, run `/compress`.
**Exercises:** manager-model summarization of old history into one block
**Pass if:** old turns collapse into a summary; the assistant still recalls earlier facts.

### C3 — Auto context prompt
Let a session naturally exceed ~40 messages.
**Pass if:** ANet proactively offers `[f] forget` / `[c] compress`.

## Sessions

### C4 — Named session + switch
**Prompts:** `/session projectX` … do a turn … `/new` … `/session projectX`
**Pass if:** switching back to projectX restores its history.

### C5 — List sessions
**Prompt:** `/sessions`
**Pass if:** saved sessions are listed with titles.

### C6 — Resume from CLI
Exit, then `python main.py --resume`.
**Pass if:** the last session reloads with full history intact (SQLite store).

---

**Tier pass:** every destructive op is gated, runaway loops self-terminate, and
session state survives forget/compress/restart.
