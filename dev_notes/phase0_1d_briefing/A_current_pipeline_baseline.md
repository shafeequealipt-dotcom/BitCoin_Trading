# Phase 0.A — Current Pipeline Baseline (pre-rewrite)

**Date:** 2026-05-01
**HEAD commit:** `4223910 xray-counter: real-project end-to-end pipeline verification`
**Tag:** `pre-1d-briefing`

## Live evidence — cycle starvation pattern

Recent 6 cycles (2026-05-01 00:00 → 00:35 UTC):

| Cycle | qualified | packages | fail_setup_none | fail_consensus | fail_regime | fail_rr | fail_blockers | brain outcome |
|---|---|---|---|---|---|---|---|---|
| `c-2026-05-01-00:00` | 2 | 2 | 27 | 7 | 4 | 8 | 2 | trade ok |
| `c-2026-05-01-00:15` | 2 | 2 | 16 | 28 | 1 | 3 | 0 | trade ok |
| `c-2026-05-01-00:20` | 2 | 2 | 16 | 27 | 1 | 4 | 0 | trade ok |
| `c-2026-05-01-00:25` | 3 | 3 | 16 | 23 | 3 | 5 | 0 | trade ok |
| `c-2026-05-01-00:30` | 3 | 3 | 16 | 20 | 3 | 8 | 0 | trade ok |
| `c-2026-05-01-00:35` | **1** | **1** | 16 | 19 | 3 | 8 | **3** | **BRAIN_INSUFFICIENT_QUALITY (1<3)** |
| `c-2026-05-01-00:40` | 1 | 1 | 16 | 20 | 3 | 7 | 3 | (next brain cycle) |

Trade dropped at 00:42:15 with `BRAIN_INSUFFICIENT_QUALITY | qualified=1 threshold=3 avg_completeness=1.00 packages=1 trades_dropped=1`.

The lone survivor (AEROUSDT) had `avg_completeness=1.00` — a structurally complete package — but brain refused to trade because cohort < 3.

## Hourly metrics (cycle_metrics, last 3h before rewrite)

| Hour | Cycles | Layer1B p50/p95 ms | Layer1C p50/p95 ms | Layer1D p50/p95 ms | Total p50/p95 ms | qualified_pct_avg | packages_count_avg |
|---|---|---|---|---|---|---|---|
| 23:00 | 35 | 2925/5653 | 3363/8450 | 20/66 | 6413/14317 | 1.69% | 2.00 |
| 22:00 | 23 | 2821/3715 | 3369/7607 | 17/79 | 6245/10353 | 1.61% | 1.74 |
| 21:00 | 11 | 2774/3616 | 3069/6375 | 13/49 | 5900/8999 | 0.82% | 0.82 |

## Layer 1D filter loss breakdown (steady state)

Out of 50 coins per cycle:
- `pass_xray=34` (XRAY classifies)
- `fail_setup_none=16` (XRAY=NONE — residual after xray-counter fix; market-conditions floor)
- `fail_consensus=19-28` (ensemble didn't reach STRONG/GOOD)
- `fail_regime=1-4` (regime not aligned with consensus direction)
- `fail_rr=3-8` (RR < 1.1 threshold)
- `fail_blockers=0-3` (funding > 0.1% / manipulation / recent loss)
- `qualified=1-3`

## Layer 1B (XRAY) classification distribution (post xray-counter fix)

```
total=50 bearish_fvg_ob=17 none=16 bearish_fvg_ob_counter=7
bullish_fvg_ob=6 bullish_fvg_ob_counter=3 bearish_structural_break=1
conf_p50=0.39 conf_p95=0.55 atr_p50=0.653
window_p50_fvg=2.00 window_p50_ob=3.00
```

Counter variants (10 coins/cycle) are firing → xray-counter fix is working.

## Layer 1C strategy consensus distribution (cycle 00:25)

```
STRAT_CONSENSUS_SUMMARY total=4 STRONG=2 GOOD=1 LEAN=1
```

Only 4 coins reach the consensus stage (out of 50). 14 coins per cycle pass `pass_xray + pass_consensus_strong + pass_consensus_good`.

## Brain side gates (live values from config.toml)

```
[brain.cold_start_protection]
min_avg_completeness = 0.85
min_per_package_completeness = 0.75
min_qualified_packages = 3       <-- THIS DROPS THE TRADE
boot_grace_period_sec = 600
boot_grace_completeness = 0.95
```

## Workers state at baseline

- `workers.py` PID 395 alive 1h+
- `server.py` PID 400 alive 1h+
- `shadow.py` PID 391 alive 1h+
- 19 healthy workers, 0 never-ticked, 0 overdue
- All 3 layers active per `data/layer_state.json`

## Frozen baseline metrics for Phase 11 comparison

| Metric | Baseline value |
|---|---|
| Mean packages/cycle (last 3h) | ~1.5 |
| Trade rate / day (estimate from log) | ~5-8 trades/day |
| `BRAIN_INSUFFICIENT_QUALITY` rate | 0-1 per hour during low-qualified cycles |
| Brain prompt size (target per `strategist.py:1265`) | ~12-14 KB |
| Cycle total elapsed p50 / p95 | 6.4s / 14.3s |
| Validator quarantine rate | 0% (all packages built ok recent cycles) |

These are the numbers Phase 11 will compare against.
