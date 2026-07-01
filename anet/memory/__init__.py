"""
anet.memory — long-term memory implementations for ANet.

Contains the native RecMem package (a recurrence-based 3-tier memory:
subconscious → episodic → semantic) and the provider adapters that let it run on
the same on-device infra ANet already ships (fastembed + chromadb + the user's
configured LLM). The memory *backend selection* (mem0 vs recmem) lives in
`anet.core.memory_store`; this package is just the RecMem engine + adapters.
"""
