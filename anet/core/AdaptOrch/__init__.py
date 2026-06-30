"""
anet.core.AdaptOrch — the AdaptOrch coordinator.

Wires the five phases (decompose → DAG → route → execute → synthesize) into a
run_turn. AdaptOrchEngine is currently the sole orchestration engine; it
inherits from anet.core.engine_base.BaseEngine for the shared infra (per-thread
state, rolling-summary maintenance, persistence).
"""
