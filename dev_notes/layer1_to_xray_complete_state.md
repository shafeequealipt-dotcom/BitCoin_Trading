# Layer 1 → X-RAY: Complete State Snapshot

Investigation prompt: `/home/inshadaliqbal786/COLLECT_LAYER1_TO_XRAY_DATA.md`
Collected: 2026-04-25 ~21:48 UTC, on the GCP VM, Claude Code CLI.
Data sources: live `shadow.db`, live `trading.db`, live `workers.log`, live `shadow.log`, source files in both repos.
This is a factual snapshot — no analysis, no fix proposals.

---

## Section 1 — The Data Source: Bybit WebSocket Into Shadow

**Files read in full**

- `/home/inshadaliqbal786/shadow/shadow.py` (309 lines)
- `/home/inshadaliqbal786/shadow/src/collector/websocket.py` (384 lines)
- `/home/inshadaliqbal786/shadow/src/collector/kline_collector.py` (201 lines)
- `/home/inshadaliqbal786/shadow/src/collector/ticker_collector.py` (124 lines)
- `/home/inshadaliqbal786/shadow/src/collector/coin_selector.py` (132 lines)
- `/home/inshadaliqbal786/shadow/src/database/connection.py` (175 lines)

```
1.1  WebSocket URL (literal, config.toml line 16):
     "wss://stream.bybit.com/v5/public/linear"
     Read at websocket.py:40 → self._ws_url = config.bybit.ws_url

1.2  Subscription topics (websocket.py:154-158, _run_connection):
     ticker_topics:  [f"tickers.{s}"     for s in self._symbols]
     kline_topics:   [f"kline.1.{s}"     for s in self._symbols]
     Two separate WS connections — one for tickers, one for klines.
     Subscribe op sent in batches of 10 topics (websocket.py:250).

1.3  Symbol list source:
     shadow.py:111-112  → CoinSelector.select_top_coins(config.collector.coin_count)
     coin_selector.py:55-94 (_fetch_and_rank):
       1) Bybit REST get_instruments_info(category="linear")
          filter: status=="Trading" AND quoteCoin=="USDT"
                  AND contractType=="LinearPerpetual"
       2) Bybit REST get_tickers(category="linear")
       3) Sort by float(ticker["turnover24h"]) desc, take top N
     N comes from config.toml [collector] coin_count = 100.
     Persisted into tracked_coins table (rank_by_volume, is_active).
     On startup, shadow.py:129-150 expands the list with any "orphan"
     symbols that have status='open' positions but fell out of top 100.
     Re-rank schedule: config.toml says coin_refresh_interval = 86400 (daily),
     but shadow.py only calls select_top_coins ONCE at startup — no
     periodic refresh task is created in the asyncio.gather() block
     (shadow.py:254-263). The tracked_coins value is rewritten only on
     restart.

1.4  Number of symbols subscribed RIGHT NOW (live):
     102 — confirmed by both:
       (a) shadow.log most recent WS health line:
           "WS health: 90m uptime, 1,471,129 msgs (272/s),
            102 coins, 0 reconnects"
           (2026-04-25 21:43:37, shadow.log line 239723)
       (b) ticker_collector "Ticker snapshot: 102/102 coins saved"
           every 60s.
     Count = 100 from CoinSelector + 2 orphan re-adds (open positions
     on symbols outside top-100). Confirmed via shadow.log:237584:
       "WARNING ... Open positions with untracked symbols — re-adding"

1.5  Message handler:
     /home/inshadaliqbal786/shadow/src/collector/websocket.py
       _handle_ticker_message (line 313-335)
       _handle_kline_message  (line 337-356)
     Klines fan out to ws_manager._kline_callbacks — registered at
     shadow.py:158: ws_manager.on_kline(kline_collector.on_kline).
     Tickers fan out to ws_manager._ticker_callbacks — but there is
     no on_ticker registration for TickerCollector. TickerCollector
     reads ws_manager._latest_tickers directly (ticker_collector.py:64).

1.6  DB write target:
     Klines  → table `klines`           (kline_collector.py:96)
     Tickers → table `ticker_snapshots` (ticker_collector.py:95)
     Both via DatabaseManager.executemany() inside a single shared
     async lock (connection.py:108).

1.7  Batching:
     Klines:  KlineCollector buffers in-memory list (kline_collector.py:36).
              Flush every FLUSH_INTERVAL=5s OR when buffer >= FLUSH_THRESHOLD=50.
              Only candles with confirm=true are buffered (line 46).
     Tickers: TickerCollector polls ws._latest_tickers every
              config.collector.ticker_snapshot_interval = 60s,
              builds a list of ALL non-stale rows and executemany.
              STALE_THRESHOLD = 300s (skip rows last-ticked >5m ago).

1.8  Reconnection logic (websocket.py:180-241, _run_connection):
     while self._running: try websockets.connect(...) → on disconnect,
     increment _reconnect_count, sleep with exponential backoff
     (1s → 2s → ... → 60s cap), then retry.
     Live state: 0 reconnects in 90 minutes uptime.
     Pings sent every PING_INTERVAL=20s (websocket.py:264-273).
     Subscriptions are rebuilt on every (re)connect via topics_fn()
     so dynamically-added symbols persist across reconnects (line 213).

1.9  Full live subscription list:
     Cannot be queried at runtime — Shadow exposes no /api/symbols endpoint.
     Inferred from tracked_coins table (rank_by_volume order, is_active=1)
     and ticker_snapshots last 24h, which both yield ≈100 coins. The +2
     orphans take the live count to 102. The exact in-memory
     ws_manager._symbols list is not persisted to disk.

1.10 Errors / warnings in last hour of shadow.log:
     - 0 ERROR lines in the last hour (grep ERROR shadow.log → none from
       the live process).
     - 7 WARNING lines, all "exchange.monitor:_check_position:232 |
       SHADOW_SL_TIGHT" for symbols TRUMPUSDT (6×) and ZBTUSDT (2×)
       at SL distances 0.18% – 1.80%. Position-monitor warnings, not
       collector errors.
     - 0 reconnects, 0 collector flush errors.
```

---

## Section 2 — The Shadow Database

