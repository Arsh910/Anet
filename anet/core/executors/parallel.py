"""
τ_P Parallel executor (AdaptOrch eq. 12).

All subtasks dispatch concurrently. Each agent runs with an ISOLATED context — it
sees only the global shared context, never its peers' outputs. Used for wide,
low-coupling DAGs where subtasks are genuinely independent.
"""
from __future__ import annotations

import asyncio

from anet.core.dag import TaskDAG
from anet.core.executors import ExecContext, StepResult, compose


async def run(dag: TaskDAG, ctx: ExecContext) -> list[StepResult]:
    async def _one(st):
        ctx.on_status(f"τ_P: {st.id} ({st.agent})")
        prompt = compose(st.description, ctx.global_context)   # global only — no peer outputs
        return await ctx.run_subtask(st, prompt)

    results = await asyncio.gather(*[_one(st) for st in dag.V])
    return list(results)
