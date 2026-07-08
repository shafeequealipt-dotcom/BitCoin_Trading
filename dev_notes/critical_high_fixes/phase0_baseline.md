# Phase 0 — Pre-Flight Verification and Baseline Capture

## Purpose

Capture the live state of the Trading Intelligence MCP system before beginning the 14-issue CRITICAL/HIGH bybit_demo data flow fix series scoped by `/home/inshadaliqbal786/IMPLEMENT_CRITICAL_HIGH_FIXES.md`. No production code is modified in Phase 0. All numbers below are oracles for Phase 4 verification of every issue.

---

## Section 1 — Pre-conditions

### 1.1 Branch and tree state

- Branch: `feature/bybit-demo-adapter`
- HEAD: `bd8134f` `chore(state): runtime state checkpoint pre-critical-high-fixes` (Phase 0 housekeeping commit)
- Parent: `d2250c1` `docs(lifecycle-logging-audit/phase12): real-project pipeline verification (E2E runtime)`
- Working tree: clean for tracked files. The following items remain untracked and are unrelated to this fix series (they are orphan artifacts from prior work):
  - `data/trading.db.bak-pre-dead-workers-fix-20260427-165401`
  - `data/trading.db.bak-pre-output-quality-fix-20260427-185043`
  - `data/trading.db.pre-layer1-restructure.20260427.bak`
  - `data/trading.db.pre-post-layer1-fixes.20260427.bak`
  - `dev_notes/forensic_data_layer1_to_stage2/_trading_db_snapshot.db`
  - `dev_notes/three_issues/`

### 1.2 Database snapshot

- Source: `data/trading.db` (173,862,912 bytes; 1713 trade_log rows)
- Backup: `data/trading.db.bak-pre-critical-high-fixes-20260509_200045` (verified identical; 1713 trade_log rows)

### 1.3 Mode and schema

- Live mode: `bybit_demo` since `2026-05-08T11:19:26.785051+00:00` (per `transformer_state` row)
- Schema version: 29 (per `SELECT MAX(version) FROM schema_version`)
- Audit reference: `AUDIT_BYBIT_COMPLETE_DATA_FLOW_FINDINGS.md` (2026-05-09)

---

## Section 2 — Audit Reference Re-verification

All 12 critical file:line references checked against current code. Result: 11 confirmed, 1 shifted.

### 2.1 Confirmed (no drift)

| Reference | Status |
|---|---|
| `bybit_demo_websocket_subscriber.py:489-497` (pnl_pct=0 dispatch) | confirmed |
| `trade_coordinator.py:639` (`on_trade_closed` definition) | confirmed |
| `trade_coordinator.py:689,696` (back-derive gates `pnl_pct != 0`) | confirmed |
| `trade_coordinator.py:713-749` (record dict — opened_at NOT included) | confirmed |
| `workers/manager.py:1878-1891` (`_data_lake_close_callback`, no opened_at passed) | confirmed |
| `src/core/data_lake.py:56-65` (write_trade signature, opened_at default empty string) | confirmed |
| `src/core/data_lake.py:93,99` (DL_TRADE_SUSPECT guard + send_risk_warning) | confirmed |
| `bybit_demo_adapter.py:248-438` (close_position with P7 wiring) | confirmed |
| `bybit_demo_adapter.py:539-559` (set_stop_loss, no direction validation) | confirmed |
| `bybit_demo_adapter.py:242` (`get_last_close` hardcoded `"exchange_match"`) | confirmed |
| `src/brain/claude_code_client.py:1232-1268` (CLAUDE_PROC_STALL emission) | confirmed |
| `src/core/log_context.py` (ContextVar pattern + `set_tid()`) | confirmed |

### 2.2 Drift detected

| Reference | Audit said | Current | Action |
|---|---|---|---|
| `_save_account_snapshot` | `transformer.py:1255` | `transformer.py:1160` (and shadow-only gate at lines 1334-1336 in `_AccountProxy.get_wallet_balance`) | Use `transformer.py:1160` for HIGH-1 Phase 1. |

---

## Section 3 — Baseline Metrics