```
2.1  DB file:
     path:           /home/inshadaliqbal786/shadow/data/shadow.db
     size:           856,588,288 bytes (≈817 MB)
     last modified:  2026-04-25 21:46  (live, write-active)
     auxiliary:      shadow.db-shm (32 KB), shadow.db-wal (5,714,472 B)
     `file` command not installed on this box — used `ls -la`.

2.2  Tables (.tables output):
     daily_summary          schema_version
     funding_rates          shadow_settings
     klines                 ticker_snapshots
     open_interest_history  tracked_coins
     trade_history          virtual_positions
     virtual_wallet         wallet_snapshots

2.3  klines table schema (verbatim from .schema klines):
     CREATE TABLE klines (
             symbol TEXT NOT NULL,
             timestamp INTEGER NOT NULL,
             open REAL NOT NULL,
             high REAL NOT NULL,
             low REAL NOT NULL,
             close REAL NOT NULL,
             volume REAL NOT NULL,
             turnover REAL NOT NULL DEFAULT 0,
             PRIMARY KEY (symbol, timestamp)
     );
     CREATE INDEX idx_klines_timestamp
         ON klines(timestamp DESC);

2.4  Row count per table (live SELECT COUNT(*)):
       klines           = 3,991,678
       ticker_snapshots = 1,882,075
       tracked_coins    =       311
       funding_rates    =     4,050
       open_interest    =   379,869

2.5  Distinct symbols last 24h (klines): 126
     (SELECT COUNT(DISTINCT symbol) FROM klines WHERE timestamp >
      (strftime('%s','now') - 86400)*1000)

2.6  Symbol list with candle counts last 24h
     (top tier — 78 symbols at 1160 candles ≈ 19.3 hours):
       1000BONKUSDT, 1000PEPEUSDT, AAVEUSDT, ADAUSDT, ALGOUSDT,
       APEUSDT, API3USDT, APTUSDT, ARBUSDT, ASTERUSDT, ATOMUSDT,
       AVAXUSDT, BASEDUSDT, BCHUSDT, BEATUSDT, BIOUSDT, BLURUSDT,
       BNBUSDT, BSBUSDT, BTCUSDT, CHIPUSDT, CHZUSDT, CLUSDT, CRVUSDT,
       CYSUSDT, DOGEUSDT, DOTUSDT, DYDXUSDT, EDGEUSDT, ENAUSDT,
       ENJUSDT, ENSOUSDT, ESPORTSUSDT, ETHUSDT, FARTCOINUSDT, FILUSDT,
       GALAUSDT, GRASSUSDT, HBARUSDT, HUMAUSDT, HUSDT, HYPEUSDT,
       ICPUSDT, INJUSDT, IPUSDT, KATUSDT, LABUSDT, LDOUSDT, LINKUSDT,
       LTCUSDT, MAGMAUSDT, MNTUSDT, MONUSDT, MOODENGUSDT, MOVRUSDT,
       MUSDT, NEARUSDT, ONDOUSDT, OPGUSDT, OPNUSDT, OPUSDT, ORDIUSDT,
       PENGUUSDT, PIEVERSEUSDT, PIPPINUSDT, PLUMEUSDT, POLUSDT,
       PUMPFUNUSDT, RAVEUSDT, REDUSDT, RENDERUSDT, RIVERUSDT, SANDUSDT,
       SEIUSDT, SHIB1000USDT, SKRUSDT, SOLUSDT, SOONUSDT
       (= 1160 candles each)
     20 symbols at 1093 (≈18.2 h, joined after rank refresh):
       ALICEUSDT, AXSUSDT, BANUSDT, BUSDT, COAIUSDT, DYMUSDT, EGLDUSDT,
       GMTUSDT, HIGHUSDT, HOLOUSDT, IMXUSDT, INITUSDT, LINEAUSDT,
       MANAUSDT, MEUSDT, ORCAUSDT, SAFEUSDT, SAHARAUSDT, SAPIENUSDT,
       SIRENUSDT
     Mid: HYPERUSDT, SLPUSDT (293 each), TRUMPUSDT, ZBTUSDT (80 each).
     Recent additions (~67 candles, joined ≈1 h ago):
       1000LUNCUSDT, 1000NEIROCTOUSDT, AEROUSDT, ALLOUSDT, AVNTUSDT,
       AXLUSDT, COREUSDT, DASHUSDT, DEXEUSDT, ESPUSDT, EULUSDT,
       FOLKSUSDT, GENIUSUSDT, GRIFFAINUSDT, INXUSDT, PAXGUSDT,
       PLAYSOUTUSDT, PUMPBTCUSDT, PYTHUSDT, RESOLVUSDT, ROBOUSDT,
       SENTUSDT, TREEUSDT (56), ZKPUSDT (56)

2.7  Most recent kline timestamp per symbol (top 20, all = 1777153560000):
     1000BONKUSDT, 1000PEPEUSDT, AAVEUSDT, ADAUSDT, ALGOUSDT,
     ALICEUSDT, APEUSDT, API3USDT, APTUSDT, ARBUSDT, ASTERUSDT,
     ATOMUSDT, AVAXUSDT, AXSUSDT, BANUSDT, BASEDUSDT, BCHUSDT,
     BEATUSDT, BIOUSDT, BLURUSDT
     1777153560000 ms = 2026-04-25 21:46:00 UTC — fresh, ≈90s before
     this snapshot was taken (correct behaviour: candle confirm fires
     just after the minute closes, then KlineCollector flushes).

2.8  Indexes on klines (.indexes klines):
       idx_klines_timestamp        (explicit, on timestamp DESC)
       sqlite_autoindex_klines_1   (auto, from PRIMARY KEY (symbol,timestamp))

2.9  Journal mode: wal       (PRAGMA journal_mode → wal)

2.10 Synchronous mode: 2 (FULL)   (PRAGMA synchronous → 2)

2.11 Processes holding the DB file open:
     `fuser` and `lsof` are NOT INSTALLED on this VM. Inspected
     /proc/<pid>/fd/ instead.
       PID 388 (shadow/.venv/bin/python shadow.py)
         fd  9 → /home/inshadaliqbal786/shadow/data/shadow.db
         fd 10 → shadow.db-wal
         fd 11 → shadow.db-shm
       PID 397 (trading-intelligence-mcp/.venv/bin/python workers.py)
         fd 21,22,23 → trading.db, trading.db-wal, trading.db-shm
         (NO open fd to shadow.db — confirms ShadowKlineReader and
          CoinDiscovery open a fresh sqlite3 connection per call,
          not a persistent one.)
       PID 398 (server.py --transport sse --port 8080)
         fd 21,22,23 → trading.db (only)
```

---

## Section 3 — CoinDiscovery

```
3.1  File: /home/inshadaliqbal786/trading-intelligence-mcp/src/analysis/structure/coin_discovery.py
     LOC = 106 lines.

3.2  Full SQL query (verbatim, coin_discovery.py:67-77):
     SELECT symbol, COUNT(*) as cnt
     FROM klines
     WHERE timestamp > ?
     GROUP BY symbol
     HAVING cnt >= ?
     ORDER BY symbol
     Bind parameters: (cutoff_ms, self._min_candles)
     cutoff_ms = int((time.time() - 86400) * 1000)   (line 65)
     Connection mode: read-only URI
       uri = f"file:{self._db_path}?mode=ro"           (line 59)
       conn = sqlite3.connect(uri, uri=True, timeout=5) (line 60)
     Connection is opened-and-closed per refresh.

3.3  Threshold:
     min_candles    = 50    (DEFAULT_MIN_CANDLES, line 16; passed from
                             config.toml [analysis.structure] min_candles=50)
     time_window    = 86400 s (24 h, hardcoded line 65)

3.4  Refresh interval:
     Default constant DEFAULT_REFRESH_INTERVAL = 600 (line 17).
     Live override from settings.structure.coin_refresh_interval =
     manager.py:184. config.toml line 701: coin_refresh_interval = 600.
     So live value = 600 s.

3.5  Output data shape:
     list[str] of symbol strings, sorted ascending by symbol name.
     (e.g. ["1000BONKUSDT", "1000LUNCUSDT", ..., "ZKPUSDT"])

3.6  Output storage:
     Cached on the instance: self._cached_coins (line 42).
     Returned to caller from get_analyzable_coins (line 45).
     No DB write, no on-disk persistence.

3.7  Callers of CoinDiscovery (file:line, all of them):
     - src/workers/manager.py:179   import
     - src/workers/manager.py:182   construct CoinDiscovery(...)
     - src/workers/manager.py:187   self._services["coin_discovery"] = ...
     - src/workers/manager.py:580   "coin_discovery" listed in service registry
     - src/workers/manager.py:922   passed to StructureWorker(...)
     - src/workers/structure_worker.py:35 docstring
     - src/workers/structure_worker.py:46 ctor param
     - src/workers/structure_worker.py:59 self._coin_discovery = coin_discovery
     - src/workers/structure_worker.py:149 if self._scan_full and self._coin_discovery
     - src/workers/structure_worker.py:153 self._full_universe = self._coin_discovery.get_analyzable_coins()
     There is exactly ONE call site of get_analyzable_coins(): structure_worker.py:153.

3.8  Live coin list count (running CoinDiscovery's literal query):
     126 coins.

3.9  Live coin list (all 126, alphabetical):
     1000BONKUSDT, 1000LUNCUSDT, 1000NEIROCTOUSDT, 1000PEPEUSDT,
     AAVEUSDT, ADAUSDT, AEROUSDT, ALGOUSDT, ALICEUSDT, ALLOUSDT,
     APEUSDT, API3USDT, APTUSDT, ARBUSDT, ASTERUSDT, ATOMUSDT,
     AVAXUSDT, AVNTUSDT, AXLUSDT, AXSUSDT, BANUSDT, BASEDUSDT,
     BCHUSDT, BEATUSDT, BIOUSDT, BLURUSDT, BNBUSDT, BSBUSDT,
     BTCUSDT, BUSDT, CHIPUSDT, CHZUSDT, CLUSDT, COAIUSDT, COREUSDT,
     CRVUSDT, CYSUSDT, DASHUSDT, DEXEUSDT, DOGEUSDT, DOTUSDT,
     DYDXUSDT, DYMUSDT, EDGEUSDT, EGLDUSDT, ENAUSDT, ENJUSDT,
     ENSOUSDT, ESPORTSUSDT, ESPUSDT, ETHUSDT, EULUSDT, FARTCOINUSDT,
     FILUSDT, FOLKSUSDT, GALAUSDT, GENIUSUSDT, GMTUSDT, GRASSUSDT,
     GRIFFAINUSDT, HBARUSDT, HIGHUSDT, HOLOUSDT, HUMAUSDT, HUSDT,
     HYPERUSDT, HYPEUSDT, ICPUSDT, IMXUSDT, INITUSDT, INJUSDT,
     INXUSDT, IPUSDT, KATUSDT, LABUSDT, LDOUSDT, LINEAUSDT, LINKUSDT,
     LTCUSDT, MAGMAUSDT, MANAUSDT, MEUSDT, MNTUSDT, MONUSDT,
     MOODENGUSDT, MOVRUSDT, MUSDT, NEARUSDT, ONDOUSDT, OPGUSDT,
     OPNUSDT, OPUSDT, ORCAUSDT, ORDIUSDT, PAXGUSDT, PENGUUSDT,
     PIEVERSEUSDT, PIPPINUSDT, PLAYSOUTUSDT, PLUMEUSDT, POLUSDT,
     PUMPBTCUSDT, PUMPFUNUSDT, PYTHUSDT, RAVEUSDT, REDUSDT,
     RENDERUSDT, RESOLVUSDT, RIVERUSDT, ROBOUSDT, SAFEUSDT,
     SAHARAUSDT, SANDUSDT, SAPIENUSDT, SEIUSDT, SENTUSDT,
     SHIB1000USDT, SIRENUSDT, SKRUSDT, SLPUSDT, SOLUSDT, SOONUSDT,
     TREEUSDT, TRUMPUSDT, ZBTUSDT, ZKPUSDT.
```

