# Q2 — Shadow's Price Feed (the central question)

## Q.2.1 — Where does Shadow get prices? Definitive answer

**Answer = (a)** Shadow has its OWN WebSocket connection to Bybit, completely independent of the main project's `PriceWorker`.

Evidence:

- `shadow.py:27` imports `from src.collector.websocket import WebSocketManager`
- `shadow.py:123-125` constructs `WebSocketManager(config); ws_manager.set_symbols(symbols)`
- `src/collector/websocket.py:14` imports `import websockets` (raw `websockets` library, NOT `pybit`)
- `src/collector/websocket.py:40` reads `self._ws_url = config.bybit.ws_url`
- `src/collector/websocket.py:155` builds subscription topics `f"tickers.{s}" for s in self._symbols`
- `src/collector/websocket.py:199-203` opens `await websockets.connect(self._ws_url, ping_interval=None, close_timeout=5)` directly

The main project uses `pybit.unified_trading.WebSocket` (wrapped in `src.trading.websocket.BybitWebSocket`); Shadow uses raw `websockets`. They are two separate TCP connections to Bybit's WSS endpoint with two separate subscription sets.

## Q.2.2 — Shadow's price cache

Defined at `src/collector/websocket.py:43-44`:

```python
self._latest_tickers: dict[str, dict[str, Any]] = {}
self._ticker_timestamps: dict[str, float] = {}
```

- **Key:** symbol
- **Value (`_latest_tickers`):** the full Bybit ticker JSON (merged delta — see `_handle_ticker_message:325-327`)
- **Value (`_ticker_timestamps`):** `time.time()` wall-clock timestamp
- **TTL:** none — entries live forever in memory; staleness is observed externally via `get_ticker_age()` (`websocket.py:121-126`). The `TickerCollector` snapshot path uses `STALE_THRESHOLD = 300` s (`ticker_collector.py:18`) to skip writes for stale coins.
- **Sample at capture timestamp 2026-05-02 11:30:27 UTC:** could not be sampled directly from PID 390's memory. The DB-backed `ticker_snapshots` table contains a continuously refreshed (60 s default cadence) reflection of `_latest_tickers`. Eight-row sample from `data/shadow.db`:

  | symbol | ts (ms) | last_price | mark_price |
  |---|---|---|---|
  | AAVEUSDT | 1777721384597 | 92.19 | 92.20 |
  | ADAUSDT | 1777721384597 | 0.2485 | 0.2485 |
  | AEROUSDT | 1777721384597 | 0.4553 | 0.4553 |
  | ALGOUSDT | 1777721384597 | 0.1076 | 0.10766 |
  | ALICEUSDT | 1777721384597 | 0.14774 | 0.14781 |
  | APTUSDT | 1777721384597 | 0.9944 | 0.9945 |
  | ARBUSDT | 1777721384597 | 0.12192 | 0.12193 |
  | ATOMUSDT | 1777721384597 | 1.8867 | 1.8867 |

  Snapshot timestamp `1777721384597` ms = `2026-05-02 11:29:44 UTC` — fresh (43 s before capture).

**DB persistence schema (`ticker_snapshots`):** written by `TickerCollector._snapshot` at `src/collector/ticker_collector.py:94-103`:

```sql
INSERT OR IGNORE INTO ticker_snapshots
(symbol, timestamp, last_price, mark_price, index_price,
 bid1_price, bid1_size, ask1_price, ask1_size,
 high_24h, low_24h, volume_24h, turnover_24h,
 price_change_24h_pct, funding_rate,
 open_interest, open_interest_value)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
```

Cadence: `config.collector.ticker_snapshot_interval` (default 60 s).

## Q.2.3 — When Shadow fills an order

Verbatim from `src/exchange/order_engine.py:174-194`:

```python
# Step 2: Get real price — WS cache first, REST fallback for newly tracked symbols
# whose WebSocket subscription hasn't received its first tick yet.
price_data = self._price_fn(symbol)         # ← reads WebSocketManager._latest_tickers
if price_data is None:
    log.info("No WS price for {sym}, falling back to REST", sym=symbol)
    price_data = await self._fetch_rest_price(symbol)
    if price_data is None:
        return _reject(f"No price available for {symbol} (WS and REST both failed)")

last_price = float(price_data["last"])
bid_price = _safe_float(price_data.get("bid"))
ask_price = _safe_float(price_data.get("ask"))
volume_24h = _safe_float(price_data.get("volume"))
funding_rate = _safe_float(price_data.get("funding"))

# Step 3: Simulate fill with slippage
slippage = self._get_slippage_pct()
if side == "Buy":
    fill_price = last_price * (1 + slippage / 100)
else:
    fill_price = last_price * (1 - slippage / 100)
```

