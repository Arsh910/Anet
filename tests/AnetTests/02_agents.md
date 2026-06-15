# Tier 02 — Per-Agent (isolation)

Goal: exercise each built-in agent on its own so you can see its model, tool
surface, and `max_steps` behavior. One agent should handle each prompt.

Prereqs: API key. C5 (computer_agent) is Windows-only.

---

### A1 — research_agent: multi-part research with citations
**Prompt:** `research the top 3 differences between HTTP/2 and HTTP/3, bullet points, cite each`
**Exercises:** `research_agent`, `web_search`, citation discipline
**Watch for:** ≤10 steps (its max_steps cap), bullets, a URL per point.
**Pass if:** 3 accurate bullets, each with a source.

### A2 — research_agent: image download
**Prompt:** `find and download a public domain image of the Eiffel Tower`
**Exercises:** `research_agent` → `web_search(type=image)` → `download_file` (now confirmed)
**Watch for:** image search → a direct `.jpg/.png` URL → a **download permission prompt**
(`download: <url>` y/n/a — approve it or press `a`) → one download → a final
`Downloaded: <abs path>` line. On a failed/blocked download, **no** `anet_files`
folder is left behind.
**Pass if:** a real image file lands in `anet_files/research_agent/` and the absolute path is printed.

### A3 — code_agent: write + run
**Prompt:** `create scratch/fizzbuzz.py that prints FizzBuzz 1..20, then run it and show the output`
**Exercises:** `code_agent`, `edit_tool` (confirm), `shell_tool` (confirm)
**Watch for:** an edit confirmation (y/n/a), a shell confirmation, correct output.
**Pass if:** file created, runs, prints correct FizzBuzz.

### A4 — code_agent: diagnose + fix
**Prompt:** `there's a syntax error in scratch/fizzbuzz.py — break it first by removing a colon, then detect and fix it`
**Exercises:** `code_agent`, `diagnose_tool`, `edit_tool`, self-correction
**Watch for:** diagnose reports the error, an edit fixes it, re-run is clean.
**Pass if:** the agent finds the error via diagnostics and repairs it.

### A5 — file_agent: copy + zip
**Prompt:** `copy README.md to scratch/readme_copy.md, then zip scratch/ into scratch.zip`
**Exercises:** `file_agent`, `file_tool` (write/copy/zip ⇒ confirmations)
**Watch for:** y/n prompts for copy and zip (destructive/write actions).
**Pass if:** copy exists and scratch.zip is created.

### C5 — computer_agent: desktop automation *(Windows)*
**Prompt:** `open Notepad and type "ANet end-to-end test ok"`
**Exercises:** `computer_agent` → `open_app(launch_and_type)` (confirm)
**Watch for:** a confirmation, Notepad launching, text typed.
**Pass if:** Notepad opens with the text. *(Skip if recording headless.)*

---

**Tier pass:** each agent completes its job within its step cap and only the
expected agent is invoked.
