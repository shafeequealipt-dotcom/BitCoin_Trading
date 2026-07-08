# Phase 11 — Comprehensive Gap Report And Operator Discussion

**Date:** 2026-05-09
**Audit:** Trade Lifecycle Logging And Observability Audit (per `/home/inshadaliqbal786/IMPLEMENT_LIFECYCLE_LOGGING_AUDIT.md`)
**Investigation phases completed:** Phase 0 (baseline) + Phases 1-10 (per-lifecycle-phase audits)
**Branch:** `feature/bybit-demo-adapter` at HEAD `895684c`

---

# Section 1 — Executive Summary

## Steps audited

| Lifecycle Phase | Steps |
|---|---|
| 1 — Analysis | 12 |
| 2 — Decision | 10 |
| 3 — Optimization | 8 |
| 4 — Validation | 15 |
| 5 — Execution | 12 |
| 6 — Active Management | 15 |
| 7 — Closure Triggers | 10 |
| 8 — Detection | 7 |
| 9 — Recording | 12 |
| 10 — Learning | 5 |
| **Total** | **106 steps** |

## Total gaps found

| Severity | Count | Notes |
|---|---|---|
| **CRITICAL** | **1** | data_lake.py 6 silent write failures (Phase 9.3-G1) |
| **HIGH** | **12** | trade-decision-affecting or safety-cuts |
| **MEDIUM** | **80** | debugging visibility / structured-tag conversions |
| **LOW** | **25** | cosmetic / prose duplicates / verifications |
| **Total** | **118** | |

## Estimated implementation effort

| Sub-phase | Effort | Rationale |
|---|---|---|
| 12.1 (Phase 1 fixes — 20 gaps) | 1-2 hours | Most fixes Trivial (single-line) |
| 12.2 (Phase 2 fixes — 13 gaps) | 1 hour | DEBUG→WARNING promotions |
| 12.3 (Phase 3 fixes — 8 gaps) | 1 hour | Easy new tags + verifications |
| 12.4 (Phase 4 fixes — 14 gaps) | 2 hours | Include validator-bypass investigation |
| 12.5 (Phase 5 fixes — 14 gaps) | 2-3 hours | Includes 5.8-G1 idempotent retry (Moderate) |
| 12.6 (Phase 6 fixes — 14 gaps) | 1-2 hours | M4_TRAIL_FLOOR compression + 22 prose lines |
| 12.7 (Phase 7 fixes — 9 gaps) | 2-4 hours | close_trigger inference is Moderate |
| 12.8 (Phase 8 fixes — 8 gaps) | 1 hour | Mostly DEBUG promotions |
| 12.9 (Phase 9 fixes — 11 gaps) | 1 hour | CRITICAL is 6 1-line changes |
| 12.10 (Phase 10 fixes — 7 gaps) | 1 hour | Includes operator confirmations |
| **Total** | **13-18 hours** | Variable based on operator scope |

Phase 13 verification: 1-2 days (live trial 6-12 hours + cycle completeness check + report).

---

# Section 2 — Top 10 Most Critical Gaps (Operator Priority)

## Rank 1 (CRITICAL) — Phase 9.3-G1 — `data_lake.py` 6 silent write failures

**Lifecycle phase:** 9 (Recording)
**Step:** 9.3 — Data Lake write_trade
**Files:** `src/core/data_lake.py:39-40, 117-118, 135-136, 156-157, 171-172, 198-199`
**Description:** All 6 `write_*` methods catch every exception and log at DEBUG (invisible at default INFO sink). Trade-data integrity depends on these writes succeeding — but the system cannot detect failures. This is the audit prompt's Section A explicit gap ("Silent write failures in data_lake.write_trade").

**Evidence:**
```python
# Line 117-118 (write_trade):
except Exception as e:
    log.debug("trade_log write failed: {err}", err=str(e))  # SILENT
```
Same pattern at lines 39-40 (market_snapshot), 135-136 (position_snapshot), 156-157 (claude_decision), 171-172 (event_log), 198-199 (daily_summary).

**Why it matters:** Trade outcomes that don't make it into trade_log are invisible. TIAS analysis and learning depend on this data. Strategy-edge measurement is impaired.

**Fix:** Promote all 6 `log.debug` → `log.warning` with structured tags:
- `DL_MARKET_SNAPSHOT_WRITE_FAIL`, `DL_TRADE_WRITE_FAIL`, `DL_POSITION_SNAPSHOT_WRITE_FAIL`, `DL_DECISION_WRITE_FAIL`, `DL_EVENT_WRITE_FAIL`, `DL_DAILY_SUMMARY_WRITE_FAIL`.
- Each carries `tid=` (when applicable), `sym=`, `err='{err}'`, `{ctx()}`.

