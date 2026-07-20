"""
skills.py — the QweenBee skill bank (QueenBee Planner §3.3, Eq. 8).

Distills anet.QweenBee.evidence's JSONL log into a bank of retrievable design
rules — Preserve (a scaffold that keeps winning), Modify (a Preserve skill
transplanted to an adjacent task size, adapt don't copy), Avoid (a shape that
keeps losing to a well-sampled champion) — and renders the applicable ones as
the `skills_ctx` block the planner prompt already accepts (Phase 2).

Pure statistics, no LLM calls. Anet has no ground truth, so every ranking
decision hangs on one proxy loss (see loss()) computed from what the evidence
log already records: failures, consistency, and token cost. Groups are keyed
by (condition, graph_hash) — canonical_hash (Phase 2's structural-dedup hash)
merges identically-shaped graphs generated under different subtask ids, so
credit doesn't fragment across aliases.

Noise discipline (the paper's anti-drift armor, trimmed to what's runnable
without a held-out bench — that's Phase 5's job): conservative credit
(mean_loss + kappa/sqrt(n)) so a lucky single run can't outrank a well-sampled
shape, a minimum sample count before any skill is deployable, and an
evidence-grounded Avoid that requires an absolute margin over a champion that
is itself well-sampled — never a bare ratio, which would flag anything as bad
whenever the best score is 0.

The bank refreshes itself: retrieve() re-distills whenever the evidence log
has grown past what the cached bank was built from. No CLI, no cron, no gate
— that ungated refresh is safe only because N_MIN + risk_loss already keep a
single lucky run from steering the prompt.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

# ── Proxy loss (Anet has no ground truth — everything ranks by this) ───────
# ponytail: hand-set proxy-loss weights; recalibrate against the Phase 5 bench
# once it exists.
W_FAIL = 1.0
W_CONS = 0.5
LAMBDA_COST = 0.05

N_MIN = 3               # min evidence rows before a skill is deployable
KAPPA = 1.0              # conservative-credit strength (Eq. 13)
AVOID_MARGIN = 0.15      # absolute loss gap required for an Avoid
MAX_SKILLS_IN_PROMPT = 5


def loss(rec: dict) -> float:
    """One lower-is-better score per evidence row (paper's J = L + λC)."""
    out = rec.get("outcome") or {}
    n_nodes = max(rec.get("n_nodes") or 1, 1)
    fail_rate = (out.get("failures") or 0) / n_nodes
    cs = out.get("consistency")
    inconsistency = 1.0 - (cs if cs is not None else 1.0)
    cost = (out.get("tokens") or 0) / 10_000.0
    return W_FAIL * fail_rate + W_CONS * inconsistency + LAMBDA_COST * cost


# ── Paths (same resolution pattern as evidence.log_run / router.log_route) ─

def _evidence_path(path=None) -> Path:
    if path is not None:
        return Path(path)
    from anet.core import paths
    return paths.home() / "orchestration" / "queenbee_evidence.jsonl"


def _bank_path(path=None) -> Path:
    if path is not None:
        return Path(path)
    from anet.core import paths
    return paths.home() / "orchestration" / "queenbee_skills.json"


def _read_rows(evidence_path=None) -> list[dict]:
    p = _evidence_path(evidence_path)
    if not p.exists():
        return []
    rows: list[dict] = []
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return rows


def _evidence_row_count(evidence_path=None) -> int:
    p = _evidence_path(evidence_path)
    if not p.exists():
        return 0
    try:
        with open(p, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0


# ── Bank persistence ─────────────────────────────────────────────────────────

def _empty_bank() -> dict:
    return {"generated_at": None, "evidence_rows": 0, "skills": [], "locked": False}


def load_bank(path=None) -> dict:
    p = _bank_path(path)
    try:
        if not p.exists():
            return _empty_bank()
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("skills"), list):
            return _empty_bank()
        return data
    except Exception:
        return _empty_bank()


def save_bank(bank: dict, path=None) -> None:
    try:
        p = _bank_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(bank, f, indent=2)
    except Exception:
        pass


def lock_state(path=None) -> bool:
    """Whether the bank is under gated evolution (Phase 5). A locked bank only
    changes via evolve.py's held-out gate — retrieve()'s ungated auto-refresh
    stops applying once the first evolve run has happened."""
    return bool(load_bank(path).get("locked"))


# ── Conditions (the trimmed transfer-trust slot: request_class x size) ─────

def _size_bucket(n_nodes: int) -> str:
    if n_nodes <= 3:
        return "small"
    if n_nodes <= 6:
        return "medium"
    return "large"


def _condition(rec: dict) -> tuple[str, str]:
    rc = rec.get("request_class") or "general"
    return (rc, _size_bucket(rec.get("n_nodes") or 0))


# ── Distillation ─────────────────────────────────────────────────────────────

def _latest_spec_row(rows: list[dict]) -> dict | None:
    """A group's most recent row with a real, planner-produced spec — fallback
    rows never carry a usable scaffold (their spec is the last rejected
    attempt, if any), so they're never a Preserve source."""
    candidates = [r for r in rows if r.get("source") != "fallback" and r.get("spec")]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r.get("ts") or "")


