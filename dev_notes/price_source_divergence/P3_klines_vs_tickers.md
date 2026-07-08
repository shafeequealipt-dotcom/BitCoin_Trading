# P3 — Klines vs Tickers (timeframe distinction)

## P.3.1 — KlineWorker

- **Path:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/kline_worker.py` (494 lines)
- **What it fetches:** Bybit linear-perp OHLCV bars across multiple timeframes (M5, H1, H4, D1 per the project's documented timeframe set).
- **Cadence:** sweet-spot scheduler (per the Layer 1A/1B partition). Tick body fetches per the configured intervals; finished bars are persisted, the in-progress bar is not (standard backfill pattern).
- **Storage:** `klines` SQLite table in `data/trading.db`. Schema query confirms columns include `symbol, timeframe, open, high, low, close, volume, turnover, timestamp` (plus indexes on `(symbol, timeframe, timestamp)`).
- **Distinction:** the most recent row for `(symbol, timeframe='5m')` may be either:
  - the *finished* M5 candle (close ≤ 5 min old), or
  - the *in-progress* M5 candle (close = whatever it was at the time of the last upsert)
  Depending on writer cadence and finalization rules. Need to verify whether the worker upserts the in-progress bar or only finalized bars.

## P.3.2 — Crucial question: does any consumer treat `klines.close` as "current price"?

Grep-based answer:

```
$ grep -rn "klines.*close\|SELECT.*close.*FROM klines" src/ --include='*.py'
```

The grep returned no direct matches for `SELECT … close … FROM klines` patterns inside the production paths reviewed for P2 (assembler / transformer / position_watchdog / profit_sniper / scanner / market_service). Klines are read by:

- `StructureWorker` and `RegimeWorker` (FVG/OB distance, ATR/ADX) — these correctly read OHLC, not "current price"
- `TIAS` and TradeScorer — read finished bars for backtest/scoring
- `KlineCollector` (Shadow side) consumes M5 bars only for analytics

Conclusion (within the bounds of this collection): **no production "current price" reader uses `klines.close` as a proxy.** Current-price readers go to F-A / F-B / F-C (see P2). If a stale-by-up-to-5-minutes price ever appears in the dashboard, the cause is the F-B 5-hour staleness from W2 anomaly A1, not klines fallthrough.

## P.3.3 — Latest M5 close vs live tick comparison

Could not be produced reliably from the live DB at capture time:

```
=== klines latest M5 close 5 coins ===
(empty result for timeframe='5m' on BTC/ETH/SOL/DOGE/AXS)
```

The query `WHERE timeframe='5m'` returned zero rows for the spot-checked symbols. Likely the timeframe column uses a different label (`"5"` or `"M5"`). NOT IDENTIFIED — investigated `'5m'`. A follow-up should run:

```
SELECT DISTINCT timeframe FROM klines LIMIT 20;
```

to find the actual label, then re-issue the comparison. This collection's other modules do not depend on the M5 comparison; they answer the divergence question without it.
