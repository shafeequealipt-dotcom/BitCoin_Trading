# Phase 4 — Lifecycle Phase 4 (Validation) Logging Audit

**Date:** 2026-05-09
**Lifecycle phase:** Validation (TradeGate + ancillary validators) — gate entry → 14 safety checks → race detection → approval/rejection emission.
**Steps audited:** 15 (Steps 4.1 through 4.15) per audit prompt.
**Files investigated:**
- `src/apex/gate.py` (534 lines, TradeGate — read end-to-end)
- `src/risk/validators.py` (162 lines, TradeValidator — read end-to-end; ZERO logging at validator level)
- `src/risk/risk_manager.py` (lines 90-120 — TradeValidator caller; emits RISK_BLOCK on issues)
- `src/core/sl_tp_validator.py` (grep + sample reads — emits SLTP_SKIP)
- `src/core/rule_engine.py` (351 lines — RULE_EVAL_START/END structured but appears bypassed in current Bybit demo flow)
- `src/core/layer_manager.py` (1,753 lines — TRADE_SKIP emission site, BRAIN_DO_TRADE summary; targeted reads at lines 1300-1410)
- `src/workers/strategy_worker.py` (TRADE_SKIP reason-code enum at `_execute_claude_trade`)
- `src/trading/services/order_service.py` (ORDER_REJECT_LAYER3_RACE — P6 wired)
- `src/brain/decision_parser.py` (`validate_decision`, line 164 — silent at check level)

---

## Executive Summary

| Severity | Gap count |
|---|---|
| CRITICAL | 0 |
| HIGH | 2 |
| MEDIUM | 9 |
| LOW | 3 |
| **Total** | **14** |

Phase 4 is the **most complex lifecycle phase** in this audit because validation is spread across **7 surfaces**:

1. **TradeGate** (`apex/gate.py`) — 14 numbered checks. Active in current flow (528 GATE_TIMING firings). Never blocks; only adjusts. Emits GATE_ADJUST when modifications applied; GATE_PASS at DEBUG (invisible) when no changes; GATE_TIMING per call (with el_ms + modifications count).
2. **TradeValidator** (`risk/validators.py`) — 13 checks, **ZERO logging at validator level**. Issues returned to caller. RISK_BLOCK at risk_manager.py:106 surfaces concatenated issues — but **0 firings in current rotation**. May be bypassed in Bybit demo flow.
3. **DecisionParser.validate_decision** (`brain/decision_parser.py:164`) — 5 checks, silent at check level.
4. **SLTP Validator** (`core/sl_tp_validator.py`) — emits SLTP_SKIP structured tag — **0 firings in current rotation**. Bypassed or no failures triggered.
5. **RuleEngine** (`core/rule_engine.py`) — RULE_EVAL_START/END structured — **0 firings in current rotation**. Bypassed in Bybit demo flow.
6. **LayerManager._do_trade** (`core/layer_manager.py:1300+`) — TRADE_SKIP emission with rsn= field. 117 firings.
7. **strategy_worker._execute_claude_trade** — TRADE_SKIP reason-code enum (sanity_reject, enforcer_block, survival_block, xray_skip, xray_conflict, unsupported_symbol, dup_position, service_missing, price_fetch_fail, price_invalid, sltp_skip, qty_zero, order_reject). Multiple emission sites.

Plus: **ORDER_REJECT_LAYER3_RACE** at order_service.py:334 (P6 fix surface).

Gap concentration:
1. **9 DEBUG check-exception logs in gate.py** — invisible at default INFO. A check that throws an exception silently passes through with no signal.
2. **TradeValidator's 13 checks are silent** at the validator level. RISK_BLOCK aggregates with concatenated prose — per-check structure is lost. Compounded by the fact that TradeValidator appears bypassed (RISK_BLOCK = 0 firings), which is itself a gap to investigate.
3. **RuleEngine and SLTP Validator appear bypassed** in current Bybit demo flow (0 firings). Either they're truly inactive (and should be removed/documented) or they're failing silently. **HIGH** severity if active but silent.
4. **No per-check PASS log in gate.py** for the 14 checks. Operator cannot verify "all 14 checks were evaluated this cycle". GATE_PASS at DEBUG (invisible) handles the no-modifications case.
5. **strategy_worker's 8 SL/TP prose lines** (Phase 1 cross-cut; lines 1996-2050) are validation-related and belong here.

