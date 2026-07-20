"""
τ_G Temporal executor (QueenBee Planner §3.2, Algorithm 1: EXECUTEGENERATEDDAG).

Runs a planner-generated anet.QweenBee.tgraph.TemporalDAG instead of one of the
four fixed topologies: for each round, deliver round-(t-1) beliefs to this
round's receivers, run them all concurrently, commit their new beliefs, repeat.
This single executor subsumes parallel/sequential/hierarchical/hybrid — they
are all special cases of a temporal graph shape.

Scheduling (which nodes run in which round) extends the paper's "every worker
does local work in round 0" assumption, which doesn't hold for Anet's
heterogeneous subtasks (a patch step is useless before its localize step
delivers). A node executes when:

    1. source init   — round 0, node never receives anywhere in the graph
    2. receiver      — this round has >=1 inbound edge for the node (first
                        time = initial run with input; later = revision)
    3. forced init    — node hasn't executed yet but must SEND next round
                        (its outbox is needed before rule 2 would fire)
    4. stranded sweep — after round T, any node that still never executed
                        runs once with no inbox (every subtask must produce
                        a StepResult)

Barrier semantics: every round snapshots belief state once (`prev`), builds
ALL of that round's prompts from `prev`, runs them concurrently, then commits
all new beliefs together — so same-round edges never see a same-round update.

The final answer is belief[g.holder] (Algorithm 1, FINALIZE). Reuses
ExecContext/StepResult/compose from executors/__init__.py unchanged.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from anet.QweenBee.dag import Subtask
from anet.QweenBee.executors import ExecContext, StepResult, compose
from anet.QweenBee.tgraph import TemporalDAG


@dataclass
class NodeTrace:
    """One execution of one node — evidence for Phase 4's skill bank."""
    node: str
    round: int              # 0 = init round; T+1 = the stranded-node sweep
    inbox_from: list[str]   # sender ids this execution received from ([] for init/sweep)
    success: bool


async def run(g: TemporalDAG, subtasks: list[Subtask], ctx: ExecContext) -> tuple[list[StepResult], list[NodeTrace]]:
    by_id = {s.id: s for s in subtasks}
    node_ids = [s.id for s in subtasks]

    has_any_incoming = {dst for _s, dst, _t in g.edges}
    incoming_by_round: dict[int, dict[str, list[str]]] = {}
    for src, dst, t in g.edges:
        incoming_by_round.setdefault(t, {}).setdefault(dst, []).append(src)

    belief: dict[str, str] = {}
    executed: dict[str, bool] = {n: False for n in node_ids}
    latest: dict[str, StepResult] = {}
    trace: list[NodeTrace] = []

    async def _run_round(r: int, targets: dict[str, list[str]], prev: dict[str, str]) -> None:
        """targets: node_id -> inbox senders ([] means init/forced-init/sweep, no inbox)."""
        async def _one(nid: str, senders: list[str]) -> None:
            st = by_id[nid]
            forward_parts: list[str] = []
            if executed.get(nid) and nid in prev:
                forward_parts.append(f"[your previous output]\n{prev[nid]}")
            for sender in sorted(senders):
                forward_parts.append(f"[from {sender}]\n{prev.get(sender, '')}")
            forward_context = "\n\n".join(forward_parts)[: ctx.seq_budget]

            instr = g.round_instructions.get(r)
            description = f"{st.description}\n\nThis round: {instr}" if instr else st.description
            prompt = compose(description, ctx.global_context, forward_context)

            label = f"τ_G[r{r}]: {nid}"
            if senders:
                label += f" ← {','.join(sorted(senders))}"
            ctx.on_status(f"{label} ({st.agent})")

            res = await ctx.run_subtask(st, prompt)
            belief[nid] = res.output
            executed[nid] = True
            latest[nid] = res
            trace.append(NodeTrace(node=nid, round=r, inbox_from=sorted(senders), success=res.success))

        await asyncio.gather(*[_one(nid, senders) for nid, senders in targets.items()])

    for r in range(0, g.T + 1):
        prev = dict(belief)
        targets: dict[str, list[str]] = {}

        if r == 0:
            for n in node_ids:
                if n not in has_any_incoming:
                    targets[n] = []

        for dst, srcs in incoming_by_round.get(r, {}).items():
            targets[dst] = srcs

        senders_next_round = {src for src, _dst, t in g.edges if t == r + 1}
        for n in senders_next_round:
            if not executed.get(n) and n not in targets:
                targets[n] = []

        if targets:
            await _run_round(r, targets, prev)

    stranded = [n for n in node_ids if not executed.get(n)]
    if stranded:
        prev = dict(belief)
        await _run_round(g.T + 1, {n: [] for n in stranded}, prev)

    results = [latest[s.id] for s in subtasks]
    return results, trace
