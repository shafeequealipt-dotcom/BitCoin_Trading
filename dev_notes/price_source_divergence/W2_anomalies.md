# W2 — Anomaly Catalog

For each anomaly: **expected** vs **observed** vs **origin (file:line)** vs **downstream impact**.

---

## A1 — `ticker_cache` is silently 5+ hours stale despite WS being healthy

**Expected:** PriceWorker's WS callback writes every ticker tick to `ticker_cache` via `market_repo.save_ticker(...)` at `price_worker.py:218`. With ~50 symbols and ~100 WS msgs/sec aggregate, `ticker_cache` should have all 50 rows updated within a few seconds and a continuous refresh thereafter.

**Observed:** at capture time 2026-05-02 11:30:27 UTC, `ticker_cache` contains **only 8 rows**, all with `updated_at` between 05:18 and 06:30 UTC — **5+ hours stale**. The 8 symbols are exactly the symbols traded today (rows written by `MarketService._fetch_ticker` REST path at `market_service.py:101`).

**Origin (file:line):** `src/workers/price_worker.py:215-220`

```python
import asyncio
try:
    loop = asyncio.get_running_loop()
    loop.create_task(self.market_repo.save_ticker(ticker))
except RuntimeError:
    pass         # ← swallows the "no running event loop" exception
                  #   in the pybit thread-pool callback context
```

The pybit `WebSocket.subscribe_ticker` callback is invoked on a `pybit`-internal thread that has NO asyncio event loop attached. `asyncio.get_running_loop()` raises `RuntimeError`. The bare `except` catches it and drops the write. There is no log line. There is no metric. The DB write simply never happens.

**Downstream impact (high):**
- Transformer enrichment (`transformer.py:716-841`) reads `ticker_cache` to derive `local_price`. For the 42 of 50 symbols never REST-fetched today, `_get_local_price` returns `None` → fallback to Shadow's price (no override). For the 8 stale-by-5h symbols, the PRICE_STALE gate at `transformer.py:701-706` (max_age 10 s) DOES fire → returns `None` → also falls back. So in current state, Transformer enrichment is essentially a no-op due to A1. Whenever a fresh REST fetch lands on a symbol (e.g. when a new order is placed), that one row becomes briefly fresh and Transformer enrichment kicks in for that symbol — producing a **per-symbol bursty divergence** every time an order opens.
- Sentiment aggregator (`aggregator.py:169-175`) reads `change_24h_pct` from `ticker_cache` — same staleness issue.
- A "PRICE_STALE" warning is logged every divergent /positions call, generating log noise.

---

## A2 — Two independent Bybit WebSockets running in two processes

**Expected:** one canonical WS feed for the whole system, with downstream components reading a single price source.

**Observed:**
- main process (PID 398) opens its own `pybit.unified_trading.WebSocket` (`src/trading/websocket.py` wraps it; subscribed at `price_worker.py:111`).
- Shadow process (PID 390) opens its own raw `websockets.client` connection (`shadow/src/collector/websocket.py:199-203`).
- Each connection produces its own packet stream from Bybit, each maintains a separate cache, each ticks at slightly different microseconds.

**Origin:**
- main: `src/workers/price_worker.py:110-111` connects + subscribes
- shadow: `shadow/src/collector/websocket.py:141-165` `run()` opens both ticker and kline connections
- Architecture decision predates this collection — Shadow was originally a "data warehouse" project that grew its own price feed; main was added later but kept its own.

**Downstream impact (foundational):** every other anomaly compounds because the system has two parallel sources of truth that drift continuously. Without merging or a shared cache, no enrichment / override scheme can fully reconcile them.

---

## A3 — Transformer enrichment recomputes `unrealized_pnl` with a different notional definition than Shadow stores

**Expected:** when overwriting `pos.mark_price` with `local_price`, recompute `unrealized_pnl` using the SAME `notional` Shadow used at fill — i.e. `position.notional_value` carried in the API response.

**Observed:** at `src/core/transformer.py:815`:

```python
notional = abs(pos.size * pos.entry_price)
```

This uses `pos.entry_price` (the slippage-adjusted entry from Shadow) and `pos.size` (= `quantity`). For Buy: `notional = qty × entry_price`. For Sell: same. This **happens to match** Shadow's `notional_value = qty × fill_price` because `entry_price == fill_price` in Shadow. So the recomputation is numerically equivalent **in this respect**.

But: Transformer's pnl_pct uses `local_price` (= F-B, possibly stale or possibly recently REST-fed) versus Shadow's pnl_pct which uses `current_price` (= F-D, freshest WS). The two pct numbers can differ even when notionals agree.

**Downstream impact (medium):** when `local_price` and `shadow_price` are within 0.5 % (the override threshold), Transformer overwrites — and the displayed pnl changes from Shadow's WS-derived value to F-B-derived value. When they diverge by >0.5 %, Shadow's value is kept. Boundary effects: a position whose `local_price` happens to drift from 0.499 % to 0.501 % causes the displayed pnl to suddenly jump from one number to another even though no real price changed — a discontinuity in the UI.

---

## A4 — Shadow's `OrderEngine.get_positions` falls back to `entry_price` (PnL = 0) when WS hasn't ticked

**Expected:** if no fresh price is available, refuse to compute pnl — surface a "no price" sentinel.

**Observed:** at `shadow/src/exchange/order_engine.py:670`:

```python
current_price = float(price_data["last"]) if price_data else row["entry_price"]
```

When `price_data is None` (WS never ticked since position opened, or WS dropped this symbol), `current_price = entry_price` → `unrealized_pct = 0` → `unrealized_usd = 0`. The position appears to be exactly break-even when in reality there's no live data.

**Downstream impact (low under normal conditions, high under WS drop):** masks real PnL during WS outages. Watchdog reads `mark_price` and decides "no SL/TP trigger needed." Operator dashboard says "open position is flat" when it could be deep in either direction.

---

## A5 — Main project's close path computes its own `pnl_usd` instead of using Shadow's `net_pnl_usd` for `time_decay_*` and `mode4_*` triggers

**Expected:** Shadow is the simulation's source of truth for fill prices and PnL. Main project should persist Shadow's `net_pnl_usd` verbatim into `trade_intelligence.pnl_usd`.

**Observed (per `T1_closed_trade_forensics.md` row analysis):** for trades closed via `manual` / `strategic_review` triggers (rows 4, 7, 8 in T1), `trade_intelligence.pnl_usd == virtual_positions.net_pnl_usd` exactly. For trades closed via `time_decay_p_win_low` or `mode4_p9` (rows 1, 2, 3, 5, 6 in T1), main records its own pnl_usd computed from main-side prices (= pre-slippage) — the Δ is +$0.16 to +$0.24 per trade.

**Origin (file:line):** the divergent close path lives somewhere in main project's close-coordinator (likely `src/workers/profit_sniper.py` for mode4 triggers and `src/workers/position_watchdog.py` for time_decay; the `trade_log` `close_reason` matches those). Both paths construct a `trade_intelligence` row themselves rather than fetching it from Shadow's `/api/position/{sym}/last_close` endpoint (which exists *specifically for this purpose* per `shadow_adapter.py:192-225` — but is only used by the watchdog for the post-close detection path, not for these self-initiated closes).

**Downstream impact (high):**
- `trade_intelligence` lifetime sums diverge from Shadow's `virtual_wallet.total_realized_pnl` — verified non-zero at $1.06 across 8 trades; extrapolates to potentially $100+ across the 1190-trade lifetime.
- `/performance` and `/history` show different numbers depending on which view the operator looks at: `/performance` reads `pnl_manager.current_pnl_pct` which is fed by Shadow wallet; `/history` reads `trade_intelligence.pnl_usd` which has its own value.
- TIAS feedback loop is fed `trade_intelligence` rows — so the AI's lessons-learned input has biased PnL.

---

## A6 — `trade_intelligence.position_size_usd` ≠ Shadow `notional_value` (by ~50 % on some trades)

**Expected:** "position size" should mean the same thing in both places.

**Observed:** for MANAUSDT (T1 row 2) and AXSUSDT (T1 row 3), `position_size_usd ≈ notional_value / 1.5`. For ONDOUSDT (T1 row 1), they match. The 1.5× ratio for those two corresponds to leverage=3 → suggests `position_size_usd` for those rows is `notional / leverage = margin_required`, NOT notional. For ONDOUSDT (lev=2) the ratio would be 0.5× — but they happen to be equal there. Likely the field semantics changed during a refactor and old rows have one meaning and new rows have another. NOT IDENTIFIED — the column source is `apex_final_size` per the `trade_intelligence.apex_final_size` field, which APEX records as a USD risk number.

**Downstream impact (low for current bug, but a separate inconsistency):** anyone aggregating "total position size today" gets a mixed-units number.

---

## A7 — `position_card` formatter and `_build_positions_text` use different formulas

NOT VERIFIED in this collection. `PortfolioHandler.positions` calls `position_card(pos)` (`portfolio.py:53`); `_show_positions` builds via `_build_positions_text` (`control_handler.py:433`). The two helpers may render the same Position with subtly different formulas. Worth a quick check by the next collector pass. Likely identical to within a sign convention.

---

## A8 — Shadow's `_latest_tickers` is unbounded (no TTL eviction)

**Expected:** stale entries should expire so the cache reflects only live data.

**Observed:** `shadow/src/collector/websocket.py:43-44` defines `_latest_tickers` and `_ticker_timestamps` as plain dicts with no TTL. `get_ticker_age()` at `:121-126` exposes age but `_latest_tickers` never evicts. If a coin falls out of subscription, its last entry persists indefinitely.

**Downstream impact (low):** rare in practice because reconnect rebuilds the subscription set, so most entries are continuously refreshed. But during a WS drop, downstream readers (`OrderEngine.get_positions`, `/api/ticker`) receive arbitrarily-old prices with no warning.

---

## Catalog summary — root causes ranked by impact

1. **A2** — two-WebSocket architecture (foundational; every other anomaly compounds)
2. **A1** — silent failure of WS→ticker_cache write path (causes Transformer enrichment to be effectively non-functional except at random instants)
3. **A5** — main-side close path producing its own pnl for time_decay and mode4 triggers (the root cause of operator-visible /performance vs /history divergence)
4. **A3** — Transformer-vs-Shadow notional/price mismatch (continuous low-amplitude drift)
5. **A4** — `OrderEngine.get_positions` `else row["entry_price"]` fallback (silently flatlines pnl during WS outages)
6. **A6, A7, A8** — secondary inconsistencies, lower priority for the operator's stated symptom
