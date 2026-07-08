# Phase 0 — Quality Issue 4: Stage 1 Internal Layer (L1-L4) Observability + Verification

## A — Current observed behaviour

StrategyWorker hosts 4 internal layers per `src/workers/strategy_worker.py:98-751`:

| Layer | Lines | Output | Currently logged |
|---|---|---|---|
| **L1 Strategy Scanner** | 432-485 | `raw_signals: list[RawSignal]` from 39 strategies × 50 coins | `STRAT_L1 | signals=N strategies=39 coins=50 el=Xms` (line 485) — NO per-strategy fire rate, NO non-firing list |
| **L2 Trade Scorer** | 495-536 | `scored: list[ScoredSetup]` (4-component scoring 0-105) | `STRAT_L2 | scored=N best=X grade=A+ el=Yms` — NO score percentile distribution, NO component breakdown average |
| **L3 Ensemble Voter** | 538-559 | `consensus_setups: list[EnsembleResult]` (STRONG/GOOD/WEAK/LEAN/CONFLICT) | `STRAT_L3 | consensus=N top_strength=X el=Zms` — NO per-category count, NO size_mult avg, NO vote count |
| **L4 Hand-off** | 561-690 | 3 caches: `_strategy_consensus`, `_strategy_consensus_summary`, `_strategy_hints` | `STRAT_L4 | hints=N filtered_from=M el=Wms` — NO per-cache size logs |

**Sample current emit:**
```
STRAT_L1 | signals=25 strategies=39 coins=50 el=37ms
STRAT_L2 | scored=25 best=82.42 grade=A+ el=98ms
STRAT_L3 | consensus=25 top_strength=82.4 el=51ms
STRAT_L4 | hints=20 filtered_from=22 el=2ms
STRAT_CYCLE_DONE | sections={gate:1,prefetch:142,l1:37,l2:98,l3:51,l4:2,misc:5} drift_ms=12
```

If L1 produces 0 signals, or L2 scores everything 0, or L3 consensus is 100% CONFLICT, the existing logs show only count + elapsed. **Operators cannot tell which layer is degenerate.**

## B — Expected behaviour

Per cycle (every 5 min):
- `STRAT_L1_DONE`: per-strategy fire-rate distribution; top 5 firing strategies + bottom 5 non-firing
- `STRAT_L2_DONE`: score percentiles (p25/p50/p75/p95); component averages [base, confluence, context, quality]
- `STRAT_L3_DONE`: consensus distribution `[STRONG:N, GOOD:N, WEAK:N, LEAN:N, CONFLICT:N]`; size_mult average; vote count p50
- `STRAT_L4_HANDOFF`: cache sizes (`_score_cache`, `_strategy_consensus`, `_strategy_consensus_summary`, `_strategy_hints`)

Healthy distributions:
- L1: 39 strategies × ~50 coins → 200-1500 raw_signals expected; per-strategy fire rate non-uniform (some hot, some cold)
- L2: score distribution should have spread (p25 < p75); not all 0, not all 100
- L3: consensus distribution bell-curve-ish: ~10% STRONG, ~30% GOOD, ~30% WEAK, ~20% LEAN, ~10% CONFLICT
- L4: cache sizes match expected (~50 for full, post-filter for summary)

## C — Root cause

This is **PURE observability gap**, not a behavioural issue. The 4 internal layers are correctly implemented. They emit count + elapsed, but the distribution metrics needed to detect degeneracy are missing.

The fix is to extend the existing emit statements with the distribution fields — no logic changes.

## D — Verification approach (post-fix)

| Metric | Measure | Target |
|---|---|---|
| All 4 events emit per cycle | grep workers.log for 5 cycles | 5 × 4 = 20 emits |
| L1 distribution non-degenerate | top_firing != bottom_firing | per-strategy fire rates vary |
| L2 distribution has spread | p25 < p75 | not all 0, not all 100 |
| L3 distribution has spread | not 100% one category | at least 3 categories represented |
| L4 cache sizes match | `_strategy_consensus` size ≈ 50 | post-Phase-4-of-prior-fix invariant |
| Latency unchanged | `_section_ms[l1/l2/l3/l4]` within 5% of baseline | new distribution computation cheap |

## E — Rollback path

Phase 4 changes are additive log fields only. Existing log shape preserved (back-compat for any downstream parsers). If logs prove too verbose, drop the per-cache size fields. Rollback: `git revert <phase4-commits>`.

## Files end-to-end mapped

| File | Lines | Role |
|---|---|---|
| `src/workers/strategy_worker.py` | 98-751 (tick), **432-485 (L1), 495-536 (L2), 538-559 (L3), 561-690 (L4)** | All 4 layers; fix target = extend the 4 emits |
| `src/strategies/scorer.py` | 1-468 (TradeScorer), **85-110 (score_batch)** | Score generation; expose batch breakdown if needed |
| `src/strategies/ensemble.py` | 1-163 (EnsembleVoter), **137-162 (vote_batch)** | Consensus voting; expose batch distribution |
| `src/strategies/registry.py` | 1-134 (StrategyRegistry) | 39 strategies registered via `register_all.py` |
| `src/strategies/register_all.py` | 1-132 | A1-K4 (39 strategies) + X1 (testnet only) |

## Phase 4 fix outline (preview)

1-3 atomic commits:
1. Extend `STRAT_L1_DONE` emit with per-strategy fire-rate distribution. Accumulate during the L1 loop in `strategy_worker.py:432-485`.
2. Extend `STRAT_L2_DONE` with score percentiles + component averages. Compute from `scored: list[ScoredSetup]` after `scorer.score_batch()`.
3. Extend `STRAT_L3_DONE` with consensus distribution + size_mult avg + vote count p50. Extend `STRAT_L4_HANDOFF` with the 4 cache sizes.
