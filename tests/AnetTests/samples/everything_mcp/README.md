# Everything MCP — sample subject for `/addmcp`

This is a stand-in for a real MCP server's README, used by
`tests/AnetTests/mcp_doctor_test.md`. It contains the kind of **Claude Desktop**
config block that most MCP servers document — exactly what the `mcpsmith` agent
translates into an ANet `mcps/<name>/config.yaml`.

`@modelcontextprotocol/server-everything` is a public reference MCP server (stdio)
made for testing. It launches via `npx`, so it needs Node.js and, on first run,
network access to fetch the package.

## Installation (Claude Desktop format)

```json
{
  "mcpServers": {
    "everything": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-everything"]
    }
  }
}
```

No API keys or env vars required.

> If this exact package name has changed, substitute any small stdio MCP, e.g.
> the filesystem server: `npx -y @modelcontextprotocol/server-filesystem C:\some\dir`.
