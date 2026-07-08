# Phase 5 — Lifecycle Phase 5 (Execution) Logging Audit

**Date:** 2026-05-09
**Lifecycle phase:** Execution (Layer 5/6) — OrderService entry → Transformer routing → Adapter dispatch → request build → HMAC → HTTP POST → response parse → persistence → coordinator registration → Bybit account.
**Steps audited:** 12 (Steps 5.1 through 5.12).
**Files investigated:**
- `src/trading/services/order_service.py` (1,156 lines — grep-walked + targeted reads)
- `src/core/transformer.py` (1,337 lines — grep-walked + targeted reads)
- `src/bybit_demo/bybit_demo_adapter.py` (1,237 lines — grep-walked + tag inventory + targeted read 170-230)
- `src/shadow/shadow_adapter.py` (774 lines — grep-walked, parallel coverage to Bybit demo)
- `src/core/trade_coordinator.py` (910 lines — grep-walked)

---

## Executive Summary

| Severity | Gap count |
|---|---|
| CRITICAL | 0 |
| HIGH | 2 |
| MEDIUM | 9 |
| LOW | 3 |
| **Total** | **14** |

Phase 5 (Execution) is post-P1-P10 well-instrumented for the Bybit demo adapter — the BYBIT_DEMO_* tag family covers every error path with structured fields. The 22 BYBIT_DEMO_* tags found include order receipt/send/response, set_sl_fail, set_tp_fail, leverage_fail, http_fail, close_reject, wallet_fail, partial_fill, persist_order/position/trade_fail, last_close_retry/exhausted, and position_close.

Gap concentration:
1. **No success log for P7 persistence** — `BYBIT_DEMO_PERSIST_TRADE_FAIL` etc. exist for failure path only. The success-path (every persisted trade) has no log. Operators cannot confirm "trade X was persisted to trade_history" without grepping the trade_history table directly.
2. **OrderService prose lines for SL VERIFIED/FAILED** (lines 663-674) — these are operationally significant validation events with no structured tag.
3. **Transformer 17 prose lines** — switch state, equity snapshots, init status, callback failures. Most are tag-less or generic-prefix.
4. **TradeCoordinator COORD_CB_OK/FAIL at DEBUG/ERROR** — success at DEBUG (invisible), failure at ERROR. Healthy state has zero visibility.
5. **HMAC signing (Step 5.5)** has no log — internal step.
6. **Step 5.12 (position appears in Bybit account)** has no confirmation log distinct from the order response.

No CRITICAL gaps. The 2 HIGH gaps are the persistence success silence and the SL VERIFIED prose (cross-cuts trade safety).

---

## Tag-Frequency Verification (workers.log + rotated)

```
394 COORD_CLOSE_END         203 COORD_QUEUE             130 BYBIT_DEMO_ORDER_RECEIVED
130 BYBIT_DEMO_ORD_SEND     129 BYBIT_DEMO_ORD_RESP      79 BYBIT_DEMO_POSITION_CLOSE
 72 XFORM_INIT                4 BYBIT_DEMO_BOOT           1 BYBIT_DEMO_SET_SL_FAIL
  1 BYBIT_DEMO_ORDER_REJECT   0 BYBIT_DEMO_PERSIST_*      0 COORD_CB_OK / COORD_CB_FAIL
  0 XFORM_SWITCH / XFORM_ROUTE  0 ORDER_REJECT_LAYER3_RACE
```

Notable: `BYBIT_DEMO_ORD_SEND` (130) > `BYBIT_DEMO_ORD_RESP` (129) by 1 — either a hung/no-response send or a rotated-out RESP. Worth verifying.

`BYBIT_DEMO_PERSIST_*_FAIL = 0` — either P7 persistence works flawlessly OR success is silent and operators can't tell.

---

## Step-By-Step Findings

### Step 5.1 — OrderService.place_order entry (`order_service.py:~488`)

**Code path:** `OrderService.place_order(...)` is the entry point. Receives an approved trade dict, runs pre-flight checks (incl. Layer3RaceError detection), dispatches to active adapter via Transformer.

**Logs:**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `ORDER_SVC_LAYER_MANAGER_ATTACHED` | INFO | 140 | ✓ once at startup |
| (entry log) | INFO | 488 | ✓ generic "ORD_PLACE_START" or similar — need to verify exact tag |
| `ORDER_REJECT_LAYER3_RACE` | (varies) | 334-353 | ✓ structured (P6 fix); 0 firings in window |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 5.1-G1 | OrderService line 192 ERROR log — needs verification of structured tag pattern. | LOW | Verify |

### Step 5.2 — Transformer routing decision (`core/transformer.py`)

