# G3 Phase 1 — Investigation: BYBIT_DEMO_WS_EXECUTION INFO promotion

## Headline finding

The audit's literal tag `BYBIT_DEMO_WS_EXECUTION` does not exist in
`src/`. The execution-WS handler at
`src/bybit_demo/bybit_demo_websocket_subscriber.py:329-417` covers all
three execution outcomes but at mixed log levels:

| Outcome | Tag | Level | Visibility |
|---------|-----|-------|------------|
| Fully-flatting close (closedSize > 0, leavesQty == 0) | `BYBIT_DEMO_WS_CLOSE_EVENT` | INFO | visible |
| Partial fill (closedSize > 0, leavesQty > 0) | `BYBIT_DEMO_WS_EXEC_PARTIAL` | INFO | visible |
| Non-close fill (closedSize == 0, e.g. opening / reduction) | `BYBIT_DEMO_WS_EXEC_NON_CLOSE` | **DEBUG** | hidden |

Opening fills, reduction fills, modification fills, funding payments,
and ADL trades all take the third path and are invisible to log greps.

The G3 fix: **promote BYBIT_DEMO_WS_EXEC_NON_CLOSE from DEBUG to INFO**
and add the same field set the CLOSE_EVENT carries so log consumers see
consistent shape across all three outcomes.

## Schema decision

Tag: keep `BYBIT_DEMO_WS_EXEC_NON_CLOSE` (matches existing cluster
naming `BYBIT_DEMO_WS_EXEC_*` family).

Fields added: `side`, `exec_price`, `exec_qty`, `exec_fee`, `exec_type`.

Before:
```
BYBIT_DEMO_WS_EXEC_NON_CLOSE | sym=BTCUSDT oid=OID-AB closed_size=0 | no_ctx
```

After:
```
BYBIT_DEMO_WS_EXEC_NON_CLOSE | sym=BTCUSDT oid=OID-AB side=Buy exec_price=82000.5 exec_qty=0.05 exec_fee=0.0012 closed_size=0 exec_type=Trade | no_ctx
```

## Volume estimate

Per trade lifecycle: ~1 opening fill + 0-2 reductions + 1 closing fill
= ~3 execution events. 20 trades / 1.5h ≈ 60 INFO events / 1.5h ≈ 40
events/hour. Well within the +30% volume budget.

## Behaviour preserved

- All non-emission logic unchanged (closed_size and leaves_qty
  classification, dedup gate, close dispatch)
- Returns from the non-close branch unchanged
- Coordinator interactions unchanged
- Tests verify `coordinator.on_trade_closed` is NOT called on the
  non-close path (existing invariant preserved)

## Tests

`tests/test_ws_execution_observability.py` (2 cases):
- Non-close fill emits at INFO with all required fields
- exec_type field propagates (Trade/Funding/Settle)