**Difficulty:** Trivial — 6 sites × 1-line each.

---

## Rank 2 (HIGH) — Phase 9.3-G2 — DL_TRADE_SUSPECT no alert

**Lifecycle phase:** 9 (Recording)
**Step:** 9.3 — Data Lake write_trade
**Files:** `src/core/data_lake.py:78-80, 83-85`
**Description:** `DL_TRADE_SUSPECT` ERROR fires when pnl=0 with non-zero entry/exit (data integrity violation), but no AlertManager call. Audit prompt named this explicitly.

**Fix:** Add `await alert_manager.send_risk_warning("DL_TRADE_SUSPECT", {tid, sym, ent, ext})` at the ERROR site.

**Difficulty:** Easy — single new AlertManager call (use existing `send_risk_warning` method per Hard Rule "use existing methods").

---

## Rank 3 (HIGH) — Phase 7.1-G1 — close_trigger="exchange_match" hardcoded

**Lifecycle phase:** 7 (Closure Triggers)
**Files:** `src/bybit_demo/bybit_demo_adapter.py:239`
**Description:** ALL exchange-initiated closes (SL hit / TP hit / Trail hit) hardcode `close_trigger="exchange_match"`. Operators cannot distinguish trigger reasons from logs (223 WD_CLOSE events all carry the same trigger). Affects data_lake / TIAS / thesis_store / strategy-edge measurement. Audit prompt's #1 named gap.

**Fix:** Trigger inference at the watchdog when a closed position is detected:
```python
# Pseudo at watchdog:
close_price = shadow_close.get("exit_price")
if abs(close_price - last_known_sl) / last_known_sl < 0.002:
    close_trigger = "sl_hit"
elif abs(close_price - last_known_tp) / last_known_tp < 0.002:
    close_trigger = "tp_hit"
else:
    close_trigger = "exchange_match"  # truly unknown
```
Surface in WD_CLOSE: `WD_CLOSE | sym=... close_trigger=sl_hit close_price=... last_sl=... last_tp=...`.

**Difficulty:** Moderate — requires inference logic + state tracking of last-known SL/TP per position.

---

## Rank 4 (HIGH) — Phase 7.4-G1 — System-initiated closes lose trigger info

**Lifecycle phase:** 7 (Closure Triggers)
**Description:** Sniper / CALL_B / Watchdog / Time decay all KNOW their trigger reason locally (mode4_p9, callb, hard_stop, time_decay_age, etc.) but `BYBIT_DEMO_POSITION_CLOSE` doesn't carry the field through.

**Fix:** Add `close_trigger=` parameter to `bybit_demo_adapter.close_position(symbol, purpose, close_trigger="...")`. Caller passes the source-specific value. BYBIT_DEMO_POSITION_CLOSE surfaces it.

**Difficulty:** Easy — extend signature + propagate at 4 call sites (sniper, callb, watchdog, time_decay).

---

## Rank 5 (HIGH) — Phase 5.10-G1 — BYBIT_DEMO_PERSIST_OK missing

**Lifecycle phase:** 5 (Execution) / overlaps with 9 (Recording)
**Description:** P7 fix added persistence to trade_history/orders/positions tables but only added FAIL tags (BYBIT_DEMO_PERSIST_*_FAIL). No success log. Operators cannot confirm "trade was persisted" without grepping the DB.

**Fix:** Add `BYBIT_DEMO_PERSIST_OK | sym={s} table=trade_history row_id={id} | {ctx()}` (and orders, positions) at success points.

**Difficulty:** Easy — 3 new logs.

---

## Rank 6 (HIGH) — Phase 5.9-G1 — SL VERIFIED prose (5 sites)

**Lifecycle phase:** 5 (Execution)
**Files:** `src/trading/services/order_service.py:663-674`
**Description:** 5 prose lines for SL VERIFIED / SL FAILED / verification-exception — the trade's primary safety boundary has unstructured logging. Operator cannot grep "SL missing on exchange after place" rate.

**Fix:** Replace with `SL_VERIFY_OK / SL_VERIFY_FAIL / SL_VERIFY_RETRY_OK / SL_VERIFY_RETRY_FAIL / SL_VERIFY_EXCEPTION` structured tags.

**Difficulty:** Easy — 5 sites.

---

## Rank 7 (HIGH) — Phase 6.14-G1 — Layer 4 Protection silent (verify active)

**Lifecycle phase:** 6 (Active Management)
**Files:** `src/risk/layer4_protection.py`
**Description:** All L4P_* tags show 0-1 firings across all rotations despite Layer 4 Realignment shipped 2026-05-06 with 130 tests. Either L4P is genuinely silent (no triggers) OR L4P is bypassed.

