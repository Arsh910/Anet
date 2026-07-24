"""
anet.core.AdaptOrch.synthesizer — Phase 5: synthesis as a dispatcher of operators.

The paper's Algorithm 2 has two outcomes (merge if consistent, arbitrate if not),
gated by a consistency score. That conflates two unrelated questions — "are these
the same?" and "do these conflict?" — and misfires on parallel subtasks that are
*distinct by design* (three different facts look dissimilar → low CS → spurious
arbiter). See the T2 finding.

So synthesis here is a DISPATCHER over five operators, each asking the right
question for its situation:

    compose    distinct complementary parts (backend/frontend/tests) → integrate
    aggregate  research / evidence (paper A/B/C)                     → unified summary
    vote       redundant attempts at the SAME answer                → majority (uses CS)
    rank       candidate generation (N options)                     → best, by quality
    resolve    a genuine contradiction                              → arbiter (+ reroute)

The operator is chosen by `select_operator()` from the execution topology and the
request class, with an optional explicit `synthesis` hint (Phase 2: the decomposer
emits it per join). Consistency score is DEMOTED — it's no longer the merge/arbiter
gate; it's just a signal inside `vote`/`resolve`. Distinct-subtask topologies
(Anet's normal case) default to compose/aggregate and never spuriously arbitrate.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from anet.core.AdaptOrch.router import Topology
from anet.core.AdaptOrch.synthesizer.consistency import (
    consistency_score, alignment, cosine, default_embed,
)

OPERATORS = {"compose", "aggregate", "vote", "rank", "resolve"}


@dataclass
class SynthesisResult:
    output: str
    operator: str                 # compose | aggregate | vote | rank | resolve | sequential_last | single
    topology: Topology
    consistency: float            # CS of the outputs (informational; gates only vote/resolve)
    iterations: int
    rerouted: bool
    history: list[dict] = field(default_factory=list)

    # Back-compat: older callers/tests read `.method`.
    @property
    def method(self) -> str:
        return self.operator


def theta_cs() -> float:
    """Consistency threshold used by vote/resolve: orchestration.theta_cs, else 0.7."""
    try:
        from anet.core.config_loader import load
        return float(((load() or {}).get("orchestration") or {}).get("theta_cs", 0.7))
    except Exception:
        return 0.7


def select_operator(*, topology: Topology, request_class: str, n_outputs: int,
                    hint: str | None = None) -> str:
    """Pick the synthesis operator. An explicit hint (Phase 2 decomposer field) wins;
    otherwise default by topology + request class. Vote/Rank/Resolve are never chosen
    by default — only via an explicit hint or a declared-redundant decomposition."""
    if n_outputs <= 1:
        return "single"
    if topology == Topology.SEQUENTIAL:
        return "sequential_last"
    if hint and hint in OPERATORS:
        return hint
    # Distinct-subtask default (no redundancy signal): combine, never arbitrate.
    if (request_class or "").lower() in ("rag", "research"):
        return "aggregate"
    return "compose"


# ── Operator prompts ─────────────────────────────────────────────────────────────

def _numbered(outputs: list[str]) -> str:
    return "\n\n".join(f"[output {i + 1}]\n{o}" for i, o in enumerate(outputs))


_COMPOSE_SYS = (
    "Each output below is a DIFFERENT part of one task (e.g. backend, frontend, tests). "
    "Integrate every part into one coherent deliverable — keep every part, don't compare "
    "or pick. Return the final result.")

_AGGREGATE_SYS = (
    "Synthesize the findings below into one unified summary. Preserve every distinct piece "
    "of information. If two outputs disagree on the SAME fact, note both rather than picking. "
    "Return the summary.")

_RANK_SYS = (
    "These are competing candidates for the same goal. Rank them by quality and return "
    "the single best one (briefly say why). Don't merge — choose.")

_RESOLVE_SYS = (
    "The outputs make CONTRADICTORY claims on the same point. Decide which is right "
    "(prefer freshly-retrieved evidence over prior knowledge), reconcile, and return "
    "the correct answer.")

# Anti-fabrication rule appended to EVERY synthesis operator. Without it the model
# pads thin real output into a confident, plausible-looking result (invented CLI
# commands, fake success JSON, a how-to guide for something that never happened).
_GROUND = (
    "\n\nGROUNDING (critical): Use ONLY what the outputs below actually contain. NEVER "
    "invent commands, code, URLs, paths, tool output, or success/JSON results that are not "
    "present in the outputs. Do not turn a task into a how-to guide of what SHOULD be done. "
    "If the outputs show the task did not finish — errors, only partial progress (e.g. reached "
    "a login screen but never logged in), or missing results — report that plainly as the "
    "outcome. Describe what ACTUALLY happened per the outputs, never a polished version of what "
    "success would look like.")


async def _combine(stage: str, system: str, outputs: list[str], task: str) -> str:
    """Run a terminal combine/resolve LLM call, streaming to the UI token sink."""
    from anet.core.AdaptOrch.stage_models import stage_call, stage_call_stream
    from anet.core.context import on_token
    msgs = [
        {"role": "system", "content": system + _GROUND},
        {"role": "user", "content": (f"Task: {task}\n\n" if task else "") + _numbered(outputs)},
    ]
    try:
        return await stage_call_stream(stage, msgs, on_token.get(), max_tokens=1800)
    except Exception:
        return await stage_call(stage, msgs, max_tokens=1800)


def _emit(text: str) -> None:
    """Emit an already-computed final answer once through the token sink (so vote /
    sequential / single outputs still appear live)."""
    try:
        from anet.core.context import on_token
        if text:
            on_token.get()(text)
    except Exception:
        pass


def _vote(outputs: list[str], embed, theta: float) -> tuple[str, bool]:
    """Majority over redundant answers: return the consensus output (the one most
    similar to the rest) and whether it has majority agreement (sim ≥ theta)."""
    vecs = (embed or default_embed)(outputs)
    n = len(vecs)
    means = [
        sum(cosine(vecs[i], vecs[j]) for j in range(n) if j != i) / (n - 1)
        for i in range(n)
    ]
    best = max(range(n), key=lambda i: means[i])
    agree = 1 + sum(1 for j in range(n) if j != best and cosine(vecs[best], vecs[j]) >= theta)
    return outputs[best], agree > n / 2


# ── The dispatcher ───────────────────────────────────────────────────────────────

async def synthesize(
    results,
    *,
    topology: Topology,
    task: str = "",
    dag=None,
    request_class: str = "general",
    synthesis: str | None = None,     # explicit operator hint (Phase 2)
    theta: float | None = None,
    reroute_fn=None,                  # async (gamma_override) -> (Topology, results)  — used only by resolve
    embed=None,
    max_iters: int = 5,
) -> SynthesisResult:
    """Synthesize executor outputs by dispatching to the right operator."""
    theta = theta_cs() if theta is None else theta
    outputs = [r.output for r in results if getattr(r, "output", "") and r.output.strip()]
    op = select_operator(topology=topology, request_class=request_class,
                         n_outputs=len(outputs), hint=synthesis)
    cs = consistency_score(outputs, embed) if len(outputs) > 1 else 1.0
    history = [{"operator": op, "cs": round(cs, 4), "k": len(outputs)}]

    # Passthrough cases — already a single coherent answer.
    if op == "sequential_last":
        final = outputs[-1] if outputs else ""
        _emit(final)
        return SynthesisResult(final, op, topology, 1.0, 1, False, history)
    if op == "single":
        final = outputs[0] if outputs else ""
        _emit(final)
        return SynthesisResult(final, op, topology, 1.0, 1, False, history)

    # Combine families — distinct outputs, no conflict assumed (stream the result).
    if op == "compose":
        return SynthesisResult(await _combine("merger", _COMPOSE_SYS, outputs, task),
                               op, topology, cs, 1, False, history)
    if op == "aggregate":
        return SynthesisResult(await _combine("merger", _AGGREGATE_SYS, outputs, task),
                               op, topology, cs, 1, False, history)
    if op == "rank":
        return SynthesisResult(await _combine("merger", _RANK_SYS, outputs, task),
                               op, topology, cs, 1, False, history)

    # Vote — redundant attempts; pick consensus, escalate to resolve only if no majority.
    if op == "vote":
        choice, has_majority = _vote(outputs, embed, theta)
        if has_majority:
            _emit(choice)
            return SynthesisResult(choice, "vote", topology, cs, 1, False, history)
        op = "resolve"   # no majority → genuine contention → arbitrate
        history.append({"operator": "resolve", "reason": "vote: no majority"})

    # Resolve — true contradiction; arbitrate, with the reroute loop (Algorithm 2).
    iters, bump = 0, 0.0
    base_gamma = float(getattr(dag, "gamma", 0.0)) if dag is not None else 0.0
    cur_results = results
    while True:
        iters += 1
        outs = [r.output for r in cur_results if getattr(r, "output", "") and r.output.strip()]
        out = await _combine("arbiter", _RESOLVE_SYS, outs, task)
        post = alignment(out, outs, embed)
        history.append({"operator": "resolve", "iter": iters, "post_cs": round(post, 4)})
        if (topology == Topology.HIERARCHICAL or post >= theta
                or reroute_fn is None or dag is None or iters >= max_iters):
            return SynthesisResult(out, "resolve", topology, cs, iters, iters > 1, history)
        bump += 0.2
        topology, cur_results = await reroute_fn(base_gamma + bump)
