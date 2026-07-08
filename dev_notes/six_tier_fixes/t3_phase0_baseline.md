# Tier 3 Phase 0 — Tier-specific pre-flight

## Tier 3 issues (Phase 5 close attribution and persistence)

- T3-1 Phase5 F-4 — 6 safety gates absent on bybit_demo path.
- T3-2 Phase5 F-8 — orders table 99% close-row loss to blank-PK INSERT OR REPLACE.
- T3-3 Phase5 F-15 — close_trigger systematic mislabel as `bybit_demo_sl_tp`.
- T3-4 Phase5 F-20 — COORD_DOUBLE_CLOSE race loses correct trigger.

## References verified

| Issue | File:line | Status |
|-------|-----------|--------|
| T3-1 safety gates | `src/trading/services/order_guards.py` | Verified. Has only `_enforce_layer3_gate` (L3) today; needs the other 5 (position-size cap, per-trade max-loss, mandatory-SL, leverage cap, post-place SL verify). |
| T3-2 blank-PK | `src/bybit_demo/bybit_demo_adapter.py:1535-1547` `_build_close_order` | Confirmed. `order_id=""` hardcoded. Today's DB shows 1 row vs 24 events emitted (23 clobbered). |
| T3-3 close_trigger | `src/bybit_demo/bybit_demo_adapter.py:~360` `close_position` | Confirmed. Adapter has `self._coordinator` (attached via `attach_coordinator`), but does NOT call `set_close_reason` even though it has `close_trigger` param. |
| T3-4 race | Same as T3-3; the race is downstream consequence of T3-3's gap. | One fix solves both. |
| Reduce-position | `src/bybit_demo/bybit_demo_adapter.py:536` `reduce_position` | reduce_position lacks the `close_trigger` parameter today. Out of scope for T3-3 minimal fix; addressable in follow-up. |

## Plan

T3-2, T3-3+T3-4 are surgical single-line / single-call fixes. T3-1 is a larger 5-gate implementation. Order:

1. T3-3 + T3-4 (single change, fixes both) — wire `coordinator.set_close_reason` in `close_position`.
2. T3-2 — `_build_close_order` accepts non-empty order_id.
3. T3-1 — implement 5 safety gates in `order_guards.py`.

Investigation + proposal docs bundled per fix.
