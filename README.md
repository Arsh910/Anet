<div align="center">
<table>
<tr>
<td><img src="ReadmeImages/anet.png" width="430" alt="ANET — gradient on binary field"></td>
<td><img src="ReadmeImages/anet-mono.png" width="430" alt="ANET — white on binary field"></td>
</tr>
<tr>
<td><img src="ReadmeImages/anet-mono-clean.png" width="430" alt="ANET — white, clean"></td>
<td><img src="ReadmeImages/anet-clean.png" width="430" alt="ANET — gradient, clean"></td>
</tr>
</table>
</div>

<h1 align="center">ANet</h1>

<p align="center">
  <strong>Run Claude for code. Gemini for research. GPT-4o for planning.<br>
  All from a single YAML file. No framework lock-in.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Engine-pure_Python-1a1a2e?style=flat" alt="Pure Python Engine">
  <img src="https://img.shields.io/badge/OpenRouter-300%2B_models-FF6B35?style=flat" alt="OpenRouter">
  <img src="https://img.shields.io/badge/Vertex_AI-Gemini-4285F4?style=flat&logo=googlecloud&logoColor=white" alt="Vertex AI">
  <img src="https://img.shields.io/badge/MCP-codegraph_%7C_playwright-8B5CF6?style=flat" alt="MCP">
  <img src="https://img.shields.io/badge/computer__agent-Windows_only-0078D4?style=flat&logo=windows&logoColor=white" alt="Windows">
  <img src="https://img.shields.io/badge/everything_else-cross--platform-22c55e?style=flat" alt="Cross-platform">
  <img src="https://img.shields.io/badge/License-MIT-f59e0b?style=flat" alt="MIT">
</p>

<p align="center">Six specialized agents. Nineteen built-in tools. Persistent memory. Self-improving skills.</p>

---

## Demo

<p align="center">
  <img src="ReadmeImages/demo.gif" alt="ANet Demo" width="900">
</p>

> *research FastAPI best practices → refactor routes.py → run tests → send Telegram notification. All in one prompt.*

---

## Why ANet?

Most agent frameworks lock you into one model, one provider, and one way of working. ANet doesn't.

| | ANet | Most others |
|---|---|---|
| Per-agent model selection | ✅ YAML config, swap anytime | ❌ hardcoded or global |
| Web search without API keys | ✅ DuckDuckGo built-in | ❌ paid API required |
| Bring your own tools | ✅ ExTools hot-reload, no restart | ⚠️ framework-specific |
| Confirmation before shell/file ops | ✅ always, `y/n/a` prompt | ❌ runs blind |
| Real LSP code intelligence | ✅ go-to-def, rename, references | ❌ grep-based |
| Session resume | ✅ `--resume` flag | ❌ starts fresh every time |
| Agents spawning sub-agents | ✅ `spawn_tool`, depth-limited | ❌ not available |
| Self-improving skills | ✅ agent writes its own procedures | ❌ not available |
| User profile across sessions | ✅ auto-built `USER.md` | ❌ no memory of you |

---

## Quickstart

**One API key is all you need to start.**

```bash
git clone https://github.com/Arsh910/Anet.git
cd Anet
pip install -r requirements.txt
```

Create a `.env` file with just one key:

```env
OPENROUTER_API_KEY=your_key_here
```

> Free models are available on OpenRouter. Web search uses DuckDuckGo — no Exa key, no paid search API needed.

```bash
python main.py
```

That's it. Everything else (Telegram, Vertex AI, MCP servers) is optional and added only when you need it.

---

## How it works

ANet routes your request through a planning layer that decides which agents to run, in what order, and in parallel where possible. Each agent has its own model, its own tool surface, and its own job.

```
You: "refactor this module, run the tests, and send me a Telegram when done"
  ↓
Manager (plans the work)
  ├─ code_agent    → edits code, runs tests, checks diagnostics
  ├─ checker_agent → validates the result
  └─ tele_agent    → sends the notification
  ↓
Anet: "Done. Tests pass. Message sent."
```

```
planner (manager model)
  │
  ├─ simple request → direct reply → done
  │
  └─ complex request → DAG of steps
        │
        ├─ step A (agent 1) ─── parallel ──── step B (agent 2)
        │         ↓                                   ↓
        │   spawn_tool → sub-agent (depth ≤ 2)        │
        │                                             │
        └─ checker_agent validates ← ← ← retry if partial
                   ↓
            synthesizer → final reply
```

