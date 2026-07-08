# Phase 13 — Verification Template (Operator Action After Restart)

**Date:** 2026-05-09
**Status:** AWAITING OPERATOR EXECUTION (restart system + 6-12 hour live trial)
**Implementation HEAD:** `ef8ac40`
**Branch:** `feature/bybit-demo-adapter`

---

## Section 1 — Pre-Verification Wiring (Required)

Apply this **before** restarting the system, otherwise DL_TRADE_SUSPECT Telegram alerts will not activate.

### 1.1 — Wire AlertManager into DataLakeWriter

In `src/workers/manager.py` (or wherever `DataLakeWriter` is constructed in `_init_services`), add **one line** after the AlertManager is created and after the DataLakeWriter is created:

```python
data_lake = DataLakeWriter(db)
data_lake.set_alert_manager(alert_manager)  # Phase 12.9 Gap 9.3-G2 wiring
```

Without this line, the structured ERROR `DL_TRADE_SUSPECT` log still fires, but no Telegram alert is sent. The audit's prompt-named gap "DL_TRADE_SUSPECT data integrity violations recorded without alerts" is functionally addressed only after this wiring.

### 1.2 — Optional: Document RiskManager + RuleEngine inactive state

Per `phase4_validator_bypass_investigation.md`, both `RiskManager.validate_trade` and `RuleEngine` are confirmed bypassed in production. Recommend either:

**Option A (recommended):** Delete the dead code in a follow-up commit. The safety contract is preserved by `apex/gate.py::TradeGate`.

**Option B:** Keep dead code for future re-enablement. Add startup logs:
```python
log.info("RISK_MANAGER_INACTIVE | reason=brain_v2_legacy_path_unused | replaced_by=apex_gate")
log.info("RULE_ENGINE_INACTIVE | reason=hints_passed_to_claude_as_context | replaced_by=strategy_hints")
```

### 1.3 — System Restart

```bash
pm2 restart trading-intelligence-mcp  # or whichever process manager
```

---

## Section 2 — Live Trial (6-12 Hours)

Let the system run for 6-12 hours under normal trading load. The longer the window, the richer the verification dataset.

---

## Section 3 — Per-Tag Verification Checklist

Run these greps after the trial. Each tag should fire as expected (or remain at 0 firings if its triggering condition didn't occur — that's also valid).

### 3.1 — CRITICAL fixes (must fire if any data_lake write failed)

```bash
cd /home/inshadaliqbal786/trading-intelligence-mcp

# Should be 0 in healthy system. If non-zero, INVESTIGATE — this is the
# audit's #1 named gap previously silent.
grep -c "DL_TRADE_WRITE_FAIL\|DL_MARKET_SNAPSHOT_WRITE_FAIL\|DL_POSITION_SNAPSHOT_WRITE_FAIL\|DL_DECISION_WRITE_FAIL\|DL_EVENT_WRITE_FAIL\|DL_DAILY_SUMMARY_WRITE_FAIL" data/logs/workers.log

# DL_TRADE_SUSPECT should fire only if data integrity violation occurred.
grep -c "DL_TRADE_SUSPECT" data/logs/workers.log

# Should show "ALERT_SENT" line right after if Section 1.1 wiring was applied.
grep -A 2 "DL_TRADE_SUSPECT" data/logs/workers.log | head -10
```

### 3.2 — HIGH fixes (verify firing patterns)

