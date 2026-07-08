# S1 — Live Divergence Capture (single instant)

## Pre-condition status

**Pre-condition NOT MET at capture time** (2026-05-02 11:30:27 UTC).

- Shadow `/api/positions` → `{"positions": []}`
- Shadow `/api/health.positions_open` = 0
- Shadow `virtual_positions WHERE status='open'` → 0 rows
- Most recent close: ONDOUSDT at 06:29 UTC, ~5 hours before capture

Per Hard Rule 5 (document gaps explicitly): the single-instant 12-source matrix described in S.1.1 cannot be produced live in this capture window. Reconstructive equivalents using closed-trade data are in `T1_closed_trade_forensics.md`.

## S.1.1 — Capture matrix (template, to be filled at next live position)

When the next position opens, run the script in S.2 below at time T and fill:

| Source | Value | File:line of source |
|---|---|---|
| Symbol | TBD | — |
| Side | TBD | — |
| Qty | TBD | — |
| Entry price (main DB `positions` table) | TBD | `data/trading.db.positions.entry_price` |
| Entry price (Shadow `virtual_positions`) | TBD | `shadow/data/shadow.db.virtual_positions.entry_price` |
| Current price from `_ws_quotes` | NOT EXTERNALLY OBSERVABLE — see W2 A2 | `src/workers/price_worker.py:66,196` |
| Current price from `ticker_cache` table | TBD via `SELECT last_price FROM ticker_cache WHERE symbol=?` | `src/database/repositories/market_repo.py:294` |
| Current price from latest M5 kline close | TBD (timeframe label NOT IDENTIFIED — see P3) | `klines.close` |
| Current price from Shadow `/api/ticker/{sym}` | TBD via `curl http://127.0.0.1:9090/api/ticker/{sym}` | `shadow/src/api/shadow_client.py:290-312` |
| Telegram /positions reported entry | TBD via `/positions` UI | `src/telegram/handlers/control_handler.py:476` |
| Telegram /positions reported "Now" | TBD via `/positions` UI | `src/telegram/handlers/control_handler.py:476` |
| Telegram /positions reported unrealized | TBD via `/positions` UI | `src/telegram/handlers/control_handler.py:477` |
| Shadow `/api/positions[i].unrealized_pnl_usd` | TBD via `curl http://127.0.0.1:9090/api/positions` | `shadow/src/exchange/order_engine.py:680` |

## S.1.2 — Expected divergences (predicted from architecture)

Based on the read paths catalogued in P1/P2/Q2/R1/R2:

1. **Entry-price divergence main vs Shadow:** Should be ZERO. Both should record the same value because main project receives the slippage-adjusted fill in `OrderService.place_order` response and persists that. Verify against `T1` data — actually, in `T1` the main project's `trade_log.entry_price` differs from Shadow's `virtual_positions.entry_price` (rounded vs full precision, and main records pre-slippage in some cases). Concretely: ONDOUSDT — main `0.27`, Shadow `0.270081`.

2. **Current-price divergence across the four live sources:**
   - `_ws_quotes` (PriceWorker WS) — fresh (≤ 5 s monotonic age)
   - `ticker_cache` table (DB) — stale (5+ h at capture; see W2 A1)
   - Shadow's `/api/ticker/{sym}` (Shadow WS) — fresh
   - M5 kline close — up to 5 min stale by design

   Expected: `_ws_quotes` and Shadow `/api/ticker` should agree within ≤ 1 tick (both come from Bybit WS). `ticker_cache` will be 5+ hours behind the live market for symbols not recently traded.

3. **PnL divergence Telegram vs Shadow:** Predicted by Transformer enrichment math (R.2.1):
   - When `ticker_cache` is fresh **and** within 0.5 % of Shadow → Telegram shows pnl computed from `ticker_cache` price + `pos.size * pos.entry_price` notional
   - Shadow shows pnl from its WS price + stored `notional_value` (= qty * fill_price)
   - These two pnl_usd values differ by exactly the slippage-on-notional term: `qty * (fill_price - entry_price-no-slip) * (price_move_pct/entry)` — i.e. small per-position but signed

## S.1.3 — Divergence trace (predicted)

Per R.2.1 chain, when divergence appears:

- Telegram side: `pnl_pct = (mark_price_local - entry_price)/entry_price`, where `mark_price_local = ticker_cache.last_price` (potentially HOURS stale).
- Shadow side: `pnl_pct = (mark_price_shadow - entry_price)/entry_price`, where `mark_price_shadow = ws_manager._latest_tickers[sym]["lastPrice"]` (fresh).

If `ticker_cache` is stale by 5 hours (current state) and the price has moved 1 % in those 5 hours, Telegram will show pnl drift of ~1 % vs Shadow on every open position. On a $200 notional that's $2.00 — well above noise.

The `transformer.py:701-706` PRICE_STALE gate (max_age default 10 s) DOES block this when working — but only when the `ticker_cache` row exists at all. For the 42 of 50 symbols not in `ticker_cache` (8 of 50 present), `_get_local_price` returns `None`, falls through to `else: fallback_count += 1` (`transformer.py:827-832`) and Shadow's mark is preserved. So the divergence pattern depends on which subset of coins has been REST-priced recently — a non-deterministic factor.
