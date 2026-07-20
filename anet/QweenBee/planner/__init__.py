"""
anet.QweenBee.planner — the QueenBee architect (paper §3, Eq. 4: G ~ π_S(·|x,N,B)).

Replaces AdaptOrch's deterministic router (Algorithm 1: DAG shape -> one of
four fixed topologies) with an LLM that directly generates a temporal
communication DAG (anet.QweenBee.tgraph.TemporalDAG): which subtask messages
which subtask, in which round, and who holds the final answer.

The planner never solves the task and never reassigns subtasks to agents —
those stay the frozen decomposer's job (Phase 1). It only designs the message
flow between the subtasks the decomposer already produced.

plan() never raises: generate -> validate -> one repair attempt -> a
deterministic fallback graph derived from the static TaskDAG's layers, so a
bad or unavailable model can never break a turn. `source` on the returned
PlanDecision records which path was taken ("planner" / "planner_repaired" /
"fallback") — that field is the fallback-rate signal for later phases.

Phase 4 will inject retrieved Preserve/Modify/Avoid skills via the
`skills_ctx` parameter already threaded through build_prompt()/plan(); until
then callers pass nothing and it's a no-op.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from anet.QweenBee import tgraph
from anet.QweenBee.dag import Subtask, build as build_static_dag
from anet.QweenBee.tgraph import TemporalDAG

# ── Prompt ───────────────────────────────────────────────────────────────────

_SYSTEM = (
    "You are a communication-topology architect for a multi-agent system. "
    "You design who sends information to whom, in which round — you never "
    "solve the task yourself, and you never reassign work between agents.\n\n"
    "Nodes are the given subtasks, each already assigned to an agent. An edge "
    "[i, j] in round t means: i's output as of the end of round t-1 is "
    "delivered to j at round t. Barrier semantics: every edge in the same "
    "round sees the PREVIOUS round's committed state, never a same-round "
    "update from another edge that landed first.\n\n"
    "HARD LIMITS:\n"
    "- at most {t_max} rounds (T <= {t_max})\n"
    "- at most {m_max} total edges\n"
    "- at most {beta} inbound edges to any single node in any single round\n\n"
    "DESIGN GUIDANCE:\n"
    "- Coverage: every subtask's output must have a path (through edges) to "
    "the final holder — don't strand a subtask's result unread.\n"
    "- Prefer staged reduction (chain merges through an intermediate node) "
    "over a single node receiving more than {beta} inputs at once.\n"
    "- An audit edge (the holder sends its draft to a checker, which reports "
    "back) is worth one extra round only when correctness matters more than "
    "cost — most tasks don't need one.\n"
    "- Fewer rounds and fewer edges is cheaper; don't add structure the task "
    "doesn't need.\n\n"
    "Return ONLY this JSON (no prose):\n"
    "{{\n"
    '  "T": <int>,\n'
    '  "rounds": [\n'
    '    {{"t": 1, "edges": [["src_id", "dst_id"]], "instruction": "<optional guidance for this round\'s receivers>"}}\n'
    "  ],\n"
    '  "holder": "<subtask id holding the final answer>",\n'
    '  "rationale": "<one line: why this shape>"\n'
    "}}"
)


def _format_subtasks(subtasks: list[Subtask]) -> str:
    lines = []
    for s in subtasks:
        deps = ", ".join(s.depends_on) if s.depends_on else "(none)"
        lines.append(f"- {s.id} [{s.agent or 'unassigned'}]: {s.description}  (depends_on: {deps})")
    return "\n".join(lines)


def build_prompt(task: str, subtasks: list[Subtask], limits: dict, skills_ctx: str = "") -> list[dict]:
    """Build the chat messages for the planner call. Pure — no model call."""
    system = _SYSTEM.format(**limits)
    user = (
        f"Task: {task}\n\n"
        f"Subtasks:\n{_format_subtasks(subtasks)}\n\n"
        "A subtask that depends on another's output should receive an edge "
        "from that subtask no later than the round before it must produce its own output."
    )
    if skills_ctx.strip():
        user = f"Design rules from prior runs:\n{skills_ctx.strip()}\n\n{user}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ── Parsing (pure — maps model JSON -> TemporalDAG) ─────────────────────────

def _parse_edge(raw, round_t: int, idx: int) -> tuple[str, str, int]:
    if isinstance(raw, dict):
        src, dst = raw.get("from"), raw.get("to")
    elif isinstance(raw, (list, tuple)) and len(raw) == 2:
        src, dst = raw
    else:
        raise ValueError(f"round {round_t} edge {idx} is malformed: {raw!r}")
    src, dst = str(src or "").strip(), str(dst or "").strip()
    if not src or not dst:
        raise ValueError(f"round {round_t} edge {idx} has an empty endpoint: {raw!r}")
    return (src, dst, round_t)


def parse_plan(data: dict, node_ids: list[str]) -> TemporalDAG:
    """Convert the planner's JSON into a TemporalDAG. Pure, deterministic.

    node_ids come from the decomposition — the model never invents nodes; any
    edge or holder referencing an id outside node_ids is a hard error."""
    if not isinstance(data, dict):
        raise ValueError("plan must be a JSON object")

    node_set = set(node_ids)
    rounds = data.get("rounds")
    if not isinstance(rounds, list) or not rounds:
        raise ValueError("plan must contain a non-empty 'rounds' list")

    edges: list[tuple[str, str, int]] = []
    round_instructions: dict[int, str] = {}
    max_t = 0
    for r in rounds:
        if not isinstance(r, dict):
            raise ValueError(f"round entry must be an object: {r!r}")
        t = r.get("t")
        if not isinstance(t, int) or isinstance(t, bool) or t < 1:
            raise ValueError(f"round entry has invalid 't': {r.get('t')!r}")
        max_t = max(max_t, t)
        instr = r.get("instruction")
        if isinstance(instr, str) and instr.strip():
            round_instructions[t] = instr.strip()
        raw_edges = r.get("edges") or []
        if not isinstance(raw_edges, list):
            raise ValueError(f"round {t} 'edges' must be a list")
        for i, raw in enumerate(raw_edges):
            src, dst, edge_t = _parse_edge(raw, t, i)
            if src not in node_set:
                raise ValueError(f"round {t} edge {i} references unknown node '{src}'")
            if dst not in node_set:
                raise ValueError(f"round {t} edge {i} references unknown node '{dst}'")
            edges.append((src, dst, edge_t))

    T = data.get("T")
    if not isinstance(T, int) or isinstance(T, bool) or T < 1:
        T = max_t if max_t > 0 else 1

    holder = str(data.get("holder", "")).strip()
    if not holder:
        raise ValueError("plan is missing a non-empty 'holder'")
    if holder not in node_set:
        raise ValueError(f"holder '{holder}' is not one of the task's subtask ids")

    rationale = str(data.get("rationale", "")).strip()

    return TemporalDAG(
        nodes=list(node_ids), T=T, edges=edges, holder=holder,
        round_instructions=round_instructions, rationale=rationale,
    )


# ── Deterministic fallback (static TaskDAG layers -> temporal graph) ───────

def _cap_fan_in_once(edges: list[tuple[str, str, int]], T: int,
                     beta: int) -> tuple[list[tuple[str, str, int]], int, bool]:
    """One pass of staged reduction: any (dst, t) group over beta senders is
    split into `beta-1` direct edges plus one collector hop, reusing an
    existing overflow sender as the intermediate (no synthetic nodes)."""
    by_group: dict[tuple[str, int], list[str]] = {}
    for src, dst, t in edges:
        by_group.setdefault((dst, t), []).append(src)
    over = {k: v for k, v in by_group.items() if len(v) > beta}
    if not over:
        return edges, T, False

    new_edges = [e for e in edges if (e[1], e[2]) not in over]
    for (dst, t), srcs in over.items():
        srcs = sorted(set(srcs))
        keep = max(beta - 1, 0)
        direct, overflow = srcs[:keep], srcs[keep:]
        for s in direct:
            new_edges.append((s, dst, t))
        if overflow:
            collector = overflow[0]
            for s in overflow[1:]:
                new_edges.append((s, collector, t))
            new_edges.append((collector, dst, t + 1))
            T = max(T, t + 1)
    return new_edges, T, True


def _cap_fan_in(edges: list[tuple[str, str, int]], T: int, beta: int,
                max_iters: int = 8) -> tuple[list[tuple[str, str, int]], int]:
    for _ in range(max_iters):
        edges, T, changed = _cap_fan_in_once(edges, T, beta)
        if not changed:
            break
    return edges, T


def _clamp_rounds(edges: list[tuple[str, str, int]], T: int, t_max: int) -> tuple[list[tuple[str, str, int]], int]:
    """Merge any round beyond t_max into the final allowed round, deduping
    edges that collide as a result."""
    if T <= t_max:
        return edges, T
    seen: set[tuple[str, str, int]] = set()
    new_edges: list[tuple[str, str, int]] = []
    for src, dst, t in edges:
        key = (src, dst, min(t, t_max))
        if key not in seen:
            seen.add(key)
            new_edges.append(key)
    return new_edges, t_max


def _fallback_graph(subtasks: list[Subtask], lims: dict) -> TemporalDAG:
    """Deterministic temporal graph derived from the static TaskDAG's Kahn
    layers — the old hybrid topology re-expressed temporally. Used only when
    the LLM planner can't produce a feasible graph after one repair attempt."""
    static = build_static_dag(list(subtasks))
    node_ids = [s.id for s in subtasks]
    layer_of = {nid: li for li, layer in enumerate(static.layers) for nid in layer}

    edges: list[tuple[str, str, int]] = [(u, v, layer_of[v]) for u, v, _c in static.E]
    T = max(len(static.layers) - 1, 1)

    successors = {u for u, _v, _t in edges}
    sinks = [n for n in node_ids if n not in successors]

    if len(sinks) == 1:
        holder = sinks[0]
    else:
        final_layer = static.layers[-1] if static.layers else node_ids
        holder = sorted(final_layer)[-1]
        extra_t = T + 1
        for n in sorted(final_layer):
            if n != holder:
                edges.append((n, holder, extra_t))
        T = extra_t

    edges, T = _cap_fan_in(edges, T, lims["beta"])
    edges, T = _clamp_rounds(edges, T, lims["t_max"])
    edges, T = _cap_fan_in(edges, T, lims["beta"])  # clamping can reintroduce fan-in violations

    return TemporalDAG(nodes=node_ids, T=T, edges=edges, holder=holder,
                       rationale="fallback: static-DAG layers")