**Fix:**
1. **Verify** L4P is actually invoked per cycle (operator action — read code path).
2. If active: add `L4P_TICK | positions_evaluated=N protections_active=N | {ctx()}` heartbeat.
3. If bypassed: investigate why and decide whether to fix wiring or remove dead code.

**Difficulty:** Easy if confirmed bypassed; Trivial if just adding heartbeat.

---

## Rank 8 (HIGH) — Phase 8.2-G1 — BYBIT_DEMO_LAST_CLOSE_RETRY DEBUG-invisible

**Lifecycle phase:** 8 (Detection)
**Files:** `src/bybit_demo/bybit_demo_adapter.py:185-189`
**Description:** P3 fix wired retry but per-retry log is at DEBUG (line 185). Operators can't see retry attempts mid-loop. The OK and EXHAUSTED tags ARE INFO and visible.

**Fix:** Promote DEBUG to INFO at line 185.

**Difficulty:** Trivial — single line.

---

## Rank 9 (HIGH) — Phase 4.X — 3 validation surfaces appear bypassed

**Lifecycle phase:** 4 (Validation)
**Files:** `src/risk/validators.py`, `src/core/sl_tp_validator.py`, `src/core/rule_engine.py`
**Description:** RISK_BLOCK = 0, SLTP_SKIP = 0, RULE_EVAL_START/END = 0 firings in current rotation. Either inactive in current Bybit demo flow OR active-but-failing-silently.

**Fix:**
1. **Verify** active call graph for each validator (operator + Phase 12 audit).
2. If bypassed: document as deprecated or operator decides to re-enable.
3. If active but silent: add `*_INVOKED` heartbeat at validator entry (DEBUG OK) so operators can confirm it's running.

**Difficulty:** Easy — depends on operator decision.

---

## Rank 10 (HIGH) — Phase 5.8-G1 — Idempotent retry missing for place_order

**Lifecycle phase:** 5 (Execution)
**Description:** Audit notes this as missing for Bybit demo. Currently `place_order` has no idempotent retry on transient failure (HTTP 5xx, rate-limit, timeout). P3 fix only addressed `last_close`.

**Fix:** This is a **functional gap, not just observability**. Add retry loop with `orderLinkId` reuse for transient failures. Would gain `BYBIT_DEMO_PLACE_RETRY` tag.

**Difficulty:** Moderate — requires retry loop + orderLinkId state tracking.

**Operator decision required:** Fix as part of this audit (scope creep) OR defer to a separate P-fix follow-up?

---

# Section 3 — Complete Gap Catalog By Lifecycle Phase

## Phase 1 (Analysis) — 20 gaps

| ID | Severity | Description | Difficulty |
|---|---|---|---|
| 1.1-G1 | LOW | 3-5 prose duplicates in price_worker | Trivial — delete |
| 1.1-G2 | MEDIUM | 2 DEBUG events in price_worker invisible | Easy — counter rollup |
| 1.2-G1 | MEDIUM | KLINE_FRESHNESS_SKIP DEBUG → WARNING | Trivial |
| 1.3-G1 | LOW | altdata "no sources due this tick" prose → ALTDATA_NO_SOURCES_DUE | Trivial |
| 1.4-G1 | MEDIUM | open_interest "Failed to fetch OI" prose → OI_FETCH_FAIL structured | Trivial |
| 1.4-G2 | MEDIUM | FUNDING_FETCH_FAIL missing `\| {ctx()}` suffix | Trivial |
| 1.4-G3 | LOW | DEBUG-only "skipped"/"fetched N" lines | Trivial |
| 1.5-G1 | LOW | "Ticker snapshot" event NOT FOUND — document equivalent (PRICE_WS_HEALTH) | Doc only |
| 1.6-G1 | MEDIUM | signal_worker per-coin INFO 50/cycle prose → demote to DEBUG | Trivial |
| 1.6-G2 | MEDIUM | sentiment aggregation prose → SIG_SENT_AGG_FAIL structured | Trivial |
| 1.6-G3 | MEDIUM | signal_worker top-level error prose → SIG_GEN_FAIL structured | Trivial |
| 1.7-G1 | MEDIUM | XRAY_CLASSIFY (NONE) DEBUG redundant with XRAY_NONE_REASON — delete | Trivial |
| 1.7-G2 | MEDIUM | XRAY_SCANNER_ERR DEBUG → WARNING | Trivial |
| 1.7-G3 | LOW | XRAY_NONE_REASON_FAIL / XRAY_CACHE_HEALTH_SKIP DEBUG | Trivial |
| 1.8-G1 | LOW | regime_worker "Regime: {r}" prose duplicates REGIME_GLOBAL | Trivial — delete |
| 1.8-G2 | MEDIUM | regime_worker silent except-pass for cleanup → REGIME_CLEANUP_FAIL | Trivial |
| 1.9-G1 | MEDIUM | ensemble.py "Strategy {n} vote failed" prose → STRAT_VOTE_FAIL | Trivial |
| 1.9-G2 | LOW | strategy_worker "Layer 4: N hints" prose duplicates STRAT_L4 | Trivial |
| 1.9-G3 | MEDIUM | strategy_worker SL/TP prose 8 sites → SLTP_ADJUST/VALIDATE/AUTO_CORRECT | Easy |

