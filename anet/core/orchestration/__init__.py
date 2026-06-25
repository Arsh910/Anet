"""
anet.core.orchestration — the AdaptOrch coordinator.

Wires the five phases (decompose → DAG → route → execute → synthesize) into a
run_turn that's a drop-in replacement for the OldEngine. Selected at startup by
`orchestration.mode: adaptorch` in anet.config.yaml; otherwise the OldEngine runs.
"""
