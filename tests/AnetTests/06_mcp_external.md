# Tier 06 — MCP Servers & External Extensions

Goal: demonstrate the extension surface — MCP servers (codegraph, playwright) and
external agents/tools loaded from `exanet.config.yaml` without touching core.

Prereqs:
- **codegraph / playwright** — Node.js installed; servers wired into an agent's
  `mcp:` list (default: codegraph→code_agent, playwright→file_agent).
- **tele_agent / tele_tool** — these ship as the **example** ExAgent + ExTool but
  are **disabled by default**. To run X6–X8: add `TELEGRAM_BOT_TOKEN` +
  `TELEGRAM_CHAT_ID` to `ExAgents/tele_agent/.env`, then uncomment the `tele_agent`
  block **and** the `tele_tool` entry in `exanet.config.yaml` and restart. See
  `ExAgents/README.md` / `ExTools/README.md`.

At startup, confirm the MCP panel lists codegraph + playwright before testing.

---

## codegraph MCP

### X1 — Index the repo
**Prompt:** `index this codebase with codegraph, then tell me when it's ready`
**Exercises:** codegraph MCP `index` / `status`
**Pass if:** indexing runs and status reports ready.

### X2 — Symbol / dependency query
**Prompt:** `using codegraph, where is the Engine class defined and what calls run_turn?`
**Exercises:** codegraph `query` / `context` / `affected`
**Pass if:** points to `anet/core/engine.py` and lists real callers.

### X3 — Impact analysis
**Prompt:** `if I change the signature of run_turn, which files are affected? use codegraph`
**Pass if:** returns a plausible affected-file set from the graph (not a grep guess).

## playwright MCP

### X4 — Navigate + snapshot
**Prompt:** `use playwright to open https://example.com and tell me the page heading`
**Exercises:** playwright `navigate` / `snapshot`
**Pass if:** returns "Example Domain" heading from a real browser session.

### X5 — Extract via evaluate
**Prompt:** `with playwright, go to https://news.ycombinator.com and list the first 3 story titles`
**Pass if:** 3 current HN titles returned.

## External agent / tools

### X6 — tele_agent notification *(enable the example first — see prereqs)*
**Prompt:** `send me a Telegram message saying "ANet e2e test passed"`
**Exercises:** external `tele_agent` → `tele_tool`, task_type routing, planner routing to an ExAgent
**Pass if:** `/agents` shows tele_agent, and the message arrives in your Telegram chat.

### X7 — tele_agent with a file
**Prompt:** `send scratch/report.md to my Telegram as a document`
**Pass if:** the document is delivered.

### X8 — ExTool / ExAgent registration round-trip
Start ANet with `tele_tool` + `tele_agent` **commented out** in
`exanet.config.yaml` → confirm `/agents` shows **no** external agent. Stop,
uncomment both blocks, restart → confirm tele_agent now appears in `/agents` and
the startup tool panel.
**Exercises:** `ex_loader` picking up `exanet.config.yaml` registrations
**Pass if:** the external agent/tool appears only after it's registered — proving
the "bring your own, nothing forced by default" model.

## Tool generator (`/newtool`)

### X9 — Generate an ExTool from existing code
Put some wrappable source in a folder, e.g. `ExTools/wordcount/wordcount_repo/`
with a function that counts words in a string. Then:
**Prompt:** `/newtool ExTools/wordcount/wordcount_repo`
**Exercises:** standalone `toolsmith` agent (bypasses the manager) → explore →
ask_user confirm → write `__init__.py` → `extool_validator` → fix loop → prints stanza
**Watch for:** an ask_user confirmation of the tool name/params, a file write
(with diff + y/n), one or more `python -m anet.core.extool_validator ...` runs
ending in **PASS**, and a printed `exanet.config.yaml` stanza.
**Pass if:** `ExTools/wordcount/__init__.py` exists, validates, and the stanza is
printed — **and no config file was edited automatically.**

### X10 — Validator standalone
**Prompt (shell, or run directly):** `python -m anet.core.extool_validator ExTools/tele_tool/__init__.py`
**Pass if:** prints `PASS` (exit 0). Try a hand-broken file → `INVALID` (exit 1).

### X11 — MCP doctor (connect-test)
**Prompt:** `/mcptest codegraph`
**Exercises:** `mcp_doctor` → real stdio launch + `list_tools`
**Watch for:** OK lines for mcp pkg / config / PATH, then a connected tool list.
**Pass if:** PASS with the codegraph tool names. Try `/mcptest doesnotexist` → INVALID.

### X12 — MCP integration agent (`/addmcp`)
Point it at a local MCP repo/README (e.g. clone a small Node MCP server beside the repo).
**Prompt:** `/addmcp ../some-mcp-server`
**Exercises:** standalone `mcpsmith` agent → read docs → ask_user confirm → write
`mcps/<name>/config.yaml` → `mcp_doctor` verify → fix loop → print wiring
**Watch for:** a config write (diff + y/n), a doctor run ending in PASS, and the
printed `anet.config.yaml` `mcp:` stanza.
**Pass if:** `mcps/<name>/config.yaml` exists and connects — and no config was
edited automatically. (If the server is HTTP/SSE-only, the agent should say so and stop.)

---

**Tier pass:** MCP tools appear in the startup panel and work; at least one
external agent/tool runs through `exanet.config.yaml` with zero core edits; and
`/newtool` scaffolds a valid, validated tool without touching config.
