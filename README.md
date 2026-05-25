<p align="center">
  <img src="ReadmeImages/anet.jpg" alt="ANet" width="900">
</p>

<h1 align="center">ANet</h1>

<p align="center">
  <strong>A config-driven multi-agent assistant for coding, research, and desktop automation.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Engine-pure_Python-1a1a2e?style=flat" alt="Pure Python Engine">
  <img src="https://img.shields.io/badge/OpenRouter-300%2B_models-FF6B35?style=flat" alt="OpenRouter">
  <img src="https://img.shields.io/badge/Vertex_AI-Gemini-4285F4?style=flat&logo=googlecloud&logoColor=white" alt="Vertex AI">
  <img src="https://img.shields.io/badge/MCP-codegraph_%7C_playwright-8B5CF6?style=flat" alt="MCP">
  <img src="https://img.shields.io/badge/Platform-Windows-0078D4?style=flat&logo=windows&logoColor=white" alt="Windows">
  <img src="https://img.shields.io/badge/License-MIT-22c55e?style=flat" alt="MIT">
</p>

<p align="center">Six specialized agents. Nineteen built-in tools. Persistent memory. Self-improving skills.</p>

---

## What it does

ANet routes your request through a planning layer that decides which agents to run, in what order, and in parallel where possible. Each agent has its own model, its own tool surface, and its own job. You get a synthesized answer when all the pieces land.

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

---

## Install

Python 3.11+ required.

```bash
git clone <repo>
cd Anet
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your keys (see [Environment variables](#environment-variables)).

```bash
python main.py
```

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

**spawn_tool** is auto-injected into every agent, allowing any agent to delegate a sub-task to another agent at runtime without returning to the manager.

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
| **web_search** | Web search via DuckDuckGo — no API key required |
| **download_file** | Download a file from a direct URL; reports image dimensions |

### Desktop Automation (Windows)

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

## How the loop works

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

- **Per-agent step cap** — each agent has a configurable `max_steps` (defaults: research 10, code 60, file 25, computer 20, checker 8). Overridable per-agent in `anet.config.yaml`.
- **Cycle detection** — same write operation repeated 3× in a sliding window stops the loop.
- **Spawn depth limit** — `spawn_tool` nesting is capped at 2 to prevent infinite delegation chains.
- **Confirmation policy** — `shell_tool` (every command), `edit_tool` (every edit), and destructive `file_tool` actions pause for explicit `y/n/a` approval.

---

## Intelligence & Memory

### Agent Persona — `SOUL.md`

The manager's personality is defined in `SOUL.md` at the repo root. It is injected into the planner and synthesizer prompts on every turn. Sub-agents are unaffected — their specialized prompts stay clean.

Edit `SOUL.md` to change how Anet presents itself, its tone, and its behaviour rules. Disable via `anet.config.yaml`:

```yaml
persona:
  enabled: false
```

### User Profile — `memory/USER.md`

Anet maintains a structured profile of you across two mechanisms:

**Incremental background agent** — every N turns (default: 5), a background task fires silently after a reply. In a single LLM call it:
- Updates `memory/USER.md` with any new facts (preferences, tech stack, projects, working style)
- Saves discrete facts to `memory_tool` (`~/.anet/memory.json`) as cross-session memories, with deduplication

**Session-end update** — on every clean exit (`exit` / `quit`), the full session history is also sent to the manager model and `memory/USER.md` is updated as a final pass.

On the next session start, the profile is injected into the planner prompt so Anet already knows you.

View the current profile with `/profile`. Configure in `anet.config.yaml`:

```yaml
memory:
  user_profile_enabled: true
  incremental_interval: 5   # turns between background reviews (0 to disable)
  # model: gemini-2.5-flash  # optional cheaper model for memory (defaults to manager)
  # provider: google
```

### 10-Turn Memory Nudge

Every 10 substantive user messages, the agent handling the current task is prompted to save any genuinely new facts to `memory_tool` before proceeding. This keeps persistent memory up to date without any manual effort.

Configure the interval (or disable) in `anet.config.yaml`:

```yaml
memory:
  nudge_enabled: true
  nudge_interval: 10    # 0 to disable
```

### Context Compression

When a session grows long (>40 messages), Anet prompts you with two options:

- **[f] forget** — drop the oldest messages, keep the last 20
- **[c] compress** — summarise old messages into a single block via the manager model

Also available as slash commands: `/forget`, `/compress`.

### Self-Improving Skills — `skills/`

After any task where an agent made ≥6 tool calls **and** self-corrected at least once (same tool called with different args, or shell command retried after failure), Anet writes a reusable procedure file to `skills/` in the background.

Before every agent task, the skills folder is keyword-searched against the task description. Up to 3 matching skills are injected into the agent's system prompt as "Relevant Skills from Past Experience". No match → nothing injected, no noise.

**Curator** — at startup, if `skills/` contains ≥5 files, a background Curator pass runs:
- Groups skills with >70% keyword similarity and merges duplicates (originals archived to `skills/archived/`)
- Improves skills that have been used ≥3 times

Configure in `anet.config.yaml`:

```yaml
skills:
  enabled: true
  creation_threshold: 6   # tool calls needed to trigger skill creation
  curator_min_skills: 5   # min files before Curator runs
  max_injected: 3         # max skills injected per task
```

View all skills with `/skills`.

---

## Configuration

### `anet.config.yaml`

```yaml
# Persona — loaded from SOUL.md, injected into manager prompts
persona:
  soul_file: SOUL.md
  enabled: true

