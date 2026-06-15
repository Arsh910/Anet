# Test: MCP doctor + `/addmcp` (mcp_doctor + mcpsmith)

A step-by-step runbook to confirm the MCP helpers work. Part A is the reliable,
near-offline test (uses the bundled `codegraph` server). Part B exercises the full
drafting agent and needs Node + network.

**Prereqs (both parts):**
```
pip install mcp        # the MCP client library ANet uses
```
Node.js must be installed and `node`/`npx` on PATH.

---

## Part A — `/mcptest` doctor (uses bundled codegraph)

This proves `mcp_doctor` connects over stdio and lists a server's tools — the
exact path ANet uses at runtime.

**Step A1 — standalone CLI (no ANet session needed).** From the repo root:
```
python -m anet.core.mcp_doctor codegraph
```
✅ Expect, ending in PASS (exit 0):
```
  OK: 'mcp' package importable
  OK: config.yaml found (command='node', 3 arg(s))
  OK: 'node' resolved on PATH
  OK: connected. 8 tool(s): codegraph_search, codegraph_callers, ...
PASS: MCP 'codegraph' connects...
```

**Step A2 — inside ANet.** Start `python main.py`, then:
```
/mcptest codegraph
```
✅ Expect the same OK lines rendered in color, ending in `PASS — 'codegraph' connects with 8 tool(s).`

**Step A3 — failure path.**
```
/mcptest doesnotexist
```
✅ Expect `FAIL: no readable config at mcps/doesnotexist/config.yaml` and `INVALID` (exit 1).

### Part A pass criteria
- [ ] A1 prints PASS with codegraph's tool list (exit 0).
- [ ] A2 shows the same inside ANet.
- [ ] A3 fails cleanly with INVALID.

> If A1 fails with **"'mcp' package not installed"** → run `pip install mcp`.
> If it fails with **"'node' not found on PATH"** → install Node.js.

---

## Part B — `/addmcp` drafting agent (needs Node + network)

Uses the sample MCP docs shipped at
`tests/AnetTests/samples/everything_mcp/README.md`.

**Step B1 — start ANet**
```
python main.py
```

**Step B2 — run the integrator**
```
/addmcp tests/AnetTests/samples/everything_mcp
```

**Step B3 — answer the confirmation**
The `mcpsmith` agent reads the README, finds the Claude Desktop block, and asks you
(via ask_user) to confirm the server name and launch command. Accept its proposal
(name `everything`, command `npx -y @modelcontextprotocol/server-everything`).

**Step B4 — approve the config write**
It writes `mcps/everything/config.yaml` → diff + `y/n/a` → press **y**.

**Step B5 — approve the verification run**
It runs `python -m anet.core.mcp_doctor everything` via the shell → `y/n/a` → **y**.
First run downloads the npx package, so allow some time. It fixes and retries on FAIL.

**Step B6 — read the final message**
✅ Expect a summary naming the discovered tools, plus a yaml block like:
```yaml
agents:
  code_agent:
    mcp: [everything]
```
and a reminder to **restart ANet**.

### Part B pass criteria
- [ ] `mcps/everything/config.yaml` exists with `command: npx` + the args.
- [ ] `python -m anet.core.mcp_doctor everything` prints **PASS** with a tool list.
- [ ] The final message printed the `anet.config.yaml` `mcp:` wiring.
- [ ] No config file was edited automatically (`anet.config.yaml` unchanged).

---

## Part C — Use it for real (optional)
Add `mcp: [everything]` under an agent in `anet.config.yaml`, **restart ANet**,
confirm it appears in the startup MCP panel, then ask the agent to use one of the
`everything` tools.

## Reset (to re-run cleanly)
```
rmdir /S /Q mcps\everything
```

## Troubleshooting
- **timeout / "server did not become ready"** → first npx fetch may be slow or
  blocked; re-run, or check network/proxy.
- **HTTP/SSE-only server** → ANet is stdio-only; the agent should say so and stop.
  Pick a server that documents a `command`/`args` launch.
- **package name changed** → substitute another stdio MCP (see the sample README's note).
