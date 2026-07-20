"""
anet.QweenBee.synthesizer — consistency scoring only.

The five-operator merge/arbitrate dispatcher is gone: the temporal executor's
generated graph does the merging (whichever node the planner made the holder
already has the combined answer). What survives is consistency_score — an
informational signal logged with each turn's evidence, not a merge gate.
"""
from __future__ import annotations

from anet.QweenBee.synthesizer.consistency import consistency_score

__all__ = ["consistency_score"]
