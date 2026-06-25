"""
toolsets.py — named capability bundles for agents.

An agent should be a coherent capability DOMAIN that owns every tool it needs to
finish work in that domain end-to-end — not a wrapper around a single tool. To
make that easy and consistent, agents declare *toolsets* (bundles) instead of
hand-listing individual tools, and every agent automatically gets the COMMON
baseline so no agent is ever helpless for routine operations.

    agents_config: "toolsets": ["filesystem", "shell", "code_intel", "web"]
    → expand_tools(...) → the deduped tool-name list, with COMMON merged in.

Backward compatible: an agent may still set "tools": [...] directly; bundles and
explicit tools are unioned. Unknown tool names survive expansion and are filtered
later against the live tool_map (so a bundle naming a not-yet-loaded tool is fine).

Editing: add a bundle or move a tool between bundles here — every agent that uses
the bundle updates automatically. Co-used tools must live in the SAME bundle so
they always travel together (e.g. read/write/edit are inseparable).
"""
from __future__ import annotations

# Auto-added to EVERY agent (built-in or user-added). The minimum to inspect the
# world, remember, plan, ask, and delegate. Keep this small and side-effect-light:
# nothing here writes to disk or runs commands without its own confirm gate.
COMMON = [
    "grep_tool",      # search file contents
    "glob_tool",      # find files
    "web_fetch",      # read a URL into context
    "memory_tool",    # long-term memory
    "todo_tool",      # task checklist
    "ask_user",       # clarify mid-task
    "spawn_tool",     # delegate to a specialist
]

# Domain bundles. Co-used tools are grouped so they're never split apart.
TOOLSETS: dict[str, list[str]] = {
    "common":     COMMON,
    "filesystem": ["file_tool", "edit_tool", "conflict_tool"],
    "shell":      ["shell_tool", "process_tool", "code_execution"],
    "code_intel": ["lsp_tool", "diagnose_tool"],
    "web":        ["web_search", "web_fetch", "download_file"],
    "desktop":    ["open_app"],
    "verify":     ["checker"],
}


def expand_toolsets(names: list[str]) -> list[str]:
    """Expand a list of bundle names into a deduped, order-preserving tool list.
    Unknown bundle names are ignored. COMMON is NOT auto-added here — that's done
    by expand_tools so it applies even to agents that declare only `tools`."""
    out: list[str] = []
    for name in names or []:
        for tool in TOOLSETS.get(name, []):
            if tool not in out:
                out.append(tool)
    return out


def expand_tools(agent: dict, *, include_common: bool = True) -> list[str]:
    """Resolve an agent's full tool list: COMMON baseline ∪ its `toolsets` bundles
    ∪ its explicit `tools`. Deduped, order-preserving. Pure — does not mutate the
    agent or check availability (callers filter against the live tool_map)."""
    out: list[str] = []
    if include_common:
        out.extend(COMMON)
    for tool in expand_toolsets(agent.get("toolsets") or []):
        if tool not in out:
            out.append(tool)
    for tool in (agent.get("tools") or []):
        if tool not in out:
            out.append(tool)
    return out