**Safety mechanisms**

- **Per-agent step cap** — each agent has a configurable `max_steps` (defaults: research 10, code 60, file 25, computer 20, checker 8).
- **Cycle detection** — same write operation repeated 3× in a sliding window stops the loop.
- **Spawn depth limit** — `spawn_tool` nesting is capped at 2 to prevent infinite delegation chains.
- **Confirmation policy** — `shell_tool` (every command), `edit_tool` (every edit), and destructive `file_tool` actions pause for explicit `y/n/a` approval.

---

## Startup screen

On launch, Anet shows an animated block-art banner (green→cyan gradient reveal) followed by a **compact status line** instead of dumping every agent, tool, and server:

```
Agents   6/6 loaded       /agents to view
Tools    16/16 ready      /tools to view
MCP      2/2 connected    /mcps to view
```

Expand any of them on demand with `/agents`, `/tools`, or `/mcps`. The banner art is generated in pure Python (`anet/cli/banner.py`) and can also be exported to high-res PNG/JPEG for docs via `save_image()`.

---

## Agents

| Agent | What it does | Key tools |
|---|---|---|
| **research_agent** | Web research, fact-finding, news, image downloads | web_search, download_file |
| **code_agent** | Write, edit, refactor, test, and debug code | edit_tool, shell_tool, grep_tool, lsp_tool, conflict_tool, diagnose_tool + codegraph MCP |
| **file_agent** | File system operations — copy, move, zip, conflict resolution | file_tool, conflict_tool, memory_tool |
| **computer_agent** | Windows desktop automation — launch apps, click, type, screenshot | open_app |
| **checker_agent** | Validates results from other agents | checker |
| **tele_agent** *(external)* | Send messages, files, photos to Telegram | tele_tool |

All agents default to **Gemini 2.5 Flash** unless overridden in `anet.config.yaml`.

**`spawn_tool`** is auto-injected into every agent — any agent can delegate a sub-task to another agent at runtime without returning to the manager.

> **Platform note:** `computer_agent` (desktop automation) requires Windows. All other agents and tools are cross-platform.

---

## Tools

### Files & Code

| Tool | What it does |
|---|---|
| **edit_tool** | Surgical file edits — old string to new string, with unified diff output |
| **file_tool** | Read, write, copy, move, delete, zip, unzip, parse CSV/JSON |
| **glob_tool** | Find files by glob pattern, sorted by modification time |
| **grep_tool** | Regex content search across files (ripgrep with Python fallback) |
| **shell_tool** | Run shell commands — tests, linters, builds, anything |
| **process_tool** | Start a command, stream output until a pattern matches or timeout fires |
| **diagnose_tool** | Run ruff/pyright for Python, eslint/tsc for JS/TS, report problems |
| **conflict_tool** | Resolve git merge conflicts — `@ours`, `@theirs`, `@base`, or custom text |
| **lsp_tool** | Code intelligence via LSP — diagnostics, hover, go-to-definition, find references, rename, symbols |

### Research & Web

| Tool | What it does |
|---|---|
| **web_search** | Web search via DuckDuckGo — **no API key required** |
| **download_file** | Download a file from a direct URL; reports image dimensions |

### Desktop Automation (Windows only)

| Tool | What it does |
|---|---|
| **open_app** | Launch apps, manage windows, type text, click elements, keyboard shortcuts, screenshots |

### Coordination & Memory

| Tool | What it does |
|---|---|
| **todo_tool** | Session-scoped task checklist shown live in the spinner |
| **memory_tool** | Persistent cross-session memory — save, search, delete facts |
| **checker** | Classify task outcomes as success / failure / partial |
| **spawn_tool** | Delegate a sub-task to any other agent at runtime (depth-limited to 2) |

### MCP Servers

MCP servers extend the tool surface without touching the core. They appear in a separate panel at startup.

| Server | Tools | What it does |
|---|---|---|
| **codegraph** | index, sync, query, context, files, affected, status | Production-grade code graph — symbol search, full-text search, file tree, dependency analysis |
| **playwright** | navigate, click, fill, screenshot, snapshot, evaluate, … | Drive a real browser — Chromium, CDP-attached apps, form filling, JS evaluation |

---

## Intelligence & Memory

### Agent Persona — `SOUL.md`