```bash
# X-RAY context build silent fail (now WARNING). Should be 0 in healthy state.
grep -c "XRAY_CTX_BUILD_FAIL" data/logs/brain.log

# SL_VERIFY family — fires per place_order with SL.
grep -c "SL_VERIFY_OK\|SL_VERIFY_FAIL\|SL_VERIFY_RETRY_OK\|SL_VERIFY_RETRY_FAIL\|SL_VERIFY_EXCEPTION" data/logs/workers.log

# BYBIT_DEMO_PERSIST_OK — should fire 1-3x per closed trade.
grep -c "BYBIT_DEMO_PERSIST_OK" data/logs/workers.log

# close_trigger field on WD_CLOSE — count distribution.
grep -oE "close_trigger=[a-z_]+" data/logs/workers.log | sort | uniq -c

# L4P_CHECK heartbeat — fires per is_protected call.
grep -c "L4P_CHECK" data/logs/workers.log

# TIAS_DEEPSEEK_OK / FAIL per analyzer call.
grep -c "TIAS_DEEPSEEK_OK\|TIAS_DEEPSEEK_FAIL" data/logs/workers.log

# Idempotent retry — fires only on transient failure.
grep -c "BYBIT_DEMO_PLACE_RETRY\|BYBIT_DEMO_PLACE_RETRY_OK" data/logs/workers.log

# BYBIT_DEMO_LAST_CLOSE_RETRY now INFO (was DEBUG).
grep -c "BYBIT_DEMO_LAST_CLOSE_RETRY\b" data/logs/workers.log
```

### 3.3 — MEDIUM fixes (additive observability)

```bash
# CALL_A/CALL_B context-build failure tags (DEBUG → WARNING).
grep -c "STRAT_CTX_BALANCE_FAIL\|STRAT_CTX_TIERED_CAPITAL_FAIL\|STRAT_CTX_DAILY_PNL_FAIL" data/logs/brain.log

# Decision parser strategy tracking on PARSE_OK.
grep -oE "strategy=[a-z]+" data/logs/brain.log | sort | uniq -c

# CLAUDE_ALERT_CALLBACK_OK fires once at startup.
grep -c "CLAUDE_ALERT_CALLBACK_OK" data/logs/brain.log

# Watchdog/sniper structured failure tags (replaced 19 prose lines).
grep -c "WD_EMERGENCY_CLOSE_FAIL\|WD_HARD_STOP_FAIL\|WD_TIMEOUT_CLOSE_FAIL\|WD_PROFIT_TAKE_FAIL\|WD_TRAIL_CLOSE_FAIL\|M4_CLOSE_FAIL\|M4_BRAIN_FAIL" data/logs/workers.log

# M4_TRAIL_FLOOR compression — should be DRAMATICALLY lower than pre-fix.
# Pre-fix: ~42,000 firings per rotation. Post-fix expected: <2,000.
grep -c "M4_TRAIL_FLOOR" data/logs/workers.log

# XFORM_* family — replaces 17 transformer prose lines.
grep -oE "XFORM_[A-Z_]+" data/logs/workers.log | sort | uniq -c

# CLOSE_FILL_CONFIRMED on close_position fill resolution.
grep -c "CLOSE_FILL_CONFIRMED" data/logs/workers.log

# WD_POSITIONS_VANISHED + POSITION_CONFIRMED set-difference signals.
grep -c "WD_POSITIONS_VANISHED\|POSITION_CONFIRMED" data/logs/workers.log

# MANUAL_CLOSE for Telegram-initiated closes.
grep -c "MANUAL_CLOSE\|MANUAL_CLOSE_OK\|MANUAL_CLOSE_FAIL" data/logs/general.log

# STRAT_CALL_B_LESSONS_INJECTED per CALL_B cycle.
grep -c "STRAT_CALL_B_LESSONS_INJECTED" data/logs/brain.log
```

---

## Section 4 — Cycle Completeness Check (5 Trades)

Pick 5 closed trades from the trial window. For each trade, walk through the lifecycle and verify EVERY step has at least one log event visible.

### Per-trade trace template

For each trade, populate this table by grepping for `tid=t-{symbol}-{ms}` (or via DB query for the trade_id):