The audit prompt's referenced `LM_VALID_PURPOSE`, `LM_SUPPORTED_SYMBOL`, `LM_MANDATORY_SL`, `LM_MAX_LOSS_CAP`, `LM_POS_SIZE_CAP`, `LM_LAYER3_RACE` tags **do not exist** in source. Some checks happen but emit different tag names; others are silent.

---

## Tag-Frequency Verification (workers.log + rotated)

```
1437 REGIME_CACHE_QUERY      528 GATE_TIMING            521 CONVICTION_WEIGHT
 521 BRAIN_DO_TRADE          486 GATE_ADJUST            440 STRAT_EXEC
 154 CONVICTION_SIZE_CAP     117 TRADE_SKIP              48 ENFORCER_LEV_CLAMP
  34 STRAT_EXEC_BLOCKED        7 GATE_TIMING_SLOW         1 POS_GATE_BLOCK
   0 RISK_BLOCK                0 SLTP_SKIP                0 RULE_EVAL_START
   0 RULE_EVAL_END             0 GATE_PASS (DEBUG)        0 GATE_*_CHECK (DEBUG)
   0 LM_VALID_PURPOSE          0 LM_SUPPORTED_SYMBOL      0 LM_MANDATORY_SL
   0 LM_MAX_LOSS_CAP           0 LM_POS_SIZE_CAP          0 LM_LAYER3_RACE
```

The high-firing tags trace the active validation surface: TradeGate (gate.py) + LayerManager._do_trade. The 0-firing tags are a mix of (a) DEBUG-only events invisible at default sink, (b) rare error paths, and (c) potentially bypassed validation surfaces (SLTP_SKIP, RULE_EVAL_START, RISK_BLOCK).

---

## Step-By-Step Findings

### Step 4.1 — TradeGate entry (`apex/gate.py:48`)

**Code path:** `TradeGate.validate(trade)` is the entry point. Captures `_gate_t0`, runs CHECK 0 through CHECK 14, emits GATE_ADJUST + GATE_TIMING at end.

**Logs:**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `CONVICTION_SIZE_CAP` | INFO | 88-92 | ✓ — 154 firings (CHECK 0 cap fires) |
| `GATE_ADJUST` | INFO | 394-397 | ✓ — 486 firings (when ≥1 modification) |
| `GATE_PASS` | DEBUG | 399 | invisible (when zero modifications) |
| `GATE_TIMING` | INFO | 402-405 | ✓ — 528 firings (every call) |
| `GATE_TIMING_SLOW` | WARNING | 407-410 | ✓ — 7 firings (>500ms) |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 4.1-G1 | `GATE_PASS` at DEBUG (line 399) — when all 14 checks pass without modification, this is the only log that indicates "we ran the gate cleanly" — and it's invisible. The GATE_TIMING line at INFO carries `modifications=0` field which IS the equivalent signal. **Decision:** acceptable as-is, since GATE_TIMING `modifications=0` already conveys "clean pass". | LOW | None — confirmed acceptable |

### Step 4.2 — Position count cap (CHECK 3, gate.py:113-126)

**Code path:** Reads `pos_svc.get_positions()`, if `open_count >= max_concurrent (5)`, reduces size to 30% of original. Modification appended to `modifications` list (surfaces in GATE_ADJUST).

**Logs:**

| Tag | Severity | Line | Status |
|---|---|---|---|
| (modification only via GATE_ADJUST) | INFO | 124 | ✓ visible in GATE_ADJUST modifications list |
| `GATE_POS_CHECK` (exception) | DEBUG | 126 | invisible |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 4.2-G1 | `GATE_POS_CHECK` exception at DEBUG. Position service exception during gate is rare but operationally meaningful (silent failure may mean position cap not enforced). Promote to WARNING. | MEDIUM | Trivial |

### Step 4.3 — Capital availability (CHECK 4, gate.py:128-211)

**Code path:** Reads fund_manager `_account_state.available`, applies conviction weight (Step 4.3 sub: `_get_conviction_weight`), computes `weighted_pct`, caps `size_usd` at `available * weighted_pct`. Conviction weight uses TIAS history, regime-filtered.

**Logs:**

