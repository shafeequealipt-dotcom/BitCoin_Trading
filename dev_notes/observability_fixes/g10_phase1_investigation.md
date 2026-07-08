# G10 Phase 1 — Investigation: SLTP_PAIR_OK success path

## Headline

The audit's claim is correct: `validate_pair` at
`src/core/sl_tp_validator.py:254` emits on all SKIP paths
(`SLTP_PAIR_SKIP`) but is silent on the `("OK", "")` return at L343.
Phase 0 confirmed zero SLTP_PAIR_OK / SLTP_VALIDATE events; only
4 SLTP_ADJUST + 1 TRADE_SKIP fired in the audited window.

This is a **real gap** — operators cannot distinguish:

  - "validator ran and passed"
  - "validator never ran" (caller skipped the call)
  - "validator returned OK but the trade still went sideways"

The audit's F-69 "BLUR invalid SL" case was visible only through
TRADE_SKIP after the fact. Positive evidence of validation would have
surfaced it before order placement.

## Schema decision

Tag: `SLTP_PAIR_OK` (matches existing `SLTP_PAIR_SKIP` sibling — same
prefix, same pair semantics, just the positive outcome).

Naming-check (Phase 0): `_OK` suffix is established in this codebase
(BD_TRADE_HISTORY_PERSIST_OK, BYBIT_DEMO_SET_SL_OK, BYBIT_DEMO_SET_TP_OK,
COORD_CB_OK, CLAUDE_CALL_OK, etc.). `SLTP_PAIR_OK` fits cleanly.

Field set per audit:

| Field | Source |
|-------|--------|
| `sym=` | `symbol` parameter |
| `side=` | derived from `direction` (`Buy`/`Sell`) |
| `sl_pct=` | `abs(sl - ref) / ref * 100` (absolute distance) |
| `tp_pct=` | `abs(tp - ref) / ref * 100` |
| `delta_bps=` | `gap_frac * 10000` (matches SKIP-line field) |
| `max_dist_pct=` | `self.max_distance_pct * 100` (10% default) |
| `min_gap_bps=` | `SL_TP_MIN_GAP_FRACTION_OF_ENTRY * 10000` (10 bps) |
| `decision=OK` | constant |

## Volume

Validator runs once per trade open. 20 trades / 1.5h = +13 events/hour.
Trivial.

## Behaviour preserved

- `validate_pair` return tuple unchanged (`("OK", "")` / `("SKIP", reason)`)
- All SKIP-path emissions unchanged
- Field computation cheap and read-only (no side effects)
- Caller `strategy_worker._execute_claude_trade` at L2122 unaffected

## Tests

`tests/test_sltp_validate_success.py` (6 cases):
- OK on Buy direction emits with full field set
- OK on Sell direction uses absolute distances
- SKIP (sl_equals_tp) preserves existing emission, no OK
- SKIP (wrong_side) preserves existing emission, no OK
- Reference uses `entry_price` when > 0
- Reference falls back to `current_price` when entry=0

7 existing sl_tp tests still pass.
