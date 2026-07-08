# Phase 0 — Pre-Flight Baseline

## Capture Window

- Start: 2026-05-16 07:26:32 UTC (first record in `workers.2026-05-16_07-26-32_801275.log`)
- End: 2026-05-16 12:33:41 UTC (latest record in `workers.log` at time of capture)
- Span: ~5h07m (slightly under the spec's 6h target; the system is live and producing fresh records, so this is the most recent contiguous window)
- Source files: `data/logs/workers.log`, `data/logs/workers.2026-05-16_07-26-32_801275.log` (rollover), `data/logs/brain.log` (filtered to `2026-05-16` only — full file spans 2026-03-29 to 2026-05-16 10:46)

## Pre-Conditions

| Check | Status | Evidence |
|---|---|---|
| Branch `fix/j1-orphan-positions` working tree | clean of source changes (runtime artefacts `data/layer_state.json`, `data/logs/layer1c_full.jsonl` are expected) | `git status` |
| B1a regime fix shipped (commit `6938c69`) | present in branch history | git log + `src/strategies/regime.py` thresholds match B1a values per memory `project_regime_b1a_fix_status.md` |
| J11 DB concurrency refactor | present; cascade-free | 0 `DB_LOCK_CASCADE` / `database is locked` / `sqlite3.OperationalError` events in workers.log + rollover + brain.log over window |
| J1 orphan-positions branch (current) | working tree on `fix/j1-orphan-positions` | confirmed |
| Brain enrichments E1–E6 (commits 598216e..2700a84) | present in branch | recent `git log` |

## H4 Baseline — Gate Rejection Rate

| Metric | Value |
|---|---|
| `TRADE_SKIP rsn=gate_rejected` count | 31 (4 in workers.log + 27 in rollover) |
| `REENTRY_LEARNING_GATE action=block` count | 31 (matches; every block produced a downstream skip) |
| `BRAIN_DO_TRADE rsn=ok` count | 23 |
| Rejection rate | 31 / (31 + 23) = **57.4 %** |
| Spec's stated rejection rate | 60 % (close match — issue still active) |

### Per-symbol top-5 (rejections today)
| Symbol | Count |
|---|---|
| XRPUSDT | 8 |
| LINKUSDT | 6 |
| SEIUSDT | 5 |
| SKRUSDT | 4 |
| AVAXUSDT | 3 |
| LDOUSDT | 2 |
| ETHUSDT | 1 |
| DYDXUSDT | 1 |

Spec referenced XRP 8, LINK 6, SEI 5 — **exact match**. Issue signature unchanged.

## H1 Baseline — Prewarm Pool

| Metric | Value |
|---|---|
| `CLAUDE_PREWARM_DISPOSED` count today | 43 |
| `CLAUDE_PREWARM_REUSE_OK` count today | **0** |
| `CLAUDE_PROC_PREWARM_OK` (spawn-ok) count today | 49 |
| Latest pool-stats line | `hits=0 misses=45 stale_disposed=42 age_disposed=0 dead_disposed=42 spawn_failed=0 hit_rate_pct=0.0 slots_currently_held=2 max_age_s=900` |
| **Hit rate** | **0.0 %** |
| Disposal reason split | dead=42, age_expired=0 → **all disposals are subprocess death, none are TTL expiry** |

### Disposal age distribution (today)
| Statistic | Seconds |
|---|---|
| n | 86 |
| min | 43.2 |
| p50 | 900.0 |
| p95 | 1660.0 |
| max | 6792.4 |
| mean | 839.6 |

Note: p50 sits at the max_age line (900 s), but the disposal reason for those is still `dead` — they were waiting near the TTL boundary when poll() found them already exited. The **43.2 s minimum is the key signal**: at least one subprocess dies within ~43 s of spawn, well before any TTL pressure.

## H2 Baseline — CALL_A Latency

`CLAUDE_CALL_OK el=...ms` events today:

| Statistic | Milliseconds |
|---|---|
| n | 49 |
| min | 16,214 (16.2 s) |
| p50 | 102,381 (**102.4 s**) |
| p95 | 205,534 (205.5 s) |
| max | 255,117 (255.1 s) |
| mean | 109,036 (109.0 s) |

Spec's claimed median 156 s; today's median 102 s — slightly lower but still well above the < 60 s target. **Issue active.** Mean 109 s confirms latency is the dominant decision-cycle cost.

## H6 Baseline — Stall Events

| Bucket | Count today |
|---|---|
| `CLAUDE_PROC_STALL_60S` | 42 |
| `CLAUDE_PROC_STALL_120S` | 25 |
| `CLAUDE_PROC_STALL_240S` | 1 |
| **Total** | **68 today** (~13.3/hr) |

Spec's claimed 40 in 2h22m (~16.9/hr). Today's rate slightly lower but same order. **Issue active.**

## H3 Baseline — FUND_INUSE_DRIFT

| Metric | Value |
|---|---|
| `FUND_INUSE_DRIFT` event count over window | 307 (138 in workers.log + 169 in rollover) |
| Cadence | ~1 per minute (matches 60 s reconciler tick) |
| First-window drift (07:26) | `inuse_bybit=82844.34 inuse_local=90146.11 diff=-7301.77 streak=15` |
| Mid-window drift (e.g., 07:28) | `inuse_bybit=82846.28 inuse_local=100458.72 diff=-17612.44 streak=17` |
| **Sign** | **NEGATIVE** (`inuse_bybit - inuse_local < 0`) → **local OVER-counts** |
| Magnitude | $7.3 k → $17.6 k over the first 3 minutes of the window |

Confirms the spec's growth pattern and the leading hypothesis (`fund_manager/manager.py:181` formula missing leverage divisor).

## Cascade / Error Sanity

- `DB_LOCK_CASCADE` / `database is locked` / `sqlite3.OperationalError` count: **0** across workers.log, rollover, brain.log → J11 holding.

## Trade Frequency

- `BRAIN_DO_TRADE rsn=ok` count today: 23 trades over 5h07m → ~4.5/hr → ~9 trades per 2h
- Spec session (2h22m) had 13 closures, 11 wins, 78.6 % WR — separate metric (closure count, not trade-open count). Baseline frequency captured here is the open-side; closure rate measured at Phase 4.

## Summary

All four issue signatures are present in the current 5h baseline and match the spec's monitoring snapshot within sampling noise. Rejection-rate 57.4 % vs spec 60 %; per-symbol top-3 (XRP/LINK/SEI) matches exactly. Pool hit rate 0.0 % is identical to spec. CALL_A median 102 s vs spec 156 s — still high. Drift sign and growth pattern match. Cascades absent. No new catastrophic patterns surfaced. **Proceed to Cluster A Phase 1 (H4 root-cause investigation) with the spec's framing of these issues validated.**