`self._price_fn` is `get_price_data` defined at `shadow.py:176-186`:

```python
def get_price_data(symbol: str):
    ticker = ws_manager.get_latest_ticker(symbol)
    if ticker is None:
        return None
    return {
        "last": ticker.get("lastPrice", 0),
        "bid": ticker.get("bid1Price"),
        "ask": ticker.get("ask1Price"),
        "volume": ticker.get("volume24h"),
        "funding": ticker.get("fundingRate"),
    }
```

So Shadow's fill = `last_price × (1 ± slippage_pct)`. Slippage is configured in `[exchange]` of `shadow/config.toml` (`taker_fee_rate`, `slippage_pct`, `slippage_mode`, `slippage_min`, `slippage_max`).

Persisted to `virtual_positions` row at `order_engine.py:216-238` — column `entry_price = fill_price` (i.e. the slippage-adjusted price), `entry_slippage_pct`, `entry_slippage_usd`, `notional_value = qty * fill_price`, `entry_fee_usd = notional * taker_fee_rate`.

## Q.2.4 — When Shadow updates an open position's mark price

Shadow does **not** materialize a separate "mark price update" cadence. The mark for any open position is computed on-demand by `OrderEngine.get_positions()` (`order_engine.py:660-701`):

```python
async def get_positions(self) -> list[dict[str, Any]]:
    rows = await self._db.fetch_all(
        "SELECT * FROM virtual_positions WHERE status = 'open' ORDER BY opened_at ASC"
    )
    positions = []
    now = _now_iso()
    for row in rows:
        price_data = self._price_fn(row["symbol"])
        current_price = float(price_data["last"]) if price_data else row["entry_price"]
        ...
```

That price is always the freshest WS tick (no TTL gate at all on this path). If the WS hasn't ticked since the position opened, `price_data` is `None` and the code falls back to **`row["entry_price"]`** (P&L = 0). See W2 anomaly A4 for the implication.

The separate `position_monitor` (`src/exchange/position_monitor.py`) ALSO calls `self._price_fn(symbol)` to evaluate SL/TP triggers; same source.

## Q.2.5 — How Shadow computes unrealized P&L

`src/exchange/order_engine.py:670-700`:

```python
entry_price = row["entry_price"]
notional = row["notional_value"]   # ← stored at fill time = qty * fill_price (slippage-adj)
if row["side"] == "Buy":
    unrealized_pct = (current_price - entry_price) / entry_price * 100
else:
    unrealized_pct = (entry_price - current_price) / entry_price * 100
unrealized_usd = unrealized_pct / 100 * notional
```

Fees / funding NOT included in unrealized.

## Q.2.6 — How Shadow computes realized P&L on close

`src/exchange/order_engine.py:327-356`:

```python
side = position["side"]
if side == "Buy":
    exit_price = current_price * (1 - slippage / 100)   # closing long = sell
else:
    exit_price = current_price * (1 + slippage / 100)   # closing short = buy
...
entry_price = position["entry_price"]
notional = position["notional_value"]
if side == "Buy":
    gross_pnl_pct = (exit_price - entry_price) / entry_price * 100
else:
    gross_pnl_pct = (entry_price - exit_price) / entry_price * 100
gross_pnl_usd = gross_pnl_pct / 100 * notional
exit_fee = notional * self._taker_fee_rate
net_pnl_usd = gross_pnl_usd - exit_fee
```

So Shadow's realized = `gross_pnl - exit_fee`. The `entry_fee` is debited at open via `wallet.deduct_entry_fee` (`order_engine.py:241`), so the wallet's `total_realized_pnl` is effectively `gross - entry_fee - exit_fee`. Both fees use `taker_fee_rate * notional_value` (where notional reflects the slippage-adjusted entry).

**Exit-price source:** `current_price = float(price_data["last"])` from WS cache (or the `close_price` arg if provided by SL/TP triggers — `order_engine.py:311-321`).