## Phase 2 (Decision) — 13 gaps

| ID | Severity | Description | Difficulty |
|---|---|---|---|
| 2.1-G1 | MEDIUM | No STRAT_CYCLE_GATE log for "fire CALL_A vs CALL_B" decision | Easy |
| 2.1-G2 | MEDIUM | STRAT_CALL_A_PRECHECK_ERR DEBUG → WARNING | Trivial |
| **2.2-G1** | **HIGH** | **X-RAY context build silent fail in CALL_A & CALL_B → XRAY_CTX_BUILD_FAIL WARNING** | **Trivial** |
| 2.2-G2 | MEDIUM | Account balance fetch fail DEBUG → STRAT_CTX_BALANCE_FAIL WARNING | Trivial |
| 2.2-G3 | MEDIUM | Tiered capital limits fail DEBUG → STRAT_CTX_TIERED_CAPITAL_FAIL | Trivial |
| 2.2-G4 | MEDIUM | Daily PnL fail DEBUG → STRAT_CTX_DAILY_PNL_FAIL | Trivial |
| 2.2-G5 | LOW | 26 other DEBUG context-build sites — STRAT_CTX_DEGRADED rollup | Easy |
| 2.3-G1 | LOW | claude_code_client "Telegram alert callback registered" prose | Trivial |
| 2.3-G2 | LOW | claude_code_client line 462 prose duplicates CLAUDE_AUTH | Trivial |
| 2.3-G3 | LOW | CLAUDE_RATE / CLAUDE_PROMPT DEBUG — acceptable as-is | None |
| 2.4-G1 | LOW | decision_parser "Parsed decision" prose duplicates PARSE_OK | Trivial |
| 2.4-G2 | MEDIUM | "Parsed watchdog decision" prose → PARSE_OK_WD structured | Trivial |
| 2.4-G3 | MEDIUM | "Invalid watchdog action" prose → PARSE_INVALID_WD_ACTION | Trivial |
| 2.4-G4 | MEDIUM | PARSE_JSON DEBUG strategy markers → strategy field on PARSE_OK | Trivial |
| 2.6-G1 | MEDIUM | Same as 2.1-G1 (cycle gate visibility for CALL_B) | Easy |
| 2.10-G1 | LOW | "Strategic plan creation failed" prose duplicates STRAT_PLAN_FAIL | Trivial |
| 2.10-G2 | MEDIUM | "Position review failed" prose → STRAT_POS_REVIEW_FAIL structured | Trivial |

## Phase 3 (Optimization) — 8 gaps

| ID | Severity | Description | Difficulty |
|---|---|---|---|
| 3.2-G1 | MEDIUM | 7 APEX_ASSEMBLE_* success at DEBUG → add APEX_ASSEMBLE_DONE per-coin INFO rollup | Easy |
| 3.2-G2 | LOW | APEX_ASSEMBLE_OB DEBUG fail — document as intentional | Trivial — comment |
| 3.3-G1 | LOW | qwen_client.py — add per-call latency log | Easy |
| 3.3-G2 | LOW | qwen_client.py session close DEBUG → INFO | Trivial |
| 3.5-G1 | MEDIUM | No APEX_SIZING tag — add input/factor/output | Easy |
| 3.7-G1 | MEDIUM | No APEX_LEVERAGE tag — add input/factor/output | Easy |
| 3.8-G1 | MEDIUM | No APEX_FALLBACK / using_defaults marker — add at fallback exit | Easy |
| 3.8-G2 | LOW | APEX_OK missing el_ms field — consolidate with APEX_TIMING | Trivial |

## Phase 4 (Validation) — 14 gaps