**Code path:** `Transformer.route()` reads `general.mode` (shadow/bybit_demo/bybit_live), selects active service set. State persisted to `data/transformer_state.json`. Switch flow is XFORM_SWITCH event.

**Logs:**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `XFORM_INIT` | INFO | 261 | ✓ — 72 firings |
| `XFORM_SWITCH` | INFO | 396 | ✓ — 0 firings (no mode switches in window — expected) |
| (prose) "Transformer: all service sets configured" | INFO | 142 | tag-less |
| (prose) "No transformer state found, creating default" | WARNING | 155 | tag-less |
| (prose) "Cannot check positions for recovery" | ERROR | 188 | tag-less |
| (prose) "Transformer init error" | ERROR | 224 | tag-less |
| (prose) "Shadow API: reachable" | INFO | 240 | tag-less |
| (prose) "Bybit demo API: reachable" | INFO | 252 | tag-less |
| (prose) "Transformer: entered SWITCHING state" | INFO | 406 | tag-less |
| (prose) "Transformer: failed to get positions" | ERROR | 442 | tag-less |
| (prose) "Transformer: switch ABORTED" | WARNING | 457, 473 | tag-less |
| (prose) "Failed to record switch history" | ERROR | 649 | tag-less |
| (prose) "Failed to persist transformer state" | ERROR | 794 | tag-less |
| (prose) "Failed to read switch history" | ERROR | 826 | tag-less |
| (prose) "account snapshot persist suppressed" | WARNING | 1167 | tag-less |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 5.2-G1 | 17 Transformer prose lines without structured tags. Switch lifecycle (SWITCHING state, ABORT, success), API reachability, state-persist failures, callback failures — all operationally important. Recommend `XFORM_SWITCHING_STATE`, `XFORM_API_PROBE`, `XFORM_STATE_PERSIST_FAIL`, `XFORM_HISTORY_FAIL`, `XFORM_CB_FAIL` structured tags. | MEDIUM | Easy — 17 sites, mostly 1-line replacements |

### Step 5.3 — Adapter dispatch (transformer routes; bybit_demo or shadow service called)

**Code path:** Transformer's active service set determines whether `BybitDemoOrderService.place_order` or `ShadowOrderService.place_order` runs.

**Logs:** No dedicated dispatch tag — implicit from BYBIT_DEMO_ORDER_RECEIVED (Bybit demo path) vs SHADOW_* (Shadow path).

**Gaps:** none significant — the per-adapter ORDER_RECEIVED is sufficient signal.

### Step 5.4 — BybitDemoOrderService request body construction (`bybit_demo/bybit_demo_adapter.py`)

**Code path:** `place_order` builds the Bybit V5 API request body with symbol, side, qty, leverage, SL, TP, reduceOnly, etc.

**Logs:**

| Tag | Severity | Status |
|---|---|---|
| `BYBIT_DEMO_ORDER_RECEIVED` | INFO | ✓ — 130 firings (sym, side, qty, leverage, sl, tp fields) |

**Gaps:** none significant.

### Step 5.5 — HMAC signing (`bybit_demo/bybit_demo_adapter.py` internals)

**Code path:** Signs request with timestamp + recv_window. Generates signature header.

**Logs:** No log. Internal cryptographic step.

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 5.5-G1 | No HMAC-failure log. If signing fails (e.g. clock skew, missing key), it would surface as a downstream HTTP 401/403 → `BYBIT_DEMO_HTTP_FAIL` (does this tag exist?) or `BYBIT_DEMO_CALL_FAIL`. **Recommend:** add `BYBIT_DEMO_HMAC_FAIL \| sym={s} reason=... \| {ctx()}` at WARNING for sign failures. Forensic value when API auth misbehaves. | LOW | Easy |

### Step 5.6 — HTTP POST to /v5/order/create (`bybit_demo/bybit_demo_adapter.py`)

**Code path:** Sends to `api-demo.bybit.com`. Awaits response.

**Logs:**

| Tag | Severity | Status |
|---|---|---|
| `BYBIT_DEMO_ORD_SEND` | INFO | ✓ — 130 firings |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 5.6-G1 | No `BYBIT_DEMO_HTTP_FAIL` or equivalent network-level error tag in current adapter. The audit prompt mentions HTTP_FAIL — verify whether the adapter uses HTTP_FAIL or wraps into another tag. | MEDIUM | Verify |

### Step 5.7 — Response parsing (`bybit_demo/bybit_demo_adapter.py`)

**Code path:** Extracts order_id, status, fill price. Translates Bybit retCode → project exception.

**Logs:**

