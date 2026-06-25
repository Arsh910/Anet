"""
anet.core.synthesizer — Phase 5 of AdaptOrch: Adaptive Synthesis Protocol (Algorithm 2).

Merges the executor's per-subtask outputs into one coherent final answer:

    1. τ_S (sequential)         → the last output is already final, return it.
    2. CS ≥ θ_CS (consistent)   → A_merge combines the agreeing outputs.
    3. CS < θ_CS (conflicting)  → A_arbiter resolves the conflict; if the result
                                  still doesn't reconcile (post-CS < θ_CS) and
                                  retries remain, RE-ROUTE with γ' = γ + 0.2 and
                                  re-execute, then synthesize again.

Termination (Proposition 2): each reroute raises γ by 0.2, so within ≤5 iterations
γ exceeds θ_γ and routing collapses to τ_H (a single arbiter); a hard max_iters cap
guarantees termination for any DAG shape.

The merge agent, arbiter agent, and the reroute-and-re-execute step are injected
(merge_fn / arbiter_fn / reroute_fn) so the protocol's control flow is unit-testable
with no LLM. Defaults call the configured per-stage models (orchestration.merger /
orchestration.arbiter, falling back to the manager model).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from anet.core.router import Topology
from anet.core.synthesizer.consistency import consistency_score, alignment


@dataclass
class SynthesisResult:
    output: str
    consistency: float            # CS of the outputs that produced this result
    topology: Topology            # topology in effect when it terminated
    method: str                   # sequential_last | single | merge | arbiter
    iterations: int               # synthesis passes (>1 means it rerouted)
    rerouted: bool
    history: list[dict] = field(default_factory=list)   # per-pass record (for the routing log)


def theta_cs() -> float:
    """Synthesis consistency threshold: orchestration.theta_cs in config, else 0.7."""
    try:
        from anet.core.config_loader import load
        return float(((load() or {}).get("orchestration") or {}).get("theta_cs", 0.7))
    except Exception:
        return 0.7


# ── Default merge / arbiter agents (configurable per-stage models) ─────────────

def _numbered(outputs: list[str]) -> str:
    return "\n\n".join(f"[output {i + 1}]\n{o}" for i, o in enumerate(outputs))


async def _default_merge(outputs: list[str], task: str = "") -> str:
    from anet.core.stage_models import stage_call, stage_call_stream
    msgs = [
        {"role": "system", "content": (
            "You merge multiple agent outputs that AGREE into one coherent final "
            "answer. Keep every correct detail, remove redundancy, resolve wording. "
            "Return only the final answer.")},
        {"role": "user", "content":
            (f"Task: {task}\n\n" if task else "") +
            "Synthesize these consistent outputs into the single best final answer:\n\n"
            + _numbered(outputs)},
    ]
    # Merge is always the terminal synthesis call → stream it live through the
    # active token sink (the default sink is a harmless no-op).
    from anet.core.context import on_token as _on_token
    try:
        return await stage_call_stream("merger", msgs, _on_token.get(), max_tokens=1800)
    except Exception:
        return await stage_call("merger", msgs, max_tokens=1800)


async def _default_arbiter(outputs: list[str], task: str = "") -> str:
    from anet.core.stage_models import stage_call
    msgs = [
        {"role": "system", "content": (
            "You are an arbiter. The agent outputs DISAGREE or conflict. Decide what "
            "is actually correct, reconcile the differences, and produce the single "
            "most correct, coherent final answer. Return only the final answer.")},
        {"role": "user", "content":
            (f"Task: {task}\n\n" if task else "") +
            "Resolve the conflicts among these outputs and produce the final answer:\n\n"
            + _numbered(outputs)},
    ]
    return await stage_call("arbiter", msgs, max_tokens=1800)


# ── Algorithm 2 ─────────────────────────────────────────────────────────────────

async def synthesize(
    results,
    *,
    topology: Topology,
    task: str = "",
    dag=None,
    theta: float | None = None,
    reroute_fn=None,       # async (gamma_override: float) -> (Topology, list[StepResult])
    merge_fn=None,         # async (outputs, task) -> str
    arbiter_fn=None,       # async (outputs, task) -> str
    embed=None,            # texts -> vectors (tests inject the lexical one)
    max_iters: int = 5,
) -> SynthesisResult:
    """Run the Adaptive Synthesis Protocol and return the final answer."""
    theta = theta_cs() if theta is None else theta
    merge_fn = merge_fn or _default_merge
    arbiter_fn = arbiter_fn or _default_arbiter
    base_gamma = float(getattr(dag, "gamma", 0.0)) if dag is not None else 0.0

    cur_topo = topology
    cur_results = list(results)
    history: list[dict] = []
    iters = 0
    bump = 0.0

    # The merge path streams itself (token-by-token); these other terminal outputs
    # are already-computed text, so emit them once through the sink so the reply
    # still appears live in the UI.
    def _emit(text: str) -> None:
        try:
            from anet.core.context import on_token as _on_token
            if text:
                _on_token.get()(text)
        except Exception:
            pass

    while True:
        iters += 1
        outputs = [r.output for r in cur_results if getattr(r, "output", "") and r.output.strip()]

        # τ_S: the last step's output is the final answer by construction.
        if cur_topo == Topology.SEQUENTIAL:
            final = outputs[-1] if outputs else ""
            _emit(final)
            return SynthesisResult(final, 1.0, cur_topo, "sequential_last", iters, iters > 1, history)

        # Single output → nothing to merge.
        if len(outputs) <= 1:
            final = outputs[0] if outputs else ""
            _emit(final)
            return SynthesisResult(final, 1.0, cur_topo, "single", iters, iters > 1, history)

        cs = consistency_score(outputs, embed)
        rec = {"iter": iters, "topology": cur_topo.value, "cs": round(cs, 4), "k": len(outputs)}

        # Consistent → merge and finish. (merge_fn streams internally.)
        if cs >= theta:
            out = await merge_fn(outputs, task)
            rec["action"] = "merge"
            history.append(rec)
            return SynthesisResult(out, cs, cur_topo, "merge", iters, iters > 1, history)

        # Inconsistent → arbiter.
        out = await arbiter_fn(outputs, task)
        post = alignment(out, outputs, embed)
        rec["action"] = "arbiter"
        rec["post_cs"] = round(post, 4)
        history.append(rec)

        # Terminate the loop when: τ_H already uses a single arbiter (Prop. 2), the
        # arbiter reconciled (post-CS ok), we can't reroute, or we hit the cap.
        if (cur_topo == Topology.HIERARCHICAL
                or post >= theta
                or reroute_fn is None
                or dag is None
                or iters >= max_iters):
            _emit(out)
            return SynthesisResult(out, cs, cur_topo, "arbiter", iters, iters > 1, history)

        # Re-route with γ' = γ + 0.2 and re-execute, then synthesize the new outputs.
        bump += 0.2
        cur_topo, cur_results = await reroute_fn(base_gamma + bump)