| ID | Severity | Description | Difficulty |
|---|---|---|---|
| 4.1-G1 | LOW | GATE_PASS DEBUG acceptable as-is | None |
| 4.2-G1 | MEDIUM | GATE_POS_CHECK exception DEBUG → WARNING | Trivial |
| 4.3-G1 | MEDIUM | GATE_CAP_CHECK + CONVICTION_WEIGHT_FAIL DEBUG → WARNING | Trivial |
| 4.5 same as 4.2 | MEDIUM | GATE_RR_CHECK exception DEBUG → WARNING | Trivial |
| 4.6-G1 | MEDIUM | SLTP_SKIP 0 firings — verify validator is in path | Easy — verify |
| 4.6-G2 | MEDIUM | GATE_TPSL_CHECK exception DEBUG → WARNING | Trivial |
| 4.8 same as 4.2 | MEDIUM | GATE_DUP_CHECK + GATE_COOL_CHECK exception DEBUG → WARNING | Trivial |
| 4.10-G1 | LOW | _VALID_PURPOSES NOT FOUND — operator confirms intent | Doc only |
| 4.11-G1 | MEDIUM | SUPPORTED_SYMBOLS silent at validator level — depends on validator-active | Easy if needed |
| 4.12-G1 | MEDIUM | Mandatory-SL silent at validator level — depends on validator-active | Easy if needed |
| 4.13-G1 | MEDIUM | No dedicated max-loss cap — operator decides if needed | Easy if needed |
| 4.14-G1 | LOW | Position-size cap covered by GATE_ADJUST — acceptable | None |
| 4.X-G1 | MEDIUM | strategy_worker SL/TP prose 8 sites (cross-cut Phase 1-G3) | Easy |
| **HIGH** | **HIGH** | **3 validation surfaces appear bypassed (RISK_BLOCK / SLTP_SKIP / RULE_EVAL = 0)** | **Easy — verify + decide** |

## Phase 5 (Execution) — 14 gaps

| ID | Severity | Description | Difficulty |
|---|---|---|---|
| 5.1-G1 | LOW | Verify OrderService.place_order entry tag pattern | Verify |
| 5.2-G1 | MEDIUM | 17 Transformer prose lines → XFORM_* structured | Easy |
| 5.5-G1 | LOW | No HMAC_FAIL tag — add for forensic | Easy |
| 5.6-G1 | MEDIUM | Verify BYBIT_DEMO_HTTP_FAIL or equivalent | Verify |
| 5.7-G1 | LOW | ORD_SEND > ORD_RESP by 1 — verify | Verify |
| **5.8-G1** | **HIGH** | **No idempotent retry for place_order — functional gap** | **Moderate** |
| **5.9-G1** | **HIGH** | **5 SL VERIFIED prose lines → SL_VERIFY_* structured** | **Easy** |
| **5.10-G1** | **HIGH** | **No BYBIT_DEMO_PERSIST_OK for P7 success path** | **Easy** |
| 5.11-G1 | MEDIUM | No TC_REGISTER tag distinct from COORD_QUEUE | Easy |
| 5.11-G2 | LOW | COORD_CB_OK DEBUG acceptable | None |
| 5.12-G1 | MEDIUM | No POSITION_CONFIRMED log | Easy |

## Phase 6 (Active Management) — 14 gaps

| ID | Severity | Description | Difficulty |
|---|---|---|---|
| 6.2-G1 | LOW | No per-call get_positions log — add WD_POSITIONS_FETCHED DEBUG | Easy |
| 6.4-G1 | MEDIUM | 11 watchdog prose error lines → WD_*_FAIL structured | Easy |
| 6.4-G2 | LOW | 4 watchdog prose informational lines | Trivial |
| 6.6-G1 | MEDIUM | M4_TRAIL_FLOOR 42k firings — compress to emit on change | Easy |
| 6.7-G1 | LOW | M4_EVAL/LOG_FAIL/SKIP 0 firings — verify | Verify |
| 6.7-G2 | LOW | M4_ACT_TIGHTEN_AGG 0 firings — verify reachability | Verify |
| 6.10-G1 | MEDIUM | mode4_p9 (691) vs M4_ACT_CLOSE (97) 7x ratio — trace | Verify |
| 6.11-G1 | LOW | TIME_DECAY_FORCE_CLOSE_TRACE / GRACE / STRUCT_INVALIDATED 0 firings | Verify |
| **6.14-G1** | **HIGH** | **Layer 4 Protection silent — verify active** | **Easy if bypassed** |
| 6.X-G1 | MEDIUM | 7-8 sniper prose error lines → M4_*_FAIL structured | Easy |

## Phase 7 (Closure Triggers) — 9 gaps

