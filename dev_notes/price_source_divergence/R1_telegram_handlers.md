# R1 — Telegram Bot Handlers

## R.1.1 — `/positions` handler

Two paths exist:

- `PortfolioHandler.positions` at `src/telegram/handlers/portfolio.py:46-56` (legacy)
- `_show_positions` at `src/telegram/handlers/control_handler.py:400` and `_build_positions_text` at `:433` (the **active** handler, registered for `/positions` per `bot.py:92` comment)

### Active path: `control_handler._build_positions_text` (`:433-477`)

Verbatim formatting block:

```python
for pos in positions:
    pnl_pct = 0.0
    if ...
        pnl_pct = (pos.mark_price - pos.entry_price) / pos.entry_price * 100
        if pos.side == "Sell" or getattr(pos.side, "value", None) == "Sell":
            pnl_pct = -pnl_pct
    ...
    f"  Entry: ${pos.entry_price:.2f} | Now: ${pos.mark_price:.2f}\n"
    f"  PnL: {pnl_pct:+.2f}%\n"
```

### Step-by-step on `/positions` invocation

1. Operator sends `/positions` to Telegram bot (`InteractiveTelegramBot` at `src/telegram/bot.py`).
2. Bot dispatches to `_show_positions(query, context)` (`control_handler.py:400`).
3. `position_service = context.bot_data.get("position_service")` is fetched (`:404-406` area).
4. `positions = await position_service.get_positions()` is called (`:408`).
5. **`position_service` is `ShadowPositionService`** when running in Shadow/paper mode (the system's only mode at present per `exchange_mode='shadow'` in `trade_log` rows). Wired via `src/factory/...` and Transformer router (Phase T3).
6. `ShadowPositionService.get_positions` does `await session.get(f"{base_url}/api/positions")` → Shadow returns positions JSON (`shadow_adapter.py:150-171`).
7. Each position dict is converted via `_build_position` (`shadow_adapter.py:673-700`) — `mark_price = data["current_price"]`, `unrealized_pnl = data["unrealized_pnl_usd"]`.
8. **CRITICAL — Transformer enrichment.** When the Transformer is wired between the bot and ShadowPositionService (which it is when running in Shadow mode per `src/core/transformer.py:947-991`), the wrapper `TransformedPositionService.get_positions` calls `await self._t._enrich_positions_with_local_prices(positions)` immediately after — `transformer.py:983-985`:

   ```python
   async def get_positions(self, symbol: str | None = None) -> list[Position]:
       positions = await self._inner.get_positions(symbol)
       await self._t._enrich_positions_with_local_prices(positions)
       return positions
   ```

9. `_enrich_positions_with_local_prices` (`transformer.py:716-841`) mutates each Position in place: replaces `pos.mark_price` with `ticker_cache.last_price`, recomputes `pos.unrealized_pnl` from `notional = pos.size * pos.entry_price`.
10. The mutated positions return to `_build_positions_text`, which formats `pnl_pct` from `(mark_price - entry_price)/entry_price` and prints `Now: ${pos.mark_price:.2f}`.

So the displayed "Now" price and PnL are **derived from main project's `ticker_cache`** (when within 0.5 % of Shadow's price) or **from Shadow's WS** (when divergence > 0.5 %, `transformer.py:771-794` keeps Shadow's mark).

## R.1.2 — `/performance` handler

`performance_command` at `src/telegram/handlers/dashboard_handler.py:1037-1157`. Registered at `:2325`:

```python
app.add_handler(CommandHandler("performance", performance_command))
```

Step-by-step:

1. Reads `pnl_manager = _svc(context, "pnl_manager")` — the `DailyPnLManager` from `src/strategies/pnl_manager.py:16`.
2. Reads `_trades_today`, `_wins_today`, `_losses_today`, `current_pnl_pct`, `current_pnl_usd`, `_best_trade_pct`, `_worst_trade_pct`, `_avg_win_pct`, `_avg_loss_pct`, `_max_drawdown_pct`, `_streak_count`, `_streak_type`, `_per_coin_stats`, `_daily_loss_limit_pct` (all attrs of `DailyPnLManager`).
3. Computes win-rate, expectancy, profit factor, risk-used inline.
4. Renders text and replies.

The values reported are entirely derived from `DailyPnLManager`'s in-memory fields. `DailyPnLManager._recalculate()` (called from `update()`) fetches the wallet via `self.account_service.get_wallet_balance()` (when Shadow-mode = `ShadowAccountService` → Shadow's `/api/balance` → Shadow's `virtual_wallet.total_realized_pnl`) plus the `position_service.get_positions()` for unrealized.

So `/performance`'s `Total PnL` is `current_pnl_pct` = `(realized + unrealized) / starting_equity * 100` — and the `realized` half comes from Shadow's authoritative wallet, while the `unrealized` half is again shaped by the Transformer enrichment described in R.1.1 step 8.

## R.1.3 — Other relevant handlers

- `PortfolioHandler.summary` (`/portfolio`) — `portfolio.py:16-43` — reads `position_service.get_positions()` and uses `pos.mark_price` (post-enrichment), formats `unrealized_pnl` from `pos.unrealized_pnl` (post-enrichment).
- `PortfolioHandler.balance` (`/balance`) — `portfolio.py:76-87` — reads `account_service.get_wallet_balance()` (= `ShadowAccountService` → Shadow's `/api/balance`).
- `PortfolioHandler.trade_history` (`/history`) — `portfolio.py:90-138` — reads main project's `trade_intelligence` table directly.
- `PortfolioHandler.pnl` (`/pnl`) — `portfolio.py:58-74` — reads `pnl_manager.get_summary()` returning `total_pnl_pct`, `realized_pnl`, `unrealized_pnl`, `mode`, `target_hit`.
- `dashboard_handler` (`/dashboard`, `/control`, etc.) — multiple price/PnL displays, all routed through the same enriched `position_service` and the `pnl_manager`.
- `EmergencyHandler` (`/emergency`) — `emergency.py:18-...` — reads `position_service.get_positions()` to display before bulk close.
- `MorningBriefing` — `features/morning_briefing.py:27-31` — same.
