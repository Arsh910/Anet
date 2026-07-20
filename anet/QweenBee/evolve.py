"""
evolve.py — the held-out acceptance gate (QueenBee Planner §3.5A, Eq. 15).

Phase 4's skill bank auto-refreshes on every turn — safe only because
conservative credit and N_MIN already keep single lucky runs out of policy.
Gated evolution goes further: a candidate bank must beat the CURRENT bank on
HELD-OUT tasks (the bench suite's val split) before it's admitted. Both val
passes run the exact same tasks through the exact same frozen qweenbee
engine — the only variable is which skill bank was active, which is the
paper's causal-claim setup (§3.1): if the candidate wins, the improvement
came from the design rules, not from luck or a different task mix.

    python -m anet.QweenBee.evolve

Costs 2 x |val split| qweenbee LLM turns per run — this only spends money on
the gate itself; ordinary usage and `bench --split train` are what accumulate
the evidence being gated. A rejected candidate is quarantined, not discarded,
so a human can see what didn't clear the bar and why.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from anet.QweenBee import skills

EPSILON = 0.02


def _quarantine_path(path=None) -> Path:
    if path is not None:
        return Path(path)
    from anet.core import paths
    return paths.home() / "orchestration" / "queenbee_skills_quarantine.json"


def accept(j_old: float, j_new: float, epsilon: float = EPSILON) -> bool:
    """Eq. 15: the candidate must beat the incumbent by an absolute margin —
    a wash (or a worse score) is not evolution."""
    return j_new <= j_old - epsilon


def _skills_signature(skl: list[dict]) -> list:
    """Order-independent comparison key: (id, stats) per skill. Ignores the
    'evidence' run-id list's exact contents — only stats determine behavior."""
    return sorted((s.get("id"), json.dumps(s.get("stats"), sort_keys=True)) for s in skl)


def _mean_loss(rows: list[dict]) -> float:
    return (sum(skills.loss(r) for r in rows) / len(rows)) if rows else 0.0


async def evolve(*, suite_path=None, bank_path=None, evidence_path=None,
                 quarantine_path=None, epsilon: float = EPSILON) -> dict:
    """Run the gate once. Returns a summary dict (also what the CLI prints)."""
    from anet.QweenBee.bench import (
        build_agents_and_tools, make_engines, load_suite, run_suite,
        _qweenbee_evidence_path, _count_lines, _rows_since,
    )

    ev_path = Path(evidence_path) if evidence_path else _qweenbee_evidence_path()

    S = skills.load_bank(bank_path)
    candidate_skills = skills.distill(evidence_path)

    if _skills_signature(candidate_skills) == _skills_signature(S.get("skills") or []):
        print("no new evidence to evolve on")
        return {"status": "no_op"}

    S_prime = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence_rows": skills._evidence_row_count(evidence_path),
        "skills": candidate_skills,
        "locked": True,
    }

    val_tasks = load_suite(suite_path, split="val")
    if not val_tasks:
        print("no val-split tasks in the suite — cannot gate")
        return {"status": "no_val_tasks"}

    agents, tools = build_agents_and_tools()
    engine = make_engines(["qweenbee"], agents, tools)["qweenbee"]
    print(f"Evolving on {len(val_tasks)} held-out task(s), epsilon={epsilon}")

    # ── Pass A: current bank S (locked so mid-pass evidence can't leak in) ──
    S_locked = dict(S)
    S_locked["locked"] = True
    skills.save_bank(S_locked, bank_path)
    start_a = _count_lines(ev_path)
    print("\n-- pass A: current bank --")
    await run_suite("qweenbee", engine, val_tasks)
    j_old = _mean_loss(_rows_since(ev_path, start_a))

    # ── Pass B: candidate bank S' ────────────────────────────────────────────
    skills.save_bank(S_prime, bank_path)
    start_b = _count_lines(ev_path)
    print("\n-- pass B: candidate bank --")
    await run_suite("qweenbee", engine, val_tasks)
    j_new = _mean_loss(_rows_since(ev_path, start_b))

    accepted = accept(j_old, j_new, epsilon)
    print(f"\nJ(S)={j_old:.4f}  J(S')={j_new:.4f}  epsilon={epsilon}  -> "
         f"{'ACCEPT' if accepted else 'REJECT'}")

    if accepted:
        skills.save_bank(S_prime, bank_path)   # already active; explicit for clarity
        return {"status": "accepted", "j_old": j_old, "j_new": j_new}

    skills.save_bank(S_locked, bank_path)   # restore S
    quarantine = {
        "rejected_at": datetime.now(timezone.utc).isoformat(),
        "j_old": j_old, "j_new": j_new, "epsilon": epsilon,
        "bank": S_prime,
    }
    try:
        qp = _quarantine_path(quarantine_path)
        qp.parent.mkdir(parents=True, exist_ok=True)
        with open(qp, "w", encoding="utf-8") as f:
            json.dump(quarantine, f, indent=2)
    except Exception:
        pass
    return {"status": "rejected", "j_old": j_old, "j_new": j_new}


def main() -> None:
    import asyncio
    asyncio.run(evolve())


if __name__ == "__main__":
    main()