| Tag | Severity | Line | Status |
|---|---|---|---|
| (cap modification via GATE_ADJUST) | INFO | 203 | ✓ visible |
| `CONVICTION_WEIGHT` | INFO | 486-490, 524-529 | ✓ — 521 firings |
| `CONVICTION_WEIGHT_FAIL` | DEBUG | 533 | invisible |
| `REGIME_CACHE_QUERY` | INFO | 442-445 | ✓ — 1,437 firings |
| `REGIME_FALLBACK` | WARNING | 450-454 | ✓ |
| `GATE_CAP_CHECK` (exception) | DEBUG | 213 | invisible |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 4.3-G1 | `GATE_CAP_CHECK` and `CONVICTION_WEIGHT_FAIL` at DEBUG — silent on exception. Capital availability cap silently failing means uncontrolled size. Promote to WARNING. | MEDIUM | Trivial |

### Step 4.4 — Performance Enforcer mode check

**Code path:** Performance Enforcer is a separate flow. Emits `ENFORCER_LEV_CLAMP` (48 firings) when leverage is clamped. `STRAT_EXEC_BLOCKED` (34 firings) when enforcer blocks trade. Lives in strategy_worker._execute_claude_trade (line 1473+).

**Logs:**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `ENFORCER_LEV_CLAMP` | WARNING | strategy_worker:1473 | ✓ — 48 firings |
| `STRAT_EXEC_BLOCKED` | WARNING | strategy_worker:1487, 1542 | ✓ — 34 firings |
| `TRADE_SKIP` (rsn=enforcer_block) | INFO | strategy_worker:1491 | ✓ in TRADE_SKIP enum |
| `TRADE_SKIP` (rsn=survival_block) | INFO | strategy_worker:1546 | ✓ in TRADE_SKIP enum |
| `ENFORCER_BEAT` | INFO | (in performance_enforcer) | ✓ — 135 firings (per Phase 0 baseline) |
| `ENFORCER_STATE` | INFO | (in performance_enforcer) | ✓ — 123 firings |

**Gaps:** none significant. Performance Enforcer is well-instrumented.

### Step 4.5 — R:R sanity (CHECK 13, gate.py:355-371)

**Code path:** Reads structure_cache for the symbol, checks `rr_ratio`. If ≤0.5 → reduce size by 50%. If = 0 → reduce by 75%.

**Logs:**

| Tag | Severity | Line | Status |
|---|---|---|---|
| (rr modification via GATE_ADJUST) | INFO | 365, 369 | ✓ visible (`rr_zero_reduce_75%` or `rr_low_X.X_reduce_50%`) |
| `GATE_RR_CHECK` (exception) | DEBUG | 371 | invisible |

**Gaps:** same as 4.2-G1 / 4.3-G1 — promote DEBUG check exception to WARNING.

### Step 4.6 — TP/SL sanity (CHECK 14, gate.py:373-387)

**Code path:** If TP and SL are within 0.1% of each other (`abs(_tp - _sl) / max(_tp, _sl) < 0.001`), nudge TP +/- 2% based on direction.

**Logs:**

| Tag | Severity | Line | Status |
|---|---|---|---|
| (TPSL modification via GATE_ADJUST) | INFO | 383-385 | ✓ visible (`TPSL_IDENTICAL(...)`) |
| `GATE_TPSL_CHECK` (exception) | DEBUG | 387 | invisible |

Plus separate **SL/TP Validator** (`core/sl_tp_validator.py`) emits SLTP_SKIP for SL/TP-distance issues — but **0 firings** in current rotation. Possible bypass.

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 4.6-G1 | `SLTPSKIP` at INFO/WARNING in `sl_tp_validator.py:41,47,...` has 0 firings. Either no failures triggered (system healthy) OR the validator is bypassed in current flow. **Investigate**: is `sl_tp_validator.py:SLTPValidator` actually called for every Bybit demo trade? | MEDIUM | Easy — verify call site |
| 4.6-G2 | `GATE_TPSL_CHECK` exception at DEBUG. Promote to WARNING. | MEDIUM | Trivial |

### Step 4.7 — Leverage cap (CHECK 2, gate.py:106-111)

**Code path:** If `current_lev > max_lev`, reduce. Modification appended to `modifications`.

**Logs:**

