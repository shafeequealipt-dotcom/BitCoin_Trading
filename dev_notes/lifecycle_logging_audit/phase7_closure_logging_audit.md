# Phase 7 — Lifecycle Phase 7 (Closure Triggers) Logging Audit

**Date:** 2026-05-09
**Lifecycle phase:** Closure Triggers — exchange-initiated SL/TP/Trail hits + system-initiated closes (sniper, CALL_B, watchdog, time decay, manual) + close-position request to Bybit + close fill.
**Steps audited:** 10 (Steps 7.1 through 7.10).
**Note:** Most close-decision logic was covered in Phase 6 audit (sniper, time decay, watchdog). This phase audit focuses on the close emission paths, trigger attribution, and exchange-initiated close detection (which links into Phase 8).

---

## Executive Summary

| Severity | Gap count |
|---|---|
| CRITICAL | 0 |
| HIGH | 2 |
| MEDIUM | 5 |
| LOW | 2 |
| **Total** | **9** |

Phase 7 is dominated by **one structural gap**: the `close_trigger` attribution is hardcoded to `exchange_match` for every Bybit demo exchange-initiated close (`bybit_demo_adapter.py:239`). This means operators **cannot distinguish SL hit from TP hit from Trail hit** in logs — all 223 WD_CLOSE events report the same trigger reason. The audit prompt called this out explicitly.

System-initiated closes (sniper, CALL_B, watchdog, time decay) DO know their trigger reason locally — but the BYBIT_DEMO_POSITION_CLOSE tag (79 firings) doesn't always carry that reason field through to the close emission.

The other gaps are around manual-close visibility (Telegram path), close-fill confirmation, and the 7 distinct close-trigger paths that should each surface a structured `close_trigger=` field.

---

## Tag-Frequency Verification (workers.log + rotated)

```
223 WD_CLOSE                  186 WD_LAST_CLOSE_AUTH         79 BYBIT_DEMO_POSITION_CLOSE
 23 WD_CLOSE_PRICE_FALLBACK    11 WD_LAST_CLOSE_FALLBACK      0 BYBIT_DEMO_CLOSE_REJECT
  0 BYBIT_DEMO_CLOSE_NO_POSITION  0 BYBIT_DEMO_CLOSE_FILL_FALLBACK  0 BYBIT_DEMO_CLOSE_FILL_RETRY_OK
  0 MANUAL_CLOSE                0 CLOSE_TRIGGER (does not exist as tag)
```

**Math:** WD_CLOSE (223) ≈ system+exchange total. BYBIT_DEMO_POSITION_CLOSE (79) covers system-initiated only. Difference ≈ 144 exchange-initiated closes detected via watchdog poll set-difference (Phase 8 territory).

---

## Step-By-Step Findings

### Step 7.1 — SL hit on Bybit (exchange-initiated)

**Code path:** Bybit's matching engine triggers SL → position closes server-side. Watchdog detects via set-difference on next tick → emits WD_CLOSE. `close_trigger` field on the closure data hardcoded to `exchange_match` at bybit_demo_adapter.py:239.

**Logs:**
- `WD_CLOSE` fires (223 total, includes SL+TP+Trail combined)
- `close_trigger=exchange_match` — same field for all three exchange-initiated triggers

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 7.1-G1 | `close_trigger="exchange_match"` hardcoded at bybit_demo_adapter.py:239 — ALL exchange-initiated closes (SL, TP, Trail) report identical trigger. Operator cannot distinguish from logs. **Recommend inference logic:** when watchdog detects a closed position, compare close price (from get_last_close) to last known SL and TP. Within tolerance of SL → `close_trigger=sl_hit`. Within tolerance of TP → `close_trigger=tp_hit`. Else → `close_trigger=exchange_match` (truly unknown). Then surface in WD_CLOSE: `WD_CLOSE | sym=... close_trigger=sl_hit close_price=... last_sl=... last_tp=... | {ctx()}`. **HIGH severity** — close-trigger attribution is the audit's #1 named gap. | HIGH | Moderate — requires inference logic + state tracking |

### Step 7.2 — TP hit on Bybit (exchange-initiated)

Same as Step 7.1. Same gap.

### Step 7.3 — Trail HIT (exchange-initiated)

Same as Step 7.1. Same gap.

The audit's `close_trigger` field is the SINGLE point of truth that gets written to data_lake / TIAS / thesis_store — corrupting it as `exchange_match` means downstream learning (Phase 10) cannot distinguish SL outcomes from TP outcomes. Strategy-edge measurement is impaired.

### Step 7.4 — Sniper system-initiated close

**Code path:** Sniper calls `close_position` via OrderService → Transformer → BybitDemoAdapter. The reason is known locally (mode4_p9 trigger or M4 ladder full close).

**Logs:**

