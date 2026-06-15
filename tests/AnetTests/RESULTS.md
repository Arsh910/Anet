# AnetTests — Results Tracker

Fill this in as you run each tier. Publish it with the repo so visitors see exactly
what was verified. Link recordings in the **Clip** column.

Legend: ✅ pass · ⚠️ partial · ❌ fail · ⬜ not run · N/A not applicable

Environment for this run:
- Date: `____`
- OS: Windows 11
- Providers configured: `____` (e.g. openrouter)
- Optional: codegraph `[ ]`  playwright `[ ]`  Telegram `[ ]`

---

## Baseline (offline tool units)
`python tests/AnetToolTests/run_all.py` → **20/20 passed** (2026-06-15) ✅

## Tier 01 — Simple
| ID | Status | Notes | Clip |
|----|--------|-------|------|
| S1 | ⬜ | | |
| S2 | ⬜ | | |
| S3 | ⬜ | | |
| S4 | ⬜ | | |
| S5 | ⬜ | | |
| S6 | ⬜ | | |

## Tier 02 — Agents
| ID | Status | Notes | Clip |
|----|--------|-------|------|
| A1 | ⬜ | | |
| A2 | ⬜ | | |
| A3 | ⬜ | | |
| A4 | ⬜ | | |
| A5 | ⬜ | | |
| A6 | ⬜ | | |
| C5 | ⬜ | computer_agent (Windows) | |

## Tier 03 — Tools
| ID | Status | Notes | Clip |
|----|--------|-------|------|
| T1 | ⬜ | | |
| T2 | ⬜ | | |
| T3 | ⬜ | | |
| T4 | ⬜ | | |
| T5 | ⬜ | | |
| T6 | ⬜ | | |
| T7 | ⬜ | | |
| T8 | ⬜ | | |
| T9 | ⬜ | | |
| T10 | ⬜ | | |

## Tier 04 — Memory & Skills
| ID | Status | Notes | Clip |
|----|--------|-------|------|
| M1 | ⬜ | | |
| M2 | ⬜ | | |
| M3 | ⬜ | | |
| M4 | ⬜ | | |
| M5 | ⬜ | profile build | |
| M6 | ⬜ | cross-session | |
| M7 | ⬜ | 10-turn nudge | |
| M8 | ⬜ | skill creation | |
| M9 | ⬜ | skill injection | |
| M10 | ⬜ | curator | |

## Tier 05 — Orchestration & Spawn
| ID | Status | Notes | Clip |
|----|--------|-------|------|
| O1 | ⬜ | | |
| O2 | ⬜ | parallel | |
| O3 | ⬜ | spawn | |
| O4 | ⬜ | depth limit | |
| O5 | ⬜ | checker retry | |
| O6 | ⬜ | | |

## Tier 06 — MCP & External
| ID | Status | Notes | Clip |
|----|--------|-------|------|
| X1 | ⬜ | codegraph | |
| X2 | ⬜ | codegraph | |
| X3 | ⬜ | codegraph | |
| X4 | ⬜ | playwright | |
| X5 | ⬜ | playwright | |
| X6 | ⬜ | telegram | |
| X7 | ⬜ | telegram | |
| X8 | ⬜ | registration round-trip | |
| X9 | ⬜ | /newtool generator | |
| X10 | ⬜ | validator standalone | |
| X11 | ⬜ | /mcptest doctor | |
| X12 | ⬜ | /addmcp agent | |

## Tier 07 — Safety, Context, Sessions
| ID | Status | Notes | Clip |
|----|--------|-------|------|
| G1 | ⬜ | | |
| G2 | ⬜ | | |
| G3 | ⬜ | | |
| G8 | ⬜ | downloads gated | |
| G9 | ⬜ | ESC stops task | |
| G4 | ⬜ | | |
| G7 | ⬜ | p = redirect path | |
| G5 | ⬜ | cycle detect | |
| G6 | ⬜ | step cap | |
| C1 | ⬜ | /forget | |
| C2 | ⬜ | /compress | |
| C3 | ⬜ | auto prompt | |
| C4 | ⬜ | sessions | |
| C5 | ⬜ | /sessions | |
| C6 | ⬜ | --resume | |

## Tier 08 — Complex Workflows
| ID | Status | Notes | Clip |
|----|--------|-------|------|
| W1 | ⬜ | | |
| W2 | ⬜ | | |
| W3 | ⬜ | telegram | |
| W4 | ⬜ | codegraph | |
| W5 | ⬜ | parallel | |
| W6 | ⬜ | memory-aware | |
| W7 | ⬜ | signature demo | |

---

## Known issues / follow-ups
_(record anything that fails or surprises you here — this is the honest log that
makes the repo credible)_

- 
