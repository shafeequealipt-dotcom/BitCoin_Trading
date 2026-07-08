# Issue 1 Phase 1 — Per-Trade Lifecycle Comparison Table

**Source:** `data/logs/brain.log` + `data/logs/workers.log`, this boot (2026-05-11 09:23–11:04 UTC). Cross-joined by `did=` correlation id.

## Today's 11 ORD_SEND trades (full lifecycle)

| # | sym | did | STRAT_DIRECTIVE | APEX_DIR_LOCK | APEX_OK | XRAY_DIR_FLIP | BYBIT_DEMO_ORD_SEND | flipped? | classification |
|---|-----|-----|-----------------|---------------|---------|----------------|---------------------|----------|----------------|
| 1 | AXSUSDT | d-…92032551 | Sell | — | (in workers.log only) | — | Sell @ 09:35:32 | no | clean |
| 2 | OPUSDT | d-…92032551 | Sell | — | — | — | Sell @ 09:35:34 | no | clean |
| 3 | ATOMUSDT | d-…92438281 | Buy | — | Sell (APEX_FLIP) | — | Sell @ 09:44:18 | yes | **legit APEX_FLIP** |
| 4 | SEIUSDT | d-…92438281 | Buy | Buy (volatile, insufficient flip evidence) @09:44:06 | Buy @ 09:44:?? | Sell @ 09:44:31 ratio=45.9x | Sell @ 09:44:?? | yes | **XRAY override of APEX_DIR_LOCK** |
| 5 | PYTHUSDT | d-…93028286 | Sell | — | Sell | Buy @ 09:53:29 ratio=45.8x | Buy | yes | **XRAY flip (no APEX lock)** |
| 6 | NEARUSDT | d-…93527139 | Buy | — | Buy | Sell @ 10:01:35 ratio=53.0x | Sell @ 10:01:35 | yes | **XRAY flip (no APEX lock)** |
| 7 | APTUSDT | d-…93527139 | Buy | Buy (volatile, insufficient flip evidence) @10:01:16 | Buy | — | Buy @ 10:01:36 | no | clean (APEX lock held, XRAY did not fire) |
| 8 | CRVUSDT | d-…93527139 | Buy | — | Buy | Sell @ 10:01:37 ratio=108.3x | Sell @ 10:01:37 | yes | **XRAY flip (no APEX lock)** |
| 9 | XRPUSDT | d-…94073038 | Sell | — | Sell | — | Sell @ 10:11:47 | no | clean |
| 10 | GMTUSDT | d-…94613425 | Buy | — | Buy @ 10:20:26.420 | Sell @ 10:20:26.649 ratio=4.6x | Sell @ 10:20:26.864 | yes | **XRAY flip (no APEX lock)** |
| 11 | ONDOUSDT | d-…94613425 | Buy | Buy (volatile, insufficient flip evidence) @10:19:55 | Buy @ 10:20:13.731 | Sell @ 10:20:47.300 ratio=19.4x | Sell @ 10:20:47.618 | yes | **XRAY override of APEX_DIR_LOCK** |

(OPUSDT/IMXUSDT/AXSUSDT entries 12-13 at 10:20:47.618 and 10:29:45 added to total — those are clean.)

## Flip taxonomy today (11 trades; 7 directional disagreements brain↔send)

| Class | Count | Examples | Has log? | Tag |
|-------|------:|----------|----------|-----|
| Clean (no flip) | 4 | AXSUSDT, OPUSDT, XRPUSDT, APTUSDT, IMXUSDT (entry 13) | n/a | — |
| APEX legitimate flip | 1 | ATOMUSDT | yes | `APEX_FLIP` + `APEX_FLIP_RESIZE_ACCEPTED` (INFO/WARNING) |
| XRAY flip without APEX_DIR_LOCK | 4 | PYTHUSDT, NEARUSDT, CRVUSDT, GMTUSDT | yes | `XRAY_DIR_FLIP` at strategy_worker.py:1738 (WARNING) |
| XRAY flip overriding APEX_DIR_LOCK | 2 | SEIUSDT, ONDOUSDT | yes (but contract-violating) | `APEX_DIR_LOCK` + `XRAY_DIR_FLIP` (no “override” marker) |

## Critical timing — GMTUSDT (the closest 3-event window)

