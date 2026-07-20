"""
coordinator.py — the QweenBee turn coordinator.

QweenBeeEngine subclasses BaseEngine for the shared infra (per-thread state,
rolling summary, persistence) and implements `run_turn` as the QweenBee
pipeline (QueenBee Planner paper):

    Phase 1  decompose  — LLM → subtasks (trivial requests fast-path out here)
    Phase 2  DAG        — build the static task DAG (feeds the planner + its fallback)
    Phase 3  plan        — the LLM planner generates a temporal communication DAG
    Phase 4  execute     — the temporal executor runs the generated graph (Algorithm 1)
    Phase 5  finalize     — the holder's final belief IS the answer; no separate merge step

Each phase emits an on_status line (shown live in the thinking panel) and every
turn is logged to the evidence stream (anet.QweenBee.evidence) — the graph shape,
motifs, and outcome the skill bank (anet.QweenBee.skills) distills into
Preserve/Modify/Avoid design rules, retrieved before planning and rendered into
the planner's prompt as skills_ctx. Per-phase token usage is captured
automatically (stage tags flow through tokens.py).

Hard pipeline errors (an unexpected exception inside a phase) surface as a
clean error EngineResult — the turn isn't silently lost, but there is no
longer a legacy fallback path.
"""
from __future__ import annotations

from anet.core import orchestrator
from anet.core.engine_base import BaseEngine, EngineResult
from anet.core.context import on_status as _status_var


def _emit(text: str) -> None:
    """Stream an already-computed final answer through the token sink once,
    so the UI shows it live even though no LLM call produced it directly
    (the reply is the holder's belief, not a fresh combine call)."""
    try:
        from anet.core.context import on_token
        if text:
            on_token.get()(text)
    except Exception:
        pass


class QweenBeeEngine(BaseEngine):
    async def run_turn(self, thread_id: str, store, user_input: str) -> EngineResult:
        try:
            return await self._qweenbee_turn(thread_id, store, user_input)
        except Exception as exc:
            self._notify(f"manager: QweenBee error ({exc})")
            reply = f"Sorry — something went wrong while handling that turn: {exc}"
            try:
                await self._persist(store, thread_id, user_input, reply, False)
            except Exception:
                pass
            return EngineResult(reply=reply)

    # ── The QweenBee pipeline ──────────────────────────────────────────────────

    async def _qweenbee_turn(self, thread_id: str, store, user_input: str) -> EngineResult:
        from anet.core import tokens
        from anet.QweenBee import decomposer, planner as plannermod, evidence, skills
        from anet.QweenBee.executors import ExecContext, StepResult
        from anet.QweenBee.executors import temporal
        from anet.QweenBee.synthesizer import consistency_score
        from anet.core import context_window as cw

        on_status = _status_var.get()
        messages = await store.load(thread_id)
        convo = messages + [{"role": "user", "content": user_input}]
        summary, keep_from = await self._maintain_summary(store, thread_id, convo)
        # Working context the decomposer (and downstream executor) reason over:
        # rolling summary + verbatim recent turns, minus the current user_input
        # itself (passed separately as `task` / appears in the executor prompt's
        # "## Your task" section). Without this, QweenBee was blind to prior turns.
        working_ctx = cw.render_for_prompt(convo, summary, keep_from, exclude_last=True)

        # ── Phase 1 — Decomposition (+ trivial fast-path) ─────────────────────
        on_status("manager: decomposing task...")
        decomp = await decomposer.decompose(user_input, self._agents, memory_ctx=working_ctx)

        if decomp.trivial:
            reply = decomp.reply or "Done."
            await self._persist(store, thread_id, user_input, reply, False)
            return EngineResult(reply=reply)

        # ── Phase 2 — Static DAG construction ──────────────────────────────────
        task_dag = decomposer.to_dag(decomp)   # raises on cycle → caught → clean error result

        # ── Phase 3 — Graph generation (the planner, conditioned on the skill bank) ─
        try:
            skills_ctx = skills.retrieve(decomp.request_class, task_dag.num_nodes)
        except Exception:
            skills_ctx = ""
        if skills_ctx:
            on_status("manager: applying learned design rules...")
        pd = await plannermod.plan(user_input, task_dag.V, skills_ctx=skills_ctx)
        on_status(f"manager: planned → {pd.source} · T={pd.graph.T} · "
                  f"{len(pd.graph.edges)} edges → holder {pd.graph.holder}")

        # ── Phase 4 — Temporal execution ─────────────────────────────────────────
        async def run_subtask(st, prompt: str) -> StepResult:
            agent = self._resolve_agent(st.agent)
            try:
                result = await orchestrator.run(agent, self._tools, prompt, [], on_status)
                text = (result or {}).get("text", "")
                return StepResult(id=st.id, agent=agent["name"], description=st.description, output=text)
            except Exception as exc:
                return StepResult(id=st.id, agent=agent["name"], description=st.description,
                                  output=f"[subtask failed: {exc}]", success=False, error=str(exc))

        ctx = ExecContext(run_subtask=run_subtask, global_context=working_ctx, on_status=on_status)

        on_status(f"manager: executing {task_dag.num_nodes} subtasks across "
                  f"{pd.graph.T} round(s) [temporal]...")
        results, trace = await temporal.run(pd.graph, task_dag.V, ctx)

        # ── Phase 5 — Finalize ────────────────────────────────────────────────
        by_id = {r.id: r for r in results}
        holder_res = by_id.get(pd.graph.holder)
        reply = ""
        if holder_res and holder_res.success and holder_res.output.strip():
            reply = holder_res.output
        if not reply:
            reply = "I worked through the task but produced no final answer."
        _emit(reply)

        outputs = [r.output for r in results if r.output and r.output.strip()]
        cs = consistency_score(outputs) if len(outputs) > 1 else 1.0
        seen_nodes: set[str] = set()
        revisions = 0
        for row in trace:   # trace is in round order — a repeat node id is a revision
            if row.node in seen_nodes:
                revisions += 1
            seen_nodes.add(row.node)
        failures = sum(1 for r in results if not r.success)
        on_status(f"manager: finalized (CS={cs:.2f}, {len(trace)} execution(s))")

        # Log the generated graph + outcome (the Phase 4 skill-bank evidence).
        usage = tokens.current()
        evidence.log_run(
            graph=pd.graph, source=pd.source, motifs=pd.motifs,
            request_class=decomp.request_class, spec=pd.spec,
            outcome={
                "consistency": round(cs, 3),
                "executions": len(trace),
                "revisions": revisions,
                "failures": failures,
                "skills_used": bool(skills_ctx),
                "tokens": usage.total if usage else None,
                "tokens_by_stage": dict(usage.by_stage) if usage else None,
            },
        )

        await self._persist(store, thread_id, user_input, reply, False)

        step_results = [
            {"agent": r.agent, "status": "ok" if r.success else "failed", "result": r.output}
            for r in results
        ]
        return EngineResult(reply=reply, step_results=step_results)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resolve_agent(self, name: str) -> dict:
        """Map a decomposer-assigned agent name to a live agent config. Falls back to
        code_agent, then the first agent, so a bad/empty assignment never crashes."""
        if name and name in self._agent_map:
            return self._agent_map[name]
        if "code_agent" in self._agent_map:
            return self._agent_map["code_agent"]
        return self._agents[0]
