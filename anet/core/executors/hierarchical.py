"""
τ_H Hierarchical executor (AdaptOrch eqs. 14–15).

A lead delegates subtasks in dependency order and keeps a ledger of reports. The
sub-agents are ISOLATED — each receives only the global context plus its own task
description, not its peers' outputs; the lead is the integrator (conflict
resolution happens in synthesis, Phase 5). Used for highly-coupled tasks with many
subtasks, where a single coordinator beats free-for-all context sharing.

First implementation: the lead schedules in topological order and collects reports.
A future refinement gives the lead a real assign→monitor→reconcile agent loop
(reusing spawn_tool) so it can adapt assignments based on incoming reports.
"""
from __future__ import annotations

from anet.core.dag import TaskDAG
from anet.core.executors import ExecContext, StepResult, compose, topo_order


async def run(dag: TaskDAG, ctx: ExecContext) -> list[StepResult]:
    by_id = {s.id: s for s in dag.V}
    results: list[StepResult] = []
    ledger: list[str] = []

    for nid in topo_order(dag):
        st = by_id[nid]
        ctx.on_status(f"τ_H: lead → {nid} ({st.agent})")
        # Sub-agent gets a minimal, isolated context — the lead holds integration.
        prompt = compose(st.description, ctx.global_context)
        r = await ctx.run_subtask(st, prompt)
        results.append(r)
        ledger.append(f"{nid}={'ok' if r.success else 'failed'}")

    ctx.on_status("τ_H: lead reconciling — " + ", ".join(ledger))
    return results
