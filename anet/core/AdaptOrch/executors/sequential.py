"""
τ_S Sequential executor (AdaptOrch eq. 13).

Subtasks execute one at a time in topological order. Each subtask receives the
global context PLUS the accumulated outputs of its direct predecessors, ranked by
coupling strength and truncated to a budget (relevance-weighted concatenation).
Used for high-coupling chains where each step builds on the last.
"""
from __future__ import annotations

from anet.core.AdaptOrch.dag import TaskDAG
from anet.core.AdaptOrch.executors import (
    ExecContext, StepResult, compose, accumulate_predecessors, topo_order,
)


async def run(dag: TaskDAG, ctx: ExecContext) -> list[StepResult]:
    by_id = {s.id: s for s in dag.V}
    outputs: dict[str, str] = {}
    results: list[StepResult] = []

    for nid in topo_order(dag):
        st = by_id[nid]
        ctx.on_status(f"τ_S: {nid} ({st.agent})")
        pred_ctx = accumulate_predecessors(st, outputs, ctx.seq_budget)
        prompt = compose(st.description, ctx.global_context, pred_ctx)
        r = await ctx.run_subtask(st, prompt)
        outputs[nid] = r.output
        results.append(r)

    return results
