<p align="center">
  <img src="ReadmeImages/anet-clean.png" alt="ANET" width="100%">
</p>

# ANET

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/multi--agent-orchestration-8B5CF6?style=for-the-badge" alt="Multi-agent">
  <img src="https://img.shields.io/badge/any_model-any_provider-FF6B35?style=for-the-badge" alt="Any model, any provider">
  <img src="https://img.shields.io/badge/MCP-compatible-22c55e?style=for-the-badge" alt="MCP compatible">
  <img src="https://img.shields.io/badge/License-MIT-blue?style=for-the-badge" alt="MIT">
</p>

**A local, multi-agent AI assistant that routes each request to the right specialist — on the model and provider _you_ choose.** Out of the box it researches the web, writes and runs code, manages files, and automates your Windows desktop — planned and coordinated automatically, with a confirmation gate before anything touches your shell or files. Anything beyond that (browsers, messaging, your own APIs) you add as tools, agents, and MCP servers — and bundle into shareable **packs**.

Use any model, per agent. Claude for code, Gemini for research, GPT for planning, a free model for the rest — set it in one YAML file via [OpenRouter](https://openrouter.ai), Google, OpenAI, Anthropic, or Vertex AI. No framework lock-in, no LangChain.

<table>
<tr><td><b>Multi-agent, multi-model</b></td><td>Five built-in agents (research, code, file, computer, checker) plus your own. Each picks its own model + provider in <code>anet.config.yaml</code>. The manager plans a DAG and runs independent steps in parallel.</td></tr>
<tr><td><b>Builds its own integrations</b></td><td>The <b>ToolSmith</b> (<code>/newtool</code>), <b>MCPSmith</b> (<code>/addmcp</code>), and <b>AgentSmith</b> (<code>/newagent</code>) scaffold a new tool, MCP server, or agent from your code/description, validate it, then wire it into the agents you pick — editing only <code>exanet.config.yaml</code>, never the core.</td></tr>
<tr><td><b>Shareable packs</b></td><td>Your whole setup — tools, agents, skills, MCP wiring, persona — is one folder. <b>PackSmith</b> (<code>/packsmith share</code>) bundles it into a zip (secrets stripped, README written); anyone runs <code>/packsmith add</code> + <code>/changepack</code> to get your exact setup. Switch between packs anytime.</td></tr>
<tr><td><b>Safe by default</b></td><td>Every shell command, file edit, and destructive file op pauses for explicit <code>y/n/a</code> approval. Per-agent step caps and cycle detection stop runaways.</td></tr>
<tr><td><b>Remembers you</b></td><td>Long-term memory via <a href="https://github.com/mem0ai/mem0">mem0</a> — a local vector store (Chroma) with on-device embeddings (fastembed); your LLM extracts and de-duplicates the salient facts as you work, so it knows your stack and preferences next time. No server, no hosted service. See <code>/profile</code>.</td></tr>
<tr><td><b>Learns from experience</b></td><td>After a complex, self-corrected task it writes a reusable <b>skill</b>; relevant skills are injected into future tasks, and a Curator improves them over time.</td></tr>
<tr><td><b>Real developer tooling</b></td><td>Built in: LSP code intelligence (go-to-def, rename, references), ripgrep search, ruff/pyright/eslint diagnostics, git conflict resolution. Plug in any MCP server for more (e.g. a code-graph or browser server).</td></tr>
<tr><td><b>A real terminal UI</b></td><td>Animated banner, slash-command autocomplete, live status spinner, session switching, <code>Ctrl+O</code> to watch/answer a running shell command, and <code>ESC</code> to interrupt.</td></tr>
</table>

---

## Install

> **One API key is all you need to start.** Free models are on OpenRouter, and web search uses DuckDuckGo — no paid search key.

**Recommended — global `anet` command via [pipx](https://pipx.pypa.io):**

```bash
pipx install https://github.com/Arsh910/Anet/releases/latest/download/anet-0.0.1-py3-none-any.whl
```

<sub>(or install straight from a tag without a release upload: `pipx install git+https://github.com/Arsh910/Anet.git@v0.0.1` — `pip install …` works too if you prefer.)</sub>

```bash
anet
```

On first run ANet creates your workspace at `~/.anet/` (config + a starter pack + a `.env`) and starts chatting. To add your API key, just run **`/keys`** in the prompt — it opens `~/.anet/.env` in your editor; fill in the provider you use (e.g. `OPENROUTER_API_KEY`) and save. (You can also set the keys as normal environment variables.)

**Node.js** is optional — only needed if you add MCP servers later. Telegram, Vertex AI, and extra providers are all opt-in.

---

## Usage

```bash
anet                      # start a conversation
anet --resume             # continue your last session
anet --session my-project # open (or create) a named session
anet --list-sessions      # list saved sessions
```

---

## Run from source (contributors)

```bash
git clone https://github.com/Arsh910/Anet.git
cd Anet
pip install -e .          # editable install (or: pip install -r requirements.txt)
python main.py            # same CLI; uses the repo's anet_pack/ as the live workspace
```

`python main.py` is a thin shim over `anet/cli/app.py`. From a checkout, your workspace is the repo's `anet_pack/` (edit a tool and test instantly); when installed, it's `~/.anet/anet_pack/`.

> **⚠️ Work in progress:** the web dashboard (`anet-server`) and the web UI (`anet-ui`) are **not functional yet** — they're under active development. The CLI is the supported way to use ANet today.

### What you'll see on launch

A banner and a **compact status line** — not a wall of text:

```text
Manager: nex-agi/nex-n2-pro:free — plans and coordinates all requests

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
| `/settings` | Edit config — pick what to open: keys · models/providers · tools/agents · an agent's prompt · persona |
| `/keys` | Shortcut straight to your API keys (same as `/settings` → 1) |
| `/skills` | List saved skills with usage counts |
| `/profile` | Show what Anet remembers about you (long-term memory) |
| `/sessions` · `/sessions <number>` · `/new` | List sessions · switch to one by number · start fresh |
| `/forget` · `/compress` | Trim or summarise old context |
| `/newtool <path>` | **ToolSmith** — scaffold, validate + register a new ExTool |
| `/newagent <description>` | **AgentSmith** — design + register a new agent |
| `/addmcp <path>` | **MCPSmith** — draft, connect-test + register an MCP server |
| `/mcptest <name>` | Connect-test an MCP server |
| `/packsmith new <name>` | **PackSmith** — create a blank pack and switch to it |
| `/packsmith share <path?>` · `/packsmith add <zip>` | **PackSmith** — bundle your setup to share, or install someone's |
| `/changepack <name?>` | Switch the active pack (your workspace) |
| `/clear` | Clear the screen and redraw the startup view |
| `/help` | Show the command list |
| `Ctrl+O` | View the running shell command live — and type input to answer any prompt it raises |
| `ESC` | Stop the running task, return to the prompt |
| `exit` / `quit` | End the session (runs a final memory pass) |

---

## Agents & tools

| Agent | Does | Key tools |
|---|---|---|
| **research_agent** | Web research, news, image downloads | `web_search`, `web_fetch`, `download_file` |
| **code_agent** | Write, edit, refactor, test, debug | `edit_tool`, `shell_tool`, `grep_tool`, `lsp_tool`, `diagnose_tool` |
| **file_agent** | File ops on non-code files | `file_tool`, `conflict_tool` |
| **computer_agent** | Windows desktop automation | `open_app` *(Windows only)* |
| **checker_agent** | Validates other agents' results | `checker` |

These five agents and ~20 built-in tools (files & code, web, desktop, coordination — `todo_tool`, `memory_tool`, `spawn_tool`, `ask_user`, …) are **core** — always present. Run `/tools` to see them all.

Everything else is per-**pack**: your own tools/agents and any MCP servers you wire in (no core changes). The default pack ships small examples (a `wordcount` tool, a Telegram agent, a playwright MCP config) to learn from — see [Packs](#-packs--share-your-whole-setup).

Each agent defaults to a model set in `anet.config.yaml` unless overridden per agent.

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
    mcp: [my_mcp_server]      # any MCP server you've added to the pack
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
| 🔧 **[ExTools](anet_pack/ExTools/README.md)** — custom tools | `/newtool <path>` | `ExTools/<name>/__init__.py` + register in `exanet.config.yaml` |
| 🤖 **[ExAgents](anet_pack/ExAgents/README.md)** — custom agents | `/newagent <description>` | inline block under `agents:` in `exanet.config.yaml` |
| 🔌 **[mcps](anet_pack/mcps/README.md)** — MCP servers | `/addmcp <path>` | `mcps/<name>/config.yaml` + wire to an agent |
| 🧠 **[skills](anet_pack/skills/README.md)** — learned procedures | written automatically | drop a `skills/<name>.md` file |

> The smiths now **finish the integration for you**: after creating and validating, they show you the available agents (built-in + your own) and attach the new tool/MCP to the ones you pick (multi-select). They only ever write `exanet.config.yaml` — never `anet.config.yaml` or the core `anet/` package.

---

## 📦 Packs — share your whole setup

A **pack** is your entire ANet workspace as one self-contained folder: your config, your custom tools and agents, your MCP wiring, your learned skills, and your persona. It lives at `~/.anet/anet_pack/` (separate from the read-only engine), so it's trivially portable.

That makes a hard-won setup **shareable**. Spend a week building a great "DevOps" or "research analyst" workspace? Hand it to a teammate — or the community — and they get your *exact* capabilities in one step.

**The full lifecycle:**

```text
/packsmith new   →   build with the smiths   →   /packsmith share   →   /packsmith add   →   /changepack
   create a            /newtool /newagent          zip it (secrets         a friend installs     activate it
   blank pack          /addmcp                      stripped, README)        your pack             anywhere
```

Packs live under your Anet home, never in the engine:

| Location | Holds | Created by |
|---|---|---|
| `~/.anet/anet_pack/` | the **default** pack | ships with ANet |
| `~/.anet/yourpacks/<name>/` | packs **you author** | `/packsmith new` |
| `~/.anet/shared_packs/<name>/` | packs **you received** | `/packsmith add` |

`/changepack` switches the active one (the workspace ANet reads); everything below walks each step.

### Start a new pack

```text
/packsmith new devops          → creates a blank pack and switches to it
/newtool ./scripts/deploy.py   → add a tool with ToolSmith
/addmcp ../k8s-mcp             → add an MCP server with MCPSmith
/newagent an agent that triages alerts and pings Slack   → add an agent
```

`/packsmith new <name>` scaffolds a fresh pack in `~/.anet/yourpacks/<name>/` (base config + empty `ExTools`/`ExAgents`/`mcps`/`skills`) and switches you to it. From there you build it up with the smiths — a clean base for authoring something you'll share. `/changepack` flips between all your packs (default, yours, and ones you've installed).

```
~/.anet/anet_pack/          ← a pack is just this folder
├── anet.config.yaml        models / providers
├── exanet.config.yaml      which tools & agents are wired up
├── ExTools/   ExAgents/     your custom tools and agents (real code)
├── mcps/                    MCP server configs
├── skills/                  learned procedures
└── SOUL.md                  persona
```

### Share one

```text
You:  /packsmith share
Anet: inspects the pack, writes a README, strips every secret,
      → ~/.anet/anet_files/anet_pack.zip
```

`PackSmith` bundles the folder into a zip — **all `.env` secrets removed**, a step-by-step README generated from what's inside (which tools/agents it has, which API keys the recipient must supply, any prerequisites).

### Install one

```text
Friend: /packsmith add ./devops-pack.zip
Anet:   extracts it to ~/.anet/shared_packs/devops-pack,
        shows what's inside, asks for the API keys it needs,
        runs only the setup its README documents (with your approval)
Friend: /changepack devops-pack      ← now using your exact setup
```

### Switch between packs

```text
/changepack            → lists: anet_pack (default), devops-pack, research-pack …
/changepack research-pack
```

Keep several packs and flip between them — your own default, a teammate's, a community one. Great for testing, too: try a shared pack, then `/changepack` back.

### Why it's safe

- **Secrets never travel.** Export strips every `.env`; the README lists which keys *you* supply on your machine.
- **Nothing auto-runs.** Installing only unpacks and (with your `y/n/a` approval) runs the setup steps the README documents — it never executes the pack's tools.
- **It's a trust decision, like a VS Code extension.** A pack contains real, runnable code; PackSmith shows you what's inside before you activate it.

> **Example pack idea:** a "Frontend" pack = a Lighthouse-audit tool + a component-scaffolding agent + a Playwright MCP + skills for your team's conventions. Share it, and every teammate's ANet can audit and scaffold the same way on day one.

---

## Documentation

| Doc | What's inside |
|---|---|
| 🏗️ **[Architecture](architecture/README.md)** | Request lifecycle, component diagram, memory loop, sessions, project layout |
| 🔧 **[ExTools guide](anet_pack/ExTools/README.md)** | Tool contract, `/newtool`, registration, credentials |
| 🤖 **[ExAgents guide](anet_pack/ExAgents/README.md)** | Agent fields, prompts, routing, worked example |
| 🔌 **[mcps guide](anet_pack/mcps/README.md)** | Launch config, `/addmcp`, `/mcptest`, constraints |
| 🧠 **[skills guide](anet_pack/skills/README.md)** | How skills are created, injected, curated, and hand-authored |

---

## Requirements

- **Python 3.11+** (that's it for the base install — `pipx install …` pulls every dependency).
- **Node.js** — optional, only if you add MCP servers (e.g. playwright via `npx`).
- **Windows** is required only for `computer_agent` (desktop automation); its deps install automatically there and are skipped elsewhere.
- **Web dashboard & UI** (`anet-server`, `anet-ui`): **under development — not working yet.** Use the CLI for now.
- **Vertex AI** providers: run `gcloud auth application-default login` once and set `VERTEX_PROJECT_ID`.

---

## Contributing

Issues and PRs welcome. The engine has no heavy framework dependency (no LangChain) — start with [`architecture/README.md`](architecture/README.md) to find your way around, then the relevant `anet/core/` module.

---

<p align="center"><sub>MIT licensed · multi-agent · any model · no LangChain, no lock-in.</sub></p> 