| ID | Severity | Description | Difficulty |
|---|---|---|---|
| **7.1-G1** | **HIGH** | **close_trigger="exchange_match" hardcoded → trigger inference** | **Moderate** |
| 7.4-G1 | HIGH | System-initiated closes lose trigger info → close_trigger= parameter | Easy |
| 7.5/7.6/7.7 | MEDIUM | (Same as 7.4-G1 — single fix covers all) | Easy |
| 7.8-G1 | MEDIUM | No MANUAL_CLOSE tag for Telegram path | Easy |
| 7.9-G1 | LOW | 5 BYBIT_DEMO_CLOSE_* error tags 0 firings — verify | Verify |
| 7.10-G1 | MEDIUM | No CLOSE_FILL_CONFIRMED log between place and last_close | Easy |

## Phase 8 (Detection) — 8 gaps

| ID | Severity | Description | Difficulty |
|---|---|---|---|
| 8.1-G1 | MEDIUM | No WD_POSITION_MISSING set-difference log | Easy |
| **8.2-G1** | **HIGH** | **BYBIT_DEMO_LAST_CLOSE_RETRY DEBUG → INFO** | **Trivial** |
| 8.2-G2 | LOW | All 3 retry tags 0 firings — verify | Verify |
| 8.4-G1 | MEDIUM | 10.3% price-fallback rate — Phase 11 analyze cause | Doc/analysis |
| 8.7-G1 | LOW | No WD_CLOSE_DEDUP idempotency log | Easy if applicable |

## Phase 9 (Recording) — 11 gaps

| ID | Severity | Description | Difficulty |
|---|---|---|---|
| 9.2-G1 | LOW | COORD_CB_OK DEBUG — acceptable (cbs_fired aggregate) | None |
| **9.3-G1** | **CRITICAL** | **6 silent data_lake exception swallows → DL_*_WRITE_FAIL WARNING** | **Trivial — 6 sites** |
| **9.3-G2** | **HIGH** | **DL_TRADE_SUSPECT no alert → wire to AlertManager.send_risk_warning** | **Easy** |
| **9.4-G1** | **HIGH** | **TIAS_PHASE2 visibility — add TIAS_DEEPSEEK_OK/FAIL tags** | **Easy** |
| 9.6-G1 | LOW | THESIS_CLOSE 413 vs expected 394 — verify extras | Verify |
| 9.6-G2 | MEDIUM | THESIS_FAIL 0 firings — verify thesis_manager exception handling | Verify |
| 9.11-G1 | LOW | "Capital pools updated:" prose duplicates FUND_POOLS — delete | Trivial |
| 9.12-G1 | MEDIUM | Recovery Planner inactive — verify | Verify |

## Phase 10 (Learning) — 7 gaps

| ID | Severity | Description | Difficulty |
|---|---|---|---|
| 10.1-G1 | MEDIUM | No per-call DeepSeek tag — add TIAS_DEEPSEEK_OK/FAIL | Easy |
| 10.2-G1 | LOW | No TIAS_REPO_UPDATE_OK — verify TIAS_ANALYZED implies UPDATE success | Verify |
| **10.3-G1** | **HIGH** | **TIAS lessons removed from CALL_A by design — operator confirms intent** | **Decision** |
| 10.4-G1 | MEDIUM | No STRAT_CALL_B_LESSONS_INJECTED log | Easy |
| 10.4-G2 | MEDIUM | "Thesis and lessons fetch failed" DEBUG → WARNING | Trivial |
| 10.5-G1 | LOW | Strategy parameter feedback not implemented — document | Doc only |

---

# Section 4 — Working Coverage (What's Healthy)

## Tags firing reliably (>100 emissions in current rotation or rotated logs)

**Phase 1 (Analysis):**
- PRICE_WS_HEALTH (189), KLINE_TICK_SUMMARY (29), KLINE_FETCH (29), ALTDATA_TICK_DONE (29)
- XRAY_CLASSIFY (136), XRAY_NONE_REASON (12), STRAT_VOTE_TRACE (13), ENSEMBLE (54)
- SCANNER_SELECTED (45), PACKAGE_VALIDATE (45), STRAT_TOP_N_APPLIED (179)

**Phase 2 (Decision):**
- STRAT_CALL_A (3,756), STRAT_CALL_B (2,678), STRAT_DIRECTIVE (2,344)
- CLAUDE_CALL_OK (1,732), CLAUDE_PROC_SPAWNED (1,049), PROMPT_BUILD_DONE (783)
- STRAT_CALL_A_END (680), STRAT_CALL_B_PARSED (353), STRAT_POS_ACT (2,394)
- POSITION_INVALIDATED (479)

**Phase 3 (Optimization):**
- APEX_PRICE_SOURCE (524), APEX_TIER (522), APEX_DIR_LOCK (346)
- APEX_FLIP (151), APEX_GUARDRAIL_TP_FLOOR (262), APEX_TIMING (28)