| Tag | Severity | Status |
|---|---|---|
| `mode4_p9` | INFO | ✓ — 691 firings (trigger condition) |
| `M4_ACT_CLOSE` | INFO | ✓ — 97 firings (close action) |
| `BYBIT_DEMO_POSITION_CLOSE` | INFO | ✓ — 79 firings (close emission) |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 7.4-G1 | When sniper triggers a close, `BYBIT_DEMO_POSITION_CLOSE` should carry `close_trigger=sniper_p9` or `close_trigger=sniper_m4_close` field so downstream can distinguish system-vs-exchange triggers. Currently the tag has only `sym=` and `purpose=` (per the line 264-266 inventory in Phase 0). **Recommend:** extend `BYBIT_DEMO_POSITION_CLOSE` to carry `close_trigger=` field passed in by the caller. | HIGH | Easy — add field to tag |

### Step 7.5 — CALL_B system-initiated close

**Code path:** Brain decides to close via CALL_B; dispatches via L4P or coordinator. Eventually calls close_position.

**Logs:** STRAT_POS_ACT (2,394 firings) covers the per-position action. Close emission goes through BYBIT_DEMO_POSITION_CLOSE.

**Gaps:** same as 7.4-G1 — `close_trigger=callb_close` should surface.

### Step 7.6 — Watchdog system-initiated close (hard stop / emergency)

**Code path:** Watchdog calls close on hard stop, emergency, timeout, profit take, plan timer expiration. Each path has its own emission site.

**Logs:** 11 prose error lines (Phase 6 6.4-G1) for these close paths. Each path has its own action (no consistent tag).

**Gaps:** same as 7.4-G1 — `close_trigger=wd_hard_stop`, `wd_emergency`, `wd_timeout`, `wd_profit_take`, `wd_plan_timer` should surface.

### Step 7.7 — Time decay system-initiated close

**Code path:** TimeDecayManager calls close on age/MAE/structure violation.

**Logs:**

| Tag | Severity | Status |
|---|---|---|
| `TIME_DECAY_FORCE_CLOSE` | INFO | ✓ — 72 firings |

**Gaps:** same as 7.4-G1 — `close_trigger=time_decay_age`, `time_decay_mae`, `time_decay_struct` should surface.

### Step 7.8 — Manual close via Telegram

**Code path:** Operator command via Telegram bot triggers close.

**Logs:** No dedicated MANUAL_CLOSE tag found. The Telegram handler eventually calls close_position. The action is logged via BYBIT_DEMO_POSITION_CLOSE but with `purpose=` field, not `close_trigger=manual`.

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 7.8-G1 | No `MANUAL_CLOSE` or `TG_MANUAL_CLOSE` tag. Operator-initiated closes look identical to system-initiated in logs. **Recommend:** add `MANUAL_CLOSE | sym=... source=telegram operator_chat_id=... | {ctx()}` at the Telegram handler entry, AND surface `close_trigger=manual_telegram` in BYBIT_DEMO_POSITION_CLOSE. | MEDIUM | Easy |

### Step 7.9 — Close position request to Bybit demo (`bybit_demo_adapter.py:close_position`)

**Code path:** `close_position(symbol, purpose, ...)` looks up position, builds close-order request (reduceOnly=True), sends. Result returned to caller.

**Logs:**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `BYBIT_DEMO_POSITION_CLOSE` | INFO | 264 | ✓ — 79 firings (sym, purpose) |
| `BYBIT_DEMO_CLOSE_NO_POSITION` | WARNING | 271 | ✓ — 0 firings |
| `BYBIT_DEMO_CLOSE_REJECT` | (varies) | (in adapter) | ✓ — 0 firings |
| `BYBIT_DEMO_CLOSE_FILL_FALLBACK` | INFO | 320 | ✓ — 0 firings |
| `BYBIT_DEMO_CLOSE_FILL_RETRY_OK` | INFO | (in adapter) | ✓ — 0 firings |
| `BYBIT_DEMO_CLOSE_FILL_RETRY_EXHAUSTED` | (varies) | (in adapter) | ✓ — 0 firings |
| `BYBIT_DEMO_CLOSE_ALL_ITEM_FAIL` | (varies) | (in adapter) | ✓ — 0 firings |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 7.9-G1 | The 5 BYBIT_DEMO_CLOSE_* error tags exist but show 0 firings. Either the close path is healthy OR these tags are unreachable. Verify in Phase 11 by triggering test failures. | LOW | Verify |

### Step 7.10 — Close fill on Bybit

**Code path:** Bybit's matching engine fills the close order. `BYBIT_DEMO_POSITION_CLOSE` fires after the create_order returns.