# ── The planner call ─────────────────────────────────────────────────────────

@dataclass
class PlanDecision:
    graph: TemporalDAG
    source: str          # "planner" | "planner_repaired" | "fallback"
    motifs: dict          # tgraph.motifs(graph), computed once here
    spec: dict | None     # the raw accepted JSON (evidence for Phase 4)
    candidates_info: dict | None = None   # {"candidates": k, "candidate_rank_scores": [...]}
                                          # set only when planner_candidates > 1 (Phase 5)


# ── Multi-candidate generation (Phase 5, Eq. 14 — off by default) ──────────
# orchestration.planner_candidates in config, default 1 = exactly today's
# single-shot behavior. k > 1 generates k candidates (2..k nudged toward a
# structurally different design from the last accepted one) and ranks valid
# ones by the skill bank's motif table instead of just taking the first hit.

_DIVERSITY_SUFFIX = (
    "\n\nA previous candidate design for this same task:\n{prev}\n\n"
    "Propose a structurally DIFFERENT design from the above — different round "
    "count, reduction shape, or holder placement. Same JSON format, same limits."
)


async def _generate_one(task: str, subtasks: list[Subtask], lims: dict, skills_ctx: str,
                        prev_specs: list[dict], node_ids: list[str]):
    """One planner call. prev_specs non-empty -> diversity nudge appended
    (candidates 2..k of a multi-candidate round). Returns
    (raw_text, spec_or_None, graph_or_None, violations)."""
    from anet.QweenBee.stage_models import stage_call, extract_json
    messages = build_prompt(task, subtasks, lims, skills_ctx)
    if prev_specs:
        messages[-1] = dict(messages[-1])
        messages[-1]["content"] += _DIVERSITY_SUFFIX.format(prev=json.dumps(prev_specs[-1]))
    try:
        text = await stage_call("planner", messages, max_tokens=1200)
        spec = extract_json(text)
        g = parse_plan(spec, node_ids)
        violations = tgraph.validate(g, limits=lims)
        if violations:
            return text, spec, None, violations
        return text, spec, g, []
    except Exception as exc:
        return "", None, None, [str(exc)]


