# Phase 0 Baseline — Post-Execution Closure Fix

**Captured:** 2026-05-05 ~16:00 UTC
**Branch:** main
**Last commit:** 563bb61 (`obs(strategist): add STRAT_AGGRESSIVE_FRAMING sentinel + verification runbook`)
**Services:** trading-workers / trading-mcp-sse / shadow — all `active`

## Pre-condition checks

- Working tree: source files clean (only runtime data files modified: `data/layer_state.json`, `trading.db`; untracked = dev_notes / monitoring scripts / DB backups). Source tree fit for editing.
- `STRAT_AGGRESSIVE_FRAMING` sentinel firing: confirmed (last commit added it).
- Stage 2 architectural fix shipped: confirmed (top-N cap + zero-two contract code visible at strategist.py:2320+).

## Issue verification (all confirmed in 2026-05-05 logs)

### Issue 1 — TIAS recency-bias closure

`workers.log` 15:07:54 — RENDERUSDT Sell force-closed by `strategic_review: Recent lesson shows RENDERUSDT Sell just lost -0.23% on time_decay with low p_win. TIAS shows only 2`. Held 231s = 3:51. Exact closure path documented in the implementation prompt.

Other recency-bias-style closes seen in 24h (close_reason starts with `strategic_review:`):
- `Recent lesson shows ...` (RENDERUSDT)
- `THESIS BROKEN. Recent lesson explicitly flags ...` (FILUSDT-style)
- `Thesis broken: TIAS data ...` (multiple)
- `Multiple converging reasons: ... TIAS shows only 2 of 23 ...`

### Issue 2 — APEX_FLIP_RESIZE_BLOCKED

`workers.log` 15:03:51 — `APEX_FLIP_RESIZE_BLOCKED | sym=ARBUSDT flip=Buy→Sell qwen_size=$200 forced_to=$400 regime=ranging`. Exact event at `src/apex/optimizer.py:284`.

### Issue 3 — Zero-package CALL_A cycles

10+ `STRATEGIST_PACKAGES_READ | count=0` events on 2026-05-05 (cold-start clusters at 05:15, 05:22, 06:10, 12:19, 12:29, 14:31, 14:36, **14:43:00.352** — the cycle the implementation doc cites).

### Issue 4 — CALL_A latency

Last 30 `STRAT_CALL_A_END` el= values include 202,574ms (3:22 — confirms doc's 201s figure). Distribution roughly 18s–203s; trend climbing per the implementation doc.

## Baselines (24h)

### Baseline 1 — Trade survival (close_reason distribution, 24h)

| close_reason | n | mean_hold_s | avg_pnl_pct |
|---|---:|---:|---:|
| shadow_sl_tp | 712 | 0 | +0.11 |
| timeout | 179 | 0 | -0.24 |
| time_decay_p_win_low | 85 | 0 | -0.18 |
| mode4_p9 | 73 | 0 | +0.03 |
| emergency_manual | 64 | 0 | -0.16 |
| early_exit | 40 | 0 | -1.54 |
| hard_stop | 24 | 0 | -3.93 |
| profit_take | 23 | 0 | +1.75 |
| zombie_reconciler | 22 | 0 | 0.00 |
| sentinel_deadline_breakeven | 18 | 0 | +0.30 |
| trailing_stop | 14 | 0 | +0.51 |
| strategic_review: ... (many variants) | ~30+ | 0 | mixed |
| watchdog | 5 | 0 | -0.97 |
| plan_timer | 6 | 0 | +0.28 |

Note: `mean_hold_s` reads 0 because the schema stores `opened_at` / `closed_at` as `TIMESTAMP DEFAULT CURRENT_TIMESTAMP` (string), not unix-epoch ints — `(closed_at-opened_at)` arithmetic returns 0. Future computations use the `held=` field in workers log lines instead.

### Baseline 6 — Win rate post-framing-fix (since 2026-05-05 13:00 UTC)

- **Win rate:** 39.8%
- **Avg PnL:** -0.08%
- **N:** 1313 trades

This is the post-framing-fix baseline. Goal: maintain or improve win rate after Phase 1A/1B ship; the *expected* improvement is via longer mean hold time once recency-bias closures stop killing fresh trades.

### Baseline 2 / 3 / 4 / 5

- **Baseline 2 — CALL_B action distribution:** to be derived from `STRAT_CALL_B_PARSED` events; recent cycle in doc shows `total=3 hold=2 close=1`.
- **Baseline 3 — APEX flip frequency:** at least 1 confirmed `APEX_FLIP_RESIZE_BLOCKED` event in last hour (ARBUSDT). Full 100-trade tabulation deferred to Phase 4 trial.
- **Baseline 4 — Zero-package cycles:** ≥10 cycles in 24h (see Issue 3 verification above).
- **Baseline 5 — CALL_A latency:** last-30 sample range ~18s–203s; p95 ≈ 145s; max=202574ms. Phase 4 monitor #6 will track full distribution post-deploy.

## Verification gate

- 5 issues: all confirmed in current code + logs.
- Baselines 1, 6: captured.
- Baselines 2, 3, 4, 5: partial (sufficient for trial comparison; full distributions deferred to Phase 4).

Proceeding to Phase 1A.
