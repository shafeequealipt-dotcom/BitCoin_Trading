# Issue 4 — Phase 1 Investigation Synthesis

## What the report said

`PHASE5_LIVE_MONITORING_REPORT.md` (Finding A2):

> The live ``PositionService.get_positions``
> (``src/trading/services/position_service.py:54``) parses positions
> from Bybit's API response and calls
> ``self._trading_repo.save_position(pos)`` for each one. The
> bybit_demo equivalent ``BybitDemoPositionService.get_positions``
> (``src/bybit_demo/bybit_demo_adapter.py:131``) parses positions
> identically but does NOT call save_position. So the ``positions``
> cache table stays at 0 rows when running in bybit_demo mode.

## What current code shows

### Live path (works correctly)

`src/trading/services/position_service.py:54-80`:
```python
positions = []
for item in result.get("list", []):
    size = float(item.get("size", "0"))
    if size == 0:
        continue
    pos = _parse_position(item)
    await self._trading_repo.save_position(pos)   # ← persists
    positions.append(pos)
```

### Bybit demo path (broken — pre-fix)

`src/bybit_demo/bybit_demo_adapter.py:131-166` (PRE-FIX):
```python
rows = (envelope.get("result") or {}).get("list") or []
positions: list[Position] = []
for row in rows:
    if _safe_float(row.get("size")) <= 0:
        continue
    positions.append(_build_position_from_v5(row))   # ← NO save
return positions
```

The constructor (`__init__`, lines 81-99) accepts and stores
`trading_repo` (P7 of the P1-P10 fix), and `close_position` (lines
287-470) DOES call `save_position` (line 459) to delete on
zero-size — so the persistence wiring exists. The `get_positions`
method just doesn't use it.

### Schema gap

`src/database/migrations.py` was at SCHEMA_VERSION=30 (HIGH-2 of CRITICAL/HIGH series). That migration added `exchange_mode TEXT NOT NULL DEFAULT 'shadow'` to `orders`, `account_snapshots`, and `trade_history`. The `positions` table was missed. PRAGMA table_info confirmed:

```
positions: 12 columns, NO exchange_mode
orders:    14 columns, exchange_mode at index 13
```

### `save_position` signature gap

`save_order` (`trading_repo.py:34`) has the kwarg: `async def save_order(self, order, *, exchange_mode: str = "")`.
`save_trade` (`trading_repo.py:223`) has the kwarg.
`save_position` (`trading_repo.py:159`, PRE-FIX) does NOT.

### Phase 0 evidence

`SELECT COUNT(*) FROM positions` = 0 (despite active trading).

### Consumer landscape

All production callers reach positions via the service layer (`PositionService.get_positions` or its proxy), not the repo directly. The repo's `get_position` and `get_all_positions` exist but have no production callers in current code. So the gap is invisible to callers that use `position_service` (they get the in-memory return value), but visible to anyone that reads the table directly:
- Telegram `/positions` command (depending on impl path)
- MCP `get_positions` tool (depending on impl path)
- Operator post-mortem queries
- Any future analytics or dashboard reading the positions cache

### WS path

`src/bybit_demo/bybit_demo_websocket_subscriber.py:246-270` `_handle_position` is logging-only (no persistence). This is by design — the WS path does not write to `positions`.

### `close_position` path (works correctly)

The close path at `bybit_demo_adapter.py:455-468` DOES call `save_position(pos)` after setting `pos.size = 0.0`, which deletes the row via the size==0 branch in `save_position`. So delete-on-close works; only the open-position INSERT is missing.

## Recommended fix point

1. **Schema v32**:
   - `ALTER TABLE positions ADD COLUMN exchange_mode TEXT NOT NULL DEFAULT 'shadow'` (mirrors v30 pattern; idempotent via the existing pre-flight column-exists check).
   - `CREATE INDEX IF NOT EXISTS idx_positions_mode ON positions(exchange_mode)` for mode-filtered consumer queries.

2. **`save_position(self, position, *, exchange_mode: str = "")`** — add kwarg that mirrors `save_order` / `save_trade`. Empty string preserves DEFAULT 'shadow' for legacy callers; explicit mode write goes through the new INSERT branch.

3. **`BybitDemoPositionService.get_positions`** — call `await self._trading_repo.save_position(pos, exchange_mode='bybit_demo')` for each non-zero position; failures logged, not raised (callers must continue to receive the parsed positions).

4. **`PositionService.get_positions`** (live) — pass `exchange_mode='shadow'` to `save_position` to make the tag explicit (column DEFAULT covers it but explicit kwarg is safer if the column default ever changes).

5. **`scripts/backfill_positions_exchange_mode.py`** — backfill for existing rows. Mirrors the pattern used by orders / trade_history / account_snapshots backfills (cut-over timestamp 2026-05-08T11:19:26 from `transformer_state.last_switched_at`).

## Estimated impact

- bybit_demo `positions` row count: 0 → matches current open positions count (typically 3-8)
- Telegram `/positions` and MCP tools that read the table see correct data
- Adds ~1 DB write per position per watchdog tick (~10s cadence) → at 8 positions, ~50/min → bounded by Issue 2's batching (which reduced contention 50-100x)
- Schema migration is online (ALTER TABLE ADD COLUMN with DEFAULT is fast on SQLite; index creation on currently-empty table is sub-second)
- Migration is reversible via `ALTER TABLE positions DROP COLUMN exchange_mode` (SQLite 3.35+) and `DROP INDEX idx_positions_mode`
- Shadow mode unchanged (live PositionService already persisted; explicit kwarg is just clearer)
- Backfill is vacuous in the production DB (positions row count was 0 at Phase 0); kept for symmetry with the v30 backfills