**Phase 4 (Validation):**
- GATE_TIMING (528), CONVICTION_WEIGHT (521), GATE_ADJUST (486)
- BRAIN_DO_TRADE (521), STRAT_EXEC (440), TRADE_SKIP (117), CONVICTION_SIZE_CAP (154)

**Phase 5 (Execution):**
- BYBIT_DEMO_ORDER_RECEIVED (130), BYBIT_DEMO_ORD_SEND (130), BYBIT_DEMO_ORD_RESP (129)
- COORD_CLOSE_END (394), COORD_QUEUE (203)

**Phase 6 (Active Management):**
- WD_TICK (43,176), M4_DECISION (23,316), M4_GATED (10,130), SNIPER_AGE_GUARD (11,442)
- TIME_DECAY_MAE_GUARD (8,656), TIME_DECAY_AGE_GUARD (1,718), TIME_DECAY_STRUCT_GUARD (1,439)
- mode4_p9 (691), M4_ACT_TIGHTEN (681), M4_ACT_CLOSE (97), SENTINEL_DEADLINE (529)

**Phase 7-8 (Closure + Detection):**
- WD_CLOSE (223), WD_LAST_CLOSE_AUTH (186), BYBIT_DEMO_POSITION_CLOSE (79)
- WD_CLOSE_PRICE_FALLBACK (23), WD_LAST_CLOSE_FALLBACK (11)

**Phase 9-10 (Recording + Learning):**
- CAPITAL_TIER (6,576), FUND_POOLS (6,264), FUND_RECONCILE (5,837)
- ENFORCER_BEAT (5,844), ENFORCER_STATE (5,812), ENFORCER_TRADE_IN (394)
- THESIS_CLOSE (413), TIAS_SAVE (394), TIAS_ANALYZED (393)

These ARE healthy reference points. Phase 12 fixes are ADDITIVE — they fill gaps without replacing what's working.

## Excellent overall coverage

- Phase 2 (Decision): the most heavily-instrumented phase. `did=` propagation is the gold standard.
- Phase 6 (Active Management): 100+ structured tags. M4_*, WD_*, SNIPER_*, TIME_DECAY_* families exhaustive.
- Phase 9 (Recording): well-covered for Performance Enforcer, Capital Tier, Fund Manager. CRITICAL gap is concentrated in data_lake.py.

---

# Section 5 — Recommended Implementation Order (For Operator Decision)

## Tier A — Critical & quick (target Phase 12.1, ~1-2 hours total)

1. **9.3-G1 (CRITICAL)** — data_lake.py 6 silent write failures → DL_*_WRITE_FAIL WARNING tags. **Trivial — 6 sites × 1-line.**
2. **9.3-G2 (HIGH)** — DL_TRADE_SUSPECT alert wiring. **Easy — single AlertManager call.**
3. **8.2-G1 (HIGH)** — BYBIT_DEMO_LAST_CLOSE_RETRY DEBUG → INFO. **Trivial — 1 line.**
4. **2.2-G1 (HIGH)** — X-RAY context build silent fail (DEBUG → WARNING + structured). **Trivial — 2 sites.**

**Tier A total:** ~10 sites, 1-2 hours, ships highest-leverage fixes immediately.

## Tier B — High value, Easy difficulty (target Phase 12.2-12.5, ~6-8 hours)

5. **5.9-G1 (HIGH)** — SL_VERIFY structured tags. Easy — 5 sites.
6. **5.10-G1 (HIGH)** — BYBIT_DEMO_PERSIST_OK. Easy — 3 sites.
7. **7.4-G1 (HIGH)** — close_trigger= parameter through close_position. Easy — 4 callers.
8. **6.14-G1 (HIGH)** — Layer 4 Protection verify + heartbeat. Easy.
9. **4.X (HIGH)** — Validator-bypass investigation + decide. Easy — verification + decision.
10. **9.4-G1 / 10.1-G1 (HIGH)** — TIAS_DEEPSEEK_OK/FAIL tags. Easy.

## Tier C — Medium value, Easy difficulty (target Phase 12.6-12.10)

- 80 MEDIUM gaps mostly DEBUG→WARNING promotions, prose→structured replacements.
- M4_TRAIL_FLOOR compression (6.6-G1) — single change, big log-volume reduction.
- Transformer 17 prose lines (5.2-G1) — single sub-phase batch.
- Watchdog 11 prose error lines (6.4-G1) — single sub-phase batch.
- Sniper 7-8 prose error lines (6.X-G1) — single sub-phase batch.

## Tier D — Verification & cleanup

- 25 LOW gaps mostly cosmetic (delete duplicates, doc-only, verify).

## Tier E — Moderate difficulty / requires design