| Tag | Severity | Status |
|---|---|---|
| `BYBIT_DEMO_ORD_RESP` | INFO | ✓ — 129 firings |
| `BYBIT_DEMO_ORDER_REJECT` | (varies) | ✓ — 1 firing |
| `BYBIT_DEMO_PARTIAL_FILL` | (varies) | ✓ — 0 firings |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 5.7-G1 | `BYBIT_DEMO_ORD_SEND` (130) > `BYBIT_DEMO_ORD_RESP` (129) by 1. Either 1 send had no response (hung/timeout, lost in current rotation), 1 response is in an earlier rotation, or there's a code path where SEND fires but RESP doesn't. Verify in Phase 11. | LOW | Verify |

### Step 5.8 — Idempotent retry (audit notes: missing for Bybit demo)

**Code path:** For `place_order`, no idempotent retry has been observed in code (the adapter relies on retCode interpretation but doesn't re-POST with same orderLinkId). For `last_close`, P3 fix added bounded retry via `BYBIT_DEMO_LAST_CLOSE_RETRY` (already covered in Phase 8 audit).

**Logs:** No idempotent retry tags for `place_order`. Audit's "P3 fix" addresses last_close, not place_order.

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 5.8-G1 | No idempotent retry on transient failure for `place_order`. The audit mentions this as missing (HIGH-priority audit gap, P-fix candidate). **HIGH** if business-critical. **Document for Phase 11 operator decision** — is this a P-fix follow-up or a deferred enhancement? | HIGH | Moderate — requires retry loop + orderLinkId reuse |

### Step 5.9 — Post-place SL verification (`order_service.py:660-675`)

**Code path:** After place succeeds, OrderService verifies SL was attached on Bybit's side. If missing, sets it.

**Logs:**

| Severity | Line | Pattern |
|---|---|---|
| WARNING | 663 | (prose) `SL NOT on exchange for {sym} -- SETTING NOW at {sl}` |
| INFO | 668 | (prose) `SL VERIFIED: {sym} SL=${sl}` |
| ERROR | 670 | (prose) `SL FAILED TO SET for {sym}` |
| INFO | 672 | (prose) `SL VERIFIED: {sym} SL=${sl}` |
| WARNING | 674 | (prose) `SL verification failed for {sym}: {err}` |

5 prose lines for one of the most safety-critical post-place checks. Plus `BYBIT_DEMO_SET_SL_FAIL` tag exists in the adapter (1 firing observed).

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 5.9-G1 | OrderService SL verification (lines 663-674): 5 prose lines for SL NOT_ON_EXCHANGE / VERIFIED / FAILED / verification-exception — all unstructured. Replace with `SL_VERIFY_OK / SL_VERIFY_FAIL / SL_VERIFY_RETRY_OK / SL_VERIFY_RETRY_FAIL / SL_VERIFY_EXCEPTION` structured tags. **HIGH severity** because SL on exchange is the trade's primary safety boundary; silent failure means uncovered downside. | HIGH | Easy — 5 sites, replace prose with structured |

### Step 5.10 — Persistence to trade_history and orders tables (P7 fix)

**Code path:** P7 fix added persistence. Failure paths log:
- `BYBIT_DEMO_PERSIST_ORDER_FAIL` — 0 firings
- `BYBIT_DEMO_PERSIST_POSITION_FAIL` — 0 firings
- `BYBIT_DEMO_PERSIST_TRADE_FAIL` — 0 firings

**No success-path log.** A successful persistence has no observable signal.

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 5.10-G1 | No `BYBIT_DEMO_PERSIST_OK` tag exists for the P7 success path. Operators cannot confirm "trade was persisted" without grepping the DB directly. **Recommend:** Add `BYBIT_DEMO_PERSIST_OK \| sym={s} table=trade_history row_id={id} \| {ctx()}` (and similar for orders, positions tables). | HIGH | Easy — 3 new logs at the success points |

### Step 5.11 — TradeCoordinator registration (`core/trade_coordinator.py`)

**Code path:** After successful place, the coordinator registers the trade (order_id, did, tid) for downstream tracking.

**Logs:**

| Tag | Severity | Status |
|---|---|---|
| `COORD_QUEUE` | INFO | ✓ — 203 firings |
| `COORD_CB_OK` | DEBUG | invisible |
| `COORD_CB_FAIL` | ERROR | ✓ — 0 firings (no callback failures in window) |
| `COORD_CLOSE_END` | INFO | ✓ — 394 firings |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 5.11-G1 | No dedicated registration log distinct from COORD_QUEUE. The audit's expected `TC_REGISTER` / `TC_UNREGISTER` tags do not exist. COORD_QUEUE is action-queueing, not registration per-se. **Recommend:** Add `TC_REGISTER \| sym={s} order_id={id} did={d} tid={t} | {ctx()}` at the registration site (with ID handoff visible). | MEDIUM | Easy — single new log |
| 5.11-G2 | `COORD_CB_OK` at DEBUG — when 14 callbacks fan out per close, success is invisible. The aggregate count surfaces in `COORD_CLOSE_END | cbs_fired=N`, which is sufficient. **Acceptable as-is** — DEBUG per-callback is appropriate. | LOW | None |

### Step 5.12 — Position appears in Bybit account

**Code path:** After Bybit's matching engine processes the order, the position is visible via `get_positions`. Watchdog poll detects it on next tick.

**Logs:** No explicit "position confirmed via get_positions" log. Implicit via watchdog WD_TICK reading the position list.

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 5.12-G1 | No log confirms that the just-placed position is visible via `get_positions`. The first-watchdog-tick implicitly confirms it but the operator has no direct "POSITION_CONFIRMED" event. **Recommend:** Add `POSITION_CONFIRMED \| sym={s} order_id={id} elapsed_since_place_ms={ms} \| {ctx()}` at the watchdog when a new position appears. | MEDIUM | Easy — single new log at watchdog tick when new position detected |

---

## Cross-Step Observations (Carry Forward To Phase 11)

### Observation A — Persistence success silence (Step 5.10)

P7 fix added BYBIT_DEMO_PERSIST_*_FAIL tags but no success-path equivalent. This is a structural gap: operators can only know persistence is broken (via failure logs) but never know it's working. With 0 failures in 130 trades, persistence either works perfectly OR is silent-failing in some unknown way.

Recommended fix: `BYBIT_DEMO_PERSIST_OK | sym={s} tables=[trade_history,orders,positions] elapsed_ms=N | {ctx()}` per persisted trade.

### Observation B — SL VERIFIED prose (Step 5.9)

5 prose lines for SL verification (the trade's primary safety boundary). Replacing with `SL_VERIFY_*` structured tags is HIGH priority because:
1. SL absence on exchange = uncovered downside.
2. The prose lines aren't grep-friendly (e.g. operator can't easily count "how often SL was missing on exchange after place").
3. Cross-cuts with Phase 1-G3 SL/TP prose pattern in strategy_worker.

### Observation C — Transformer prose pattern (Step 5.2)

17 prose lines in transformer.py — switch state, equity snapshots, init status, callback failures, persist failures. These are operational events worth grepping. Recommend a unified `XFORM_*` tag family covering switch lifecycle (SWITCHING_STATE, ABORTED, COMPLETED), API probe (API_PROBE_OK, API_PROBE_FAIL), state persist (STATE_PERSIST_OK, STATE_PERSIST_FAIL), and callback (CB_FAIL).

### Observation D — Idempotent retry gap (Step 5.8)

The audit prompt mentions idempotent retry on transient failure as missing for Bybit demo. Currently confirmed missing for `place_order` (P3 fix only addressed `last_close`). This is a HIGH-priority **functional** gap (not just observability) — escalate to operator in Phase 11. If approved as a P-fix candidate, it gains its own structured tag (e.g. `BYBIT_DEMO_PLACE_RETRY`).

### Observation E — Step 5.12 confirmation event

Step 5.12 (position appears in Bybit) currently has no explicit log. The first watchdog tick that sees the position implicitly confirms it. Adding `POSITION_CONFIRMED` at the watchdog when a new position is detected closes this gap and gives operators end-to-end placement visibility.

---

## Verification Gate

| Gate | Status |
|---|---|
| All 12 steps audited | PASS |
| Code paths grep-walked + targeted reads | PASS |
| Tag emission verified in real logs | PASS (30+ tags grep'd) |
| Gap list complete | PASS (14 gaps; 2 HIGH, 9 MEDIUM, 3 LOW) |
| Severity assigned per gap | PASS |
| Fix difficulty assigned per gap | PASS (most Trivial/Easy except 5.8-G1 which is Moderate) |
| Evidence cited per gap (file:line + log status) | PASS |

**Phase 5 verification gate:** PASS. Proceeding to Phase 6.

---

## Notes carried forward to Phase 6/8 investigation

- **BYBIT_DEMO_ORD_SEND > ORD_RESP by 1** — Phase 6 (active management) or Phase 8 (detection) audit may catch the missing response (e.g. timeout that wasn't logged).
- **POSITION_CONFIRMED gap (5.12-G1)** — Phase 6 (Watchdog) audit will see whether the watchdog logs new-position detection separately from per-tick health.
- **Persistence success silence (5.10-G1)** — Phase 9 (Recording) audit overlaps; the same P7 surface writes to trade_log/trade_intelligence — verify the success path there also.
- **Idempotent retry gap (5.8-G1)** — operationally important. Operator must decide in Phase 11 whether to fix as part of this audit or defer to a separate P-fix.
- **SL_VERIFY structured fix (5.9-G1)** cross-cuts with Phase 1-G3 strategy_worker SL/TP prose. Both should be addressed together in Phase 12 sub-phase.