# Memory — user profile + background agent + memory nudge
memory:
  user_profile_enabled: true
  incremental_interval: 5   # background memory review every N turns (0 to disable)
  nudge_enabled: true
  nudge_interval: 10
  # model: gemini-2.5-flash  # optional cheaper model for background memory
  # provider: google

# Self-improving skills
skills:
  enabled: true
  creation_threshold: 6
  curator_min_skills: 5
  max_injected: 3

# Manager model
manager:
  model: google/gemini-2.5-flash
  provider: vertex_google

# Per-agent overrides — model, provider, max_steps, extra_tools, mcp
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
| `openrouter` | `OPENROUTER_API_KEY` | 300+ models via one key |
| `openai` | `OPENAI_API_KEY` | GPT models |
| `claude` | `ANTHROPIC_API_KEY` | Claude models |
| `vertex_google` | `VERTEX_PROJECT_ID` + ADC | Gemini on Vertex AI (uses GCP credits) |
| `vertex_claude` | `VERTEX_PROJECT_ID` + ADC | Claude on Vertex AI |

For Vertex AI: run `gcloud auth application-default login` once, then set `VERTEX_PROJECT_ID` in `.env`.

---

## Environment variables

### Minimum to start

Create a `.env` file in the project root with just one key:

```env
OPENROUTER_API_KEY=your_key_here
```

`OPENROUTER_API_KEY` drives all agents (free models available). Web search uses DuckDuckGo — **no API key required**. That's it — everything else is optional.

### Switching to a different provider

Add whichever key matches the provider you set in `anet.config.yaml`:

```env
GOOGLE_API_KEY=...          # provider: google
OPENAI_API_KEY=...          # provider: openai
ANTHROPIC_API_KEY=...       # provider: claude

# Vertex AI — also run: gcloud auth application-default login
VERTEX_PROJECT_ID=your-gcp-project-id
VERTEX_LOCATION=us-central1  # optional, default is us-central1
```

### External agent credentials

Each external agent keeps its own `.env` in its folder — **not** in the root `.env`. For example, `tele_agent` needs:

```
ExAgents/tele_agent/.env
─────────────────────────
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

ANet loads these automatically at startup for each external agent that has one.

---

## Slash commands

| Command | What it does |
|---|---|
| `/agents` | Show loaded agents, models, and tool lists |
| `/skills` | List all saved skills with their applies-to description and usage count |
| `/profile` | Show the current user profile (`memory/USER.md`) |
| `/sessions` | List all saved sessions |
| `/session <name>` | Switch to a named session (creates it if new) |
| `/new` | Start a fresh session |
| `/forget` | Drop oldest messages, keep last 20 |
| `/compress` | Summarise old messages into one block |
| `/clear` | Clear the screen |
| `/help` | Show this list |
| `exit` or `quit` | End the session (triggers USER.md update) |

Sessions persist in `memory/<session_id>/checkpoint.db`. Resume with `--resume` or open a specific session with `--session <name>`.

```bash
python main.py --resume
python main.py --session my-project
python main.py --list-sessions
```

---

## Extending ANet

### Add an external tool — ExTools

Create `ExTools/<tool_name>/__init__.py` with a `SCHEMA` dict and an async `run(params)` function. Register it in `exanet.config.yaml`:

```yaml
ex_tools:
  - name: my_tool
    path: ExTools/my_tool
```

### Add an external agent — ExAgents

Create `ExAgents/<agent_name>/` with `agent.py` (config dict) and optionally `prompt.md` and `.env`. Register in `exanet.config.yaml`:

```yaml
ex_agents:
  - name: my_agent
    path: ExAgents/my_agent
```

External agents automatically get `spawn_tool` injected — they can delegate to other agents without any extra config.

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
│   └── core/
│       ├── engine.py        # Pure-Python planner→executor→checker→synthesizer (replaces LangGraph)
│       ├── store.py         # ConversationStore — aiosqlite-backed message persistence
│       ├── memory_agent.py  # Background memory agent — updates USER.md + saves facts to memory_tool
│       ├── orchestrator.py  # Agentic loop, cycle detection, skill tracking
│       ├── agent_runner.py  # Model calls, provider dispatch
│       ├── skill_manager.py # Self-improving skills — search, create, curate
│       ├── mcp_loader.py    # MCP server lifecycle management
│       ├── tool_loader.py   # Built-in tool loader
│       ├── ex_loader.py     # External agent/tool loader
│       └── config_loader.py # anet.config.yaml reader + soul/profile loaders
│
├── mcps/
│   ├── codegraph/           # Code graph MCP (config.yaml + source)
│   └── playwright/          # Browser automation MCP
│
├── skills/                  # Auto-written skill procedures (grows over time)
├── ExAgents/                # Your custom agents (not in core)
├── ExTools/                 # Your custom tools (not in core)
└── memory/
    ├── USER.md              # Auto-built user profile (updated incrementally + on session exit)
    └── <session_id>.db      # Per-session SQLite conversation store (aiosqlite)
```

---

## Web dashboard

A FastAPI-based dashboard runs alongside the CLI:

```bash
python server.py
```

Opens at `http://localhost:8000`.

---

## Requirements

- Python 3.11+
- Windows (for `computer_agent` / desktop automation)
- Node.js (for MCP servers — codegraph, playwright)
- `pip install -r requirements.txt`
- Optional: `pip install pyautogui pywinauto Pillow` for desktop automation
- Optional: `pip install google-auth` for Vertex AI providers