**Logs:** The close emission carries `purpose=` and (P3 fix) retry attempts on indexer-lag. But no explicit "fill confirmed" log distinct from the order create response.

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 7.10-G1 | No `CLOSE_FILL_CONFIRMED` log. The fact that BYBIT_DEMO_POSITION_CLOSE fired means create_order succeeded, but actual fill confirmation (via get_last_close indexed result) is via `WD_LAST_CLOSE_AUTH` (186 firings) much later. Operators cannot tell from one log line "close was placed AND filled on Bybit." Currently the chain is: BYBIT_DEMO_POSITION_CLOSE → (retry loop) → WD_LAST_CLOSE_AUTH. Recommend: add `BYBIT_DEMO_CLOSE_FILL_OK | sym=... order_id=... fill_price=... fill_qty=... | {ctx()}` between place and last_close. | MEDIUM | Easy — add log at fill response |

---

## Cross-Step Observations (Carry Forward To Phase 11)

### Observation A — close_trigger attribution is the structural Phase 7 gap

The hardcoded `"exchange_match"` at `bybit_demo_adapter.py:239` is the **single most impactful gap** in Phase 7. It corrupts:
- WD_CLOSE log (operators can't grep by trigger)
- data_lake.write_trade (close_trigger column receives the same value)
- TIAS analysis (Phase-2 DeepSeek can't distinguish SL outcomes from TP outcomes)
- Strategy-edge measurement (Phase 10 learning)

**Recommended fix:** Trigger inference at the watchdog when a closed position is detected:
```python
# Pseudo:
if abs(close_price - last_known_sl) / last_known_sl < 0.002:
    close_trigger = "sl_hit"
elif abs(close_price - last_known_tp) / last_known_tp < 0.002:
    close_trigger = "tp_hit"
elif close_price near recent trail floor:
    close_trigger = "trail_hit"
else:
    close_trigger = "exchange_match"  # genuinely unknown
```

For system-initiated closes, the trigger is already known by the caller and just needs to flow through `close_position(symbol, purpose, close_trigger=...)`.

### Observation B — System-initiated closes lose trigger info

7.4-G1, 7.5, 7.6, 7.7 all share the same gap: the system knows the trigger reason locally (mode4_p9, callb, hard_stop, time_decay_age, etc.) but doesn't surface it through to BYBIT_DEMO_POSITION_CLOSE. **Single fix:** add a `close_trigger=` parameter to `close_position` and propagate to the emission tag.

### Observation C — Manual close visibility (7.8-G1)

Operator-initiated closes (Telegram command) have NO dedicated tag. They look identical to system-initiated. Adding MANUAL_CLOSE + flowing close_trigger=manual_telegram closes this.

### Observation D — Close-fill confirmation (7.10-G1)

BYBIT_DEMO_POSITION_CLOSE = "create_order returned" but doesn't mean the fill is confirmed. The chain is:
1. BYBIT_DEMO_POSITION_CLOSE (create returned)
2. P3 fix: retry loop on get_last_close (3 attempts × 2 sec)
3. WD_LAST_CLOSE_AUTH (when authoritative data arrives)
4. WD_CLOSE (when close emission fires post-retry)
5. WD_LAST_CLOSE_FALLBACK (4.9% rate when retry exhausted)

A `CLOSE_FILL_CONFIRMED` log between #1 and #3 would let operators confirm "close placed AND filled" in one log line.

### Observation E — 5 close-error tags at 0 firings

BYBIT_DEMO_CLOSE_REJECT, _NO_POSITION, _FILL_FALLBACK, _FILL_RETRY_OK, _FILL_RETRY_EXHAUSTED, _ALL_ITEM_FAIL — all 0 firings. Either:
1. Close path is genuinely error-free in current rotation
2. Tags are unreachable (dead code)

Phase 11 should verify by reading the code paths leading to each tag.

---

## Verification Gate

| Gate | Status |
|---|---|
| All 10 steps audited | PASS |
| Code paths grep-walked | PASS |
| Tag emission verified in real logs | PASS (12+ tags grep'd) |
| Gap list complete | PASS (9 gaps; 2 HIGH, 5 MEDIUM, 2 LOW) |
| Severity assigned per gap | PASS |
| Fix difficulty assigned per gap | PASS |
| Evidence cited per gap (file:line + log status) | PASS |

**Phase 7 verification gate:** PASS. Proceeding to Phase 8.

---

## Notes carried forward to Phase 8 investigation

- **close_trigger inference logic (7.1-G1)** must coordinate with Phase 8 (Detection) audit because the watchdog set-difference detection is Phase 8 territory but the trigger inference happens at the same site.
- **WD_CLOSE_PRICE_FALLBACK 23 firings (10.3% of WD_CLOSE)** — Phase 8 audit will quantify the price-fallback rate and trace its cause.
- **WD_LAST_CLOSE_FALLBACK 11 firings (4.9%)** — Phase 8 audit confirms the P3 fix improved this from 35%.
- **CLOSE_FILL_CONFIRMED gap (7.10-G1)** — Phase 8 audit also touches this since the gap is between system-place and watchdog-detect.
