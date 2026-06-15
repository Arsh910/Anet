# Tier 03 — Per-Tool (driven through prompts)

Goal: hit each built-in tool through natural language so you can demo the tool
surface end-to-end (the offline unit tests already prove `run()` correctness —
this shows the agent *choosing and using* them).

Prereqs: API key. `web_search`/`download_file` need network.

Set up a scratch area first:
**Prompt:** `create a folder scratch/tools and put a file demo.py in it containing three functions: add, sub, mul`

---

### T1 — edit_tool (surgical replace)
**Prompt:** `in scratch/tools/demo.py rename the function "sub" to "subtract" everywhere`
**Pass if:** a unified diff is shown, edit confirmed, `subtract` replaces `sub`.

### T2 — grep_tool (regex search)
**Prompt:** `search scratch/tools for any function definition using a regex and list the matches`
**Pass if:** all `def ...` lines are reported with file:line.

### T3 — glob_tool (pattern + mtime)
**Prompt:** `find every .py file in the repo modified most recently — show the top 5`
**Pass if:** 5 paths returned, newest first.

### T4 — shell_tool (with confirmation)
**Prompt:** `run "python --version" and tell me the version`
**Pass if:** a y/n confirmation appears, command runs, version printed.

### T5 — process_tool (stream until pattern)
**Setup:** `create scratch/tools/count.py that prints the numbers 0 to 9 one per line, sleeping 0.3 seconds between each (flush each line)`
**Prompt:** `run scratch/tools/count.py and stop it the moment its output shows the number 5 — don't let it run to the end`
**Exercises:** `process_tool` with `success_pattern="5"` and early termination
**Watch for:** the agent picks **process_tool** (not shell_tool). The clean script path
avoids the nested-quote escaping that breaks the command otherwise.
**Pass if:** it stops at/around 5 and does **not** print 6–9. If it routes to
`shell_tool` and prints the full 0–9, it did **not** exercise process_tool — rephrase
explicitly: `use process_tool to run scratch/tools/count.py and stop as soon as you see 5`.

### T6 — diagnose_tool (linters)
**Prompt:** `run diagnostics on scratch/tools/demo.py and report any problems`
**Pass if:** ruff/pyright run; clean file reports no errors (or real ones if present).

### T7 — lsp_tool (code intelligence)
**Prompt:** `using LSP, find all references to the "add" function in scratch/tools/demo.py and show its symbols`
**Pass if:** references + document symbols returned (real LSP, not grep).

### T8 — conflict_tool (merge conflicts)
**Prompt:** `create scratch/tools/conflict.txt with a fake git merge conflict (<<<<<<< ours / ======= / >>>>>>> theirs), then resolve it keeping "ours"`
**Pass if:** conflict markers detected and resolved to the ours side.

### T9 — web_search (no API key)
**Prompt:** `web search: who won the most recent FIFA World Cup — cite the source`
**Pass if:** DuckDuckGo result with a citation, no paid API used.

### T10 — download_file (direct URL)
**Prompt:** `download https://raw.githubusercontent.com/python/cpython/main/README.rst into scratch/tools`
**Pass if:** file saved, absolute path reported.

---

**Tier pass:** every tool is selected by the agent and returns a sane result.
Note any tool the planner failed to route to.