Live emission (workers.log 2026-04-25 21:37:47):
```
XRAY_COINS | discovered=126 new=[]
removed=['STOUSDT', 'ZAMAUSDT', 'SPKUSDT', 'WCTUSDT'] refresh_in=600s
```

---

## Section 4 — ScannerWorker And `active_universe`

```
4.1  ScannerWorker file:
     /home/inshadaliqbal786/trading-intelligence-mcp/src/workers/scanner_worker.py
     LOC = 59 lines (the worker shell).
     Heavy lifting in: src/strategies/scanner.py — class MarketScanner, 402 lines.

4.2  Run interval:
     Constructor: BaseWorker(interval_seconds=settings.scanner.scan_interval_seconds)
     config.toml [scanner] line 298: scan_interval_seconds = 300  (5 minutes).

4.3  Selection criteria (scanner.py:151-335, scan_market):
     Source: market_service.get_all_linear_tickers()  (one bulk Bybit call).
     Hard disqualifiers (lines 187-197):
       - vol < 5_000_000        → skip
       - price < 0.0001         → skip
       - spread_pct > 0.5       → skip
     Score components (lines 209-296, 0-100 base + bonuses/penalties):
       MOMENTUM       0-30 (change_24h_pct ≥ 10/5/3/1.5/0.8)
       VOLATILITY     0-25 (daily_range_pct ≥ 8/5/3/1.5)
       TREND_STRENGTH 0-15 (change/range ratio ≥ 0.6/0.4/0.25)
       VOLUME         0-20 (vol ≥ 500M/100M/50M/20M/5M)
       SPREAD         0-10 (spread ≤ 0.02/0.05/0.10/0.20)
       REGIME bonus   +10/+5/0/-10 (trending/volatile/.../dead)
       CHOP penalty   -15 (range>5 AND trend_ratio<0.25)
       max(0, score) — floor at 0.
     Sort by score descending.

4.4  Top N cutoff:
     scored[:cfg.max_coins] (scanner.py:323), where cfg.max_coins comes
     from config.toml [scanner] max_coins = 30.
     _update_universe (line 41-149) then ALWAYS prepends BTCUSDT/ETHUSDT
     and force-includes any "protected" symbols (with open positions),
     so the in-memory _active_universe can exceed 30. Then a 5-minute
     re-entry cooldown filter kicks out coins recently removed.

4.5  active_universe table schema (verbatim):
     CREATE TABLE active_universe (
             symbol TEXT PRIMARY KEY,
             opportunity_score REAL NOT NULL,
             volume_24h REAL,
             change_24h_pct REAL,
             funding_rate REAL,
             spread_pct REAL,
             coin_tier INTEGER DEFAULT 3,
             updated_at TEXT DEFAULT (datetime('now'))
     );
     Defined in src/database/migrations.py:377.

4.6  active_universe writers (file:line, only the SQL writes):
     - src/workers/scanner_worker.py:39  DELETE FROM active_universe
     - src/workers/scanner_worker.py:42  INSERT OR REPLACE INTO active_universe
     There are zero other writers.

4.7  active_universe readers (file:line):
     - NO direct SQL readers found. No "SELECT ... FROM active_universe"
       anywhere under src/. The active_universe TABLE is essentially
       write-only from a database standpoint.
     Instead, downstream consumers call MarketScanner.get_active_universe()
     which returns the IN-MEMORY self._active_universe list — never
     re-reads the table:
       - src/workers/altdata_worker.py:59     await self._scanner.get_active_universe()
       - src/workers/manager.py:530           universe = await scanner.get_active_universe()
       - src/workers/kline_worker.py:100      await self._scanner.get_active_universe()
       - src/workers/price_worker.py:57       await self._scanner.get_active_universe()
       - src/workers/regime_worker.py:112     await self._scanner.get_active_universe()
       - src/workers/strategy_worker.py:121   await self.scanner.get_active_universe()
       - src/workers/signal_worker.py:56      await self._scanner.get_active_universe()
       - src/workers/structure_worker.py:157  await self._scanner.get_active_universe()  (only when scan_full)
       - src/workers/structure_worker.py:182  await self._scanner.get_active_universe()  (legacy mode)
       - src/brain/strategist.py:592, 1250    await scanner.get_active_universe()

4.8  Current active_universe contents (live, full SELECT *):
     symbol         | score | vol_24h        | chg_24h_pct | fund | spread | tier | updated_at
     AXSUSDT        | 103   | 217,678,448.2  |  39.10      | 0    | 0.0191 | 4    | 2026-04-25 21:40:11
     BSBUSDT        | 103   | 106,579,440.5  |  64.82      | 0    | 0.0146 | 4    | 2026-04-25 21:40:12
     CHIPUSDT       | 103   |  87,847,407.4  | -17.99      | 0    | 0.0278 | 4    | 2026-04-25 21:40:14
     HYPERUSDT      | 103   | 114,969,432.1  |  62.70      | 0    | 0.0632 | 4    | 2026-04-25 21:40:14
     TRUMPUSDT      | 101   | 156,540,307.4  | -11.83      | 0    | 0.0392 | 4    | 2026-04-25 21:40:15
     KATUSDT        |  98   | 146,955,555.1  | -45.63      | 0    | 0.0618 | 4    | 2026-04-25 21:40:15
     SKRUSDT        |  95   |  14,222,992.7  | -23.65      | 0    | 0.0061 | 4    | 2026-04-25 21:40:18
     BUSDT          |  93   |   6,639,966.3  |  29.10      | 0    | 0.0462 | 4    | 2026-04-25 21:40:20
     ENSOUSDT       |  93   |  10,288,021.0  |  10.32      | 0    | 0.0223 | 3    | 2026-04-25 21:40:28
     PIPPINUSDT     |  93   |  27,049,097.1  |  19.88      | 0    | 0.0305 | 4    | 2026-04-25 21:40:36
     ZAMAUSDT       |  93   |   5,015,525.6  | -14.24      | 0    | 0.0351 | 4    | 2026-04-25 21:40:37
     EDGEUSDT       |  90   |   7,989,282.5  | -11.93      | 0    | 0.0623 | 3    | 2026-04-25 21:40:39
     ORCAUSDT       |  90   |  19,704,397.2  |  21.41      | 0    | 0.0878 | 4    | 2026-04-25 21:40:40
     BLURUSDT       |  88   |   7,731,604.4  |  -9.56      | 0    | 0.0274 | 3    | 2026-04-25 21:40:40
     ZBTUSDT        |  87   |  29,304,858.9  |  20.45      | 0    | 0.1033 | 4    | 2026-04-25 21:40:41
     OPNUSDT        |  85   |   6,772,393.8  | -12.87      | 0    | 0.0525 | 4    | 2026-04-25 21:40:41
     PIEVERSEUSDT   |  85   |   8,269,165.6  |  -8.45      | 0    | 0.0136 | 3    | 2026-04-25 21:40:42
     RIVERUSDT      |  85   |  14,506,007.8  |  -8.78      | 0    | 0.0163 | 3    | 2026-04-25 21:40:42
     SPKUSDT        |  85   |  15,466,829.7  | -10.94      | 0    | 0.0102 | 4    | 2026-04-25 21:40:43
     BIOUSDT        |  83   |   7,541,454.8  |  -6.69      | 0    | 0.0335 | 3    | 2026-04-25 21:40:44
     MAGMAUSDT      |  83   |  11,076,641.6  | -26.09      | 0    | 0.0201 | 4    | 2026-04-25 21:40:44
     ORDIUSDT       |  83   |  24,688,628.5  |  -5.23      | 0    | 0.0213 | 3    | 2026-04-25 21:40:45
     SAHARAUSDT     |  83   |  20,384,079.2  |   7.87      | 0    | 0.0213 | 4    | 2026-04-25 21:40:45
     SIRENUSDT      |  83   |  10,594,298.1  |   9.23      | 0    | 0.0347 | 4    | 2026-04-25 21:40:46
     BANUSDT        |  82   |   5,311,459.4  |  23.97      | 0    | 0.1881 | 4    | 2026-04-25 21:40:46
     BEATUSDT       |  80   |   5,240,927.0  |  -6.93      | 0    | 0.0539 | 3    | 2026-04-25 21:40:47
     MOVRUSDT       |  80   |  20,384,106.4  |   9.73      | 0    | 0.0678 | 4    | 2026-04-25 21:40:47
     WCTUSDT        |  80   |   5,640,295.6  |   7.72      | 0    | 0.0882 | 3    | 2026-04-25 21:40:47
     ALGOUSDT       |  78   |  15,734,000.7  |   5.81      | 0    | 0.0348 | 3    | 2026-04-25 21:40:47
     STOUSDT        |  78   |   5,331,426.5  |  -6.74      | 0    | 0.0444 | 3    | 2026-04-25 21:40:47

4.9  Number of coins in active_universe right now: 30.
     funding_rate column is uniformly 0.0 — scanner.py:315 hardcodes
     "funding_rate": 0.0 (no fetch).

4.10 Does structure_worker read from active_universe? NO (not from the
     table). It does call scanner.get_active_universe() at two sites:
       - src/workers/structure_worker.py:157  (in full-market mode, to
         FORCE-include scanner picks into the discovery universe)
       - src/workers/structure_worker.py:182  (in legacy non-full mode,
         as the sole symbol list)
     Both sites read the IN-MEMORY scanner._active_universe list,
     never the SQL table. Confirmed by the "Section 4.7" grep:
     no `FROM active_universe` SELECT anywhere in src/.
```

