# Phase 0 — Pre-Flight + Baseline

**Started:** 2026-05-11 11:04 UTC
**Branch:** `fix/five-critical-fixes-2026-05-11` (cut from `fix/cascade-i4-positions-parity` @ `a02d81d`)
**Working tree:** clean post-stash (`pre-five-critical-fixes-2026-05-11`)
**System state:** workers / MCP / shadow all active since 2026-05-11 09:23 UTC (continuous boot — same as monitoring reports)

## 0.1 — System State Verification

| Check | Result |
|-------|--------|
| `trading-mcp-sse.service` | active |
| `trading-workers.service` | active |
| Workers PID 393 | up 1 h 41 min |
| MCP PID 394 | up 1 h 41 min |
| Shadow PID 389 | up 1 h 41 min |
| `data/logs/workers.log` last write | 2026-05-11 11:04:47 UTC (live) |
| `data/logs/general.log` last write | 2026-05-11 11:00:44 UTC (live) |
| `data/logs/brain.log` last write | 2026-05-11 10:40 UTC (live) |

Same boot as the monitoring reports; the reports' "stopped on user request at 10:27 UTC" referred to the monitoring session, not the workers.

## 0.2 — File:Line Reference Drift vs Reports

| Cited in reports | Verified location | Drift |
|------------------|-------------------|-------|
| `BybitDemoOrderService.place_order` `bybit_demo_adapter.py:831–1056` | `bybit_demo_adapter.py:829` (entry); `BYBIT_DEMO_ORD_SEND` `:934` | minor (2 lines) |
| `Transformer._OrderProxy.place_order` `transformer.py:1228` | `transformer.py:1264` class / `:1270` method | yes (42 lines) |
| `XRAY_DIR_FLIP` emission | `strategy_worker.py:1738` (WARNING level) | confirmed |
| `APEX_OK` emission | `optimizer.py:766` (was reported `:767`) | minor |
| `APEX_FLIP` emission | `optimizer.py:745` (was reported `:746`) | minor |
| `APEX_DIR_LOCK` emission | `optimizer.py:222` (was reported `:223`) | minor |
| `WD_CLOSE` emission | `position_watchdog.py:3148` (was reported `:3149`) | minor |
| `on_trade_closed` start | `trade_coordinator.py:639` | confirmed |
| `COORD_DOUBLE_CLOSE` (silent-skip) | `trade_coordinator.py:671` | confirmed |
| `MODE4_STALL_ESCALATE` | `profit_sniper.py:2692` (yesterday) / `:2704` (today) | two locations |

No reference is fundamentally wrong — minor line-number drifts from cascade-fix-series commits. The structural diagnoses in the reports map cleanly to current code.

## 0.3 — Issue 1 Baseline (silent direction flips)

### STRAT_DIRECTIVE ↔ BYBIT_DEMO_ORD_SEND join (full log span, both days)

- STRAT_DIRECTIVE rows in `brain.log`: 2 568
- BYBIT_DEMO_ORD_SEND rows in `workers.log`: 32 (only 32 of 2 568 directives actually placed — rest were gated upstream)
- Direction matched: **22 / 32 (69 %)**
- Direction mismatched: **10 / 32 (31 %)**

### Mismatch breakdown

| sym | did | brain | ord | classification | log evidence |
|-----|-----|-------|-----|----------------|--------------|
| HBARUSDT | d-…32513966 | Buy | Sell | XRAY-flip (yesterday) | `XRAY_DIR_FLIP` `strategy_worker.py:1738` 2026-05-10 17:05:32 |
| ARBUSDT | d-…35763433 | Buy | Sell | XRAY-flip (yesterday) | `XRAY_DIR_FLIP` 2026-05-10 18:00:38 |
| FILUSDT | d-…35183689 | Buy | Sell | **APEX legitimate** (yesterday) | `APEX_FLIP` `optimizer.py:745` 2026-05-10 17:49:51 + `APEX_FLIP_RESIZE_ACCEPTED` |
| ATOMUSDT | d-…92438281 | Buy | Sell | **APEX legitimate** (today) | `APEX_FLIP` 2026-05-11 09:44:17 + `APEX_FLIP_RESIZE_ACCEPTED` |
| SEIUSDT | d-…92438281 | Buy | Sell | XRAY-flip overriding APEX_DIR_LOCK | `APEX_DIR_LOCK | sym=SEIUSDT dir=Buy regime=volatile reason='volatile regime, insufficient flip evidence'` 09:44:06 then `XRAY_DIR_FLIP | sym=SEIUSDT … rr_original=0.1 rr_flipped=6.4 ratio=45.9x` at 09:44:31 |
| PYTHUSDT | d-…93028286 | Sell | Buy | XRAY-flip | `XRAY_DIR_FLIP | sym=PYTHUSDT … ratio=45.8x` 09:53:29 |
| NEARUSDT | d-…93527139 | Buy | Sell | XRAY-flip | `XRAY_DIR_FLIP | sym=NEARUSDT … ratio=53.0x` 10:01:35 |
| CRVUSDT | d-…93527139 | Buy | Sell | XRAY-flip | `XRAY_DIR_FLIP | sym=CRVUSDT … ratio=108.3x` 10:01:37 |
| GMTUSDT | d-…94613425 | Buy | Sell | XRAY-flip | `XRAY_DIR_FLIP | sym=GMTUSDT … ratio=4.6x` 10:20:26 |
| ONDOUSDT | d-…94613425 | Buy | Sell | XRAY-flip overriding APEX_DIR_LOCK | `APEX_DIR_LOCK | sym=ONDOUSDT dir=Buy regime=volatile … insufficient flip evidence` 10:19:55 then `XRAY_DIR_FLIP | sym=ONDOUSDT … ratio=19.4x` 10:20:47 |

