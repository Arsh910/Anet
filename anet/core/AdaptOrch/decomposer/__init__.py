"""
anet.core.AdaptOrch.decomposer — Phase 1 of AdaptOrch: Task Decomposition.

Given a user task T, an LLM decomposer extracts a set of subtasks annotated with
dependencies, estimated complexity, and context coupling. Those annotations are
mapped through the canonical tables in anet.core.AdaptOrch.dag (COMPLEXITY_WEIGHT → w_i,
COUPLING → c(u,v)) into Subtask objects, which dag.build() then turns into the
formal DAG (Phase 2).

Cost fast-path: trivial/atomic requests are detected here and short-circuit the
whole orchestration pipeline — the decomposer returns a direct reply instead of a
decomposition, so a "hi" or a one-line fact never spins up agents + synthesis.

The decomposer's model is configurable per-stage (orchestration.decomposer in
anet.config.yaml); it falls back to the manager model. See stage_models.py.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from anet.core.AdaptOrch.dag import (
    Subtask, TaskDAG, build as build_dag,
    COUPLING, COMPLEXITY_WEIGHT, DEFAULT_COUPLING,
)

_VALID_CLASSES = {"coding", "reasoning", "rag", "general"}


@dataclass
class Decomposition:
    """Result of Phase 1. Either a trivial fast-path reply, or a list of Subtasks
    ready for dag.build()."""
    trivial: bool
    reply: str | None              # direct answer when trivial=True
    subtasks: list[Subtask] = field(default_factory=list)
    request_class: str = "general"  # coding | reasoning | rag | general
    raw: dict | None = None         # the model's raw JSON (for logging/debug)


# ── Prompt (AdaptOrch §B.1 template, extended with agent routing) ──────────────

_SYSTEM = (
    "You are the task-decomposition planner of a multi-agent system. You break a "
    "user request into a directed acyclic graph of subtasks and route each one to "
    "the best-suited agent. You never execute the task yourself — you only plan it.\n\n"
    "COST FAST-PATH: if the request is trivial or atomic — a greeting, a single "
    "fact, a one-line question, or anything a single agent does in one step with no "
    "sub-structure — DO NOT decompose. Return exactly:\n"
    '  {"trivial": true, "reply": "<short direct answer>"}\n\n'
    "Otherwise return a decomposition (schema below). Prefer the FEWEST subtasks "
    "that capture the real structure: only split work that genuinely has distinct "
    "steps or can run in parallel. Over-decomposition wastes tokens.\n\n"
    "DECOMPOSITION RULES (decomposition quality is the single biggest driver of "
    "system performance — follow these precisely):\n"
    "- MAXIMIZE PARALLELISM: add a dependency ONLY when one subtask's output is "
    "genuinely required as another subtask's input. Anything not semantically "
    "required to wait should have depends_on: [] so it can run concurrently.\n"
    "- COUPLING is the relationship to a dependency: 'strong' = the output of one "
    "subtask is a direct input to another; 'weak' = subtasks share domain knowledge "
    "but not data; 'critical' = semantic coherence across them is required; 'none' = "
    "fully independent.\n"
    "- A typical CODING decomposition is a chain: localize files → understand context "
    "→ generate patch → verify patch. A typical RESEARCH/RAG task fans out (gather "
    "sources in parallel) then fans in (synthesize). Adapt to the actual task.\n"
    "- TOOL-COMPLETE ROUTING: route each subtask to the SINGLE agent that can finish "
    "it end-to-end with its own tools. Never split a subtask across agents just to "
    "reach a tool — agents are tool-complete within their domain (the coding agent "
    "reads, edits, runs, and verifies code by itself; the research agent searches, "
    "fetches, and writes its findings). Splitting to borrow a tool creates needless "
    "coupling and hurts parallelism."
)

# The AdaptOrch decomposition prompt template, verbatim, with point 5 added so each
# subtask routes to one of Anet's specialized agents.
_TEMPLATE = (
    "Analyze the following task and decompose it into independent subtasks.\n"
    "For each subtask, specify:\n"
    "1. A unique identifier and description\n"
    "2. Required inputs from other subtasks (dependencies)\n"
    "3. Estimated complexity (tokens: low/medium/high)\n"
    "4. Context coupling with dependencies (none/weak/strong/critical)\n"
    "5. The agent best suited to perform it (from AVAILABLE AGENTS)\n"
    "Task: {T}"
)

_OUTPUT_SPEC = (
    "AVAILABLE AGENTS (assign each subtask's \"agent\" to the best match):\n"
    "{agents}\n\n"
    "Coupling meaning: none=outputs independent, weak=shared context helps, "
    "strong=output is a direct input, critical=semantic coherence required.\n\n"
    "Respond with ONLY a JSON object, no prose:\n"
    "{{\n"
    '  "trivial": false,\n'
    '  "request_class": "coding" | "reasoning" | "rag" | "general",\n'
    '  "subtasks": [\n'
    "    {{\n"
    '      "id": "s1",\n'
    '      "description": "<what to do>",\n'
    '      "agent": "<one of the available agents>",\n'
    '      "depends_on": [],\n'
    '      "complexity": "low" | "medium" | "high",\n'
    '      "estimated_tokens": 500,\n'
    '      "coupling": "none" | "weak" | "strong" | "critical"\n'
    "    }}\n"
    "  ]\n"
    "}}\n\n"
    "Notes:\n"
    "- ids are short strings, unique, and referenced by depends_on.\n"
    "- depends_on lists the ids whose output this subtask needs (its inputs).\n"
    "- cost: give EITHER complexity (low/medium/high) OR estimated_tokens (a number). "
    "estimated_tokens is preferred when you can estimate it; complexity is the coarse "
    "fallback.\n"
    "- coupling describes this subtask's relationship to its dependencies; you may "
    "give a single label (applied to all deps) or an object {{\"<dep_id>\": \"strong\"}}.\n"
    "- subtasks with depends_on: [] can run in parallel."
)


def _format_agents(agents: list[dict] | None) -> str:
    if not agents:
        return "- (no specialized agents declared — use \"agent\": \"\")"
    lines = []
    for a in agents:
        name = a.get("name", "")
        if not name:
            continue
        types = ", ".join(a.get("task_types", []) or [])
        lines.append(f"- {name}: {types}" if types else f"- {name}")
    return "\n".join(lines) if lines else "- (none)"


def build_prompt(task: str, agents: list[dict] | None = None, memory_ctx: str = "") -> list[dict]:
    """Build the chat messages for the decomposer call. Pure — no model call."""
    user = _TEMPLATE.format(T=task) + "\n\n" + _OUTPUT_SPEC.format(agents=_format_agents(agents))
    if memory_ctx.strip():
        user = f"Relevant context from memory:\n{memory_ctx.strip()}\n\n{user}"
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]


# ── Parsing (pure — maps model JSON → Subtasks via the canonical tables) ───────

def _coupling_map(raw_coupling, depends_on: list[str]) -> dict[str, float]:
    """Resolve a subtask's coupling spec into {pred_id: c(u,v)}.

    Accepts a single label string (applied to every dependency) or a per-dep
    object. Unknown labels and missing entries fall back to DEFAULT_COUPLING via
    dag.build (we only record what's explicitly stated)."""
    out: dict[str, float] = {}
    if isinstance(raw_coupling, dict):
        for dep, label in raw_coupling.items():
            out[str(dep)] = COUPLING.get(str(label).lower(), DEFAULT_COUPLING)
    elif isinstance(raw_coupling, str) and raw_coupling.strip():
        c = COUPLING.get(raw_coupling.strip().lower(), DEFAULT_COUPLING)
        for dep in depends_on:
            out[dep] = c
    return out


def parse_decomposition(data: dict) -> Decomposition:
    """Convert the decomposer's JSON into a Decomposition. Pure, deterministic."""
    if not isinstance(data, dict):
        raise ValueError("decomposition must be a JSON object")

    rc = str(data.get("request_class", "general")).lower()
    if rc not in _VALID_CLASSES:
        rc = "general"

    if data.get("trivial"):
        return Decomposition(
            trivial=True,
            reply=str(data.get("reply", "")).strip(),
            subtasks=[],
            request_class=rc,
            raw=data,
        )

    raw_subs = data.get("subtasks") or []
    if not isinstance(raw_subs, list) or not raw_subs:
        raise ValueError("non-trivial decomposition must contain a non-empty 'subtasks' list")

    subtasks: list[Subtask] = []
    for s in raw_subs:
        if not isinstance(s, dict) or not str(s.get("id", "")).strip():
            raise ValueError(f"each subtask needs a non-empty 'id': {s!r}")
        sid = str(s["id"]).strip()
        depends_on = [str(x).strip() for x in (s.get("depends_on") or []) if str(x).strip()]
        # Cost weight w_i: prefer a concrete estimated_tokens count (B.1); fall back
        # to the coarse complexity bucket {low:1, medium:3, high:9}.
        est = s.get("estimated_tokens")
        if isinstance(est, (int, float)) and not isinstance(est, bool) and est > 0:
            w = float(est)
        else:
            w = COMPLEXITY_WEIGHT.get(str(s.get("complexity", "medium")).lower(),
                                      COMPLEXITY_WEIGHT["medium"])
        check = s.get("check")
        subtasks.append(Subtask(
            id=sid,
            description=str(s.get("description", "")).strip(),
            agent=str(s.get("agent", "")).strip(),
            w=w,
            depends_on=depends_on,
            coupling=_coupling_map(s.get("coupling"), depends_on),
            success_criteria=str(s.get("success_criteria", "")).strip(),
            check=check if isinstance(check, str) else (json.dumps(check) if check else ""),
        ))

    return Decomposition(trivial=False, reply=None, subtasks=subtasks, request_class=rc, raw=data)


def to_dag(decomp: Decomposition) -> TaskDAG:
    """Build the formal DAG from a (non-trivial) decomposition. Raises if trivial."""
    if decomp.trivial:
        raise ValueError("trivial decompositions have no DAG")
    return build_dag(decomp.subtasks)


# ── The decomposer call ─────────────────────────────────────────────────────────

async def decompose(task: str, agents: list[dict] | None = None, *, memory_ctx: str = "") -> Decomposition:
    """Run Phase 1: call the configured decomposer model and parse the result.

    Returns a Decomposition (trivial fast-path, or subtasks). Raises ValueError if
    the model's output can't be parsed into a valid decomposition.
    """
    if not task or not task.strip():
        raise ValueError("task is required")

    from anet.core.AdaptOrch.stage_models import stage_call, extract_json
    text = await stage_call("decomposer", build_prompt(task, agents, memory_ctx), max_tokens=1800)
    return parse_decomposition(extract_json(text))