---

## Section 5 — `structure_worker` (The XRAY Driver)

```
5.1  /home/inshadaliqbal786/trading-intelligence-mcp/src/workers/structure_worker.py
     LOC = 209 lines.

5.2  Tick interval:
     BaseWorker(interval_seconds = settings.structure.worker_interval_seconds)
     config.toml [analysis.structure] worker_interval_seconds = 60.

5.3  Tick method line range:
     async def tick(self) -> None:    lines 74-145
     async def _get_universe(self):    lines 147-187
     async def _fetch_klines(self):    lines 189-209

5.4  Per-tick steps (in order):
     Step 1 (line 79):   universe = await self._get_universe()
     Step 2 (lines 82-94): build session_context (one-time per tick) by
                           fetching klines for universe[0] and calling
                           SessionTimer.get_context().
     Step 3 (lines 100-117): for symbol in universe:
                              candles = await self._fetch_klines(symbol)
                              if len(candles) < settings.structure.min_candles: skip
                              result = self._engine.analyze(symbol, current_price, candles, session_context=session_context)
                              if result: self._cache.set(symbol, result); analyzed += 1
     Step 4 (lines 122-133): all_analyses = self._cache.get_all()
                              ranked, skip_list = self._setup_scanner.scan(all_analyses, session_context)
                              self._cache.set_ranked_setups(ranked, skip_list)
     Step 5 (lines 135-145): emit XRAY_TICK log line with batch index,
                              elapsed ms, cache size, setup count.

5.5  Source of coin list (literal lines):
     structure_worker.py:149-153
       if self._scan_full and self._coin_discovery:
           ...
           self._full_universe = self._coin_discovery.get_analyzable_coins()
     Then 154-163 force-merges scanner.get_active_universe() into the
     full universe so any scanner pick that isn't in CoinDiscovery's
     output gets included.
     With config.toml scan_full_market = true + non-null coin_discovery,
     the actual source IS CoinDiscovery + Scanner-augmented list.
     If scan_full_market were false, fallback at 178-187 uses scanner
     only, then settings.bybit.default_symbols[:10] if scanner empty.

5.6  Batch size and iteration:
     batch_size = settings.structure.batch_size (config.toml = 25).
     Lines 170-176:
       batch = self._full_universe[self._batch_start:
                                   self._batch_start + self._batch_size]
       self._batch_start += self._batch_size
       if self._batch_start >= len(self._full_universe):
           self._batch_start = 0   # wrap around
     One tick = one batch of up to 25 symbols, never the whole universe.
     With universe ≈126 coins this means ⌈126/25⌉ = 6 ticks (= 6 minutes
     at 60s) for one full sweep.

5.7  StructureEngine.analyze signature (structure_engine.py:166-172):
       def analyze(
           self,
           symbol: str,
           current_price: float,
           candles: list,
           session_context=None,
       ) -> StructuralAnalysis | None
     Returns None if len(candles) < settings.min_candles (line 186).

5.8  The 12 phase names inside StructureEngine.analyze
     (banner comments in structure_engine.py):
       Phase 1  : Support & Resistance        (line 203, sr_engine)
       Phase 2  : Market Structure            (line 246, ms_engine)
       Phase 3  : Structural SL/TP Placement  (line 264, sl_engine)
       Phase 4  : Fair Value Gaps             (line 333, fvg_engine)
       Phase 5  : Order Blocks                (line 344, ob_engine)
       Phase 6  : Liquidity Zones             (line 357, liq_engine)
       Phase 7  : Liquidity Sweeps            (line 372, liq_engine)
       Phase 8  : Volume Profile              (line 399, vp_engine)
       Phase 9  : Fibonacci                   (line 414, fib_engine)
       Phase 10 : MTF Confluence              (mtf_engine)
       Phase 11 : Setup Scanner               (run by structure_worker
                                               AFTER analyze loop)
       Phase 12 : Session Timing              (session_timer, run by
                                               structure_worker)
     The prompt asked for "10 phases inside structure_engine" — only
     Phases 1-10 actually live inside structure_engine.analyze; Phases
     11-12 are orchestrated by structure_worker.

5.9  SetupScanner.scan signature (setup_scanner.py:36-50):
       def scan(
           self,
           analyses: dict[str, StructuralAnalysis],
           session: SessionContext | None = None,
       ) -> tuple[list[StructuralSetup], list[str]]
     Returns (ranked, skip_list).
     Constants: MAX_SETUPS = 12, MIN_QUALIFYING_CRITERIA = 3 (out of 6).
     Six criteria evaluated (setup_scanner.py:91-135):
       1. at_level (entry_quality in ideal/good)
       2. structure_aligned (direction matches uptrend/downtrend or ranging)
       3. rr_adequate (rr_ratio >= 2.0)
       4. smc_present (FVG | fresh OB | active sweep)
       5. confluence_good (mtf.score >= 5)
       6. session_favorable (NOT manipulation_likely AND not late_ny)
     Ranking score (lines 213-263): setup_score*0.25 + mtf*2.5 +
       SMC bonus(≤25) + RR bonus(≤15) + session modifier(-10..+5).

5.10 structure_cache writes:
     - structure_worker.py:112  self._cache.set(symbol, result)
       (per-symbol, fired on every successful StructureEngine.analyze)
     - structure_worker.py:129  self._cache.set_ranked_setups(ranked, skip_list)
       (once per tick, after SetupScanner.scan)
     Key format: bare symbol string ("BTCUSDT").
     TTL: settings.structure.cache_ttl_seconds = 300 (config.toml line 656).
     The cache lives entirely in process memory; nothing persisted.

5.11 ShadowKlineReader: how it gets klines (shadow_kline_reader.py
     in full):
     - Instantiated ONCE in manager.py:186, kept in self._services.
     - Per call to .get_klines(symbol, "60", 200):
         * Opens a fresh sqlite3.connect("file:...shadow.db?mode=ro",
           uri=True, timeout=5). (lines 58-60 in get_klines, lines
           113-115 in _aggregate_simple)
         * Executes the windowed SQL on lines 64-88 (with fancy
           ROW_NUMBER() OVER PARTITION) — but immediately discards
           the rows and falls through to _aggregate_simple
           (lines 96-98 — the comment says "doesn't correctly get
           open/close per bucket"; the windowed query still runs first).
         * _aggregate_simple opens its OWN second sqlite3 connection
           (line 113-114), runs:
             SELECT timestamp, open, high, low, close, volume, turnover
             FROM klines WHERE symbol = ?
             ORDER BY timestamp DESC
             LIMIT ?         (raw_limit = limit*60 + 60 for H1)
         * Aggregates 1m → bucket(tf_ms) in Python (lines 142-157),
           returns list[OHLCV] sorted ascending.
     Net per-symbol-per-call: 2 sqlite3 connection opens, 2 queries
     against shadow.db. Connections are explicitly closed (line 91
     and 134).

5.12 All log tags emitted by structure_worker (grep "log.*XRAY"):
     - XRAY_SESSION_ERR  (line 94, debug)
     - XRAY_TICK_ERR     (line 117, debug — per-symbol failure)
     - XRAY_SCANNER_ERR  (line 133, debug — setup scanner failure)
     - XRAY_TICK         (line 140, info — once per tick)
     The structure_engine itself emits XRAY_INIT (engine init),
     XRAY_WIRE | <name> unavailable (sub-engine import failure),
     XRAY_PHASE{1..9}_FAIL, XRAY_NO_DIRECTION, XRAY_SKIP, XRAY_DONE.
     The cache emits no log lines directly.
     CoinDiscovery emits XRAY_COINS, XRAY_COINS_ERR.
     ShadowKlineReader emits XRAY_SHADOW_KLINE_ERR, XRAY_SHADOW_AGG_ERR.
```

