# ANet — Architecture

Internals and design of ANet. For installation and day-to-day usage, see the
[main README](../README.md). For extending ANet, see the per-folder guides:
[ExTools](../ExTools/README.md) · [ExAgents](../ExAgents/README.md) ·
[mcps](../mcps/README.md) · [skills](../skills/README.md).

---

## Request lifecycle

ANet routes every request through a **planning layer** (the "manager") that
decides which agents run, in what order, and which can run in parallel. Each
agent has its own model, its own tools, and its own job.

```mermaid
flowchart TD
    U(["You — natural-language request"]) --> M{"Manager · Planner"}
    M -->|"greeting / simple fact"| R(["Direct reply"])
    M -->|"real task"| P["Plan a DAG of steps"]
    P --> A1["research_agent"]
    P --> A2["code_agent"]
    P --> A3["computer_agent"]
    A1 --> C{"checker_agent"}
    A2 --> C
    A3 --> C
    C -->|"partial / failed"| P
    C -->|"success"| S["Synthesizer"]
    S --> O(["Final reply"])
    A2 -. spawn_tool .-> A1
```

- **Planner** — classifies the request as `simple` (direct reply) or a `plan`
  (a DAG of steps). Steps declare `depends_on` and `wait_for_async`.
- **Executor** — runs all *ready* steps concurrently (`asyncio.gather`);
  dependent steps wait for their predecessors.
- **Checker** — classifies each step's result as **success / partial / failure**
  and can return an *adjustment* that triggers a bounded retry.
- **Synthesizer** — streams the final answer from the combined step results.

---

## Component architecture

```mermaid
flowchart LR
    subgraph CLI["CLI · main.py"]
        IN["Prompt + slash commands"]
    end
    subgraph ENG["Engine · engine.py"]
        PL["Planner"] --> EX["Executor"] --> CH["Checker"] --> SY["Synthesizer"]
    end
    subgraph RUN["Orchestrator + agent_runner"]
        LOOP["Agentic loop · cycle detection"]
    end

    IN --> PL
    SY --> IN
    EX --> LOOP
    LOOP --> TOOLS[("Built-in tools<br/>+ ExTools")]
    LOOP --> MCP[("MCP servers<br/>codegraph · playwright")]
    ENG --> STORE[("conversations.db<br/>shared, keyed by thread")]
    RUN --> MEM[("USER.md<br/>+ memory_tool")]
    RUN --> SK[("skills/<br/>learned procedures")]
```

| Module | Responsibility |
|---|---|
| `anet/core/engine.py` | Planner → executor → checker → synthesizer pipeline (pure Python, no LangChain) |
| `anet/core/orchestrator.py` | The agentic loop for one agent: model ↔ tool-call iterations, cycle detection, confirmation gate, skill tracking |
| `anet/core/agent_runner.py` | One model call; provider dispatch (OpenAI-compatible, Anthropic, Vertex) |
| `anet/core/store.py` | `aiosqlite` conversation store — one shared DB keyed by `thread` |
| `anet/core/memory_agent.py` | Background memory — updates `USER.md` and `memory_tool` |
| `anet/core/skill_manager.py` | Self-improving skills — search, create, curate |
| `anet/core/mcp_loader.py` | MCP server lifecycle (launch, list tools, keep alive) |
| `anet/core/ex_loader.py` | Load ExTools/ExAgents from `exanet.config.yaml` |
| `anet/cli/banner.py` | Animated startup banner + README image export |

---

## Safety mechanisms

| Guard | Behaviour |
|---|---|
| **Confirmation gate** | `shell_tool` (every command), `edit_tool` (every edit), and destructive `file_tool` actions pause for explicit `y` / `n` / `a` approval |
| **Per-agent step cap** | each agent has a `max_steps` limit (defaults: research 10, code 60, file 25, computer 20, checker 8) |
| **Cycle detection** | the same write operation repeated 3× in a sliding window stops the loop (reads are exempt) |
| **Spawn depth limit** | `spawn_tool` nesting is capped at 2 to prevent runaway delegation |

---

## Memory & learning loop

```mermaid
flowchart TD
    T["Conversation turns"] -->|"every 5 turns"| BG["Background memory agent"]
    T -->|"every 10 turns"| NUDGE["Memory nudge to active agent"]
    EXIT(["Clean exit"]) --> FINAL["Final USER.md pass"]
    BG --> U[("memory/USER.md")]
    NUDGE --> M[("memory_tool facts")]
    FINAL --> U
    U -->|"injected next session"| PLAN["Planner already knows you"]
```

- **User profile (`USER.md`)** — a background agent updates it every
  `incremental_interval` turns (default 5); a final pass runs on clean exit. It's
  injected into the planner next session.
- **Memory nudge** — every `nudge_interval` turns (default 10), the active agent
  is prompted to persist genuinely new facts to `memory_tool`.
- **Context compression** — past ~40 messages, ANet offers **[f] forget**
  (keep last 20) or **[c] compress** (summarise). Also `/forget`, `/compress`.
- **Self-improving skills** — see [skills/README.md](../skills/README.md).

---

## Sessions & persistence

All sessions share a single `conversations.db`, keyed by a `thread` column. This
makes `/session <name>` switching instant and lossless — switching is a string
change, not a database reconnect. Each session also keeps a small folder for
metadata (e.g. `title.txt`).

```text
<anet-home>/                 # e.g. ~/.anet  (or ANET_HOME)
├── USER.md                  # auto-built user profile
└── sessions/
    ├── conversations.db     # one shared store for ALL sessions, keyed by thread
    └── <session_id>/        # per-session folder — metadata only (title.txt)
```

> Legacy per-session `checkpoint.db` files (from older versions) are folded into
> the shared store automatically on first run.

---

## The Smiths — assisted integration

ANet ships two standalone agents that scaffold and **validate** integrations for
you, then print the config to paste (they never edit your config files).

```mermaid
flowchart LR
    P["/newtool &lt;path&gt;"] --> E["Explore the source"]
    E --> Q["Confirm name + capability with you"]
    Q --> W["Write ExTools/&lt;name&gt;/__init__.py"]
    W --> V{"extool_validator"}
    V -->|"fix &amp; retry"| W
    V -->|"PASS"| OUT["Print the registration stanza to paste"]
```

- **ToolSmith** (`/newtool <path>`) → validates with `python -m anet.core.extool_validator`.
- **MCPSmith** (`/addmcp <path>`) → verifies with `python -m anet.core.mcp_doctor <name>`.

Details: [ExTools](../ExTools/README.md) and [mcps](../mcps/README.md).

---

## Full project layout

```text
Anet/
├── main.py                  # CLI entry point
├── server.py                # Web dashboard
├── anet.config.yaml         # Models, persona, memory, skills, per-agent overrides
├── exanet.config.yaml       # External tools + agents
├── SOUL.md                  # Manager persona
│
├── anet/
│   ├── AnetAgents/          # Built-in agent definitions
│   ├── AnetTools/           # Built-in tool implementations
│   ├── cli/banner.py        # Animated startup banner + README image export
│   └── core/                # engine, orchestrator, agent_runner, store, memory, skills, loaders
│
├── architecture/            # ← you are here
├── mcps/                    # MCP servers (codegraph, playwright)
├── skills/                  # Auto-written procedures (grows over time)
├── ExAgents/  ExTools/      # Your custom agents and tools
└── memory/ → <anet-home>    # USER.md + sessions/ (see above)
```
