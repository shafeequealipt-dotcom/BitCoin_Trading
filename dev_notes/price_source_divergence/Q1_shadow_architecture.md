# Q1 — Shadow Architecture

## Q.1.1 — Process model

- **Same VM, separate process** as the main project.
- **Process:** `inshada+ 390 ... shadow.py` (verified via `ps aux | grep python`)
- **Working dir:** `/home/inshadaliqbal786/shadow`
- **Entry point:** `/home/inshadaliqbal786/shadow/shadow.py` (318 lines)
- **Started by:** systemd unit (directory `systemd/` present in shadow root) — exact unit not opened during collection.
- **Listens on:** `127.0.0.1:9090` (verified via `ss -tlnp`: `LISTEN 0 128 127.0.0.1:9090 ... users:(("python",pid=390,fd=14))`)
- **Database:** `data/shadow.db` (separate from main project's `data/trading.db`)

## Q.1.2 — Directory structure

```
/home/inshadaliqbal786/shadow/
├── backups/
├── config.toml
├── data/
│   ├── shadow.db
│   ├── shadow.db-shm
│   └── shadow.db-wal
├── layer_manager.py
├── logs/
├── requirements.txt
├── shadow.py                     ← entry point
├── src/
│   ├── api/                      ← HTTP API (aiohttp)
│   │   └── shadow_client.py      ← ALL endpoint handlers live here
│   ├── collector/                ← market-data ingest
│   │   ├── coin_selector.py
│   │   ├── funding_collector.py
│   │   ├── kline_collector.py
│   │   ├── oi_collector.py
│   │   ├── ticker_collector.py
│   │   └── websocket.py          ← WebSocketManager (Shadow's OWN WS feed)
│   ├── database/
│   │   ├── connection.py
│   │   └── migrations.py
│   ├── exchange/
│   │   ├── daily_rollup.py
│   │   ├── order_engine.py       ← order lifecycle, fills, P&L compute
│   │   ├── position_monitor.py   ← SL/TP monitor
│   │   ├── trade_recorder.py
│   │   ├── wallet.py             ← VirtualWallet
│   │   └── wallet_snapshotter.py
│   ├── telegram/                 ← (Shadow has its OWN Telegram bot)
│   └── utils/
├── systemd/
└── tests/
```

## Q.1.3 — API surface

From `src/api/shadow_client.py:84-97` (route registration):

| Method | Path | Purpose | Handler |
|---|---|---|---|
| POST | `/api/order` | Place a new order (MARKET) | `handle_place_order` |
| POST | `/api/close` | Close a position (full close) | `handle_close_position` |
| POST | `/api/reduce` | Reduce a position by qty (partial close) | `handle_reduce_position` |
| POST | `/api/set-sl` | Set stop loss | `handle_set_sl` |
| POST | `/api/set-tp` | Set take profit | `handle_set_tp` |
| GET | `/api/positions` | All open positions with live PnL | `handle_get_positions` |
| GET | `/api/position/{symbol}` | Single open position | `handle_get_position` |
| GET | `/api/position/{symbol}/last_close` | Most recent closed position record | `handle_get_last_close` |
| GET | `/api/balance` | Wallet balance | `handle_get_balance` |
| GET | `/api/ticker/{symbol}` | Latest ticker (Shadow's own WS cache) | `handle_get_ticker` |
| GET | `/api/health` | System health | `handle_health` |

**Endpoints used by main project's `OrderService` / `PositionService` adapters** (via `ShadowOrderService`, `ShadowPositionService`, `ShadowAccountService` in `src/shadow/shadow_adapter.py`):

- `POST /api/order` — order placement
- `POST /api/close` — full close
- `POST /api/reduce` — partial close
- `POST /api/set-sl`, `POST /api/set-tp` — risk modifications
- `GET /api/positions` — position state (the path that **carries Shadow's price-derived `unrealized_pnl_usd`**)
- `GET /api/position/{sym}/last_close` — authoritative close record for the watchdog (added to bypass a previous Bug 2 race; see `shadow_adapter.py:192-225`)
- `GET /api/balance` — equity / margin
- `GET /api/health` — health check