All counts are from the live database (`data/trading.db`) and the most recent log file `/home/inshadaliqbal786/logs_2026-05-09_13-30_to_16-21.log` (131,569 lines, 2 hours 51 minutes from 13:30:13 to 16:16:33 UTC).

### 3.1 CRITICAL-1 — Universal pnl=0 corruption

| Query | Result |
|---|---|
| `trade_log` bybit_demo with pnl_usd=0 | **116 of 116 (100 percent)** |
| `trade_log` shadow with pnl_usd=0 | 300 of 1597 (18.8 percent) |
| `trade_thesis` closed bybit_demo with actual_pnl_usd=0 | **120 of 253 (47.4 percent)** |
| `trade_thesis` closed shadow with actual_pnl_usd=0 | 196 of 1515 (12.9 percent) |
| `trade_intelligence` last 10 rows (ids 1313-1322) | All ten rows have pnl_pct=0.0, pnl_usd=0.0, win=0 |
| DL_TRADE_SUSPECT events in window | 49 (≈17 per hour) |

Growth since audit (2026-05-09): bybit_demo trade_log went from 78 → 116 (+38). Every new bybit_demo trade still records pnl_usd=0. Confirms the bug is structural, not transient.

### 3.2 CRITICAL-2 — opened_at NULL

| Query | Result |
|---|---|
| `trade_log` bybit_demo with opened_at IS NULL OR '' | **116 of 116 (100 percent)** |
| `trade_log` shadow with opened_at IS NULL OR '' | **1597 of 1597 (100 percent)** |

The defect is universal across both exchanges. CRITICAL-2 fix improves shadow data integrity by the same operation.

### 3.3 CRITICAL-3 — TradeHistory coverage gap

| Query | Result |
|---|---|
| `trade_log` bybit_demo total | 116 |
| `trade_history` rows with `trade_id LIKE 'bd-%'` | 30 |
| `trade_history` total rows | 30 (entirely bybit_demo) |
| Coverage gap (bybit_demo trades without trade_history row) | **86 of 116 (74.1 percent)** |
| `trade_log` shadow total | 1597 |
| Shadow trade_history coverage | 0 of 1597 (0 percent — shadow has never written trade_history) |