The manager's personality is defined in `SOUL.md` at the repo root. Injected into the planner and synthesizer prompts on every turn. Edit it to change Anet's name, tone, and behaviour rules. Sub-agents are unaffected — their specialized prompts stay clean.

```yaml
# anet.config.yaml
persona:
  enabled: false   # disable if you want a neutral system prompt
```

### User Profile — `memory/USER.md`

Anet builds a structured profile of you automatically across two mechanisms:

**Background agent** — every 5 turns (configurable), a silent background task updates `memory/USER.md` with new facts (preferences, tech stack, projects, working style) and saves discrete facts to `memory_tool` with deduplication.

**Session-end update** — on every clean exit, the full session history is reviewed and `USER.md` gets a final pass.

On the next session start, the profile is injected into the planner so Anet already knows you. View it with `/profile`.

### 10-Turn Memory Nudge

Every 10 substantive messages, the active agent is prompted to push any genuinely new facts to `memory_tool` before proceeding — so nothing important slips through between background reviews.

### Context Compression

When a session grows long (>40 messages), Anet prompts with two options:

- **[f] forget** — drop the oldest messages, keep the last 20
- **[c] compress** — summarise old messages into a single block via the manager model

Also available as slash commands: `/forget`, `/compress`.

### Self-Improving Skills — `skills/`

After any task where an agent made ≥6 tool calls **and** self-corrected at least once, Anet writes a reusable procedure file to `skills/` in the background.

Before every agent task, the skills folder is keyword-searched against the task description. Up to 3 matching skills are injected into the agent's system prompt as "Relevant Skills from Past Experience". No match — nothing injected, no noise.

**Curator** — at startup, if `skills/` has ≥5 files, a background Curator pass runs: merges duplicate skills and improves skills used ≥3 times. Originals are archived to `skills/archived/`.

View all skills with `/skills`.

---

## Configuration

### `anet.config.yaml`

```yaml
# Persona
persona:
  soul_file: SOUL.md
  enabled: true

# Memory + skills
memory:
  user_profile_enabled: true
  incremental_interval: 5   # background memory review every N turns (0 to disable)
  nudge_enabled: true
  nudge_interval: 10
  # model: gemini-2.5-flash  # cheaper model for background memory tasks

skills:
  enabled: true
  creation_threshold: 6
  curator_min_skills: 5
  max_injected: 3

# Manager model
manager:
  model: google/gemini-2.5-flash
  provider: vertex_google

# Per-agent overrides
agents:
  code_agent:
    model: claude-opus-4-7
    provider: claude
    max_steps: 80
    mcp: [codegraph]
  research_agent:
    model: google/gemini-2.5-flash
    provider: vertex_google
    max_steps: 10
```

### Supported providers

| Key | API key env var | Notes |
|---|---|---|
| `google` | `GOOGLE_API_KEY` | Gemini models direct |
| `openrouter` | `OPENROUTER_API_KEY` | 300+ models via one key, free tier available |
| `openai` | `OPENAI_API_KEY` | GPT models |
| `claude` | `ANTHROPIC_API_KEY` | Claude models |
| `vertex_google` | `VERTEX_PROJECT_ID` + ADC | Gemini on Vertex AI (GCP credits) |
| `vertex_claude` | `VERTEX_PROJECT_ID` + ADC | Claude on Vertex AI |

For Vertex AI: run `gcloud auth application-default login` once, then set `VERTEX_PROJECT_ID` in `.env`.

---

## Environment variables

### Minimum to start

```env
OPENROUTER_API_KEY=your_key_here
```

Free models available. Web search is DuckDuckGo — no extra key needed.

### Adding other providers

```env
GOOGLE_API_KEY=...          # provider: google
OPENAI_API_KEY=...          # provider: openai
ANTHROPIC_API_KEY=...       # provider: claude

# Vertex AI — also run: gcloud auth application-default login
VERTEX_PROJECT_ID=your-gcp-project-id
VERTEX_LOCATION=us-central1
```

### External agent credentials

Each external agent keeps its own `.env` in its folder — not in the root `.env`. Example for `tele_agent`:

