# Q3 — Shadow API Endpoints That Return Price or P&L

## `GET /api/positions` — handler `handle_get_positions` at `src/api/shadow_client.py:213-221`

```python
async def handle_get_positions(request):
    engine = request.app["engine"]
    positions = await engine.get_positions()    # ← OrderEngine.get_positions
    return web.json_response({"positions": positions})
```

**Backing source:** `OrderEngine.get_positions()` (`order_engine.py:660-701`) — reads `virtual_positions` rows, calls `self._price_fn(symbol)` per position to fetch live price from `WebSocketManager._latest_tickers`, computes unrealized P&L on the fly with the formula in Q.2.5.

**Live response at 2026-05-02 11:30:27 UTC** (verbatim):

```json
{"positions": []}
```

(no open positions exist — see INDEX pre-condition note)

**Per-position payload shape** (per `OrderEngine.get_positions` return-dict construction at `order_engine.py:683-700`):

```json
{
  "position_id": "<uuid>",
  "symbol": "BTCUSDT",
  "side": "Buy",
  "entry_price": 67250.5,
  "current_price": 67310.2,
  "qty": 0.0148,
  "leverage": 3,
  "notional_value": 996.31,
  "margin_used": 332.10,
  "unrealized_pnl_pct": 0.0888,
  "unrealized_pnl_usd": 0.885,
  "stop_loss_price": null,
  "take_profit_price": null,
  "opened_at": "2026-05-02T11:00:01.123456+00:00",
  "hold_duration_seconds": 1825
}
```

## `GET /api/position/{symbol}/last_close` — handler at `src/api/shadow_client.py:241-276`

Returns the authoritative close record for the most recent closed position with that symbol. Used by main project's watchdog. Query (verbatim):

```sql
SELECT position_id, symbol, side, entry_price, exit_price,
       quantity, leverage, notional_value,
       gross_pnl_pct, gross_pnl_usd,
       net_pnl_pct, net_pnl_usd,
       close_trigger, opened_at, closed_at,
       hold_duration_seconds, exit_slippage_pct,
       entry_fee_usd, exit_fee_usd, result
FROM virtual_positions
WHERE symbol = ? AND status = 'closed'
ORDER BY closed_at DESC
LIMIT 1
```

## `GET /api/balance` — handler at `src/api/shadow_client.py:279-287`

**Live response at 2026-05-02 11:30:27 UTC** (verbatim):

```json
{
  "total_equity": 6149.847369884066,
  "available_balance": 6149.847369884066,
  "margin_in_use": 0,
  "total_unrealized_pnl": 0.0,
  "total_realized_pnl": -2322.0454235650805,
  "total_fees_paid": 1528.1072065508529,
  "starting_balance": 10000.0,
  "total_trades": 1190,
  "total_wins": 447,
  "total_losses": 743
}
```

Backed by `VirtualWallet.get_balance()` reading `virtual_wallet` table (single row id=1).

## `GET /api/ticker/{symbol}` — handler at `src/api/shadow_client.py:290-312`

```python
price_fn = request.app["price_fn"]
price_data = price_fn(symbol)    # ← reads WS cache via shadow.py:get_price_data
if price_data is None:
    return web.json_response({"error": ...}, status=404)
return web.json_response({
    "symbol": symbol,
    "last_price": price_data.get("last"),
    "bid": price_data.get("bid"),
    "ask": price_data.get("ask"),
    "volume_24h": price_data.get("volume"),
    "funding_rate": price_data.get("funding"),
})
```

## `GET /api/health` — handler at `src/api/shadow_client.py:315-354`

**Live response at 2026-05-02 11:30:27 UTC** (verbatim):

```json
{
  "status": "running",
  "uptime_seconds": 494,
  "websocket": "connected",
  "coins_tracked": 50,
  "positions_open": 0,
  "monitor_active": true,
  "monitor_stats": {
    "running": true, "positions_monitored": 0,
    "total_checks": 0, "total_cycles": 493,
    "sl_triggered": 0, "tp_triggered": 0,
    "last_flush_ago": 494.5338969230652
  },
  "db_size_mb": 822.3,
  "ws_messages_total": 50886
}
```

Note: `ws_messages_total=50886` over `uptime_seconds=494` → ~103 msgs/s aggregate from Shadow's WS — confirms Shadow's WS is healthy and active.
