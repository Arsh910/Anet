<p align="center">
  <img src="ReadmeImages/anet-clean.png" alt="ANET" width="100%">
</p>

# ANET

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Engine-pure_Python-1a1a2e?style=for-the-badge" alt="Pure Python">
  <img src="https://img.shields.io/badge/OpenRouter-300%2B_models-FF6B35?style=for-the-badge" alt="OpenRouter">
  <img src="https://img.shields.io/badge/MCP-codegraph_%7C_playwright-8B5CF6?style=for-the-badge" alt="MCP">
  <img src="https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge" alt="MIT">
</p>

**A local, multi-agent AI assistant that routes each request to the right specialist — on the model and provider _you_ choose.** One prompt can research the web, write and run code, drive a browser, automate your Windows desktop, and notify you on Telegram — planned and coordinated automatically, with a confirmation gate before anything touches your shell or files.

Use any model, per agent. Claude for code, Gemini for research, GPT for planning, a free model for the rest — set it in one YAML file via [OpenRouter](https://openrouter.ai) (300+ models), Google, OpenAI, Anthropic, or Vertex AI. No framework lock-in, no LangChain — a pure-Python engine you can read end to end.

<table>
<tr><td><b>Multi-agent, multi-model</b></td><td>Five built-in agents (research, code, file, computer, checker) plus your own. Each picks its own model + provider in <code>anet.config.yaml</code>. The manager plans a DAG and runs independent steps in parallel.</td></tr>
<tr><td><b>Builds its own integrations</b></td><td>The <b>ToolSmith</b> (<code>/newtool</code>) and <b>MCPSmith</b> (<code>/addmcp</code>) scaffold a new tool or MCP server from your code/docs, validate it until it passes, and hand you the exact config to paste.</td></tr>
<tr><td><b>Safe by default</b></td><td>Every shell command, file edit, and destructive file op pauses for explicit <code>y/n/a</code> approval. Per-agent step caps and cycle detection stop runaways.</td></tr>
<tr><td><b>Remembers you</b></td><td>Auto-built user profile (<code>USER.md</code>), cross-session memory, and a 10-turn memory nudge — so it knows your stack and preferences next time.</td></tr>
<tr><td><b>Learns from experience</b></td><td>After a complex, self-corrected task it writes a reusable <b>skill</b>; relevant skills are injected into future tasks, and a Curator improves them over time.</td></tr>
<tr><td><b>Real developer tooling</b></td><td>LSP code intelligence (go-to-def, rename, references), ripgrep search, ruff/pyright/eslint diagnostics, git conflict resolution, and a <code>codegraph</code> MCP for whole-repo symbol/dependency analysis.</td></tr>
<tr><td><b>A real terminal UI</b></td><td>Animated banner, slash-command autocomplete, live status spinner, session resume, and <code>ESC</code> to interrupt.</td></tr>
</table>

---

## Quick install

> **One API key is all you need to start.** Free models are on OpenRouter, and web search uses DuckDuckGo — no paid search key.

```bash
git clone https://github.com/Arsh910/Anet.git
cd Anet
pip install -r requirements.txt
```

Create a `.env` with one key:

```env
OPENROUTER_API_KEY=your_key_here
```

Start it:

```bash
python main.py
```

That's it. Telegram, Vertex AI, MCP servers, and extra providers are all optional, added only when you want them.

---

## Getting started

```bash
python main.py                      # start a conversation
python main.py --resume             # continue your last session
python main.py --session my-project # open (or create) a named session
python main.py --list-sessions      # list saved sessions
python server.py                    # optional web dashboard → http://localhost:8000
```

### What you'll see on launch

A banner and a **compact status line** — not a wall of text:

```text
Manager: anthropic/claude-sonnet-4.6 — plans and coordinates all requests

  Agents   6/6 loaded       /agents to view
  Tools    20/20 ready      /tools to view
  MCP      2/2 connected    /mcps to view
```

Then just talk to it:

```text
You: find the latest Node.js LTS and write it to version.txt
You: refactor src/api.py and run the tests
You: open notepad and type today's AI headlines
```

---

## Command reference

| Command | What it does |
|---|---|
| `/agents` · `/tools` · `/mcps` | Show loaded agents / tools / MCP servers |
| `/skills` | List saved skills with usage counts |
| `/profile` | Show the user profile (`USER.md`) |
| `/sessions` · `/session <name>` · `/new` | List / switch / start sessions |
| `/forget` · `/compress` | Trim or summarise old context |
| `/newtool <path>` | **ToolSmith** — scaffold + validate a new ExTool |
| `/addmcp <path>` | **MCPSmith** — draft + connect-test an MCP server |
| `/mcptest <name>` | Connect-test an MCP server |
| `/clear` | Clear the screen and redraw the startup view |
| `/help` | Show the command list |
| `ESC` | Stop the running task, return to the prompt |
| `exit` / `quit` | End the session (updates `USER.md`) |

---

## Agents & tools

| Agent | Does | Key tools |
|---|---|---|
| **research_agent** | Web research, news, image downloads | `web_search`, `web_fetch`, `download_file` |
| **code_agent** | Write, edit, refactor, test, debug | `edit_tool`, `shell_tool`, `grep_tool`, `lsp_tool`, `diagnose_tool` + codegraph MCP |
| **file_agent** | File ops on non-code files | `file_tool`, `conflict_tool` |
| **computer_agent** | Windows desktop automation | `open_app` *(Windows only)* |
| **checker_agent** | Validates other agents' results | `checker` |

20+ built-in tools span files & code, web, desktop, and coordination (`todo_tool`, `memory_tool`, `spawn_tool`, `ask_user`). Run `/tools` to see them all. MCP servers (`codegraph`, `playwright`) extend the surface with no code changes.

Each agent defaults to `gemini-2.5-flash` via OpenRouter unless overridden.

---

## Configuration

Set the manager model and per-agent overrides in `anet.config.yaml`:

```yaml
manager:
  model: anthropic/claude-sonnet-4.6
  provider: openrouter

agents:
  code_agent:
    model: claude-opus-4-7
    provider: anthropic
    max_steps: 80
    mcp: [codegraph]
  research_agent:
    model: google/gemini-2.5-flash
    provider: openrouter
```

| Provider key | Auth | Notes |
|---|---|---|
| `openrouter` | `OPENROUTER_API_KEY` | 300+ models, free tier |
| `google` | `GOOGLE_API_KEY` | Gemini direct |
| `openai` | `OPENAI_API_KEY` | GPT models |
| `anthropic` | `ANTHROPIC_API_KEY` | Claude *(legacy alias: `claude`)* |
| `vertex_google` / `vertex_anthropic` | `VERTEX_PROJECT_ID` + ADC | Gemini / Claude on Vertex AI |

Add keys to `.env`. For Vertex AI, run `gcloud auth application-default login` once and set `VERTEX_PROJECT_ID`.

---

## Extend it

Add your own tools, agents, MCP servers, and skills **without touching the core `anet/` package**. Each folder has a focused how-to guide covering both the assisted (smith) path and the manual path:

| Guide | Add via smith | Add manually |
|---|---|---|
| 🔧 **[ExTools](ExTools/README.md)** — custom tools | `/newtool <path>` | `ExTools/<name>/__init__.py` + register in `exanet.config.yaml` |
| 🤖 **[ExAgents](ExAgents/README.md)** — custom agents | — | inline block under `agents:` in `exanet.config.yaml` |
| 🔌 **[mcps](mcps/README.md)** — MCP servers | `/addmcp <path>` | `mcps/<name>/config.yaml` + wire to an agent |
| 🧠 **[skills](skills/README.md)** — learned procedures | written automatically | drop a `skills/<name>.md` file |

---

## Documentation

| Doc | What's inside |
|---|---|
| 🏗️ **[Architecture](architecture/README.md)** | Request lifecycle, component diagram, memory loop, sessions, project layout |
| 🔧 **[ExTools guide](ExTools/README.md)** | Tool contract, `/newtool`, registration, credentials |
| 🤖 **[ExAgents guide](ExAgents/README.md)** | Agent fields, prompts, routing, worked example |
| 🔌 **[mcps guide](mcps/README.md)** | Launch config, `/addmcp`, `/mcptest`, constraints |
| 🧠 **[skills guide](skills/README.md)** | How skills are created, injected, curated, and hand-authored |

---

## Requirements

- **Python 3.11+**
- **Node.js** — only for MCP servers (codegraph, playwright)
- `pip install -r requirements.txt`
- **Windows only** for `computer_agent`: `pip install pyautogui pywinauto Pillow`
- **Vertex AI** providers: `pip install google-auth` + `gcloud auth application-default login`

---

## Contributing

Issues and PRs welcome. The engine is pure Python with no framework dependency — start with [`architecture/README.md`](architecture/README.md) to find your way around, then the relevant `anet/core/` module.

---

<p align="center"><sub>MIT licensed · pure-Python engine · no LangChain, no lock-in.</sub></p>