### Key Phase 0 finding for Issue 1

**Every direction-mismatch case has a log entry.** Eight of the ten are XRAY_DIR_FLIP emissions at `strategy_worker.py:1738` (WARNING level). Two of the eight (SEIUSDT, ONDOUSDT) overrode an explicit APEX_DIR_LOCK. The report's framing — "silent path emits no flip log at all" — is inaccurate at the code level: XRAY_DIR_FLIP fires before every silent flip. The operator-perceived gap is:
- XRAY_DIR_FLIP is named outside the `APEX_FLIP*` family; audits filtering for `APEX_FLIP*` miss it
- XRAY_DIR_FLIP is emitted at WARNING, but operators often filter INFO
- XRAY_DIR_FLIP overrides APEX_DIR_LOCK without any contract-violation emission

Investigation in Phase 1 must confirm this end-to-end and decide whether the desired behavior is to (a) rename/escalate the XRAY flip log, (b) respect APEX_DIR_LOCK, (c) gate XRAY flip on brain conviction, or (d) some hybrid.

### Today-only metric (matches operator's 55 % claim)

- Today's ORD_SENDs (2026-05-11): 11 trades placed
- Today's direction mismatches: 7 (ATOMUSDT, SEIUSDT, PYTHUSDT, NEARUSDT, CRVUSDT, GMTUSDT, ONDOUSDT)
- Of those, 1 = APEX-legit (ATOMUSDT), 6 = XRAY-driven
- Silent-flip rate (operator definition: not via APEX_FLIP*): **6 / 11 = 55 %** ✅ matches report exactly

## 0.4 — Issue 2 Baseline (zombie positions)

`positions` table at 11:04 UTC (6 rows):

| symbol | side | size | updated_at | status |
|--------|------|------|------------|--------|
| APTUSDT | Buy | 1193.52 | 10:27:02 | closed at 10:27:03 per trade_history (`bd-9351ad8a` pnl=$4.89) — **zombie** |
| ATOMUSDT | Sell | 2445.2 | 10:00:03 | closed at 10:00:05 (`bd-67a9ca7e` pnl=-$39.86) — **zombie** |
| CRVUSDT | Sell | 1033.2 | 10:12:08 | closed at 10:12:12 (`bd-2e0988fe` pnl=-$1.86) — **zombie** |
| GMTUSDT | Sell | 116722.0 | 10:24:24 | closed at 10:24:30 (`bd-b7420cf8` pnl=$19.49) — **zombie** |
| NEARUSDT | Sell | 867.1 | 10:10:29 | closed at 10:10:32 (`bd-39233627` pnl=-$5.20) — **zombie** |
| PYTHUSDT | Buy | 3451.0 | 10:28:13 | closed at 10:28:16 (`bd-28945555` pnl=$0.90) — **zombie** |

**All 6 positions are zombies** — each has a corresponding closed trade_history row but the positions row was never deleted. Time elapsed since close: 36 min (PYTHUSDT) to 64 min (ATOMUSDT). The trading_repo.py:182 DELETE-on-size==0 path either never fired or fired before the row was re-inserted.

## 0.5 — Issue 3 Baseline (corrupted WD_CLOSE)

- Total WD_CLOSE events in `workers.log`: **1**
- Events matching corruption signatures (`ent=$0`, `pnl$=+0.0000`, `dir= empty`, `price_src=ticker_fallback`): **1 / 1 (100 %)**

The single event:

```
2026-05-11 10:11:23.783 | WARNING | src.workers.position_watchdog:_detect_and_record_closes:3148
WD_CLOSE | sym=FILUSDT pnl=-0.2747% pnl$=+0.0000 ent=$0.00000000 ext=$1.13210000 dir=
  price_src=ticker_fallback rsn=bybit_demo_sl_tp close_trigger=exchange_match win=N
```

