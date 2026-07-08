# Q1 Step 1.1 — Regime Detector Code Locations (verified at HEAD 848fe40)

## Canonical files

| Artifact | Path | Lines | Purpose |
|---|---|---|---|
| `RegimeDetector` class | `src/strategies/regime.py` | 21-233 (233 total) | Per-symbol regime classification with hysteresis |
| `RegimeWorker` class | `src/workers/regime_worker.py` | 23-315 (315 total) | Sweet-spot scheduler; global + per-coin batch detection per cycle |
| `MarketRegime` enum + `RegimeState` dataclass + `REGIME_ACTIVE_CATEGORIES` | `src/strategies/models/regime_types.py` | 1-80 (80 total) | Type system and strategy gating map |
| `regime_history` table schema | `src/database/migrations.py` | 389-403 | Global (BTCUSDT) regime persistence; 60-day retention |
| `coin_regime_history` table schema | `src/database/migrations.py` | 1246-1261 | Per-coin regime persistence; 24-hour retention |
| TAEngine consumer of klines | `src/analysis/engine.py` | 52-... | `TAEngine.analyze(candles)` produces ADX, choppiness_index, atr_14, natr_14, volume_sma_ratio |

## Detector public surface (used by external callers)

| Method | Returns | Caller verified |
|---|---|---|
| `RegimeDetector.detect(symbol)` | `RegimeState` | `RegimeWorker.tick()`; on-demand global detection |
| `RegimeDetector.detect_per_coin(symbols)` | `dict[str, RegimeState]` | `RegimeWorker.tick()` per-coin batch |
| `RegimeDetector.get_coin_regime(symbol)` | `RegimeState | None` | scanner, strategist, volatility_profile, layer4_protection, tias |
| `RegimeDetector.get_last_regime()` | `RegimeState | None` | strategist (cheap reads in market_data prompt build) |
| `RegimeDetector.is_ready()` | `bool` | APEX/TIAS cold-start race avoidance |

## Internal state (per-symbol keying)

| Field | Type | Keyed by symbol? |
|---|---|---|
| `_last_regime` | `RegimeState | None` | No — single value; updated on every `detect()` |
| `_per_coin_regimes` | `dict[str, RegimeState]` | Yes |
| `_confirmed_regimes` | `dict[str, RegimeState]` | Yes — hysteresis confirmed state |
| `_pending_regime` | `dict[str, tuple[MarketRegime, int]]` | Yes — hysteresis candidate + count |

## Settings consumed

From `Settings.regime` (loaded by `_build_regime` in `src/config/settings.py`):

- `primary_symbol` (default `BTCUSDT`)
- `trending_adx_threshold` (configured: 25)
- `volatile_atr_percentile` (configured: 150 — unreachable)
- `ranging_adx_threshold` (configured: 20)
- `ranging_choppiness_threshold` (configured: 60)
- `dead_adx_threshold` (configured: 15)
- `dead_volume_ratio` (configured: 0.5)
- `hysteresis_count` (configured: 2)
