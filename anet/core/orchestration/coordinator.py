"""
coordinator.py — the AdaptOrch turn coordinator.

AdaptOrchEngine subclasses the OldEngine `Engine` to reuse its persistence,
short-term-summary maintenance, and async-state accessors (so the background
notifier keeps working unchanged), and overrides `run_turn` to run the AdaptOrch
pipeline instead of the legacy planner→executor→checker→synthesizer:

    Phase 1  decompose  — LLM → subtasks (trivial requests fast-path out here)
    Phase 2  DAG        — build G_T, compute ω / δ / γ / layers
    Phase 3  route      — Algorithm 1 picks a topology from the DAG shape
    Phase 4  execute    — the topology's executor runs each subtask via the agent loop
    Phase 5  synthesize — Algorithm 2 merges/arbitrates, rerouting on low consistency

Each phase emits an on_status line (shown live in the thinking panel) and the
routing decision + outcome is logged for the learned-routing dataset. Per-phase
token usage is captured automatically (stage tags flow through tokens.py).

Anything the pipeline can't handle (cycle in the DAG, async-resume, hard error)
falls back to the legacy engine, so a turn is never lost.
"""
from __future__ import annotations

from anet.core.OldEngine.engine import Engine, EngineResult
from anet.core.OldEngine import orchestrator
from anet.core.context import on_status as _status_var


class AdaptOrchEngine(Engine):
    async def run_turn(self, thread_id: str, store, user_input: str) -> EngineResult:
        # Async-resume (blocked offloaded tasks) is an OldEngine concern — defer to it.
        if user_input.startswith("__anet_async_resume__"):
            return await super().run_turn(thread_id, store, user_input)

        try:
            return await self._adaptorch_turn(thread_id, store, user_input)
        except Exception as exc:
            self._notify(f"manager: AdaptOrch error ({exc}) — falling back to legacy")
            return await super().run_turn(thread_id, store, user_input)

    # ── The AdaptOrch pipeline ──────────────────────────────────────────────────

    async def _adaptorch_turn(self, thread_id: str, store, user_input: str) -> EngineResult:
        from anet.core import decomposer, router as routermod, synthesizer, tokens
        from anet.core.executors import ExecContext, StepResult, execute

        on_status = _status_var.get()
        messages = await store.load(thread_id)
        summary, _keep = await self._maintain_summary(
            store, thread_id, messages + [{"role": "user", "content": user_input}]
        )

        # ── Phase 1 — Decomposition (+ trivial fast-path) ─────────────────────
        on_status("manager: decomposing task...")
        decomp = await decomposer.decompose(user_input, self._agents, memory_ctx=summary)

        if decomp.trivial:
            reply = decomp.reply or "Done."
            await self._persist(store, thread_id, user_input, reply, False)
            return EngineResult(reply=reply)

        # ── Phase 2 — DAG construction ────────────────────────────────────────
        task_dag = decomposer.to_dag(decomp)   # raises on cycle → caught → legacy fallback

        # ── Phase 3 — Topology routing ────────────────────────────────────────
        decision = routermod.route(task_dag)
        on_status(f"manager: routed → {decision.topology.symbol} · {task_dag.summary()}")

        # ── Phase 4 — Execution ───────────────────────────────────────────────
        async def run_subtask(st, prompt: str) -> StepResult:
            agent = self._resolve_agent(st.agent)
            try:
                result = await orchestrator.run(agent, self._tools, prompt, [], on_status)
                text = (result or {}).get("text", "")
                return StepResult(id=st.id, agent=agent["name"], description=st.description, output=text)
            except Exception as exc:
                return StepResult(id=st.id, agent=agent["name"], description=st.description,
                                  output=f"[subtask failed: {exc}]", success=False, error=str(exc))

        ctx = ExecContext(run_subtask=run_subtask, global_context=summary, on_status=on_status)

        async def reroute_fn(gamma_override: float):
            d2 = routermod.route(task_dag, gamma_override=gamma_override)
            on_status(f"manager: re-routing → {d2.topology.symbol} (γ'={gamma_override:.2f})")
            return d2.topology, await execute(d2.topology, task_dag, ctx)

        layers = len(task_dag.layers)
        on_status(f"manager: executing {task_dag.num_nodes} subtasks across {layers} layer(s) "
                  f"[{decision.topology.value}]...")
        results = await execute(decision.topology, task_dag, ctx)

        # ── Phase 5 — Adaptive synthesis ──────────────────────────────────────
        on_status("manager: synthesizing...")
        synth = await synthesizer.synthesize(
            results, topology=decision.topology, task=user_input,
            dag=task_dag, reroute_fn=reroute_fn,
        )
        note = f"CS={synth.consistency:.2f}"
        if synth.rerouted:
            note += f", rerouted ×{synth.iterations - 1}"
        on_status(f"manager: {synth.method} ({note})")

        reply = synth.output or "I worked through the task but produced no final answer."

        # Log the routing decision + outcome (the learned-routing dataset).
        usage = tokens.current()
        routermod.log_route(decision, request_class=decomp.request_class, outcome={
            "method": synth.method,
            "consistency": round(synth.consistency, 3),
            "iterations": synth.iterations,
            "rerouted": synth.rerouted,
            "tokens": usage.total if usage else None,
            "tokens_by_stage": dict(usage.by_stage) if usage else None,
        })

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