```
ExAgents/tele_agent/.env
─────────────────────────
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

ANet loads these automatically at startup.

---

## Slash commands

| Command | What it does |
|---|---|
| `/agents` | Show loaded agents, models, and tool lists |
| `/tools` | Show loaded tools and their status |
| `/mcps` | Show connected MCP servers and their tools |
| `/skills` | List all saved skills with description and usage count |
| `/profile` | Show the current user profile (`memory/USER.md`) |
| `/sessions` | List all saved sessions |
| `/session <name>` | Switch to a named session (creates it if new) |
| `/new` | Start a fresh session |
| `/forget` | Drop oldest messages, keep last 20 |
| `/compress` | Summarise old messages into one block |
| `/clear` | Clear the screen |
| `/help` | Show this list |
| `exit` or `quit` | End the session (triggers USER.md update) |

```bash
python main.py --resume                  # pick up your last session
python main.py --session my-project      # open a named session
python main.py --list-sessions           # see all sessions
```

---

## Web Dashboard

A FastAPI-based dashboard runs alongside the CLI:

```bash
python server.py   # opens at http://localhost:8000
```

---

## Extending ANet

### Add a tool — ExTools

Create `ExTools/<tool_name>/__init__.py` with a `SCHEMA` dict and an async `run(params)` function. Register in `exanet.config.yaml`:

```yaml
ex_tools:
  - name: my_tool
    path: ExTools/my_tool
```

No restart needed — hot-reload picks it up when `exanet.config.yaml` changes.

### Add an agent — ExAgents

Create `ExAgents/<agent_name>/` with `agent.py` (config dict) and optionally `prompt.md` and `.env`. Register in `exanet.config.yaml`:

```yaml
ex_agents:
  - name: my_agent
    path: ExAgents/my_agent
```

External agents automatically get `spawn_tool` injected — they can delegate to other agents without extra config.

### Add an MCP server

1. Create `mcps/<server_name>/config.yaml`:

```yaml
command: node
args:
  - /path/to/server.js
  - serve
  - --mcp
```

2. Add the server name to an agent's `mcp:` list in `anet.config.yaml`:

```yaml
agents:
  code_agent:
    mcp:
      - my_server
```

The server starts once on boot, stays alive for the session, and its tools are injected into every agent that declares it.

---

## Project structure

```
Anet/
├── main.py                  # CLI entry point
├── server.py                # Web dashboard entry point
├── anet.config.yaml         # Model/provider/persona/memory/skills config
├── exanet.config.yaml       # External agents and tools
├── SOUL.md                  # Agent persona — edit to change Anet's personality
├── requirements.txt
├── .env                     # API credentials (not committed)
│
├── anet/
│   ├── AnetAgents/          # Built-in agent definitions
│   ├── AnetTools/           # Built-in tool implementations
│   │   └── spawn_tool/      # Runtime subagent delegation
│   ├── cli/
│   │   └── banner.py        # Animated ANET startup banner + README image export
│   └── core/
│       ├── engine.py        # Pure-Python planner→executor→checker→synthesizer
│       ├── store.py         # aiosqlite conversation store — one shared db keyed by thread
│       ├── memory_agent.py  # Background memory — updates USER.md + memory_tool
│       ├── orchestrator.py  # Agentic loop, cycle detection, skill tracking
│       ├── agent_runner.py  # Model calls, provider dispatch
│       ├── skill_manager.py # Self-improving skills — search, create, curate
│       ├── mcp_loader.py    # MCP server lifecycle management
│       ├── tool_loader.py   # Built-in tool loader
│       ├── ex_loader.py     # External agent/tool loader
│       └── config_loader.py # Config + soul/profile loaders
│
├── mcps/
│   ├── codegraph/           # Code graph MCP
│   └── playwright/          # Browser automation MCP
│
├── skills/                  # Auto-written skill procedures (grows over time)
├── ExAgents/                # Your custom agents
├── ExTools/                 # Your custom tools
└── memory/                  # (under your Anet home, e.g. ~/.anet)
    ├── USER.md              # Auto-built user profile
    └── sessions/
        ├── conversations.db    # One shared SQLite store for ALL sessions, keyed by thread
        └── <session_id>/       # Per-session folder — metadata only (title.txt)
```

> **Sessions** all share a single `conversations.db` keyed by `thread`, so
> switching with `/session <name>` is instant and never loses context. Each
> session keeps a small folder for its title and metadata. (Legacy per-session
> `checkpoint.db` files are folded into the shared store automatically on first run.)

---

## Requirements

- Python 3.11+
- Node.js (for MCP servers — codegraph, playwright)
- `pip install -r requirements.txt`
- Windows only for `computer_agent`: `pip install pyautogui pywinauto Pillow`
- Vertex AI providers: `pip install google-auth` + `gcloud auth application-default login`