| Lifecycle Step | Expected Tag(s) | Found? | Notes |
|---|---|---|---|
| 1.1 Price ingestion | PRICE_WS_HEALTH | Y/N | tid not bound at this layer |
| 1.6 Signal generation | SIG_BATCH / SIG_TICK_SUMMARY | Y/N | per-cycle aggregate |
| 1.7 Structure analysis | XRAY_TICK_SUMMARY / XRAY_CLASSIFY | Y/N | per-cycle |
| 1.11 Scanner ranking | SCANNER_SELECTED / SCANNER_LABELED | Y/N | per-coin, per-cycle |
| 1.12 Package construction | PACKAGE_VALIDATE / SCANNER_PACKAGE_BUILD_DONE | Y/N | per-cycle |
| 2.1 CALL_A scheduling | STRAT_CALL_A_START | Y/N | did= bound |
| 2.2 CALL_A prompt | STRAT_CALL_A_CTX / PROMPT_BUILD_DONE | Y/N | did= |
| 2.3 Claude CLI invocation | CLAUDE_CALL_START / CLAUDE_CALL_OK | Y/N | did= |
| 2.4 Response parse | PARSE_OK | Y/N | did= |
| 2.5 Directive emission | STRAT_DIRECTIVE | Y/N | did= |
| 3.1 APEX directive receipt | APEX_TIER / APEX_DIR_LOCK | Y/N | sym= |
| 3.3 OpenRouter call | APEX_TIMING / APEX_OK | Y/N | sym= |
| 3.6 APEX TP cap | APEX_TP_CAP | Y/N | only if cap fired |
| 4.1 TradeGate entry | GATE_TIMING | Y/N | sym= |
| 4.X Gate adjustments | GATE_ADJUST | Y/N | only if modifications |
| 4.15 Approval emission | STRAT_EXEC | Y/N | sym=, dir= |
| 5.4 BybitDemo body | BYBIT_DEMO_ORDER_RECEIVED | Y/N | sym=, link_id= |
| 5.6 HTTP POST | BYBIT_DEMO_ORD_SEND | Y/N | sym=, link_id= |
| 5.7 Response parse | BYBIT_DEMO_ORD_RESP | Y/N | sym=, oid= |
| 5.9 SL verification | SL_VERIFY_OK / SL_VERIFY_FAIL | Y/N | sym= |
| 5.10 Persistence | BYBIT_DEMO_PERSIST_OK ×3 (orders, trade, position) | Y/N | sym= |
| 5.11 TC registration | COORD_REG | Y/N | sym=, did=, order_id= |
| 5.12 Position confirmation | POSITION_CONFIRMED | Y/N | sym= |
| 6.X Active management | WD_TICK / M4_DECISION / SNIPER_*_GUARD | Y/N | per-tick, per-position |
| 6.14 L4P checks | L4P_CHECK | Y/N | sym=, protected= |
| 7.X Closure trigger | mode4_p9 / TIME_DECAY_FORCE_CLOSE / WD_HARD_STOP / etc. | Y/N | sym= |
| 7.10 Close fill confirmed | CLOSE_FILL_CONFIRMED | Y/N | sym=, oid= |
| 8.1 Position absent | WD_POSITIONS_VANISHED | Y/N | sym in list |
| 8.6 Close emission | WD_CLOSE | Y/N | sym=, close_trigger= |
| 9.1 Coordinator close end | COORD_CLOSE_END | Y/N | sym=, cbs_fired= |
| 9.3 Data lake write | DL_TRADE | Y/N | tid=, sym=, mode= |
| 9.4 TIAS analysis | TIAS_ANALYZED / TIAS_SAVE | Y/N | sym= |
| 9.6 Thesis update | THESIS_CLOSE | Y/N | sym= |
| 9.9 Performance enforcer | ENFORCER_TRADE_IN | Y/N | per-trade |
| 9.10 Capital tier | CAPITAL_TIER | Y/N | per-cycle |
| 9.11 Fund pools | FUND_POOLS | Y/N | per-cycle |
| 10.1 DeepSeek call | TIAS_DEEPSEEK_OK | Y/N | model=, latency_ms= |

If any cycle step shows N for all 5 trades, that's a verified gap that survived implementation.

---

## Section 5 — Performance Impact Measurement

