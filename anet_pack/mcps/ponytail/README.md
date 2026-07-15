# Ponytail MCP server

Exposes Ponytail's lazy-senior-dev ruleset ("YAGNI, stdlib first, smallest
correct change") as an MCP tool, `ponytail_instructions(mode)`, so any ANet
agent can pull it into context on demand.

Source: a subdirectory of https://github.com/DietrichGebert/ponytail (the
`ponytail-mcp/` folder). That folder is NOT standalone — it imports sibling
files from the monorepo (`../hooks/ponytail-config.js`,
`../hooks/ponytail-instructions.js`, `../skills/ponytail/SKILL.md`, and the
repo root `package.json`). This directory vendors exactly those pieces
alongside it so the imports resolve.

## If this vendored copy is missing (fresh pack install)

1. Clone the full repo (not just the subfolder):
   `git clone https://github.com/DietrichGebert/ponytail`
2. Copy these into `anet_pack/mcps/ponytail/`, preserving structure:
   - `package.json` (repo root)
   - `hooks/ponytail-config.js`
   - `hooks/ponytail-instructions.js`
   - `skills/ponytail/SKILL.md`
   - `ponytail-mcp/` (the whole folder)
3. `cd anet_pack/mcps/ponytail/ponytail-mcp && npm install`
4. Restart ANet — `config.yaml` in this folder points `node` at
   `ponytail-mcp/index.js` with `cwd: anet_pack/mcps/ponytail`.

## Tools

- `ponytail_instructions(mode)` — returns the ruleset text for `lite`,
  `full`, or `ultra` (defaults to `full`). Read-only, no side effects.
