# ExTools — Bring Your Own Tools

`ExTools/` is where you add custom tools to ANet **without touching the core
`anet/` package**. A tool is just a folder with one Python file that exposes a
schema and a `run()` function. ANet loads it from `exanet.config.yaml` at startup.

> **Nothing here is active by default.** A fresh clone registers zero ExTools so
> the first run never breaks on a missing key. One complete example ships in
> `ExTools/tele_tool/` — read it, then register it to see the flow.

---

## Don't want to write the `__init__.py` by hand? Use `/newtool`

ANet ships a **tool generator**. Drop the source you want to wrap into a folder
(e.g. `ExTools/myzip/myzip_repo/`), then in the ANet CLI run:

```
/newtool ExTools/myzip/myzip_repo
```

A standalone `toolsmith` agent explores the code, confirms the tool name and the
capability to expose, writes `ExTools/myzip/__init__.py`, then **validates and
self-corrects it** until it passes:

```
python -m anet.core.extool_validator ExTools/myzip/__init__.py
```

Then it **finishes the integration for you**: it registers the tool in
`exanet.config.yaml`, shows you the available agents (built-in **and** your own),
and attaches the tool to the one(s) you pick — you can select several. It does
all of this through a safe `registrar` tool that edits **`exanet.config.yaml`
only**; it never touches `anet.config.yaml` or the core `anet/` package. The tool
is live on your next message (`exanet.config.yaml` hot-reloads between turns).

You can still run the validator yourself on any hand-written tool. The rest of
this doc explains the contract `/newtool` generates against — worth reading so you
can review or tweak what it produces.

---

## The contract

Create `ExTools/<your_tool>/__init__.py` exporting exactly two names:

```python
# ExTools/my_tool/__init__.py

SCHEMA = {
    "type": "function",
    "function": {
        "name": "my_tool",                       # must match the registered name
        "description": "What it does and when to use it. The model reads this.",
        "parameters": {                          # OpenAI function-calling JSON schema
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "..."},
            },
            "required": ["text"],
        },
    },
}

async def run(params: dict) -> dict:             # may be sync OR async
    text = params.get("text", "")
    if not text:
        return {"error": "text is required"}     # errors → {"error": "..."}
    return {"result": f"got: {text}"}            # success → {"result": ...} (any JSON)
```

Rules:
- **`run(params: dict) -> dict`** — sync or async, both work. Return a JSON-serializable dict.
- Use `{"result": ...}` on success and `{"error": "..."}` on failure. The agent sees this verbatim.
- Read secrets from environment variables (`os.getenv(...)`), never hard-code them. See [Credentials](#credentials).
- Keep it dependency-light, or add your deps to the project `requirements.txt`.

## Register it

In `exanet.config.yaml`, under `tools:`:

```yaml
tools:
  - name: my_tool            # must equal SCHEMA.function.name
    path: ExTools/my_tool    # folder path, relative to repo root
```

Restart ANet. The tool is now loadable.

## Make an agent actually use it

A registered tool does nothing until an agent has it. `/newtool` does this step
for you (it asks which agents to attach to). To wire it by hand, there are three
ways:

**A — bolt it onto a built-in agent** (your own edit, in `anet.config.yaml`):
```yaml
agents:
  code_agent:
    extra_tools: [my_tool]
```

**B — attach it to your own ExAgent** (in `exanet.config.yaml`, see `../ExAgents/README.md`):
```yaml
agents:
  - name: my_agent
    tools: [my_tool]
```

**C — attach it to a built-in agent from `exanet.config.yaml`** (the path the smiths
use — no `anet.config.yaml` edit needed):
```yaml
attach:
  code_agent:
    tools: [my_tool]
```

> Only the ToolSmith/MCPSmith may attach to **built-in** agents automatically; by
> hand you can use any of the above.

## Credentials

Tools never store secrets in code. Put them in a `.env` (any `*.env` is
gitignored) and read with `os.getenv`. For tools owned by an ExAgent, the
convention is a per-agent file at `ExAgents/<agent>/.env`, loaded automatically
at startup.

## Worked example — `tele_tool`

`ExTools/tele_tool/__init__.py` sends text or files to Telegram. It shows the
full pattern: a real `SCHEMA`, an `async run()`, env-var credentials
(`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`), input validation, and structured
`{"result"|"error"}` returns. Copy it as a starting template.

To try it live:
1. `ExAgents/tele_agent/.env` → add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
2. In `exanet.config.yaml`, uncomment the `tele_tool` entry **and** the
   `tele_agent` block.
3. Restart, then ask ANet: *"send me a Telegram saying hello"*.

## Checklist

- [ ] `ExTools/<name>/__init__.py` exports `SCHEMA` and `run`
- [ ] `SCHEMA.function.name` equals the registered `name`
- [ ] Registered under `tools:` in `exanet.config.yaml`
- [ ] Given to an agent via `extra_tools:` or an ExAgent's `tools:`
- [ ] Secrets come from env vars, not code
