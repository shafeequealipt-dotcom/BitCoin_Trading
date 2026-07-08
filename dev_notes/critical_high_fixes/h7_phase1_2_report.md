# HIGH-7 Phase 1+2 — Investigation and Operator Discussion

## Audit reference

HIGH-7 — REDUCE_FALLBACK alert relay swallows partial-reduce failure context.

## Phase 0 baseline + investigation

In the 2.85h audit window: 2 REDUCE_FALLBACK events. Sample from logs:

```
2026-05-09 13:58:19.367 | WARNING | src.bybit_demo.bybit_demo_adapter:reduce_position:477 |
REDUCE_FALLBACK | sym=OPUSDT qty=1650.35 reason=bybit_reject 
err='[2026-05-09T13:58:19.367842+00:00] BybitAPIError: Bybit demo: API error 
(10001: Qty invalid) | details={'ret_code': 10001, 'ret_msg': 'Qty invalid', 'op': 'redu'
```

The `err=` field is the str(e) of the TradingMCPError, truncated to 160 chars per `[:160]` slice at adapter line 511. The truncation cuts off mid-detail (`'op': 'redu'` should be `'op': 'reduce_position'`). The alert relay (`bybit_demo_alert_relay.py:228`) routes REDUCE_FALLBACK as send_error_alert WARNING. Telegram operators see the truncated message — losing structural fields like the full ret_msg, op, and any other details Bybit provides.

The TradingMCPError `e.details` attribute is a structured dict (`{'ret_code': 10001, 'ret_msg': 'Qty invalid', 'op': 'reduce_position'}`). Extracting these explicitly into the log line gives operators (and the alert) machine-parseable fields without truncation drama.

### Three current REDUCE_FALLBACK emit sites

`bybit_demo_adapter.py`:
- Line 482-485: `reason=no_position` (no qty/err context — fine, no failure to report)
- Line 510-512: `reason=bybit_reject err='{str(e)[:160]}'` (the audit's case — truncated)
- Implicit line 488-490: `qty >= pos.size` falls back to close_position; emits NO REDUCE_FALLBACK (silently downgrades). Could be flagged for visibility.

## Two options considered

### Option A — Extract ret_code/ret_msg/op from e.details (recommended)

In the bybit_reject branch, parse `e.details` (which is a dict on TradingMCPError) and emit structured fields explicitly. Keep err= for back-compat but make it shorter and structural. Add a third line for the qty-too-large degrade case (currently silent).

Pros:
- Operators see ret_code/ret_msg/op as separate fields — easier to grep, easier to alert on specific conditions
- No truncation issue (each field is short)
- Telegram alert (which routes the full log message) carries the structured fields

Cons:
- Adds ~5 lines per branch

### Option B — Increase the err= truncation cap

Just bump `[:160]` to `[:500]` so the details aren't cut.

Pros: smallest change.
Cons: doesn't help operators grep for ret_code; still embeds structured data in a stringified blob.

## Recommendation

**Option A.** Structured fields are the project's logging convention (every other adapter log uses key=val format).

## Implementation plan

Single atomic commit. Files modified:

1. `src/bybit_demo/bybit_demo_adapter.py:reduce_position`:
   - In the `except TradingMCPError as e:` block (line 508+), extract `ret_code`, `ret_msg`, `op` from `e.details` (or empty if absent). Emit structured fields in the log.
   - Add a REDUCE_FALLBACK emit before the `qty >= pos.size` close_position fall-through so this case is visible too.

2. `tests/test_high7_reduce_fallback_context.py`:
   - bybit_reject path emits ret_code, ret_msg, op as structured fields
   - no_position path unchanged (no err context)
   - qty-too-large path emits new REDUCE_FALLBACK with reason=qty_exceeds_size

## Open questions

None blocking.