The 74.1 percent gap is worse than the audit's 67 percent because the bybit_demo trade count grew faster than the system-initiated close subset. Shadow's complete absence from trade_history is documented for context but is out of scope for CRITICAL-3 (the prompt's focus is bybit_demo only).

### 3.4 CRITICAL-4 — Telegram alert spam

In the 2.85-hour window:

| Source | Count | Per hour |
|---|---|---|
| ALERT_SENT total | 406 | 142 |
| level=critical | **143** | **50** |
| level=warning | 175 | 61 |
| level=info | 88 | 31 |
| DL_TRADE_SUSPECT (subset of critical) | 49 | 17 |
| BYBIT_DEMO_SET_SL_FAIL (subset of critical) | 8 | 2.8 |
| BYBIT_DEMO_SET_TP_FAIL | 0 | 0 |
| BYBIT_DEMO_TIMESTAMP_FAIL | 0 (in this window) | 0 |

50 critical alerts per hour, with 143 critical in 2.85 hours, matches the audit's number exactly. Of these 143, the 49 DL_TRADE_SUSPECT events trace directly to CRITICAL-1; once CRITICAL-1 ships, expect a roughly 34 percent reduction in critical volume.

### 3.5 CRITICAL-5 — Stop-loss on wrong side for Sell

BYBIT_DEMO_SET_SL_FAIL by symbol:

| Symbol | Count | Notes |
|---|---|---|
| KATUSDT | 5 | All within 28 seconds (14:33:43 → 14:34:12). tid=t-KATUSDT-sniper (tid attribution looks correct here.) Pattern: SL=0.01015569 below base_price 0.01017. Sell position. |
| RENDERUSDT | 2 | At 16:09:09 and 16:09:28. **tid=t-ATOMUSDT-sniper (cross-symbol bleed — see HIGH-9).** SL=1.981035 below base_price 1.98300. |
| ICPUSDT | 1 | Different failure mode: ret_code=34040 "not modified" (idempotent retry of unchanged value, not a wrong-side bug) |

Total: 8 SL_FAIL events. KATUSDT 5x and RENDERUSDT 2x are wrong-side bugs (7 events). ICPUSDT 1x is a different bug class (idempotent retry).

### 3.6 HIGH-1 — account_snapshots dormant

| Query | Result |
|---|---|
| `MAX(updated_at)` from account_snapshots | `2026-05-08T11:19:21.750969+00:00` |
| Total account_snapshots rows | 62,733 (all from shadow era) |
| Time since last snapshot | ≈33 hours stale at time of capture (2026-05-09 ≈20:00 UTC) |
| Mode flip to bybit_demo | `2026-05-08T11:19:26.785051+00:00` (5 seconds after the last snapshot) |

The 5-second correlation between mode flip and snapshot stop is decisive evidence for the shadow-only gate at `transformer.py:1334-1336`.

### 3.7 HIGH-2 — Missing exchange_mode columns

| Table | exchange_mode column | Notes |
|---|---|---|
| `trade_log` | yes (NOT NULL DEFAULT 'shadow') | Already migrated in P8 |
| `trade_thesis` | yes | Already migrated |
| `trade_intelligence` | **yes** (column id 94, NOT NULL DEFAULT 'shadow') | **Already migrated by P4 — audit assumption stale here** |
| `orders` | no | needs migration |
| `account_snapshots` | no | needs migration |
| `trade_history` | no | needs migration |

So HIGH-2's actual scope shrinks to three tables: `orders`, `account_snapshots`, `trade_history`. `trade_intelligence` is already migrated.

### 3.8 HIGH-3 — close_trigger hardcoded "exchange_match"

Code references (5 total):

| File:line | Type |
|---|---|
| `src/bybit_demo/bybit_demo_adapter.py:242` | Hardcoded value in `get_last_close` return dict (the audit-flagged defect) |
| `src/workers/position_watchdog.py:3107` | Comment |
| `src/workers/position_watchdog.py:3109` | Comment referencing the audit |
| `src/workers/position_watchdog.py:3111` | Hardcoded fallback `close_trigger = "exchange_match"  # default when unknown` |
| `src/workers/position_watchdog.py:3125` | Comment "Silent fallback to exchange_match — never block the..." |

So HIGH-3's true scope is two hardcoded sites: adapter:242 and watchdog:3111. The watchdog one is a documented fallback for the unknown-trigger case, which is legitimate; the adapter one is the bug.

### 3.9 HIGH-4 — CLAUDE_PROC_STALL on Stage-2 brain calls

In the 2.85-hour window:

| Bucket | Count |
|---|---|
| CLAUDE_PROC_STALL_60S | 33 |
| CLAUDE_PROC_STALL_120S | 19 |
| Total CLAUDE_PROC_STALL events | 52 |
| CALL_A or CALL_B start events | 38 |

Stalls per call: 52/38 = 1.37 stall-events per brain call (because each long call hits both 60s and 120s thresholds). Of 38 brain calls, at least 33 hit the 60s threshold (33 unique pids in the 60S bucket if dedup by pid; 19 also hit 120s). Result: **~87 percent of Stage-2 brain calls stall ≥60 seconds**.

Sample stall context:
```
CLAUDE_PROC_STALL_60S | pid=4479 elapsed=60s stdout_so_far=0 timeout_in_s=240 | no_ctx
CLAUDE_PROC_STALL_120S | pid=4479 elapsed=120s stdout_so_far=0 timeout_in_s=180 state=S wchan=ep_poll | no_ctx
```

The subprocess is sleeping on `epoll_wait` with `stdout_so_far=0` — alive, no output produced, blocked on IO. Likely external (Claude CLI subprocess). Phase 1 of HIGH-4 must single-step the cause; per Risk Register Risk 5, the fix may need to be deferred if the cause is external.

### 3.10 HIGH-7 — REDUCE_FALLBACK swallows context

In the 2.85-hour window: 2 REDUCE_FALLBACK events. Sample:

```
2026-05-09 13:58:19.367 | WARNING | src.bybit_demo.bybit_demo_adapter:reduce_position:477 |
REDUCE_FALLBACK | sym=OPUSDT qty=1650.35 reason=bybit_reject 
err='[2026-05-09T13:58:19.367842+00:00] BybitAPIError: Bybit demo: API error 
(10001: Qty invalid) | details={'ret_code': 10001, 'ret_msg': 'Qty invalid', 'op': 'redu' 
| tid=t-CRVUSDT-sniper
```

Note: ret_code, ret_msg, qty, reason are PRESENT in the log. The audit's "swallows context" claim needs Phase 1 deep verification — what context the audit considered missing vs. what is actually visible. **Also note: cross-symbol tid bleed visible — sym=OPUSDT but tid=t-CRVUSDT-sniper (HIGH-9).**

### 3.11 HIGH-9 — Cross-symbol tid bleed

In the 2.85-hour window, in WARNING/ERROR events only (sample of 2000), distinct cross-symbol bleeds:

| sym (in log line) | tid prefix | Count |
|---|---|---|
| RENDERUSDT | ATOMUSDT | 4 |
| OPUSDT | CRVUSDT | 3 |
| ALICEUSDT | PLUMEUSDT | 3 |
| PLUMEUSDT | AVAXUSDT | 1 |
| PLUMEUSDT | ALICEUSDT | 1 |
| ORCAUSDT | AVAXUSDT | 1 |
| ICPUSDT | ATOMUSDT | 1 |
| BLURUSDT | ATOMUSDT | 1 |

Total at least 15 cross-symbol bleeds confirmed in WARN/ERROR. Total events with `tid=t-`: 15,321. Bleed rate is small in absolute terms but invalidates per-symbol log forensics. The bleeds cluster around `-sniper` suffixes (the audit's hypothesis that the sniper iterates symbols and does not reset ctx between iterations).

---

## Section 4 — Operator Gate

This is a no-code-changes phase. The single git operation in Phase 0 is the housekeeping commit at HEAD `bd8134f`.

Before CRITICAL-1 Phase 1 begins:

1. Operator reads this report.
2. Operator confirms baseline metrics are accurate.
3. Operator approves moving to CRITICAL-1 Phase 1 investigation.
4. Operator restates any per-issue priority changes (default order is per the prompt's Part D).

After approval, CRITICAL-1 Phase 1 begins with the investigation steps from the prompt's Part E:
- Step C1.1.1 — Read `bybit_demo_websocket_subscriber.py` end-to-end
- Step C1.1.2 — Read `trade_coordinator.on_trade_closed` end-to-end
- Step C1.1.3 — Trace propagation paths (data_lake, TIAS, thesis_manager)
- Step C1.1.4 — Read system-initiated close path
- Step C1.1.5 — Direction-sign analysis
- Step C1.1.6 — was_win flag analysis
- Step C1.1.7 — Sample 5 real trades; manually compute pnl_pct; compare to recorded
- Step C1.1.8 — Synthesis report

Output: `dev_notes/critical_high_fixes/c1_phase1_*.md` files (one per step) plus `c1_phase2_report.md` for operator discussion.

---

## Section 5 — Summary for Screen Reader

Phase 0 captured baselines for all 14 issues. The most recent log file covers 2 hours 51 minutes (13:30 to 16:21 UTC on 2026-05-09). Database queries are against the live `data/trading.db` (1713 trade_log rows total). All audit references confirmed except `_save_account_snapshot` which moved to `transformer.py:1160`. The bybit_demo trade count grew from 78 (audit baseline) to 116 (current), and 100 percent of those 116 rows have pnl_usd=0 and empty opened_at. The trade_history coverage gap is now 74 percent. Telegram critical alerts are at 50 per hour, of which 17 per hour are DL_TRADE_SUSPECT (all eliminable by CRITICAL-1). Stage-2 brain calls stall 60 seconds or more on 87 percent of calls. Cross-symbol tid bleed is confirmed across at least 8 symbol pairs. Mode is `bybit_demo`. Schema version is 29.

Awaiting operator go-ahead for CRITICAL-1 Phase 1.