def _rank_candidate(g: TemporalDAG, table: dict) -> float | None:
    """Eq. 14: weighted-average risk_loss over the candidate's motif features
    that appear in the skill bank's motif table. None if it shares zero known
    features — such a candidate is NOT fabricated into false confidence; it
    just sorts after every scored candidate."""
    m = tgraph.motifs(g)
    matched = [table[key] for k, v in m.items() if (key := f"{k}={v}") in table]
    total_n = sum(e["n"] for e in matched)
    if not matched or total_n == 0:
        return None
    return sum(e["n"] * e["risk_loss"] for e in matched) / total_n


async def plan(task: str, subtasks: list[Subtask], *, skills_ctx: str = "") -> PlanDecision:
    """Run the planner: generate a temporal DAG, validate, repair once on
    failure, and fall back to a deterministic graph if the model still can't
    produce a feasible one. Never raises — the live turn must always get back
    a usable graph.

    When orchestration.planner_candidates > 1, generates that many candidates
    first and ranks the valid ones by the motif table (Eq. 14); the repair
    attempt only fires if EVERY candidate failed validation."""
    from anet.QweenBee.stage_models import stage_call, extract_json

    node_ids = [s.id for s in subtasks]
    lims = tgraph.limits()
    k = max(int(lims.get("planner_candidates", 1) or 1), 1)

    valid: list[tuple[dict, TemporalDAG]] = []
    prev_specs: list[dict] = []
    last_text, last_spec, last_violations = "", None, ["planner call failed"]

    for _ in range(k):
        text_i, spec_i, g_i, violations_i = await _generate_one(
            task, subtasks, lims, skills_ctx, prev_specs, node_ids)
        last_text, last_spec, last_violations = text_i, spec_i, violations_i
        if g_i is not None:
            valid.append((spec_i, g_i))
            prev_specs.append(spec_i)

    if valid:
        if k <= 1:
            spec, g = valid[0]
            return PlanDecision(graph=g, source="planner", motifs=tgraph.motifs(g), spec=spec)

        from anet.QweenBee import skills as _skills
        table = _skills.motif_table()
        scored = [(_rank_candidate(g, table), i, spec, g) for i, (spec, g) in enumerate(valid)]
        scored.sort(key=lambda t: (t[0] is None, t[0] if t[0] is not None else 0.0, t[1]))
        _best_score, _best_i, best_spec, best_g = scored[0]
        return PlanDecision(
            graph=best_g, source="planner", motifs=tgraph.motifs(best_g), spec=best_spec,
            candidates_info={"candidates": k, "candidate_rank_scores": [s for s, _, _, _ in scored]},
        )

    text, spec, violations = last_text, last_spec, last_violations

    # ── One repair attempt (only when NO candidate validated) ──────────────
    try:
        messages = build_prompt(task, subtasks, lims, skills_ctx)
        repair_messages = messages + [
            {"role": "assistant", "content": text or json.dumps(spec or {})},
            {"role": "user", "content": (
                "Your graph violates:\n" + "\n".join(f"- {v}" for v in violations) +
                "\n\nReturn the corrected JSON only, following the same format."
            )},
        ]
        text2 = await stage_call("planner", repair_messages, max_tokens=1200)
        spec2 = extract_json(text2)
        g2 = parse_plan(spec2, node_ids)
        violations2 = tgraph.validate(g2, limits=lims)
        if not violations2:
            return PlanDecision(graph=g2, source="planner_repaired", motifs=tgraph.motifs(g2), spec=spec2)
        spec = spec2
    except Exception:
        pass

    fb = _fallback_graph(subtasks, lims)
    return PlanDecision(graph=fb, source="fallback", motifs=tgraph.motifs(fb), spec=spec)
