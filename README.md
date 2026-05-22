<p align="center">
  <img src="ReadmeImages/anet.jpg" alt="ANet" width="900">
</p>

<h1 align="center">ANet</h1>

<p align="center">
  <strong>A config-driven multi-agent assistant for coding, research, and desktop automation.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/LangGraph-orchestration-1a1a2e?style=flat" alt="LangGraph">
  <img src="https://img.shields.io/badge/OpenRouter-300%2B_models-FF6B35?style=flat" alt="OpenRouter">
  <img src="https://img.shields.io/badge/MCP-codegraph_%7C_playwright-8B5CF6?style=flat" alt="MCP">
  <img src="https://img.shields.io/badge/Platform-Windows-0078D4?style=flat&logo=windows&logoColor=white" alt="Windows">
  <img src="https://img.shields.io/badge/License-MIT-22c55e?style=flat" alt="MIT">
</p>

<p align="center">Six specialized agents. Eighteen built-in tools. One conversation.</p>

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
ANet: "Done. Tests pass. Message sent."
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
| **research_agent** | Web research, fact-finding, news, image downloads | web_search, download_file, memory_tool |
| **code_agent** | Write, edit, refactor, test, and debug code | edit_tool, shell_tool, grep_tool, lsp_tool, conflict_tool, diagnose_tool + codegraph MCP |
| **file_agent** | File system operations — copy, move, zip, conflict resolution | file_tool, conflict_tool, memory_tool |
| **computer_agent** | Windows desktop automation — launch apps, click, type, screenshot | open_app |
| **checker_agent** | Validates results from other agents | checker |
| **tele_agent** *(external)* | Send messages, files, photos to Telegram | tele_tool |

All agents default to **Gemini 2.5 Flash** unless overridden in `anet.config.yaml`.

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
| **conflict_tool** | Resolve git merge conflicts — `@ours`, `@theirs`, `@base`, or custom text. Supports diff3 style. |
| **lsp_tool** | Code intelligence via LSP — diagnostics, hover, go-to-definition, find references, rename, symbols |

### Research & Web

| Tool | What it does |
|---|---|
| **web_search** | Semantic web search via Exa API |
| **download_file** | Download a file from a URL; reports image dimensions |

### Desktop Automation (Windows)

| Tool | What it does |
|---|---|
| **open_app** | Launch apps, manage windows, type text, click elements, keyboard shortcuts, take screenshots |

### Coordination & Memory

| Tool | What it does |
|---|---|
| **todo_tool** | Session-scoped task checklist shown in the live spinner |
| **memory_tool** | Persistent cross-session memory — save, search, delete facts |
| **checker** | Classify task outcomes as success / failure / partial |

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
        └─ checker_agent validates ← ← ← retry if partial
                   ↓
            synthesizer → final reply
```

**Safety mechanisms**

- **Safety cap** — agent loops terminate after 80 tool calls.
- **Cycle detection** — same write operation repeated 3x in a sliding window stops the loop.
- **Confirmation policy** — `shell_tool` (every command), `edit_tool` (every edit), and destructive `file_tool` actions pause for explicit `y/n/a` approval.

---

## Configuration

### `anet.config.yaml` — swap models, add MCP

```yaml
manager:
  model: gemini-2.5-pro
  provider: google

agents:
  code_agent:
    model: claude-opus-4-7
    provider: claude
    mcp:
      - codegraph     # injects all codegraph tools into this agent
  research_agent:
    model: gpt-4o
    provider: openai
```

### Supported providers

| Key | API key env var | Notes |
|---|---|---|
| `google` | `GOOGLE_API_KEY` | Gemini models |
| `openrouter` | `OPENROUTER_API_KEY` | 300+ models via one key |
| `openai` | `OPENAI_API_KEY` | GPT models |
| `claude` | `ANTHROPIC_API_KEY` | Claude models |
| `vertex_google` | `VERTEX_PROJECT_ID` | Gemini on Vertex AI |
| `vertex_claude` | `VERTEX_PROJECT_ID` | Claude on Vertex AI |

---

## Environment variables

Create a `.env` file in the project root:

```env
# Required — at least one provider key
OPENROUTER_API_KEY=...
GOOGLE_API_KEY=...
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...

# Web search (research_agent)
EXA_API_KEY=...

# Telegram (tele_agent — optional)
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# Vertex AI (optional)
VERTEX_PROJECT_ID=...
VERTEX_LOCATION=us-central1

# Display name in the CLI (default: Anet)
ASSISTANT_NAME=Anet
```

---

## Slash commands

| Command | What it does |
|---|---|
| `/agents` | Show loaded agents and their current tool lists |
| `/sessions` | List all saved sessions |
| `/session <name>` | Switch to a named session (creates it if new) |
| `/new` | Start a fresh session |
| `/clear` | Clear the screen |
| `/help` | Show this list |
| `exit` or `quit` | End the session |

Sessions are persisted in `memory/<session_id>/checkpoint.db` and survive restarts. Use `--resume` to pick up the last one, or `--session <name>` to open a specific session.

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

Create `ExAgents/<agent_name>/agent.py` with an agent config dict. Register in `exanet.config.yaml`:

```yaml
ex_agents:
  - name: my_agent
    path: ExAgents/my_agent
```

External agents and tools live outside the `anet/` core. Hot-reload picks them up when `exanet.config.yaml` changes — no restart needed.

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
├── anet.config.yaml         # Model/provider overrides per agent
├── exanet.config.yaml       # External agents and tools
├── requirements.txt
├── .env                     # API credentials (not committed)
│
├── anet/
│   ├── AnetAgents/          # Built-in agent definitions
│   ├── AnetTools/           # Built-in tool implementations
│   └── core/
│       ├── graph_builder.py # LangGraph StateGraph construction
│       ├── orchestrator.py  # Agentic loop, cycle detection, confirmation
│       ├── agent_runner.py  # Model calls, provider dispatch
│       ├── mcp_loader.py    # MCP server lifecycle management
│       ├── tool_loader.py   # Built-in tool loader
│       ├── ex_loader.py     # External agent/tool loader
│       └── config_loader.py # anet.config.yaml / exanet.config.yaml reader
│
├── mcps/
│   ├── codegraph/           # Code graph MCP (config.yaml + source)
│   └── playwright/          # Browser automation MCP
│
├── ExAgents/                # Your custom agents (not in core)
├── ExTools/                 # Your custom tools (not in core)
└── memory/                  # Per-session SQLite checkpoints + memories
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
