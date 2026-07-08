# J1 Phase 1 Step 1.1.7 — Shadow Parity Verification

Captured 2026-05-14 22:55 UTC. Read-only.

## Claim To Verify

The bybit_demo adapter writes positions to the local `positions` cache (and accumulates stale rows on missed cleanups). Shadow does not. Therefore the J1 fix in bybit_demo must not affect Shadow's path.

## Verified Facts

### `ShadowPositionService.get_positions_with_confirmation` (`src/shadow/shadow_adapter.py:164-210`)

```python
async def get_positions_with_confirmation(
    self, symbol: str | None = None
) -> PositionsQueryResult:
    data = await _shadow_get_with_retry(
        self._session,
        f"{self._url}/api/positions",
        log=self._log,
        op="positions",
    )
    if data is None:
        self._log.warning(
            f"SHADOW_POSITIONS_UNKNOWN_STATE | "
            f"reason=transport_failure | {ctx()}"
        )
        return PositionsQueryResult(
            confirmed=False, reason="transport_failure",
        )
    positions = []
    for p in data.get("positions", []):
        pos = _build_position(p)
        if symbol is None or pos.symbol == symbol:
            positions.append(pos)
    return PositionsQueryResult(
        confirmed=True, positions=tuple(positions),
    )
```

**There is no `save_position` call.** No write to the `positions` table. Shadow positions live entirely in the Shadow service's own state (the `/api/positions` HTTP endpoint exposes them); the watchdog reads them live every tick. No cache to drift.

### Symmetric contract: same confirmation flag

Shadow implements `confirmed=False` on transport failure (line 199-201). The contract matches `BybitDemoPositionService.get_positions_with_confirmation` at `src/bybit_demo/bybit_demo_adapter.py:235-237` (returns `confirmed=False` on `ret_code=10002`). The watchdog's `get_positions_with_confirmation` branch (`position_watchdog.py:520-532`) preserves last-known state when `confirmed=False` for both modes uniformly.

### Live `PositionService` (Bybit production) — has the same write pattern as bybit_demo

`src/trading/services/position_service.py:54-91`:

```python
async def get_positions(self, symbol: str | None = None) -> list[Position]:
    ...
    for item in result.get("list", []):
        size = float(item.get("size", "0"))
        if size == 0:
            continue
        pos = _parse_position(item)
        await self._trading_repo.save_position(pos, exchange_mode="shadow")
        positions.append(pos)
    ...
```

Note: the live PositionService passes `exchange_mode="shadow"` (the in-line comment at line 76-86 explains this is for legacy/Shadow callers in the live mode path). The live mode is not the operator's primary trading mode — bybit_demo is — but the live mode's writes do tag rows with `exchange_mode='shadow'`.

This raises a subtle concern: if the J1 fix prunes "rows whose symbol is missing from the response set" scoped to `exchange_mode='bybit_demo'`, the live-mode rows (tagged 'shadow') are not affected. Good.

But there is a potential collision: live PositionService writes `exchange_mode='shadow'` to the same `positions` table that Shadow's get_positions does NOT write to. The Shadow adapter reads from its own HTTP API, not from the cache. The live PositionService writes to the cache, but it is not currently the active path in the operator's bybit_demo mode. The fix needs to be careful that:

1. **bybit_demo adapter prune** deletes only `exchange_mode='bybit_demo'` rows missing from its response. (Correct scoping.)
2. **PositionReconciler** queries `WHERE exchange_mode=current_mode` so it only audits the active mode's drift.

### Schema implication

`positions` PRIMARY KEY is `symbol` alone (not composite with `exchange_mode`). This means a row tagged 'shadow' and a row tagged 'bybit_demo' for the same symbol would collide. In practice this only happens during transformer-switch operations (operator-initiated mode change). The transformer-switch path has its own handling (see `_thesis_close_callback` with `close_reason=transformer_switch`).

For J1, the fix scope is bybit_demo (the operator's active mode). Shadow and live PositionService are not touched.

## Shadow Verification Requirement For The J1 Fix

After implementing any J1 fix:

- Run the existing Shadow integration tests (if any cover the `positions` table — likely the I2 test at `tests/test_i2_ticker_fallback_orphan.py` does).
- Manually verify Shadow's get_positions does not call `save_position` (grep at the post-fix state).
- Run a smoke test: start workers in shadow mode (operator-supervised), open a trade, verify Shadow's view is unchanged, close the trade, verify Shadow's view is still unchanged.

This is Rule 10 compliance.

## Conclusion

Shadow's path does not interact with the local `positions` cache. The J1 fix can be safely scoped to bybit_demo via `exchange_mode='bybit_demo'` filters. No Shadow regression risk if the scoping is correct.