```bash
# CALL_A latency distribution (compare to pre-audit baseline).
grep -oE "STRAT_CALL_A_END.*el=([0-9]+)ms" data/logs/brain.log | grep -oE "el=[0-9]+" | sort -t= -k2 -n | tail -20

# CALL_B latency distribution.
grep -oE "STRAT_CALL_B_END.*el=([0-9]+)ms" data/logs/brain.log | grep -oE "el=[0-9]+" | sort -t= -k2 -n | tail -20

# Watchdog tick latency (slow ticks = >500ms?).
grep -c "WD_TICK_SLOW" data/logs/workers.log

# Gate latency (slow gates = >500ms?).
grep -c "GATE_TIMING_SLOW" data/logs/workers.log

# Order placement latency. Compare BYBIT_DEMO_ORD_SEND timestamp to BYBIT_DEMO_ORD_RESP.
# (ad-hoc analysis — Python script if needed)
```

**Pass criteria:** No statistically significant degradation versus pre-audit baselines. Acceptable if median latency unchanged or within ±5%.

---

## Section 6 — Alert Volume Sanity

```bash
# Total alerts sent during trial.
grep -c "ALERT_SENT" data/logs/general.log

# Throttle / dedup events (DEBUG, may be invisible).
# If DEBUG enabled: grep -c "ALERT_THROTTLE" data/logs/general.log

# Per-priority breakdown.
grep -oE "ALERT_SENT \| level=([a-z]+)" data/logs/general.log | sort | uniq -c

# DL_TRADE_SUSPECT alerts (NEW — should be 0 in healthy state, fires only on data integrity violation).
grep -c "DL_TRADE_SUSPECT" data/logs/workers.log
grep -B 1 "DL_TRADE_SUSPECT" data/logs/general.log | head -10
```

**Pass criteria:**
- ALERT_SENT volume not significantly higher than pre-audit baseline (avoid Telegram spam).
- No DL_TRADE_SUSPECT firings (ideal) — or if firings exist, AlertManager.send_risk_warning fired alongside.

---

## Section 7 — Verification Report Output

Create `dev_notes/lifecycle_logging_audit/phase13_verification_report_<DATE>.md` with the following sections:

1. **Trial window:** start ts → end ts, duration, # of trades closed.
2. **Per-tag firing counts:** all greps above with absolute counts.
3. **Cycle completeness:** the 5-trade table fully populated.
4. **Performance impact:** latency distributions before/after.
5. **Alert volume:** counts + assessment.
6. **Outstanding gaps:** any tag that should have fired but didn't, or any tag that fired more than expected (noise).
7. **Sign-off:** operator signature + date.

---

## Section 8 — Post-Verification Decisions

After Phase 13 trial completes, the operator decides:

1. **Validator-bypass cleanup** (Phase 4.X): delete RiskManager.validate_trade + RuleEngine OR add inactive-state startup logs.
2. **Remaining ~30 MEDIUM gaps:** schedule a follow-up session for:
   - Phase 3 (APEX): APEX_ASSEMBLE_DONE rollup + APEX_SIZING + APEX_LEVERAGE additive tags
   - Phase 5: HMAC fail tag (small)
   - Phase 7: per-caller close_trigger= refinements at sniper/CALL_B/watchdog/time_decay sites
   - Phase 9: thesis verifications, Recovery Planner verification
3. **Remaining ~13 LOW gaps:** opportunistic cleanup (cosmetic).
4. **10.3-G1 TIAS lessons in CALL_A:** separate aggressive-framing review (if operator wants to re-enable).

---

## Phase 13 verification gate

| Gate | Status |
|---|---|
| Wiring 1.1 applied | AWAITING OPERATOR |
| System restarted | AWAITING OPERATOR |
| 6-12h trial run | AWAITING OPERATOR |
| Section 3 tag-firing checks completed | AWAITING OPERATOR |
| Section 4 cycle completeness check completed | AWAITING OPERATOR |
| Section 5 performance check completed | AWAITING OPERATOR |
| Section 6 alert volume sanity check completed | AWAITING OPERATOR |
| Section 7 verification report written | AWAITING OPERATOR |
| Section 8 outstanding decisions made | AWAITING OPERATOR |

**Audit complete when Phase 13 verification report is signed off.**
