# G5 Phase 1 — Investigation: BYBIT_DEMO_WS_ORDER promotion + full fields

## Headline finding

Two defects in one tag:

1. Level: emission was at DEBUG → invisible to standard INFO greps.
2. Filter: only the three terminal states (Filled / Cancelled /
   Rejected) emitted at all. Intermediate transitions (Created, New,
   PartiallyFilled, Triggered, Untriggered) were silent.

The audit's expected fields (`side, qty, price, sl_price, tp_price,
link_id`) were also absent — only `sym, oid, status` were logged.

## Schema decision

- Promote DEBUG → INFO
- Remove the terminal-only filter — emit on every order message that
  has `(sym, oid, status)`
- Add fields: `side, qty, price, sl_price, tp_price, order_type, link_id`
- `price` falls back to `avgPrice` for filled orders (Bybit populates
  `avgPrice` on Filled but `price` may be empty)
- `link_id` truncated to 24 chars (trade-plan IDs are long)
- `oid` truncated to 12 chars (consistent with existing CLOSE_EVENT)

## Volume estimate

- Per order lifecycle: ~3 transitions (New → Filled OR New →
  PartiallyFilled → Filled → Cancelled)
- 20 trades × 2 orders per trade (open + close) × 3 transitions = ~120
  events / 1.5 h ≈ +80 events/hour
- Well under +30% Phase 0 volume budget

## Behaviour preserved

- Coordinator on_trade_closed NOT called from this handler (unchanged)
- Parse-fail path unchanged
- Defensive skip when `(sym, oid, status)` triple is incomplete
- Multi-order message handling unchanged (one event per order)

## Tests

`tests/test_ws_order_observability.py` (10 cases):
- Parametrized over 6 status values — all emit at INFO
- Full field set present and parseable
- Missing-status skipped silently
- `price` falls back to `avgPrice` on Filled
- Multi-order messages emit one event per order
