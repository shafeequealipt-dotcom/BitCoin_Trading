# Q1 Step 1.3 — Detector Input Signals

The `RegimeDetector.detect(symbol)` method pulls all of its decision inputs from a single source: `TAEngine.analyze(candles)` where `candles` is the per-symbol 200-bar H1 kline window. **Every input is per-coin. No cross-coin signal, no BTC override, no fear-greed, no funding, no OI feeds into the regime decision.**

## Input source

`market_repo.get_klines(symbol, TimeFrame.H1.value, 200)` at `src/strategies/regime.py:90` — 200 hourly candles for the given symbol from `MarketRepository` (backed by SQLite `klines` table). Hourly granularity means a regime decision reflects the last ~8.3 days of price action.

If the fetched kline count < 50, the detector returns RANGING with confidence 0.3 (insufficient-data fallback). This fires for symbols on the watch_list with thin local history.

## Input table

| Input | Extracted from | Indicator source file | Per-coin? | Notes |
|---|---|---|---|---|
| `adx` | `ta["trend"]["adx"]["adx"]` | `src/analysis/indicators/trend.py:169` (`adx()`) | Yes | 14-bar Wilder ADX |
| `plus_di` | `ta["trend"]["adx"]["plus_di"]` | `src/analysis/indicators/trend.py:169` (same `adx()` returns tuple) | Yes | Directional indicator for uptrend |
| `minus_di` | `ta["trend"]["adx"]["minus_di"]` | `src/analysis/indicators/trend.py:169` | Yes | Directional indicator for downtrend |
| `choppiness` | `ta["volatility"]["choppiness_index"]` | `src/analysis/indicators/volatility.py:199` (`choppiness_index()`) — last-valid value via `_last_valid(ci)` at `src/analysis/engine.py:295,320` | Yes | 14-bar Choppiness Index |
| `atr` | `ta["volatility"]["atr_14"]` | `src/analysis/engine.py` (passed through) | Yes | Used only to feed atr_percentile via NATR; not a direct branch input |
| `natr` | `ta["volatility"]["natr_14"]` | `src/analysis/engine.py` | Yes | Normalized ATR; `atr_percentile = natr * 100` (lines 124-126) |
| `volume_ratio` | `ta["volume"]["volume_sma_ratio"]` | `src/analysis/engine.py:351` | Yes | Current volume / SMA volume; used in VOLATILE (>2.0) and DEAD (<0.5) branches |

## Per-coin verification

All 7 inputs derive from `candles=klines` where `klines` was fetched for the specific `symbol` argument. There is **no shared state, no BTC override, no aggregate computation** along the path. Every call to `detect(symbol)` recomputes the full pipeline from that symbol's klines.

## Freshness

- Kline retrieval is from the SQLite `klines` table. The `MarketRepository.get_klines(symbol, "60", 200)` returns the latest 200 hourly candles ordered DESC. Latency depends on how recently the kline_worker landed the most recent hour.
- `TAEngine.analyze` is computed on demand and not cached at the detector level. The hysteresis state machine caches the resulting `RegimeState`, but the inputs are not memoized.
- `RegimeWorker` fires at sweet spot `"1:15"` (every cycle, minute 15) — typically every 5 minutes. So per-coin regime is refreshed approximately every 5 minutes.

## Implication for Q1

Every input is per-coin. Architecturally the detector is unambiguously per-coin. The empirical evidence in `q1_empirical_variance.md` confirms this in production: 92.5% of 5-minute time buckets show >1 distinct regime label across the 50 watch_list symbols.