def _skill_id(kind: str, condition: dict, key: str) -> str:
    payload = json.dumps({"kind": kind, "condition": condition, "key": key}, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _rule_text_preserve(motifs: dict) -> str:
    return (f"T={motifs.get('steps')} shape, {motifs.get('messages')} message(s), "
           f"max fan-in {motifs.get('max_fan_in')} ({motifs.get('fan_in_bucket')}), "
           f"reduction_depth={motifs.get('reduction_depth')} — best record for this kind of task.")


def _rule_text_avoid(motifs: dict) -> str:
    return (f"{motifs.get('steps')}-round shape, fan-in bucket '{motifs.get('fan_in_bucket')}' "
           f"(max {motifs.get('max_fan_in')}) — repeatedly underperformed for this kind of task.")


def _build_preserve_skill(group: dict, condition: dict) -> dict:
    row = _latest_spec_row(group["rows"])
    motifs = (row.get("motifs") if row else None) or {}
    evidence = [r.get("run_id") for r in group["rows"] if r.get("run_id")][-10:]
    return {
        "id": _skill_id("scaffold", condition, group["graph_hash"]),
        "kind": "scaffold",
        "action": "preserve",
        "condition": condition,
        "rule": _rule_text_preserve(motifs),
        "graph_spec": row.get("spec") if row else None,
        "graph_hash": group["graph_hash"],
        "motifs": motifs,
        "stats": {"n": group["n"], "mean_loss": round(group["mean_loss"], 4),
                  "risk_loss": round(group["risk_loss"], 4)},
        "evidence": evidence,
    }


def _build_avoid_skill(group: dict, condition: dict) -> dict:
    motifs = (group["rows"][-1].get("motifs")) or {}
    evidence = [r.get("run_id") for r in group["rows"] if r.get("run_id")][-10:]
    return {
        "id": _skill_id("avoid_shape", condition, group["graph_hash"]),
        "kind": "avoid_shape",
        "action": "avoid",
        "condition": condition,
        "rule": _rule_text_avoid(motifs),
        "graph_spec": None,
        "graph_hash": group["graph_hash"],
        "motifs": motifs,
        "stats": {"n": group["n"], "mean_loss": round(group["mean_loss"], 4),
                  "risk_loss": round(group["risk_loss"], 4)},
        "evidence": evidence,
    }


def distill(evidence_path=None) -> list[dict]:
    """Read the evidence log, group by (condition, graph_hash), and emit
    Preserve/Avoid skills that clear the sample-count + margin bars."""
    rows = [r for r in _read_rows(evidence_path)
            if (r.get("n_nodes") or 0) > 1 and r.get("graph_hash")]
    if not rows:
        return []

    groups: dict[tuple, dict] = {}
    for r in rows:
        key = (_condition(r), r["graph_hash"])
        g = groups.setdefault(key, {"rows": [], "condition": key[0], "graph_hash": key[1]})
        g["rows"].append(r)

    for g in groups.values():
        losses = [loss(r) for r in g["rows"]]
        n = len(losses)
        g["n"] = n
        g["mean_loss"] = sum(losses) / n
        g["risk_loss"] = g["mean_loss"] + KAPPA / math.sqrt(n)

    by_condition: dict[tuple, list[dict]] = {}
    for g in groups.values():
        by_condition.setdefault(g["condition"], []).append(g)

    skills: list[dict] = []
    for cond, glist in by_condition.items():
        condition = {"request_class": cond[0], "size_bucket": cond[1]}

        preserve_pool = [g for g in glist if g["n"] >= N_MIN and _latest_spec_row(g["rows"])]
        if preserve_pool:
            best = min(preserve_pool, key=lambda g: g["risk_loss"])
            skills.append(_build_preserve_skill(best, condition))

        eligible = [g for g in glist if g["n"] >= N_MIN]
        if eligible:
            champion = min(eligible, key=lambda g: g["mean_loss"])
            for g in eligible:
                if g is champion:
                    continue
                if g["mean_loss"] >= champion["mean_loss"] + AVOID_MARGIN:
                    skills.append(_build_avoid_skill(g, condition))

    return skills


def motif_table(evidence_path=None) -> dict[str, dict]:
    """Per-feature (not per-shape) credit — Eq. 12-13. A brand-new graph can
    share individual φ(G) features with history even when its exact shape has
    never been seen. Fallback rows are included here (unlike distill()'s
    Preserve pool) since a feature's track record is still real evidence
    regardless of how the graph that carried it was produced."""
    rows = [r for r in _read_rows(evidence_path) if (r.get("n_nodes") or 0) > 1]
    buckets: dict[str, list[float]] = {}
    for r in rows:
        motifs = r.get("motifs") or {}
        row_loss = loss(r)
        for k, v in motifs.items():
            buckets.setdefault(f"{k}={v}", []).append(row_loss)

    table: dict[str, dict] = {}
    for key, losses in buckets.items():
        n = len(losses)
        mean_loss = sum(losses) / n
        table[key] = {"n": n, "mean_loss": round(mean_loss, 4),
                     "risk_loss": round(mean_loss + KAPPA / math.sqrt(n), 4)}
    return table


# ── Retrieval (the skills_ctx the planner prompt renders verbatim) ─────────

def _render_preserve(sk: dict) -> str:
    spec = json.dumps(sk["graph_spec"]) if sk.get("graph_spec") else "{}"
    return (f"- PRESERVE (n={sk['stats']['n']}, loss={sk['stats']['mean_loss']:.2f}): "
           f"{sk['rule']} Scaffold: {spec}")


def _render_modify(sk: dict) -> str:
    spec = json.dumps(sk["graph_spec"]) if sk.get("graph_spec") else "{}"
    return (f"- MODIFY (n={sk['stats']['n']}, loss={sk['stats']['mean_loss']:.2f}): "
           f"{sk['rule']} Adapt this scaffold to the current task, don't copy verbatim. "
           f"Reference: {spec}")


def _render_avoid(sk: dict) -> str:
    return f"- AVOID (n={sk['stats']['n']}, loss={sk['stats']['mean_loss']:.2f}): {sk['rule']}"


def _maybe_refresh(bank_path=None, evidence_path=None) -> dict:
    bank = load_bank(bank_path)
    if bank.get("locked"):
        # Gated mode (Phase 5): only evolve.py's held-out gate may update the
        # bank from here on — retrieve() just reads whatever it last accepted.
        return bank
    current_rows = _evidence_row_count(evidence_path)
    if current_rows <= (bank.get("evidence_rows") or 0):
        return bank
    skills = distill(evidence_path)
    bank = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence_rows": current_rows,
        "skills": skills,
    }
    save_bank(bank, bank_path)
    return bank


