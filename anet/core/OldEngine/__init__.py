"""
OldEngine — the legacy single-topology orchestration engine.

engine.py here is the original planner → executor → checker → synthesizer
coordinator. It is being superseded by the AdaptOrch pipeline (anet/core/dag,
decomposer, router, executors, synthesizer) but stays in service until the
integration lands behind the orchestration.mode flag. Kept in its own folder to
keep anet/core/ clean while both paths coexist.

Note: the per-agent agentic loop (anet/core/orchestrator.py) is NOT part of the old
engine — it's the shared agent runtime used by spawn_tool today and by the new
AdaptOrch executors, so it stays in anet/core/.
"""
