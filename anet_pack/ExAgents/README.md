# ExAgents — Bring Your Own Agents

`ExAgents/` is where you add custom agents to ANet **without touching the core
`anet/` package**. An ExAgent is mostly declarative: you describe it in
`exanet.config.yaml` (model, the tasks it handles, the tools it gets) and drop a
prompt file beside it. No Python required unless the agent needs a custom tool
(those live in `../ExTools/`).

> **Nothing here is active by default.** A fresh clone registers zero ExAgents so
> the first run is clean. One complete example ships in `ExAgents/tele_agent/` —
> read it, then enable it to see the flow.

---

## Don't want to write the YAML by hand? Use `/newagent`

ANet ships an **agent designer**. Describe the agent you want, in the CLI:

```
/newagent an agent that summarises PDFs and emails me the result
```

A standalone `agentsmith` agent decides the name and `task_types`, then shows you
the **available tools and MCP servers** and asks which to give the agent (pick as
many as you like). It writes the prompt to `ExAgents/<name>/prompt.md` and
registers the agent — all through a safe `registrar` tool that edits
**`exanet.config.yaml` only** (never `anet.config.yaml` or the core `anet/`
package). The agent is live on your next message (`exanet.config.yaml` hot-reloads
between turns); confirm with `/agents`.

The rest of this doc explains the schema `/newagent` generates — useful for
reviewing or tweaking what it produces, or building one entirely by hand.

---

## How an agent gets used

ANet's **planner** routes each user request to an agent by matching the request
against that agent's `task_types`. So `task_types` is the most important field —
it's how the planner "discovers" your agent. `ask_user` is added to every agent
automatically; to let an agent delegate a sub-task to another agent at runtime,
add `spawn_tool` to its `tools:` list explicitly.

## Define an agent

In `exanet.config.yaml`, under `agents:`:

```yaml
agents:
  - name: my_agent
    model: openai/gpt-oss-20b:free      # any model your provider supports
    provider: openrouter                # google | openrouter | openai | anthropic | vertex_*
    enabled: true                       # false (or omit block) = dormant
    prompt_file: ExAgents/my_agent/prompt.md   # or use system_prompt: "..."
    task_types:                         # phrases the planner matches against
      - summarize a document
      - condense long text
    tools:                              # built-in ANet tools OR ExTools names
      - file_tool
    mcp: []                             # optional: MCP servers from mcps/<name>/
```

Field reference:

| Field | Required | Notes |
|---|---|---|
| `name` | ✅ | Unique agent id. |
| `model` / `provider` | ✅ | Defaults to `gemini-2.5-flash` / `google` if omitted. |
| `task_types` | ✅ (in practice) | Drives planner routing. List the kinds of requests it handles. |
| `prompt_file` *or* `system_prompt` | ✅ | File path (relative to repo root) or inline string. |
| `tools` | optional | Built-in tool names or ExTools registered in this file. |
| `mcp` | optional | MCP server names from `mcps/<name>/config.yaml`. |
| `enabled` | optional | Defaults to `true`. Set `false` to keep it dormant. |

## Folder layout

```
ExAgents/my_agent/
├── prompt.md      # the system prompt (referenced by prompt_file)
└── .env           # optional, per-agent secrets — auto-loaded, gitignored
```

## Write a good prompt

The prompt defines the agent's job and boundaries. Keep it tight:
- State the single job in the first line.
- List the exact tool-call patterns it should use.
- Tell it to **act, not narrate** ("just call the tool and return the result").
- Spell out hard rules (absolute paths only, never guess, when to stop).

See `ExAgents/tele_agent/prompt.md` for a compact, working example.

## Credentials

Per-agent secrets go in `ExAgents/<agent>/.env` (auto-loaded at startup; any
`*.env` is gitignored). Read them in your tool code via `os.getenv(...)`.
**Never commit real tokens** — if you fork/publish, double-check your `.env`
files are ignored.

## Worked example — `tele_agent`

`ExAgents/tele_agent/` is a complete, enable-it-and-go agent that sends Telegram
messages/files via the `tele_tool` ExTool. It demonstrates everything: a focused
`prompt.md`, `task_types` for planner routing, a `tools:` binding to an ExTool,
and per-agent credentials in `.env`.

To try it live:
1. `ExAgents/tele_agent/.env` → set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
   (create a bot with @BotFather; get your chat id from @userinfobot).
2. In `exanet.config.yaml`, uncomment **both** the `tele_agent` block and the
   `tele_tool` entry (the agent needs its tool registered too).
3. Restart, then ask: *"send me a Telegram saying the build passed"* — the
   planner routes it to `tele_agent` via `task_types`.

Verify it loaded with `/agents`.

## Checklist

- [ ] Block added under `agents:` in `exanet.config.yaml`
- [ ] `task_types` describe the requests it should catch
- [ ] `prompt_file` exists (or `system_prompt` is set)
- [ ] Every name in `tools:` is a built-in tool or a registered ExTool
- [ ] Secrets in `ExAgents/<agent>/.env`, not in the prompt or code
- [ ] Shows up in `/agents` after restart