def retrieve(request_class: str, n_nodes: int, *, bank_path=None, evidence_path=None) -> str:
    """Return the skills_ctx block for this task's condition, or "" if the
    bank has nothing applicable (cold generation — today's behavior)."""
    try:
        bank = _maybe_refresh(bank_path, evidence_path)
    except Exception:
        return ""

    rc = request_class or "general"
    size_bucket = _size_bucket(n_nodes)

    exact_preserves, adjacent_preserves, avoids = [], [], []
    for sk in bank.get("skills") or []:
        cond = sk.get("condition") or {}
        if cond.get("request_class") != rc:
            continue
        if sk.get("kind") == "avoid_shape":
            avoids.append(sk)
        elif cond.get("size_bucket") == size_bucket:
            exact_preserves.append(sk)
        else:
            adjacent_preserves.append(sk)

    exact_preserves.sort(key=lambda s: s["stats"]["risk_loss"])
    adjacent_preserves.sort(key=lambda s: s["stats"]["risk_loss"])
    avoids.sort(key=lambda s: s["stats"]["risk_loss"])

    lines: list[str] = []
    budget = MAX_SKILLS_IN_PROMPT
    for sk in exact_preserves:
        if budget <= 0:
            break
        lines.append(_render_preserve(sk)); budget -= 1
    for sk in adjacent_preserves:
        if budget <= 0:
            break
        lines.append(_render_modify(sk)); budget -= 1
    for sk in avoids:
        if budget <= 0:
            break
        lines.append(_render_avoid(sk)); budget -= 1

    return "\n".join(lines)