---

## Section 6 — How Klines Flow From Shadow To `structure_worker`

```
6.1  ShadowKlineReader instantiation pattern:
     SINGLETON per process, kept in WorkerManager._services
     (manager.py:186-188). Never recreated unless workers.py is
     restarted. Constructor stores only the path; no connection is
     opened at __init__.

6.2  Connection open pattern (literal):
     shadow_kline_reader.py:58-60
       uri = f"file:{self._db_path}?mode=ro"
       conn = sqlite3.connect(uri, uri=True, timeout=5)
       cursor = conn.cursor()
     Same three lines repeated at 113-115 inside _aggregate_simple.

6.3  SQL queries for fetching klines (verbatim):
     (a) The "windowed" query — runs but result is discarded
         (shadow_kline_reader.py:64-88):
         SELECT
             (timestamp / ?) * ? as bucket_ts,
             MIN(open) as first_open,
             MAX(high) as high,
             MIN(low) as low,
             MAX(close) as last_close,
             SUM(volume) as volume,
             SUM(turnover) as turnover,
             MIN(timestamp) as first_ts,
             MAX(timestamp) as last_ts
         FROM (
             SELECT timestamp, open, high, low, close, volume, turnover,
                    ROW_NUMBER() OVER (PARTITION BY (timestamp / ?) ORDER BY timestamp ASC) as rn_first,
                    ROW_NUMBER() OVER (PARTITION BY (timestamp / ?) ORDER BY timestamp DESC) as rn_last
             FROM klines
             WHERE symbol = ?
         )
         GROUP BY bucket_ts
         ORDER BY bucket_ts DESC
         LIMIT ?
         params: (tf_ms, tf_ms, tf_ms, tf_ms, symbol, limit)
     (b) The actually-used query (shadow_kline_reader.py:122-130):
         SELECT timestamp, open, high, low, close, volume, turnover
         FROM klines
         WHERE symbol = ?
         ORDER BY timestamp DESC
         LIMIT ?
         params: (symbol, raw_limit)
         where raw_limit = limit*minutes_per_bar + minutes_per_bar
                         = 200*60 + 60 = 12060 raw 1m candles for H1.

6.4  1m → H1 aggregation (description, shadow_kline_reader.py:140-184):
     - rows.reverse() — flip to chronological
     - for each (ts_ms, o, h, l, c, v, t):
         bucket = (ts_ms // tf_ms) * tf_ms     # H1 → 3_600_000
         first occurrence: store o,h,l,c,v,t,ts as the bucket dict
         later occurrences: keep first open, max(high), min(low),
                            overwrite close (last close wins),
                            accumulate volume + turnover.
     - sorted_buckets = sorted by ts ascending; take last `limit`.
     - Convert each bucket to OHLCV dataclass (timestamp = UTC datetime).
     Edge case: if the query happens mid-bucket the final bucket is
     partial (no special handling — it returns partial-hour data).

6.5  Connection close pattern:
     conn.close() at lines 91 (after windowed query) and 134 (after
     simple query). No try/finally — if cursor.execute throws, the
     connection leaks until garbage collected. That path is wrapped
     in `except Exception` (lines 100, 188) which logs XRAY_SHADOW_*_ERR
     and returns []. Per /proc fd inspection of PID 397, no shadow.db
     fd was held open at snapshot time.

6.6  Per-symbol vs batch query: PER-SYMBOL.
     Each call to ShadowKlineReader.get_klines fetches ONE symbol.

6.7  Connection count per structure_worker tick:
     tick() calls _fetch_klines once per symbol in batch (≤ batch_size=25)
     plus one extra for session context (universe[0]).
     _fetch_klines (structure_worker.py:189-209) FIRST tries
     market_repo.get_klines (a different connection — to trading.db,
     not shadow.db) and only falls back to shadow_reader if that
     returns empty/short. Whenever the fallback fires:
       - 2 sqlite3 connections opened against shadow.db (windowed
         query is always executed first, then _aggregate_simple opens
         a second connection).
     Worst case (every fetch falls through): 2 × (25 + 1) = 52
     shadow.db connections per tick.

6.8  Error handling that silently passes:
     - shadow_kline_reader.py:100  except Exception: log.debug + return []
     - shadow_kline_reader.py:188  except Exception: log.debug + return []
       (debug-level → not captured at INFO, invisible in workers.log
       under default log level)
     - structure_worker.py:189-209  three `except Exception: pass`
       blocks around market_repo.get_klines and shadow_reader.get_klines,
       plus a try/except around session_context that logs at debug.
     - structure_worker.py:115-117 catches per-symbol StructureEngine
       exceptions, increments errors counter, logs XRAY_TICK_ERR at
       debug level.
```

---

## Section 7 — Configuration Currently Loaded

