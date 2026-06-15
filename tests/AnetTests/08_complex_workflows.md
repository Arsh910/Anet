# Tier 08 — Complex Full-Stack Workflows

Goal: the headline demos. Each prompt chains **many systems in one turn** —
planner DAG + multiple agents + tools + memory + (optionally) MCP and external
notification. These are your best screen-recording candidates.

Prereqs: API key. W3/W5 use Telegram + MCP (skip those legs if not configured).

---

### W1 — Research → build → test → verify
**Prompt:**
`research the recommended way to structure a FastAPI project, then scaffold scratch/api/ with main.py and a /health route following that, run it briefly to confirm it imports, and have the checker confirm the route exists`
**Exercises:** research_agent → code_agent (edit+shell+diagnose) → checker_agent, DAG + retry
**Pass if:** a working scaffold exists, imports cleanly, checker confirms `/health`.

### W2 — Refactor with code intelligence
**Prompt:**
`in scratch/tools/demo.py, rename "mul" to "multiply" using LSP rename so all references update, run diagnostics to confirm nothing broke, and show me the diff`
**Exercises:** lsp_tool (rename/references) + diagnose_tool + edit visibility
**Pass if:** rename propagates via LSP, diagnostics clean, diff shown.

### W3 — Research → code → notify *(Telegram)*
**Prompt:**
`find the latest stable Rust version, write it into scratch/rust.txt, then send me a Telegram message with that version`
**Exercises:** research_agent → file_agent → tele_agent (external), 3-agent chain
**Pass if:** file written and a Telegram message with the version arrives.

### W4 — Codebase Q&A with the graph
**Prompt:**
`using codegraph, explain how a user prompt flows from main.py through the engine to a tool call and back — name the key files and functions`
**Exercises:** codegraph MCP + code_agent synthesis over the real graph
**Pass if:** an accurate trace naming main.py → engine.run_turn → _plan/_execute → orchestrator → tool.

### W5 — Parallel research + summary doc
**Prompt:**
`in parallel, research (1) what MCP is and (2) what an agentic DAG is; then write scratch/concepts.md with a section for each and a one-line TL;DR at top`
**Exercises:** parallel planner branches → file_agent synthesis, todo tracking
**Pass if:** both topics researched concurrently and merged into one well-structured doc.

### W6 — Memory-aware multi-step
**Prompt (after Tier 04 ran):**
`using what you remember about my coding preferences, generate scratch/utils.py with two helper functions, then verify they match my style`
**Exercises:** memory recall → code_agent generation → checker validation
**Pass if:** generated code matches saved prefs (indents/type hints) and checker agrees.

### W7 — The README demo (signature run)
**Prompt:**
`research FastAPI best practices, apply one improvement to scratch/api/main.py, run a quick check, and notify me on Telegram when done`
**Exercises:** the exact flow from the README demo gif — research → code → check → notify
**Pass if:** all four stages complete in a single prompt. **This is the clip to pin.**

---

**Tier pass:** at least W1, W2, W5 run clean on API-key-only. W3/W4/W7 round out
the demo when Telegram + codegraph are configured.

## Recording checklist
- [ ] Terminal font large enough to read in video
- [ ] `--session demo` so runs are reproducible
- [ ] Show the startup panel (agents + MCP servers) once at the top
- [ ] Capture at least one confirmation prompt (proves the safety story)
- [ ] Capture one parallel DAG and one spawn (proves the orchestration story)
- [ ] Save clips as `clips/<ID>_<short-name>.mp4` next to these files
