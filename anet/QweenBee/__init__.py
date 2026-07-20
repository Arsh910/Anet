"""
anet.QweenBee — the QweenBee coordinator.

Wires the five phases (decompose → DAG → route → execute → synthesize) into a
run_turn. QweenBeeEngine is a second, selectable orchestration engine (see
orchestration.engine in anet.config.yaml) forked from AdaptOrch for A/B
comparison; it inherits from anet.core.engine_base.BaseEngine for the shared
infra (per-thread state, rolling-summary maintenance, persistence).
"""
