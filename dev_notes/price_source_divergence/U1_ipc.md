# U1 — Cross-Process IPC Between Main Project ↔ Shadow

## U.1.1 — Order flow main → Shadow

**Main project sends order via HTTP POST.**

- File:line where main posts: `src/shadow/shadow_adapter.py:507-510` —

  ```python
  async with self._session.post(
      f"{self._url}/api/order", json=payload
  ) as resp:
      data = await resp.json()
  ```

- Payload schema (`shadow_adapter.py:496-503`):

  ```python
  payload = {
      "symbol": symbol,
      "side": side_str,           # "Buy" or "Sell"
      "qty": qty,
      "leverage": leverage or 1,
      "sl": stop_loss,
      "tp": take_profit,
  }
  ```

- File:line where Shadow receives: `shadow/src/api/shadow_client.py:105-124` (`handle_place_order`) → calls `engine.place_order(symbol, side, qty, leverage, sl_price, tp_price)`.
- File:line where Shadow returns: `shadow/src/exchange/order_engine.py:253-264` (the `result_data` dict — `order_id, symbol, side, qty, price (= fill_price, post-slippage), status="Filled", fee, leverage, margin, notional`).
- Adapter parses response: `shadow_adapter.py:531-547` — builds `Order(price=fill_price, ...)`.

**Close flow:** `shadow_adapter.py:254-271` → `POST /api/close` → `shadow_client.py:127-142` → `OrderEngine.close_position` → returns `close_result` dict (`order_engine.py:438-462`) with `entry_price, exit_price, gross/net pnl_pct/usd, hold_duration_seconds, close_trigger`.

**Reduce flow:** `shadow_adapter.py:289-330` → `POST /api/reduce` → `shadow_client.py:145-174` → `OrderEngine.reduce_position` → returns partial-close payload.

**SL/TP modify flow:** `POST /api/set-sl` and `POST /api/set-tp`.

## U.1.2 — State queries main → Shadow

**Yes, main project queries Shadow continuously.** Main never queries Bybit for positions/balance — it only queries Shadow for the simulated portfolio state.

- **Positions:** `ShadowPositionService.get_positions` (`shadow_adapter.py:150-171`) → `GET /api/positions`. Called by every dashboard handler, by `DailyPnLManager.update`, by Layer 4 watchdog tick, by `/portfolio` / `/positions` / `/pnl` / `/emergency`.
- **Single position:** `ShadowPositionService.get_position(symbol)` (`shadow_adapter.py:173-190`) → `GET /api/position/{symbol}`. Called when watchdog needs SL/TP context for one symbol.
- **Last-close:** `ShadowPositionService.get_last_close(symbol)` (`shadow_adapter.py:192-225`) → `GET /api/position/{symbol}/last_close`. Called by watchdog after a poll-detected close to fetch authoritative exit_price/net_pnl (Bug-2 fix).
- **Balance:** `ShadowAccountService.get_wallet_balance` (`shadow_adapter.py:611-626`) → `GET /api/balance`. Called by `DailyPnLManager`, `/balance`, every dashboard refresh.
- **Health:** `health_check()` on each adapter → `GET /api/health`. Called by liveness watchdogs.

**Main does NOT query Shadow for current prices.** Main has its own PriceWorker WS feed (`_ws_quotes`), and reads `ticker_cache` SQLite for Transformer enrichment. The only place main reads "Shadow's price" is implicitly via the `current_price` field embedded in `/api/positions` response — which arrives co-bundled with the position state, not as a separate price-query call.

This is the central architectural decision behind the divergence: **main and Shadow each maintain independent live price feeds; the only point of contact is the position-state response payload, where main then OVERWRITES Shadow's price with its own.**

## U.1.3 — Data Shadow returns

For each endpoint:

| Endpoint | Returns (verbatim from `shadow_client.py`) | Where main stores |
|---|---|---|
| `POST /api/order` | `{order_id, symbol, side, qty, price (fill, post-slip), status, fee, leverage, margin, notional}` | Adapter constructs `Order` dataclass; coordinator persists to `orders` and `trade_log` tables |
| `POST /api/close` | `{symbol, side, entry_price, exit_price, qty, gross_pnl_pct/usd, exit_fee, net_pnl_pct/usd, result, close_trigger, hold_duration_seconds}` | Adapter builds `Order` (status=FILLED, price=exit_price); coordinator persists to `trade_log`/`trade_intelligence` (some fields recomputed — see T1) |
| `GET /api/positions` | `{positions: [{position_id, symbol, side, entry_price, current_price, qty, leverage, notional_value, margin_used, unrealized_pnl_pct/usd, stop_loss_price, take_profit_price, opened_at, hold_duration_seconds}, ...]}` | Adapter builds `Position` list; Transformer enrichment OVERWRITES `mark_price`, `unrealized_pnl` |
| `GET /api/position/{sym}/last_close` | full row from `virtual_positions` | Adapter returns dict; watchdog reads `exit_price`, `net_pnl_usd`, `closed_at`, `hold_duration_seconds` |
| `GET /api/balance` | `{total_equity, available_balance, margin_in_use, total_unrealized_pnl, total_realized_pnl, total_fees_paid, starting_balance, total_trades, total_wins, total_losses}` | Adapter builds `AccountInfo`; `DailyPnLManager` reads `total_equity` and `unrealized_pnl` |
