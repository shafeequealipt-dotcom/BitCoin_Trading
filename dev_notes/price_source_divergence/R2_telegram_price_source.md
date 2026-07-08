# R2 — Telegram's Price/PnL Source Of Truth (THE ANSWER)

## R.2.1 — Unrealized PnL on open positions — definitive source

**The chain (verbatim, file:line citations):**

1. Operator sends `/positions`.
2. `_show_positions` → `position_service.get_positions()` (`control_handler.py:408`).
3. `position_service` resolved at startup to `Transformer.PositionServiceWrapper` (the `TransformedPositionService` class at `transformer.py:977-991`) wrapping `ShadowPositionService` (`shadow_adapter.py:135`).
4. `TransformedPositionService.get_positions`:

   ```python
   async def get_positions(self, symbol=None):
       positions = await self._inner.get_positions(symbol)   # ← Shadow API
       await self._t._enrich_positions_with_local_prices(positions)
       return positions
   ```
   (`transformer.py:982-985`)

5. `ShadowPositionService.get_positions` issues `GET /api/positions` to Shadow.
6. Shadow's `handle_get_positions` → `OrderEngine.get_positions()` (`shadow_client.py:213-221` → `order_engine.py:660-701`):

   ```python
   for row in rows:
       price_data = self._price_fn(row["symbol"])
       current_price = float(price_data["last"]) if price_data else row["entry_price"]
       ...
       unrealized_pct = (current_price - entry_price) / entry_price * 100   # Buy
       unrealized_usd = unrealized_pct / 100 * notional   # notional = stored fill-time notional_value
   ```

   where `_price_fn → shadow.py:get_price_data → ws_manager.get_latest_ticker(symbol)["lastPrice"]` (Shadow's OWN WS `_latest_tickers`).

7. JSON returns: `current_price` (Shadow WS), `unrealized_pnl_usd` (Shadow-computed).

8. Adapter builds `Position` dataclass with `mark_price = data["current_price"]`, `unrealized_pnl = data["unrealized_pnl_usd"]` (`shadow_adapter.py:688-700`).

9. Transformer enrichment runs:

   ```python
   local_price = await self._get_local_price(pos.symbol)     # ← ticker_cache table
   if local_price is not None:
       shadow_price = pos.mark_price
       diff_pct = (local_price - shadow_price) / shadow_price * 100
       if abs(diff_pct) > override_threshold:        # default 0.5 %
           # KEEP Shadow's price; emit PRICE_OVERRIDE warning
           continue
       pos.mark_price = local_price                  # ← OVERWRITE
       # Recalculate unrealized PnL from local price
       notional = abs(pos.size * pos.entry_price)    # ← USES entry_price * size, NOT stored notional
       if pos.side in (Side.BUY, "Buy"):
           pnl_pct = (local_price - pos.entry_price) / pos.entry_price * 100
       else:
           pnl_pct = (pos.entry_price - local_price) / pos.entry_price * 100
       pos.unrealized_pnl = pnl_pct / 100 * notional
   ```
   (`transformer.py:748-816`, abridged)

10. `_show_positions` reads `pos.mark_price` and `pos.entry_price`, recomputes pnl_pct from `(mark - entry)/entry`. The displayed value is therefore **driven by `ticker_cache.last_price`** when within 0.5 % of Shadow, and **by Shadow's WS price** otherwise.

**Source breakdown:**

| Component | Reads from |
|---|---|
| `entry_price` (display) | Shadow's `virtual_positions.entry_price` (slippage-adjusted fill) |
| `current_price` ("Now: $...") | `ticker_cache.last_price` if fresh + within 0.5 % of Shadow; else Shadow's WS `_latest_tickers["lastPrice"]` |
| `unrealized_pnl_usd` (display) | Recomputed in transformer from `pos.size * pos.entry_price * pnl_pct/100` (NOT Shadow's stored `notional_value`) when override-threshold not breached; else Shadow's value |
| `pnl_pct` (display) | Recomputed in `_build_positions_text` from `(pos.mark_price - pos.entry_price)/pos.entry_price` |

This is the divergence surface for unrealized PnL. The hypothesis "100% mostly a price-fetching difference" is correct in shape — three layered transforms, two independent live feeds, one stale fallback.

## R.2.2 — Realized PnL on closed trades — definitive source

For `/performance`:

- The "Today's PnL" shown in `/performance` reads `DailyPnLManager.current_pnl_pct` and `current_pnl_usd` (in-memory, refreshed by `pnl_manager.update()` calls scattered across handlers).
- `DailyPnLManager._recalculate` computes `current_pnl_pct = (realized_pnl + unrealized_pnl) / starting_equity * 100`.
- `realized_pnl` is fed by:
  - Wallet equity delta from `account_service.get_wallet_balance()` (= Shadow's `/api/balance`)
  - And/or per-trade summing from main project's `trade_log` table (`portfolio.py:90-104` reads `trade_intelligence`)
- `unrealized_pnl` is fed by `account_service.get_wallet_balance().unrealized_pnl` (Shadow), which is the sum of Shadow's per-position `unrealized_pnl_usd`.

For `/history`:

- `PortfolioHandler.trade_history` (`portfolio.py:90-138`) directly reads `trade_intelligence` table (main project DB):

  ```sql
  SELECT symbol, direction, pnl_pct, pnl_usd, win, strategy_name,
         hold_seconds, leverage, trade_closed_at
  FROM trade_intelligence ORDER BY id DESC LIMIT ?
  ```

- The `pnl_pct` and `pnl_usd` columns in `trade_intelligence` are populated by the trade-coordinator path on close. Concrete numeric divergence between `trade_intelligence.pnl_usd` and Shadow's `virtual_positions.net_pnl_usd` for the same trade is documented in `T1_closed_trade_forensics.md`.

**Source breakdown:**

| Display field | Reads from | Side-of-truth |
|---|---|---|
| `/performance` Daily PnL | `DailyPnLManager.current_pnl_*` | Hybrid: Shadow wallet equity + Shadow unrealized |
| `/history` per-trade pnl_usd | main project `trade_intelligence.pnl_usd` | **Diverges from Shadow** — Shadow's authoritative `virtual_positions.net_pnl_usd` differs (see T1) |
| `/portfolio` open-position unrealized | enriched `pos.unrealized_pnl` | Transformer enrichment (R.2.1) |

## R.2.3 — Same-position direct comparison

Could not be produced — no open positions exist at capture time. The reconstructive equivalent based on closed trades is in `T1_closed_trade_forensics.md`. Re-run when the next position opens; the script template:

```bash
# At time T (within 1 second):
curl -s http://127.0.0.1:9090/api/positions          # Shadow's truth
sqlite3 data/trading.db \
  "SELECT symbol, last_price, updated_at FROM ticker_cache;"   # Main's local prices
# Then capture Telegram /positions output via the bot UI
# Compare: shadow.current_price vs ticker_cache.last_price vs Telegram-displayed Now:$
```