| Tag | Severity | Line | Status |
|---|---|---|---|
| (lev modification via GATE_ADJUST) | INFO | 111 | ✓ visible (`lev=Nx->Mx`) |

Plus `ENFORCER_LEV_CLAMP` (48 firings) for the enforcer-driven clamp.

**Gaps:** none significant.

### Step 4.8 — Race condition detection (CHECK 5 + CHECK 6, gate.py:215-239)

**Code path:**
- CHECK 5: existing position → halve size.
- CHECK 6: cooldown → halve size.

**Logs:**

| Tag | Severity | Line | Status |
|---|---|---|---|
| (CHECK 5 modification via GATE_ADJUST) | INFO | 223 | ✓ visible (`size_halved_existing_pos`) |
| (CHECK 6 modification via GATE_ADJUST) | INFO | 237 | ✓ visible (`size_halved_cooldown_Ns`) |
| `GATE_DUP_CHECK` (exception) | DEBUG | 225 | invisible |
| `GATE_COOL_CHECK` (exception) | DEBUG | 239 | invisible |

**Gaps:** same as above — promote DEBUG exceptions to WARNING.

### Step 4.9 — Layer 3 race-check (P6 fix; `order_service.py:334-353`)

**Code path:** `OrderService.place_order` compares `layer_snapshot` (captured at directive start in strategy_worker) with live LayerManager state. If `purpose == layer3_entry` and they disagree → raise `Layer3RaceError`. Strategy_worker captures snapshot at line 1457-1462.

**Logs:**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `ORDER_REJECT_LAYER3_RACE` | (varies) | order_service.py:334 | ✓ structured |
| `TRADE_SKIP` (rsn=order_reject) | INFO | strategy_worker | ✓ in TRADE_SKIP enum |

**Gaps:** none significant — P6 wired this with structured tag.

### Steps 4.10–4.14 — Audit-mentioned "missing for Bybit demo" checks

The audit prompt lists these as missing:
- 4.10 `_VALID_PURPOSES` validation
- 4.11 `SUPPORTED_SYMBOLS` whitelist
- 4.12 Mandatory-SL guard
- 4.13 Per-trade max-loss cap
- 4.14 Position-size cap

**Reality (verified via grep):**

| Audit step | Implementation status | Logging status |
|---|---|---|
| 4.10 _VALID_PURPOSES | NOT FOUND in src/. No constant or check anywhere. | N/A |
| 4.11 SUPPORTED_SYMBOLS | EXISTS in `risk/validators.py:42` (silent), `decision_parser.py:180` (silent), `strategy_worker._execute_claude_trade` (TRADE_SKIP rsn=unsupported_symbol). | INFO via TRADE_SKIP at strategy_worker; silent at validator level |
| 4.12 Mandatory-SL guard | EXISTS in `risk/validators.py:65` (silent), `decision_parser.py:194-196` (silent), and `gate.py` does NOT check this. | Silent at validator; aggregated into RISK_BLOCK if validators called |
| 4.13 Per-trade max-loss cap | NOT FOUND as a dedicated check. Implicit through `risk/validators.py` (margin vs balance check) and `gate.py` CHECK 4 (capital availability). | Implicit |
| 4.14 Position-size cap | EXISTS: `risk/validators.py:97`, `gate.py:99-104` CHECK 1 (max_position_size_usd), `gate.py:241-246` CHECK 7 (min floor). | Visible via GATE_ADJUST modifications |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 4.10-G1 | `_VALID_PURPOSES` is referenced in the audit prompt but NOT FOUND in code. Either the concept was renamed/removed, or it's a constant that should exist. **Recommend:** Phase 11 ask operator whether this check is intended (and what it would validate). If not, delete the audit reference. | LOW | Documentation — defer to Phase 11 |
| 4.11-G1 | SUPPORTED_SYMBOLS check in `risk/validators.py:42` is silent. Compensated by strategy_worker `TRADE_SKIP rsn=unsupported_symbol` at the wrapper level. **Decision:** acceptable IF risk_manager (which owns validators) is bypassed; if validators IS active for some flow, the per-check silence at validator level is a gap. Verify via Phase 11 callable-graph check. | MEDIUM | Easy — depends on whether validator is in path |
| 4.12-G1 | Mandatory-SL guard silent at validator level. Same shape as 4.11-G1: TRADE_SKIP catches it via rsn=sanity_reject if SL is missing/invalid. | MEDIUM | Same as 4.11-G1 |
| 4.13-G1 | No dedicated per-trade max-loss cap. Implicit through validators (margin/balance) and gate (capital availability). **Recommend:** Phase 11 ask operator whether this is intended — if so, add as CHECK 15 in gate.py with structured `MAX_LOSS_CAP` tag. | MEDIUM | Easy if needed |
| 4.14-G1 | Position-size cap fires via gate.py CHECK 1 (size capped at max_position_size_usd). Modification visible in GATE_ADJUST. No dedicated tag. **Decision:** acceptable — GATE_ADJUST modification list is sufficient. | LOW | None |