Preceded immediately by:
```
WD_CLOSE_PRICE_FALLBACK | sym=FILUSDT src=ticker_fallback reason=stale_close
```

Followed by `COORD_DOUBLE_CLOSE | sym=FILUSDT … already closed — skipping duplicate` — meaning the trade had ALREADY been closed via another path before this WD_CLOSE fired, so the row written here is purely the late-detection backup with no original TradeState to enrich from.

This matches the directive's diagnosis: late-detected close path lacks the CRITICAL-1 back-derive that the WS path has at `on_trade_closed:716-731`.

## 0.6 — Issue 4 Baseline (partial-close inflation)

- `partial_close` actions actually executed (sniper) today: **0** (10 proposals were all gated by `M4_GATED reason=cooldown`)
- All 8 `MODE4_STALL_ESCALATE` events today escalated to `full_close`, not partial
- FILUSDT trade_history row at 10:11:23: `qty=4430.2 entry=1.1286 exit=1.1274 pnl=$5.32`
- BYBIT_DEMO_WS_CLOSE_EVENT for FILUSDT at 10:11:23: `exec_qty=2215.1 closed_size=2215.1` (HALF of trade_history qty)

This inconsistency suggests EITHER (a) the report's A17 "50 % partial close" diagnosis is about a different event series than I've found in current logs, OR (b) the qty/closed-size discrepancy comes from a different mechanism (e.g., Bybit already closed half externally before our close order, so our close order only filled the residual 2215.1). Phase 1 investigation must reconcile this carefully — do not assume the directive's framing without verification.

## 0.7 — Issue 5 Baseline (silent close after partial)

- trade_history rows today: 15
- trade_log rows today: 15
- Count parity: **15 == 15** — no silent skips visible today

The directive's claim of "12 recorded + 1 silent = 13 actual" was at session-stop (10:27 UTC). Since then, 3+ more closes have been written. Without partial closes today, Issue 5's specific scenario (residual silent close) cannot be observed in current logs. Phase 1 investigation must determine whether this is (a) an artifact of report-time vs current-time, (b) the bug was always conditional on partial closes happening (which they didn't today after 10:11), or (c) the bug was fixed silently between report and now.

Also notable: **11 `COORD_DOUBLE_CLOSE` events** at `trade_coordinator.py:671` (`already closed — skipping duplicate`) — this is the silent-skip path the directive flagged as the Issue 5 candidate. 5 of 11 are FILUSDT (matching the 22-min ghost period).

## 0.8 — System Totals (this boot, 09:23 → 11:04 UTC)

| Counter | Value |
|---------|------:|
| BYBIT_DEMO_ORD_SEND | 32 |
| BYBIT_DEMO_POSITION_CLOSE | 20 |
| COORD_DOUBLE_CLOSE | 11 |
| MODE4_STALL_ESCALATE | 8 today (1 yesterday remains in buffer) |
| XRAY_DIR_FLIP | 6 today (8 total) |
| APEX_FLIP / APEX_FLIP_RESIZE_ACCEPTED | 1 / 1 today |
| APEX_DIR_LOCK | 5 today (+2 APEX_DIR_LOCK_OVERRIDE) |
| WD_CLOSE | 1 |
| trade_history rows today | 15 |
| trade_log rows today | 15 |
| positions table rows | 6 (all zombies) |

## 0.9 — Previous-Fix Regression Check

- `on_trade_closed:716-731` CRITICAL-1 back-derive — present in current `trade_coordinator.py` (will verify code-level in Phase 1)
- A2 positions-table write — present in `trading_repo.py:189-229` per Explore agent
- Schema: `positions` has `exchange_mode TEXT NOT NULL DEFAULT 'shadow'` (matches v32 per memory `project_cascade_fixes_status.md`)
- `bybit_demo_websocket_subscriber._dispatch_close` (430-457) → coordinator (494) — present

No regressions detected.

## 0.10 — Decisions for Phase 1

Starting Issue 1 Phase 1 immediately with the focused finding that XRAY_DIR_FLIP is the flip mechanism, and the operator-relevant gap is:
1. Naming inconsistency (XRAY_DIR_FLIP vs APEX_FLIP family)
2. APEX_DIR_LOCK override semantics
3. Whether XRAY's R:R-based flip should be subject to a brain-conviction gate or APEX-veto

The Phase 1 deep reads will:
- Trace `strategy_worker._execute_claude_trade` (1417–2434) line-by-line, especially the XRAY block (1604–1748)
- Trace APEX `_apply_flip_resize_policy`, `_check_direction_lock`, `_enforce_flip_confidence`, `_check_flip_evidence`
- Confirm the contract: APEX_DIR_LOCK is a hard lock or advisory?
- Build a definitive per-trade table including the 2 APEX-DIR-LOCK-override cases