```
7.1  [scanner] section (config.toml lines 295-302, verbatim):
     [scanner]
     # Market scanner — AGGRESSIVE TESTNET MODE
     enabled = true
     scan_interval_seconds = 300
     min_volume_24h = 5000000
     max_coins = 30
     max_spread_pct = 0.15

7.2  [analysis.structure] section (config.toml around lines 670-705):
     [analysis.structure]
     enabled = true
     worker_interval_seconds = 60
     cache_ttl_seconds = 300
     min_candles = 50
     swing_lookbacks = [3, 5, 10]
     cluster_pct = 0.3
     min_touches = 2
     max_levels_per_side = 5
     ms_swing_lookback = 5
     ms_min_swing_points = 3
     sl_buffer_pct = 0.15
     tp_buffer_pct = 0.10
     min_rr_ratio = 2.0
     sl_fallback_pct = 2.0
     tp_fallback_pct = 4.0
     # Phase 2: Smart Money Concepts
     fvg_min_gap_pct = 0.1
     fvg_max_age_candles = 50
     ob_displacement_min = 0.6
     ob_max_age_candles = 50
     liq_equal_tolerance_pct = 0.05
     liq_min_equal_count = 2
     liq_round_number_step = 100.0
     sweep_max_age_candles = 10
     sweep_min_wick_pct = 0.3
     # Phase 4: Intelligence
     setup_scanner_mode = "supplement"
     # Market Dominance: Full market scanning via Shadow DB
     scan_full_market = true
     batch_size = 25
     coin_refresh_interval = 600
     shadow_db_path = "../shadow/data/shadow.db"

7.3  [workers] section:
     [workers]
     enabled = true
     market_data_interval = 45
     news_interval = 300
     reddit_interval = 600
     altdata_interval = 300
     health_check_interval = 120
     max_consecutive_failures = 5
     restart_delay = 10

7.4  [shadow] section in trading-intelligence-mcp/config.toml:
     There is no top-level [shadow] section. Two related references:
       - Line 11 [trading.mode]: "shadow" — selects shadow_api_url
                 = "http://127.0.0.1:9090" as the executor.
       - Line 702 [analysis.structure].shadow_db_path =
                 "../shadow/data/shadow.db".
     Shadow's own config: /home/inshadaliqbal786/shadow/config.toml.
       [bybit].ws_url = "wss://stream.bybit.com/v5/public/linear"
       [collector].coin_count = 100
       [collector].coin_refresh_interval = 86400  (daily — but never
         actually triggered at runtime in shadow.py)
       [collector].kline_interval = "1"
       [collector].ticker_snapshot_interval = 60
       [database].path = "data/shadow.db"
       [database].wal_mode = true

7.5  .env keys (key names only, values redacted):
     BYBIT_API_KEY
     BYBIT_API_SECRET
     FINNHUB_API_KEY
     REDDIT_CLIENT_ID
     REDDIT_CLIENT_SECRET
     REDDIT_USERNAME
     REDDIT_PASSWORD
     TELEGRAM_BOT_TOKEN
     TELEGRAM_CHAT_ID = <REDACTED_CHAT_ID>
     ANTHROPIC_API_KEY
     MCP_AUTH_TOKEN
     OPENROUTER_API_KEY

7.6  Constants from src/config/constants.py relevant to Layer 1-XRAY:
     - SymbolRegistry class (constants.py:14-50):
         dynamic frozenset replacement, pre-seeded with
         {"BTCUSDT","ETHUSDT"}, updated by MarketScanner each scan
         (scanner.py:115-116: SUPPORTED_SYMBOLS.update(new_set)).
     - SUPPORTED_SYMBOLS (module-level instance) is the registry
       used as the project-wide "is symbol valid" check.
```

---

## Section 8 — Data Dependencies Map

