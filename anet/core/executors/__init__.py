"""
anet.core.executors — Phase 4 of AdaptOrch: topology-specific execution.

Four executors, one per canonical topology, behind a common interface:

    async def run(dag: TaskDAG, ctx: ExecContext) -> list[StepResult]

    τ_P parallel      — all subtasks at once; each sees global context only
    τ_S sequential    — topological order; each sees accumulated predecessor outputs
    τ_H hierarchical  — lead delegates in order; sub-agents isolated (lead integrates)
    τ_X hybrid        — layer by layer; parallel within a layer, prior layers roll forward

The executors are PURE topology + context-assembly logic: they don't call an LLM
or know about agents/tools directly. They schedule subtasks and build each one's
prompt, then call ctx.run_subtask(subtask, prompt) — an injected coroutine that
the engine wires to the real agent loop (orchestrator.run). That keeps the
scheduling/context behaviour unit-testable with a fake run_subtask.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

from anet.core.dag import TaskDAG, Subtask, DEFAULT_COUPLING
from anet.core.router import Topology


@dataclass
class StepResult:
    id: str
    agent: str
    description: str
    output: str
    success: bool = True
    error: str | None = None


# run_subtask: async (subtask, assembled_prompt) -> StepResult
RunSubtask = Callable[[Subtask, str], Awaitable["StepResult"]]


@dataclass
class ExecContext:
    run_subtask: RunSubtask                       # how to actually execute one subtask
    global_context: str = ""                      # rolling summary / shared world-state
    on_status: Callable[[str], None] = lambda _s: None
    seq_budget: int = 6000                        # char budget for accumulated context


_DEFAULT_SEQ_BUDGET = 6000


# ── Shared context assembly ─────────────────────────────────────────────────────

def compose(description: str, global_context: str = "", forward_context: str = "") -> str:
    """Build the prompt a subtask's agent receives: shared context (if any), then
    relevant prior results (if any), then the task itself."""
    parts: list[str] = []
    if global_context and global_context.strip():
        parts.append(f"## Shared context\n{global_context.strip()}")
    if forward_context and forward_context.strip():
        parts.append(f"## Relevant prior results\n{forward_context.strip()}")
    parts.append(f"## Your task\n{description.strip()}")
    return "\n\n".join(parts)


def accumulate_predecessors(st: Subtask, outputs: dict[str, str], budget: int) -> str:
    """Concatenate the outputs of st's direct predecessors, ranked by coupling
    strength c(u,v) (most-coupled first), truncated to a char budget.

    This is the relevance-weighted truncation of eq. (13) — coupling is the
    relevance proxy. (Embedding-similarity ranking is a future refinement.)
    """
    preds = sorted(
        st.depends_on,
        key=lambda u: st.coupling.get(u, DEFAULT_COUPLING),
        reverse=True,
    )
    chunks: list[str] = []
    used = 0
    for u in preds:
        out = (outputs.get(u) or "").strip()
        if not out:
            continue
        block = f"[from {u}]\n{out}"
        if used + len(block) > budget:
            block = block[: max(0, budget - used)]
        if not block:
            break
        chunks.append(block)
        used += len(block)
        if used >= budget:
            break
    return "\n\n".join(chunks)


def topo_order(dag: TaskDAG) -> list[str]:
    """Flatten the DAG's layers into a single topological order."""
    return [nid for layer in dag.layers for nid in layer]


# ── Registry / dispatch ─────────────────────────────────────────────────────────

def get_executor(topology: Topology):
    """Return the executor coroutine for a topology."""
    from anet.core.executors import parallel, sequential, hierarchical, hybrid
    table = {
        Topology.PARALLEL:     parallel.run,
        Topology.SEQUENTIAL:   sequential.run,
        Topology.HIERARCHICAL: hierarchical.run,
        Topology.HYBRID:       hybrid.run,
    }
    return table[topology]


async def execute(topology: Topology, dag: TaskDAG, ctx: ExecContext) -> list[StepResult]:
    """Run the DAG under the chosen topology and return results in subtask order."""
    return await get_executor(topology)(dag, ctx)
