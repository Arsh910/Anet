"""
τ_X Hybrid executor (AdaptOrch eq. 16).

The DAG is partitioned into topological layers. Within a layer, subtasks run in
parallel; between layers, execution is sequential — each layer sees the merged
outputs of all PRIOR layers (but not its own peers). This is the general case:
most real tasks mix parallel and sequential structure.
"""
from __future__ import annotations

import asyncio

from anet.core.dag import TaskDAG
from anet.core.executors import ExecContext, StepResult, compose


async def run(dag: TaskDAG, ctx: ExecContext) -> list[StepResult]:
    by_id = {s.id: s for s in dag.V}
    results: list[StepResult] = []
    prior_outputs: list[str] = []   # merged outputs of all completed layers

    for depth, layer in enumerate(dag.layers):
        forward = "\n\n".join(prior_outputs)[: ctx.seq_budget]

        async def _one(nid, forward=forward):     # bind forward per layer
            st = by_id[nid]
            ctx.on_status(f"τ_X[L{depth}]: {nid} ({st.agent})")
            prompt = compose(st.description, ctx.global_context, forward)
            return await ctx.run_subtask(st, prompt)

        layer_results = await asyncio.gather(*[_one(nid) for nid in layer])
        results.extend(layer_results)
        prior_outputs.extend(r.output for r in layer_results)

    return results
