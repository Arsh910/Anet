"""
anet_pack — the default ANet pack.

A "pack" is a self-contained, shareable bundle of an ANet workspace: the default
config plus starter ExTools, ExAgents, MCP servers, and skills. This package ships
the *default* pack; on first run its contents are copied into the user's workspace
(~/.anet). Users can later create and share their own packs.

It lives as a top-level package (beside `anet/`, not inside it) so it ships with a
pip/pipx install yet stays clearly separate from the read-only core — contributors
edit the defaults here without touching the engine.
"""
