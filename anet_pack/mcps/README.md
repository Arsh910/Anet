# mcps — Add MCP Servers to ANet

`mcps/` is where you plug in [Model Context Protocol](https://modelcontextprotocol.io)
servers. Unlike ExTools, **there's no code and no schema to write** — ANet launches
the server and **auto-discovers its tools** at runtime (`list_tools`). Your entire
job is a small launch config plus one line wiring it to an agent.

> Two helpers do the work for you: **`/addmcp`** drafts the config from a server's
> docs and verifies it; **`/mcptest`** connect-tests a config you already have.

---

## The two things you author

**1. `mcps/<name>/config.yaml`** — how to launch the server (stdio):

```yaml
command: npx                 # must be on PATH: node/npx, uvx, python, or an absolute path
args:
  - -y
  - "@scope/some-mcp-server"
env:                          # optional — only if the server needs it
  SOME_API_KEY: "${SOME_API_KEY}"   # reference an env var; never hardcode a real key
```

**2. attach it to an agent** in `anet.config.yaml`, then **restart ANet**:

```yaml
agents:
  code_agent:
    mcp: [some_mcp]
```

That's it. On boot the server starts once, stays alive for the session, and its
tools are injected into every agent that declares it.

## Where a server writes its data

By default each server is launched with its **working directory set to
`<anet-home>/mcp/<name>/`** (e.g. `~/.anet/mcp/playwright/`). So any folders a
server drops in its cwd (playwright's `.playwright-mcp` logs/screenshots, etc.)
land there instead of cluttering the repo root.

Override per server with a `cwd:` key in `config.yaml`:

```yaml
cwd: "."          # run in the repo root (relative paths resolve against it)
# cwd: /abs/path  # or an absolute directory
```

> **codegraph is special:** it stores its graph *inside the indexed project root*
> (`.code-review-graph/`, located by walking parent dirs — like `.git`), so its
> config pins `cwd: "."`. That folder is git-ignored rather than relocated; moving
> it would break codegraph's project lookup.

## Important constraints

- **stdio transport only.** ANet speaks MCP over a subprocess's stdin/stdout.
  Remote **HTTP/SSE-only** servers are not supported — `/mcptest` will tell you.
- **`command` must resolve on PATH** (or be absolute). Node servers usually need
  Node.js; Python servers usually use `uvx`/`pip`. Install prerequisites first.
- **`pip install mcp`** is required (the MCP client library).
- `anet.config.yaml` is **not hot-reloaded** — restart ANet after adding the
  `mcp:` line. (Registering the `config.yaml` itself needs no restart to *test*.)

## Translating a server's README

Most MCP docs give a **Claude Desktop** block:

```json
"mcpServers": { "foo": { "command": "npx", "args": ["-y","foo-mcp"], "env": {"K":"v"} } }
```

Copy the inner `command` / `args` / `env` straight into `mcps/foo/config.yaml`.
`/addmcp` does this translation (and the verification) for you.

---

## `/addmcp <path>` — draft + verify from docs

Point it at a local MCP repo or README:

```
/addmcp ../some-mcp-server
```

A standalone `mcpsmith` agent reads the docs, confirms the server name + launch
command with you, writes `mcps/<name>/config.yaml`, then runs the doctor until it
connects. It then **attaches the server for you**: it shows the available agents
(built-in **and** your own) and wires the server into the one(s) you pick (you can
select several) via a safe `registrar` tool that edits **`exanet.config.yaml`
only** — never `anet.config.yaml` or the core `anet/` package. Active on your next
message (`exanet.config.yaml` hot-reloads between turns).

## `/mcptest <name>` — connect-test an existing config

```
/mcptest codegraph
```

Runs the exact path ANet uses at runtime and reports pass/fail with the discovered
tool list. Also available standalone:

```
python -m anet.core.mcp_doctor codegraph
```

Example output:
```
  OK: 'mcp' package importable
  OK: config.yaml found (command='node', 3 arg(s))
  OK: 'node' resolved on PATH
  OK: connected. 8 tool(s): codegraph_search, codegraph_callers, ...
  PASS
```

---

## Making an MCP shareable (authoring rules)

When you `/packsmith share` a pack, PackSmith writes a recipient README and produces
a sanitized zip. For that to work for **your** MCP, follow two rules:

**1. Prefer a package-based launch.** If the server is published (npm/PyPI), launch
it with `npx`/`uvx` so it auto-fetches on the recipient's machine — nothing to ship:

```yaml
command: npx
args: [-y, codegraph, serve, --mcp]
```

PackSmith sees this is `package_based` and the recipient just needs the runtime
(Node/Python). This is the smoothest path — use it whenever the server is published.

**2. If the server runs from a cloned repo, add `mcps/<name>/README.md`.** A server
launched from local code (an absolute path to `dist/bin/server.js`, a vendored clone,
etc.) **cannot be shipped** — on export PackSmith **strips that code automatically**
(it detects the project manifest like `package.json`/`pyproject.toml`) and ships only
your `config.yaml` + this README. So the README must tell the recipient how to obtain
and build it. Use these headings:

```markdown
# <name> MCP

Source: repo                 (one of: package | repo | local)
Repo: https://github.com/owner/<name>
Install: npm install -g <name>      (or: git clone … && npm install && npm run build)
Entry: <name>                       (what config.yaml's command/args should point at)
Env: SOME_API_KEY                   (any keys it needs; omit if none)

One short paragraph: what this server does.
```

PackSmith reads this to write accurate clone/build/point-the-config steps. Because
it's prose, a rough README is still usable — the recipient's PackSmith (and their
own judgment) can fill gaps. **Without it**, PackSmith can only guess from the config
and will flag that the docs are missing.

> `/addmcp` writes a starter `README.md` for you from the server's docs — review and
> tighten the Source/Repo/Install/Entry lines before sharing.

## Checklist

- [ ] `mcps/<name>/config.yaml` has a valid `command` (+ `args`, `env`)
- [ ] prerequisites installed (`pip install mcp`, plus node/uvx/etc. on PATH)
- [ ] `/mcptest <name>` prints PASS and lists tools
- [ ] `mcp: [<name>]` added to an agent in `anet.config.yaml`
- [ ] ANet restarted so the agent picks up the server
- [ ] **to share:** package-based launch, or a `README.md` with Source/Repo/Install/Entry