- **7.1-G1 (HIGH)** — close_trigger inference logic at watchdog. **Moderate.** Requires last-known-SL/TP state tracking.
- **5.8-G1 (HIGH)** — Idempotent retry for place_order. **Moderate.** Functional gap, not just observability. **Operator decides scope.**
- **10.3-G1 (HIGH)** — TIAS lessons re-injection into CALL_A. **Operator decision** — current code intentionally removed; if operator wants to re-enable, that's a separate aggressive-framing review.

## Operator decisions required

1. **Implement Tier A only? Tier A+B? Full A+B+C? All four (A+B+C+D)? Or A+B+C+D+E?**
2. **5.8-G1 idempotent retry**: include in this audit (scope creep) or defer to separate P-fix?
3. **7.1-G1 close_trigger inference**: include (Moderate complexity) or defer?
4. **10.3-G1 TIAS lessons removed from CALL_A**: confirm intentional. If re-enabling, separate review.
5. **6.14-G1 Layer 4 Protection silent**: investigation result determines fix shape.
6. **Phase 4 validator bypass**: confirm or audit deeper before deciding.

---

# Section 6 — Operator Discussion Format

The operator will read this report and make decisions. Suggested discussion structure:

## Per Tier — Quick proposal

**Tier A — fast critical fixes (1-2 hours)**
> *Recommendation:* Implement immediately. All Trivial fixes that close the audit's #1-named gap (silent data_lake) plus the X-RAY context fail and last-close retry visibility. Zero risk to existing flow; pure observability addition.

**Tier B — high-value Easy fixes (6-8 hours)**
> *Recommendation:* Implement after Tier A. Fills the named-by-audit gaps (SL_VERIFY structured, persist OK, close_trigger param, Layer 4 verify, TIAS DeepSeek tags). Closes most HIGH gaps.

**Tier C — Medium gaps (6-10 hours)**
> *Recommendation:* Implement subset based on operator priority. M4_TRAIL_FLOOR compression alone is highest-leverage (drops daily log volume noticeably). Watchdog/sniper prose batch is single-commit-friendly.

**Tier D — Cleanup**
> *Recommendation:* Implement opportunistically — most are verifications or single-line deletes.

**Tier E — Moderate / scope decisions**
> *Recommendation per item:*
> - 7.1-G1: WORTH IT — closes the audit's #1 named gap structurally; ~4 hours.
> - 5.8-G1: DEFER to separate P-fix — functional change, not in audit scope.
> - 10.3-G1: CONFIRM intent — if re-enabling, separate review.

---

# Section 7 — Operator Sign-Off Section

**Operator approved scope (2026-05-09):** **Full A + B + C + D + E** — all 118 gaps addressed.

**Specific decisions:**
- **5.8-G1 (idempotent retry for place_order):** **INCLUDE** in Phase 12 (functional + observability).
- **7.1-G1 (close_trigger inference at watchdog):** **INCLUDE** in Tier B (the structural fix for the audit's #1 named gap).
- **10.3-G1 (TIAS lessons re-injection into CALL_A):** **DEFER** — separate aggressive-framing review later.

**Implementation strategy:**
- Sub-phases 12.1 through 12.10 in lifecycle order, atomic commits per sub-phase (or per file within sub-phase).
- Tier A items (CRITICAL data_lake + DL_TRADE_SUSPECT alert + last_close DEBUG promotion + X-RAY ctx fail) execute as part of their respective sub-phases (12.2, 12.8, 12.9) but flagged as priority.
- `pytest tests/test_logging_routing.py` runs once per sub-phase to confirm no `COMPONENT_ROUTING` regressions.
- Live verification deferred to Phase 13 batch (per Phase 0 operator decision).

**Gaps deferred:** 10.3-G1 (TIAS lessons re-injection — separate review).

**Estimated total Phase 12 effort:** ~20-25 hours.

**Date approved:** 2026-05-09

---

## Phase 11 verification gate

| Gate | Status |
|---|---|
| All 10 lifecycle phase audits consolidated | PASS |
| Executive summary with severity counts | PASS |
| Top-10 critical gaps listed with evidence | PASS |
| Complete gap catalog by phase | PASS (118 gaps) |
| Working coverage section (what's healthy) | PASS |
| Recommended implementation order (tiers) | PASS |
| Operator decisions enumerated | PASS |
| Operator sign-off section | AWAITING OPERATOR |

**Phase 11 verification gate:** PASS for the report. **AWAITING OPERATOR APPROVAL** before Phase 12 implementation begins.

---

## Phase 12 cannot begin until operator approves scope

Per audit prompt Hard Rule 2 ("Discuss with operator before implementing") and the Phase 11 verification gate ("Operator has approved implementation plan, plan documented") — Phase 12 implementation is BLOCKED until operator reviews this report and decides scope.