```
10:19:55.105  brain.log    STRAT_DIRECTIVE  | GMTUSDT dir=Buy lev=4 (#1 of Call A)
10:20:26.420  workers.log  APEX_OK           | GMTUSDT dir=Buy sl=0.8% tp=1.4% sz=$18000→$600 conf=60% regime=ranging ms=5871
10:20:26.649  workers.log  XRAY_DIR_FLIP     | GMTUSDT original_dir=Buy flipped_dir=Sell rr_original=0.6 rr_flipped=2.9 ratio=4.6x
10:20:26.864  workers.log  BYBIT_DEMO_ORD_SEND | GMTUSDT side=Sell qty=116722.0 lev=5
```

**229 ms between APEX_OK and XRAY_DIR_FLIP**; **215 ms between XRAY_DIR_FLIP and ORD_SEND**. The flip sits cleanly between APEX (which reported Buy) and the adapter call (which sent Sell). Both logs exist — but they live in different file:line/level combinations.

## Critical evidence — SEIUSDT (the APEX_DIR_LOCK violation)

```
09:44:06.157  workers.log  APEX_DIR_LOCK     | SEIUSDT dir=Buy regime=volatile reason='volatile regime, insufficient flip evidence'
09:44:??.???  workers.log  APEX_OK           | SEIUSDT dir=Buy (lock held inside APEX; reverted any DeepSeek flip via APEX_DIR_LOCK_OVERRIDE if needed)
09:44:31.458  workers.log  XRAY_DIR_FLIP     | SEIUSDT original_dir=Buy flipped_dir=Sell rr_original=0.1 rr_flipped=6.4 ratio=45.9x size_usd=$360
09:44:??.???  workers.log  BYBIT_DEMO_ORD_SEND | SEIUSDT side=Sell ...
```

APEX explicitly refused to flip (volatile regime, no TIAS evidence). XRAY then flipped the same direction in a different code module 25 seconds later **with no awareness of the lock**.

## P&L outcomes for today's 6 XRAY flips (settled rows)

| sym | flip | XRAY ratio | exit | pnl_pct | pnl$ | win? | trade_history exit_time |
|-----|------|-----------:|------|--------:|------:|------|------------------------|
| SEIUSDT | Buy→Sell | 45.9x | 0.07433 | -0.337 % | -$6.07 | N | 10:20:16.632 |
| PYTHUSDT | Sell→Buy | 45.8x | 0.0582  | +0.449 % | +$0.90 | Y | 10:28:16.282 |
| NEARUSDT | Buy→Sell | 53.0x | 1.5628  | -0.385 % | -$5.20 | N | 10:10:32.589 |
| CRVUSDT | Buy→Sell | 108.3x| 0.2631  | -0.689 % | -$1.86 | N | 10:12:12.131 |
| GMTUSDT | Buy→Sell | 4.6x  | 0.012684| +1.300 % | +$19.49| Y | 10:24:30.158 |
| ONDOUSDT | Buy→Sell | 19.4x | 0.4247  | -0.024 % | -$0.59 | N | 10:40:29.668 |

**4 losses, 2 wins. Aggregate: +$6.67.** Dominated by the GMTUSDT TP-hit win ($19.49). Excluding GMTUSDT, XRAY-flipped trades net -$12.82 across 5 trades. The ratios are huge (4.6x to 108.3x) but the rest of the trade — leverage, sizing, market conditions — still controls the P&L outcome. The "ratio" is a *structural-target* R:R, not realized.

## What the data establishes

1. There is **no truly silent flip site**. Every direction change today emits a structured log event.
2. The audit's "55 % silent rate" coincides exactly with the `XRAY_DIR_FLIP` count today (6) → the audit was searching for `APEX_FLIP*` tags, missed `XRAY_DIR_FLIP`.
3. `XRAY_DIR_FLIP` is observable at WARNING but doesn't share the `APEX_FLIP` prefix — operationally easy to miss.
4. Two of six XRAY flips (33 %) **overrode an explicit APEX_DIR_LOCK**. This is the actual contract bug: APEX_DIR_LOCK is enforced only inside APEX itself.
5. APEX_DIR_LOCK held the line correctly for APTUSDT (no XRAY fire). For SEIUSDT and ONDOUSDT, XRAY's flip-ratio was large enough to trigger its own threshold; the lock was invisible at strategy_worker boundary.