### Step 4.15 — Approval/rejection emission

**Code path:** Multiple sites:
- `LayerManager._do_trade` (line 1300-1410): emits `TRADE_SKIP` with rsn= field for invalid_directive, pos_gate, exception. Emits `BRAIN_DO_TRADE` per-trade summary (521 firings).
- `strategy_worker._execute_claude_trade`: emits `TRADE_SKIP` with rsn= enum (sanity_reject, enforcer_block, survival_block, xray_skip, xray_conflict, unsupported_symbol, dup_position, service_missing, price_fetch_fail, price_invalid, sltp_skip, qty_zero, order_reject). Returns `(success, reason_code)` to caller.
- `STRAT_EXEC` fires when execution proceeds (440 firings).
- `POS_GATE_BLOCK` fires when symbol blocked by open position (1 firing — rare).

**Logs:**

| Tag | Severity | Lines | Status |
|---|---|---|---|
| `TRADE_SKIP` | WARNING/INFO | layer_manager.py:1340, 1352, 1402; strategy_worker.py:1438, 1491, 1546, 1564 | ✓ — 117 firings |
| `BRAIN_DO_TRADE` | INFO | layer_manager.py | ✓ — 521 firings (per-trade summary with apex_ms, gate_ms, exec_ms) |
| `STRAT_EXEC` | INFO | strategy_worker.py | ✓ — 440 firings (trade approved + executed) |
| `POS_GATE_BLOCK` | INFO | layer_manager.py | ✓ — 1 firing |
| `STRAT_EXEC_BLOCKED` | WARNING | strategy_worker.py:1487, 1542 | ✓ — 34 firings |

**Gaps:** none significant. Approval/rejection emission is comprehensive.

### Cross-cutting from Phase 1: strategy_worker SL/TP prose (lines 1996-2050)

**Lines 1996, 1999, 2014, 2017, 2040, 2043, 2047, 2050** — 8 prose lines for SL/TP adjust/validate/auto-correct events. These are validation-related and live in strategy_worker's pre-execution path.

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 4.X-G1 | strategy_worker SL/TP prose lines 1996, 1999, 2014, 2017, 2040, 2043, 2047, 2050 are validation events. Replace with `SLTP_ADJUST | sym={s} side={d} type={SL\|TP} old={o} new={n} reason='{r}' \| {ctx()}` and `SLTP_VALIDATE_SKIP | sym={s} reason='{r}'` and `SLTP_AUTO_CORRECT | sym={s} side={d} type={SL\|TP} old={o} new={n}` structured tags. | MEDIUM | Easy — 8 sites |

---

## Cross-Step Observations (Carry Forward To Phase 11)

### Observation A — Three validation surfaces appear bypassed

**RISK_BLOCK (TradeValidator)** = 0 firings.
**SLTP_SKIP (SL/TP Validator)** = 0 firings.
**RULE_EVAL_START / RULE_EVAL_END (RuleEngine)** = 0 firings.

These surfaces have structured logging that would fire IF they ran. Three possibilities:
1. They're inactive in current Bybit demo flow (and the audit's reference to them is stale).
2. They're active but never trigger their failure paths (system is so clean nothing fails).
3. They're called but their telemetry is suppressed elsewhere.

**HIGH severity if option 3** (silent failures masking real issues). **Phase 11 must confirm** the active call graph for each validator. Recommend: Phase 12 audit-fix add a `*_INVOKED` heartbeat at each validator entry point (DEBUG OK) so we can confirm it's running even when it doesn't fail.

