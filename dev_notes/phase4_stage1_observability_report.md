# Phase 4 — Stage 1 L1-L4 Distribution Observability Report

**Date:** 2026-04-27
**Commits:**
- (single commit) `phase4(quality): extend STRAT_L1/L2/L3/L4 with distribution metrics`

## Bug summary

Stage 1's 4 internal layers (L1 Strategy Scanner, L2 Trade Scorer, L3 Ensemble Voter, L4 Hand-off) already emit count + elapsed events. Distribution metrics needed to detect degeneracy were missing. If any layer produces a degenerate distribution (all 0, all one bucket), operators cannot tell from logs.

## Fix summary

Added 4 new `*_DONE` / `_HANDOFF` events ADDITIVELY (existing tags preserved for back-compat):

| Tag | New fields |
|---|---|
| `STRAT_L1_DONE` | per_strategy_avg, top_firing[name:count×5], non_firing[name×5] |
| `STRAT_L2_DONE` | score_p25/p50/p75/p95, score_components_avg[base,confluence,context,quality] |
| `STRAT_L3_DONE` | consensus_dist[STRONG:N,GOOD:N,WEAK:N,LEAN:N,CONFLICT:N], size_mult_avg |
| `STRAT_L4_HANDOFF` | score_cache_size, consensus_size, consensus_summary_size, hints_top20_size |

## Verification — automated

```
pytest 105 passed (signal multi-source, xray diagnose, state_sync,
                    persistence, worker_liveness x2, corrected_layer1,
                    universe, logging_routing)
```

## Verification — operator-driven (post-deploy, 25 min = 5 cycles)

| # | Trial | Pass criterion |
|---|---|---|
| 4.1 | All 4 events emit per cycle | 5×4 = 20 emits over 5 cycles |
| 4.2 | L1 distribution non-degenerate | top_firing != non_firing; per-strategy fire rates vary |
| 4.3 | L2 distribution has spread | score_p25 < score_p75; not all 0, not all 100 |
| 4.4 | L3 distribution has spread | ≥3 of 5 consensus categories represented |
| 4.5 | L4 cache sizes match expected | consensus_size ≈ 50 (full universe), summary_size ≤ filtered count |
| 4.6 | Latency unchanged | _section_ms[l1/l2/l3/l4] within 5% of pre-fix baseline |