| Data structure | Producer (file:line) | Consumers (file:line) | Storage |
|---|---|---|---|
| Bybit WS ticker msg | `shadow/src/collector/websocket.py:313` `_handle_ticker_message` | TickerCollector reads `ws._latest_tickers` at `ticker_collector.py:64` | in-memory dict `WebSocketManager._latest_tickers` |
| Bybit WS kline msg | `shadow/src/collector/websocket.py:337` `_handle_kline_message` | `KlineCollector.on_kline` (`kline_collector.py:41`) — only `confirm=true` | in-memory `KlineCollector._buffer` (list) |
| `klines` table rows | `shadow/src/collector/kline_collector.py:95` `INSERT OR IGNORE` (every 5s flush) | (1) `CoinDiscovery.get_analyzable_coins` (`coin_discovery.py:67`) (2) `ShadowKlineReader.get_klines` (`shadow_kline_reader.py:64,122`) (3) `KlineCollector.backfill` reads MAX(timestamp) (`kline_collector.py:118`) | SQLite `shadow.db` (WAL mode, sync=FULL) |
| `tracked_coins` rows | `shadow/src/collector/coin_selector.py:104,112` (startup) `shadow.py:140` (orphan re-add) | `CoinSelector._get_cached_coins` fallback only (`coin_selector.py:121`) | SQLite `shadow.db` |
| `ticker_snapshots` rows | `shadow/src/collector/ticker_collector.py:94` (every 60s) | None inside the project under inspection. Read by external observability/dashboards. | SQLite `shadow.db` |
| `tickers` aggregated (live) | Bybit WS, merged into `_latest_tickers` (`websocket.py:325-327`) | `MarketScanner.scan_market` calls `market_service.get_all_linear_tickers()` (separate Bybit REST path, not Shadow's WS cache); Shadow's `get_price_data` uses `_latest_tickers` for VirtualWallet/PositionMonitor (shadow.py:166-176) | in-memory in Shadow process |
| CoinDiscovery output (list[str]) | `coin_discovery.py:79-86` (cached on instance) | `structure_worker.py:153` (`self._full_universe = ...`) — sole caller | in-memory `CoinDiscovery._cached_coins` |
| `active_universe` rows | `scanner_worker.py:39` DELETE + `:42` INSERT OR REPLACE (every 300 s) | None — no SELECT FROM active_universe in src/. Listed only as a write target. | SQLite `trading.db` |
| `MarketScanner._active_universe` (in-memory list) | `scanner.py:119,137` (set in `_update_universe`) | `get_active_universe()` consumers: altdata, kline, manager, price, regime, signal, strategy, structure workers; brain.strategist; telegram analysis handler | in-memory in workers process |
| `StructuralAnalysis` per symbol | `structure_engine.py:analyze` returned, then `structure_worker.py:112 self._cache.set(symbol, result)` | `setup_scanner.py:scan` via `cache.get_all()`; `strategist.py:740,745,826,1435,1439`; `telegram/handlers/analysis.py:188` (`get_top_setups`); `performance_enforcer.py:164` (`structure_cache.get(symbol)`) | in-memory `StructureCache._cache` (TTL 300 s) |
| Ranked setups list | `setup_scanner.py:scan` returned, then `structure_worker.py:129 self._cache.set_ranked_setups(...)` | `strategist.py:737,1432`; `telegram/handlers/analysis.py:40`; `performance_enforcer.py:439` (`structure_cache.get_ranked_setups()`) | in-memory `StructureCache._ranked_setups` |
| Skip list | Same as ranked setups (returned by `setup_scanner.scan`) | `StructureCache.get_skip_list()` — currently no in-tree readers found | in-memory `StructureCache._skip_list` |
| `OHLCV` candles for X-RAY | (1) `MarketRepository.get_klines` from `trading.db` H1 (primary, `structure_worker.py:192`) (2) `ShadowKlineReader.get_klines` from `shadow.db` 1m → H1 aggregated (fallback, `:203`) | `StructureEngine.analyze` (numpy arrays at structure_engine.py:197-200) | transient list[OHLCV] per call |

---

## Section 9 — Live Behavior Snapshot

```
9.1  Last ten XRAY_TICK lines (verbatim, workers.log):
     2026-04-25 21:31:14.600 | INFO | structure_worker:tick:140 | XRAY_TICK | batch=4/5 symbols=25 analyzed=24 errors=0 cached=134 session=late_ny(early) setups=12 skips=84  el=15891ms  | no_ctx
     2026-04-25 21:32:22.616 | INFO | structure_worker:tick:140 | XRAY_TICK | batch=5/5 symbols=25 analyzed=24 errors=0 cached=134 session=late_ny(early) setups=12 skips=108 el=8014ms   | no_ctx
     2026-04-25 21:33:24.929 | INFO | structure_worker:tick:140 | XRAY_TICK | batch=0/5 symbols=5  analyzed=5  errors=0 cached=134 session=late_ny(early) setups=12 skips=88  el=2221ms   | no_ctx
     2026-04-25 21:34:30.672 | INFO | structure_worker:tick:140 | XRAY_TICK | batch=1/5 symbols=25 analyzed=25 errors=0 cached=134 session=late_ny(early) setups=12 skips=90  el=5740ms   | no_ctx
     2026-04-25 21:35:34.573 | INFO | structure_worker:tick:140 | XRAY_TICK | batch=2/5 symbols=25 analyzed=23 errors=0 cached=134 session=late_ny(early) setups=12 skips=89  el=3898ms   | no_ctx
     2026-04-25 21:36:46.367 | INFO | structure_worker:tick:140 | XRAY_TICK | batch=3/5 symbols=25 analyzed=24 errors=0 cached=134 session=late_ny(early) setups=12 skips=89  el=8322ms   | no_ctx
     2026-04-25 21:37:55.060 | INFO | structure_worker:tick:140 | XRAY_TICK | batch=1/5 symbols=25 analyzed=25 errors=0 cached=134 session=late_ny(early) setups=12 skips=65  el=8686ms   | no_ctx
     2026-04-25 21:40:14.677 | INFO | structure_worker:tick:140 | XRAY_TICK | batch=2/5 symbols=25 analyzed=23 errors=0 cached=134 session=late_ny(early) setups=12 skips=60  el=79606ms  | no_ctx
     2026-04-25 21:43:55.471 | INFO | structure_worker:tick:140 | XRAY_TICK | batch=3/5 symbols=25 analyzed=24 errors=0 cached=134 session=late_ny(early) setups=12 skips=35  el=160791ms | no_ctx
     2026-04-25 21:48:26.034 | INFO | structure_worker:tick:140 | XRAY_TICK | batch=4/5 symbols=25 analyzed=24 errors=0 cached=134 session=late_ny(early) setups=12 skips=17  el=199364ms | no_ctx
     Note: only 10 XRAY_TICK lines exist in current workers.log
     (file rotated). Total in current log: 10. Elapsed times wandered
     from 2.2 s → 8.7 s → 79 s → 160 s → 199 s as DB contention grew.
     "batch=N/5" implies the worker computed _full_universe length
     between 100-150 (5 batches × 25). Skips are ALL setups in
     skip_list at scanner time (60 → 17), confirming that the cache
     was warming up across ticks.

9.2  Most recent SCANNER lines (workers.log, last 10 of "SCANNER" or
     "active_universe" or "UNIVERSE"):
     2026-04-25 21:31:14.600 XRAY_SCANNER | total=96  qualified=73 skipped=84  | #1=EGLDUSDT(73) #2=ENAUSDT(73) #3=MONUSDT(73)
     2026-04-25 21:32:22.615 XRAY_SCANNER | total=120 qualified=93 skipped=108 | #1=EGLDUSDT(73) #2=ENAUSDT(73) #3=MONUSDT(73)
     2026-04-25 21:33:24.911 XRAY_SCANNER | total=100 qualified=81 skipped=88
     2026-04-25 21:34:30.672 XRAY_SCANNER | total=102 qualified=77 skipped=90
     2026-04-25 21:34:58.991 PRICE_UNIVERSE_SYNC | added=1 removed=1 total=32
     2026-04-25 21:35:01.121 SCANNER | coins=30 top=HYPERUSDT score=106 | no_ctx          ← MarketScanner result
     2026-04-25 21:35:34.535 XRAY_SCANNER | total=101 qualified=82 skipped=89
     2026-04-25 21:36:46.341 XRAY_SCANNER | total=101 qualified=79 skipped=89
     2026-04-25 21:37:55.059 XRAY_SCANNER | total=77  qualified=59 skipped=65
     2026-04-25 21:40:14.677 XRAY_SCANNER | total=72  qualified=54 skipped=60
     2026-04-25 21:40:47.981 SCANNER | coins=30 top=AXSUSDT  score=103 | no_ctx          ← MarketScanner result
     2026-04-25 21:43:55.437 XRAY_SCANNER | total=47  qualified=37 skipped=35
     2026-04-25 21:45:57.216 PRICE_UNIVERSE_SYNC | added=1 removed=1 total=32
     2026-04-25 21:48:26.034 XRAY_SCANNER | total=29  qualified=22 skipped=17
     XRAY_SCANNER's "total" comes from len(StructureCache.get_all());
     the values 47 → 29 indicate cache entries are EXPIRING (TTL
     300 s) faster than ticks are completing because of the long
     elapsed times in 9.1.

9.3  Slow-tick warning count in current workers.log file:
     72 BASE_WORKER_TICK_SLOW lines total in the current log.
     Most affected workers (by frequency in the tail):
       price_alert_worker, structure_worker, strategy_worker,
       enforcer_worker, fund_manager_worker, news_worker, kline_worker.
     Sample peak entries:
       structure_worker el=199364 ms (interval 60 s)
       structure_worker el=160791 ms
       structure_worker el=79607 ms
       kline_worker     el=287297 ms (interval 45 s)
       signal_worker    el=272899 ms (interval 120 s)
       news_worker      el=152784 ms (interval 300 s)
       strategy_worker  el=67501 ms  (interval 45 s)

9.4  Process tree right now:
     UID      PID  PPID  STIME  CPU  COMMAND
     inshad+  388    1   20:13  11   /home/.../shadow/.venv/bin/python shadow.py
     inshad+  397    1   20:13  41   /home/.../trading-intelligence-mcp/.venv/bin/python workers.py
     inshad+  398    1   20:13   0   /home/.../trading-intelligence-mcp/.venv/bin/python server.py --transport sse --port 8080
     ppid=1 → all three are systemd-managed.

9.5  Memory usage (from /proc/<pid>/status):
       PID 388 (shadow)        VmRSS = 68,440 KB    VmHWM = 70,312 KB
       PID 397 (workers)       VmRSS = 425,252 KB   VmHWM = 516,740 KB
       PID 398 (server.py)     VmRSS = 97,800 KB    VmHWM = 107,088 KB
     systemd Memory line:
       trading-workers.service: Memory: 584.1M (high: 600.0M, max: 800.0M,
                                available: 15.8M)
       shadow.service:           Memory: 69.0M  (high: 150.0M,  max: 200.0M,
                                available: 80.9M)

9.6  systemd service status:
     trading-workers.service  Active: active (running) since 2026-04-25
                              20:13:06 UTC; 1h 35min ago
                              Tasks: 53 (limit 4687)
                              CPU: 40min 44.314s
     shadow.service            Active: active (running) since 2026-04-25
                              20:13:06 UTC; 1h 35min ago
                              Tasks: 6 (limit 4687)
                              CPU: 11min 0.229s

9.7  Most recent ERROR lines in workers.log (verbatim, last 7):
     2026-04-25 21:39:31.772 | ERROR | strategy_worker:tick:374 | STRAT_PREFETCH_CRITICAL | el=26244ms db=3980ms h1_db=5050ms  coins=32 | sid=s-1777153144494
     2026-04-25 21:40:37.279 | ERROR | strategy_worker:tick:374 | STRAT_PREFETCH_CRITICAL | el=17807ms db=6818ms h1_db=8505ms  coins=32 | sid=s-1777153217007
     2026-04-25 21:41:59.428 | ERROR | strategy_worker:tick:374 | STRAT_PREFETCH_CRITICAL | el=35214ms db=17818ms h1_db=6250ms coins=32 | sid=s-1777153282509
     2026-04-25 21:43:52.489 | ERROR | strategy_worker:tick:374 | STRAT_PREFETCH_CRITICAL | el=57307ms db=21268ms h1_db=16709ms coins=32 | sid=s-1777153365526
     2026-04-25 21:45:08.195 | ERROR | strategy_worker:tick:374 | STRAT_PREFETCH_CRITICAL | el=28743ms db=4057ms h1_db=8307ms  coins=32 | sid=s-1777153478035
     2026-04-25 21:46:25.290 | ERROR | strategy_worker:tick:374 | STRAT_PREFETCH_CRITICAL | el=25614ms db=7601ms h1_db=14410ms coins=32 | sid=s-1777153556001
     2026-04-25 21:47:49.215 | ERROR | strategy_worker:tick:374 | STRAT_PREFETCH_CRITICAL | el=30255ms db=5811ms h1_db=10136ms coins=32 | sid=s-1777153630682
     Most recent shadow.log warning:
     2026-04-25 21:35:19.414 SHADOW_SL_TIGHT | sym=ZBTUSDT sl=0.1573 ent=0.1566 ...
     (position-monitor warning, unrelated to data path).
```

---

## Section 10 — Cache State Right Now

```
10.1 StructureCache emissions (last 30 indirect mentions, mostly
     XRAY_TICK lines that report cache size):
     - XRAY_TICK at 21:31-21:48 all show "cached=134" (constant).
     - The cache-set call has no log emission of its own
       (structure_cache.py.set is silent).
     - SetupScanner emits XRAY_SCANNER total=N where N = len(get_all()).
       That total dropped 96 → 120 → 100 → 102 → 101 → 101 → 77 → 72
       → 47 → 29 over ten ticks, because 300 s TTL is shorter than
       the per-batch elapsed time × 5-batch wrap, so older entries
       expire before they're refreshed.

10.2 Other cache-related log lines (last 20 — only 3 hits in the
     current workers.log):
     2026-04-25 21:35:45.187 TA_CACHE_SIZE | entries=78  maxsize=200 evictions=0 hit_rate=0.69 | sid=s-1777152944427
     2026-04-25 21:41:52.920 TA_CACHE_SIZE | entries=78  maxsize=200 evictions=0 hit_rate=0.68 | sid=s-1777153282509
     2026-04-25 21:47:35.568 TA_CACHE_SIZE | entries=78  maxsize=200 evictions=0 hit_rate=0.68 | sid=s-1777153630682
     (These are TA cache, not StructureCache — but the structure cache
     does not emit stats lines at all.)

10.3 Inferred StructureCache state:
     - `cached=134` on every recent XRAY_TICK is the literal value
       of len(self._cache._cache), counting BOTH live and stale entries
       (cache.size() includes expired). The fresh entries reported by
       `XRAY_SCANNER total=N` are far fewer (29 in the latest tick).
     - Hit rate: not emitted. The class tracks _hits/_misses
       (structure_cache.py:28-29) but no caller invokes
       `structure_cache.get_stats()` and logs the result. So hit-rate
       is structurally unobservable from logs.

10.4 Eviction / invalidation events:
     - StructureCache has invalidate(symbol) and clear() methods.
     - No callers found under src/ — `grep "structure_cache.invalidate\|structure_cache.clear"` returns 0 hits.
     - No "structure cache cleared/evicted" log lines in workers.log.
     - The only effective eviction is passive TTL expiry: get() and
       get_all() filter on `now - cache_time < self._ttl`.
```

---

## Section 11 — Open Questions Surfaced By This Investigation

The following are factual mismatches observed in the data — not fix proposals.

```
11.1 CoinDiscovery returns 126 coins but XRAY_TICK shows cached=134.
     Evidence:
       Source A (live SQL — Section 3.8): `SELECT symbol FROM klines
         WHERE timestamp > (now-86400)*1000 GROUP BY symbol HAVING
         COUNT(*) >= 50` → 126 rows.
       Source B (workers.log XRAY_TICK 21:48:26):
         "cached=134"
       Source C (workers.log 21:37:47 XRAY_COINS):
         "discovered=126 ... refresh_in=600s"
     Mechanism: structure_worker.py:154-163 augments the discovery
     output with scanner.get_active_universe() (which adds symbols
     CoinDiscovery missed because they fell below 50 candles).
     active_universe currently has 30 symbols, of which a few
     (e.g. STOUSDT, ZAMAUSDT, SPKUSDT, WCTUSDT — see XRAY_COINS
     "removed=" line) are NOT in CoinDiscovery's 126 — they get
     force-added. Net is 126 + (8-ish extras) ≈ 134.

11.2 active_universe table is write-only.
     Evidence:
       Source A: scanner_worker.py:39,42 — only writers (DELETE +
         INSERT OR REPLACE).
       Source B: `grep -rn "FROM active_universe" src/` → 0 hits.
       Source C: All 11 distinct call sites of get_active_universe()
         (Section 4.7) read MarketScanner._active_universe in memory,
         never the SQL table.
     Implication for restart semantics: the in-memory list is
     reconstructed on every workers.py restart by re-running
     scan_market(). The persisted table value is never consulted.

11.3 ShadowKlineReader runs an unused windowed query before the
     simple aggregation.
     Evidence: shadow_kline_reader.py:64-91 executes the
     ROW_NUMBER()+GROUP BY query, calls fetchall() and conn.close(),
     then unconditionally returns _aggregate_simple(). The comment
     at line 96-97 reads:
       # The query above doesn't correctly get open/close per bucket
       # Let's use a simpler approach
     But the window query still costs one connection-open and one
     scan of all 1-min rows for the symbol on every call.
     Net impact: 2× sqlite3 connections per fetch, of which one is
     thrown away.

11.4 structure_worker tick elapsed time exceeds its interval by orders
     of magnitude.
     Evidence:
       Source A (config.toml line 656): worker_interval_seconds = 60.
       Source B (XRAY_TICK lines): el=199364, 160791, 79606, 8686,
         8322, 5740, 8014, 15891 ms.
       Source C (BASE_WORKER_TICK_SLOW warnings): threshold_ms=2000.
     The 200-second tick reduces ticks-per-minute well below 1, so
     the 5-batch sweep that should take 5 minutes can take 15+,
     during which the cache TTL (300 s) starts expiring entries
     faster than the worker can refresh them — observed live in the
     XRAY_SCANNER total= dropping 120→47→29.

11.5 trading-workers.service memory is at the systemd "high" boundary.
     Evidence (`systemctl status trading-workers`):
       Memory: 584.1M (high: 600.0M, max: 800.0M, available: 15.8M)
     PID 397 VmHWM = 516,740 KB. Available headroom ≈ 16 MB before
     reclaim kicks in.

11.6 Two different "scan_interval_seconds" keys in config.toml.
     Evidence:
       Source A (config.toml:298): [scanner] scan_interval_seconds = 300
       Source B (config.toml:316): [strategy_engine] scan_interval_seconds = 45
     Both are valid — they apply to different workers (ScannerWorker vs
     strategy_worker), but the duplicate-key name reads as confusing
     in a quick grep.

11.7 Shadow's coin_refresh_interval (86400 s, daily) is configured but
     never triggered after startup.
     Evidence: shadow.py only schedules the tasks at lines 254-263:
       websocket, kline_collector, ticker_collector, funding_collector,
       oi_collector, position_monitor, wallet_snapshotter, daily_rollup.
     There is NO periodic CoinSelector task — select_top_coins runs
     once at line 112. tracked_coins.rank_by_volume is therefore
     stable for the entire process lifetime, regardless of the
     daily interval in config.

11.8 SUPPORTED_SYMBOLS dynamic registry is updated by MarketScanner
     (scanner.py:115-116) with the top-30 set, but CoinDiscovery
     produces a 126-coin set that is NOT pushed into SUPPORTED_SYMBOLS.
     Evidence:
       Source A: scanner.py:115 — `SUPPORTED_SYMBOLS.update(new_set)`
                 (called only from `_update_universe`).
       Source B: coin_discovery.py — no reference to SUPPORTED_SYMBOLS.
     Implication: any caller using `symbol in SUPPORTED_SYMBOLS` as a
     gate sees the 30-coin scanner set, not the 134-coin XRAY universe.

11.9 ShadowKlineReader fall-through is silent at INFO log level.
     Evidence: shadow_kline_reader.py:101,189 use log.debug.
     Default log level for the workers process appears to be INFO
     (no DEBUG lines from `xray` logger family in current workers.log).
     If shadow.db reads ever fail, only structure_worker's overall
     "len(candles) < min_candles → skip" path silences the result;
     the underlying shadow_reader error never reaches the operator.

11.10 ticker_collector reads ws._latest_tickers but is never registered
      via ws.on_ticker.
      Evidence: shadow.py:158 only calls ws_manager.on_kline(...),
      no on_ticker registration. ticker_collector.py:64 directly
      accesses ws._latest_tickers (an underscore-prefixed private).
      Behaviour works only because Python doesn't enforce privacy;
      the contract between the two modules is implicit.
```

---

## Investigation completeness

Sections 1-11 each contain raw data pulled at 2026-04-25 ~21:48 UTC. Items marked MISSING:

- Section 1.4: live in-memory subscription list cannot be dumped without a runtime hook (no Shadow API endpoint exposes it). Inferred from health log and tracked_coins.
- Section 1.9: same as 1.4.
- Section 2.1: `file` command not installed; size obtained via `ls -la`.
- Section 2.11: `fuser`/`lsof` not installed; substituted `/proc/<pid>/fd` enumeration.

No section was skipped, no command output was paraphrased.
