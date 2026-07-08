# U2 — Shared Storage Between Main Project and Shadow

## U.2.1 — Shared database file?

**No.** Main project uses `data/trading.db`. Shadow uses `data/shadow.db`. Verified via:

- Main config refs `data/trading.db` (default in `src/config/settings.py`)
- Shadow config: `shadow/config.toml [database] path = "data/shadow.db"` (relative to shadow root → `/home/inshadaliqbal786/shadow/data/shadow.db`)
- File listing confirms two distinct files:

  ```
  /home/inshadaliqbal786/trading-intelligence-mcp/data/trading.db   # main
  /home/inshadaliqbal786/shadow/data/shadow.db                       # shadow
  ```

Tables in each:

- **main `trading.db`** (60+ tables): `trade_log`, `trade_intelligence`, `positions`, `orders`, `ticker_cache`, `klines`, `signals`, `regime_history`, `tias_results`, `apex_*`, etc.
- **shadow `shadow.db`** (13 tables): `daily_summary, funding_rates, klines, open_interest_history, schema_version, shadow_settings, sqlite_stat1, ticker_snapshots, tracked_coins, trade_history, virtual_positions, virtual_wallet, wallet_snapshots`

Note both have a `klines` table independently — they each persist their own kline backfill.

## U.2.2 — Shared cache, file, or shared memory?

**No shared in-memory state.** The two processes communicate strictly via HTTP on `127.0.0.1:9090` (verified `ss -tlnp` — Shadow PID 390 owns the socket; main PID 398 holds no listening socket on that port).

No pickle / json / shared-memory file path is read by both processes. Confirmed by inspecting:

- main entry `workers.py` (no shadow.db reads)
- shadow entry `shadow.py` (no trading.db reads)

The closest thing to shared persistence is `dev_notes/` (used as a working dir for forensic notes by humans/agents), but neither process reads from it.

## U.2.3 — Shared environment variables

**Yes.** Both processes inherit the systemd unit env from the same user `inshadaliqbal786`. Shared env vars likely to be:

- `BYBIT_API_KEY`, `BYBIT_API_SECRET` — both projects load Bybit creds (main for live trading; Shadow for `pybit.HTTP` REST fallback in `order_engine.py:25`)
- `TELEGRAM_BOT_TOKEN` — Shadow has its OWN bot (`shadow/src/telegram/bot.py`), main has its OWN bot (`src/telegram/bot.py`). If both use the same token they would step on each other; investigation shows the Shadow bot is enabled only when its own token is set, and they're separate per `shadow/config.toml [telegram]` block.
- `OPENROUTER_API_KEY` — main only

NOT IDENTIFIED — no exhaustive `env | sort` was captured during this collection. Investigated locations: `/etc/systemd/system/<unit>.service` Environment= lines (not opened during collection because systemd unit name was not enumerated).