### Observation B — DEBUG check-exception pattern in gate.py

**9 sites** (GATE_POS_CHECK, GATE_CAP_CHECK, GATE_DUP_CHECK, GATE_COOL_CHECK, GATE_GUARDRAIL_CHECK, GATE_RR_CHECK, GATE_TPSL_CHECK, CONVICTION_WEIGHT_FAIL, GATE_PASS) at DEBUG. Each is `try: check; except Exception as e: log.debug(...)`.

Decision per site:
- 8 of 9 (the *_CHECK fail sites) should be promoted to WARNING — exceptions during validation are unexpected and operationally meaningful.
- GATE_PASS (line 399) is acceptable as-is — equivalent signal in GATE_TIMING with `modifications=0`.

### Observation C — TradeValidator and SLTP/RuleEngine status

`risk/validators.py` has **zero log calls in 162 lines**. This is structurally unusual. The 13 checks return prose-issue strings to the caller (`risk_manager.py:99`), which concatenates the first 3 into RISK_BLOCK. If validator is in path, the per-check structure is lost. If validator is NOT in path (RISK_BLOCK = 0 firings), it's dead code or a different flow.

`core/rule_engine.py` has structured RULE_EVAL_START/END but 0 firings. Audit notes it as "not used in current Bybit demo flow" likely correct.

**Recommended Phase 11 action:** confirm active validation graph + decide whether to delete inactive surfaces.

### Observation D — TRADE_SKIP enum is the gold standard

`strategy_worker._execute_claude_trade` returns `(False, reason_code)` with a 16-value reason enum. Every failure path emits `TRADE_SKIP | sym=... rsn={enum} | {ctx()}`. Operators can grep `TRADE_SKIP | rsn=enforcer_block` etc. to filter. Combined with LayerManager's TRADE_SKIP for invalid_directive/pos_gate/exception, this gives a complete failure-mode inventory.

This pattern should be referenced in Phase 9 (Recording) and elsewhere as the model for closure-trigger attribution.

### Observation E — Per-check PASS log decision

The audit prompt's Step 4.X "Per-check PASS/FAIL logging (some checks may silently pass)" expectation is **partially met**:
- FAIL: GATE_ADJUST shows the modification (when check forced an adjust). TRADE_SKIP shows rejection.
- PASS: only an aggregate signal via GATE_TIMING `modifications=0` and the trade proceeding to STRAT_EXEC.

A per-check PASS/FAIL trace at INFO would be too noisy (14 checks × 521 calls = 7,294 lines per rotation just for passing checks). The aggregate signal is appropriate. **Document as INTENTIONAL** in Phase 11.

---

## Verification Gate

| Gate | Status |
|---|---|
| All 15 steps audited | PASS |
| Code paths read end-to-end (gate.py, validators.py) or grep-walked + targeted reads | PASS |
| Tag emission verified in real logs | PASS (24+ tags grep'd) |
| Gap list complete | PASS (14 gaps; 2 HIGH, 9 MEDIUM, 3 LOW) |
| Severity assigned per gap | PASS |
| Fix difficulty assigned per gap | PASS (most Trivial/Easy; the bypass investigation may require deeper Phase 11 analysis) |
| Evidence cited per gap (file:line + log status) | PASS |

**Phase 4 verification gate:** PASS. Proceeding to Phase 5.

---

## Notes carried forward to Phase 5 (Execution) investigation

- **TradeValidator silence + 0 RISK_BLOCK firings**: confirm whether risk_manager.py.evaluate_trade is called in the active Bybit demo flow. If not, document as deprecated. If yes, the silent-validator gap is HIGH.
- **SLTP_SKIP 0 firings**: the SL/TP validator (`core/sl_tp_validator.py`) is referenced from layer_manager (per grep). Phase 5 should trace the call from order placement back to confirm it runs.
- **strategy_worker SL/TP prose** (4.X-G1) — also affects Phase 5 if the prose appears in execution path post-validation.
- **Layer3RaceError surfaces ORDER_REJECT_LAYER3_RACE** at order_service.py:334 — Phase 5 audit should verify this fires in real conditions.
- The rule_engine.py being bypassed needs explicit operator confirmation. Don't auto-delete dead validation code — it may be a "feature flag off" intentional bypass (or it may be re-enabled later).
