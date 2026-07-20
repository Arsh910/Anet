"""
evidence.py — the QweenBee run log (replaces AdaptOrch's router.log_route).

Every turn appends one JSON line to <home>/orchestration/queenbee_evidence.jsonl:
the generated graph's shape (source, motifs, canonical hash) plus its execution
outcome (consistency, executions, revisions, failures, tokens). This is the
evidence stream Phase 4's skill bank distills into Preserve/Modify/Avoid rules —
named queenbee_evidence.jsonl from the start so Phase 4 never has to rename it.

Best-effort: never raises. A bad log write must not break a turn.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from anet.QweenBee import tgraph
from anet.QweenBee.tgraph import TemporalDAG


def log_run(*, graph: TemporalDAG, source: str, motifs: dict,
           request_class: str | None, spec: dict | None, outcome: dict | None,
           log_path=None) -> None:
    """Append one evidence row for a completed turn."""
    try:
        if log_path is None:
            from anet.core import paths
            d = paths.home() / "orchestration"
            d.mkdir(parents=True, exist_ok=True)
            log_path = d / "queenbee_evidence.jsonl"
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": uuid.uuid4().hex[:12],
            "source": source,
            "graph_hash": tgraph.canonical_hash(graph),
            "T": graph.T,
            "n_nodes": len(graph.nodes),
            "n_edges": len(graph.edges),
            "holder": graph.holder,
            "motifs": motifs,
            "request_class": request_class,
            "spec": spec,
        }
        if outcome:
            rec["outcome"] = outcome
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass
