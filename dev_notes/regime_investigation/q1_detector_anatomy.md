# Q1 Step 1.2 — RegimeDetector Anatomy

Read end-to-end at `src/strategies/regime.py`. The class is 233 lines and contains one constructor, one async public method, three small read-only methods, and an internal hysteresis state machine.

## Lifecycle

Constructor at `regime.py:30-44` takes three injected dependencies:

- `settings: Settings`
- `ta_engine: TAEngine`
- `market_repo: MarketRepository`

Wired in `src/workers/regime_worker.py:44` and `__init__.py`.

## Public methods

1. **`async detect(symbol: str | None = None)`** at `regime.py:78-223`. The full pipeline:
   - Step 1 (lines 87-90): Resolve symbol; fetch 200 H1 klines via `market_repo.get_klines(symbol, TimeFrame.H1.value, 200)`.
   - Step 2 (lines 91-113): If `len(klines) < 50`, return RANGING fallback with `confidence=0.3`. Critically the fallback ALSO updates `self._last_regime`, so subsequent `get_last_regime()` calls do not retrigger detection.
   - Step 3 (lines 115-122): Call `await ta_engine.analyze(candles=klines)` and extract `adx`, `plus_di`, `minus_di`, `choppiness`, `atr`, `volume_ratio`, `natr` from the returned nested dict.
   - Step 4 (line 126): `atr_percentile = natr * 100`. Capped near 100 in normal markets.
   - Step 5 (lines 128-156): Branch on the values to one of 5 regimes. Falls through to `RANGING` with `confidence=0.4` on no match.
   - Step 6 (lines 158-169): Build `RegimeState` with `regime`, `confidence`, `adx`, `atr_percentile`, `choppiness`, `volume_ratio`, `trend_direction`, `active_strategy_categories`.
   - Step 7 (line 171): Emit structured `REGIME |` log line.
   - Step 8 (lines 176-223): Apply per-symbol hysteresis. If no confirmed state yet → confirm immediately. If same as confirmed → update in-place. If different → require N consecutive readings of the new label before confirming.

2. **`async detect_per_coin(symbols: list[str])`** at `regime.py:225-233`. Loops over `symbols` calling `detect(symbol)` for each. Catches per-symbol exception, logs a warning, continues. Returns `dict[str, RegimeState]` populated by successful detections.

## Read-only methods

3. **`get_coin_regime(symbol)`** at `regime.py:46-48`. Zero-cost cache lookup in `_per_coin_regimes`.
4. **`is_ready()`** at `regime.py:50-59`. Returns `True` iff at least one per-coin entry exists. Cold-start gate.
5. **`get_last_regime()`** at `regime.py:61-76`. Returns the most-recent global `RegimeState`. Used by strategist for prompt construction at faster cadence than the worker.

## Shared state

The class holds four per-instance dicts/scalars:

- `_last_regime` — single most-recent global RegimeState.
- `_per_coin_regimes` — per-symbol cache.
- `_confirmed_regimes` — per-symbol hysteresis-confirmed state.
- `_pending_regime` — per-symbol candidate + count.

All four are per-symbol-keyed except `_last_regime`. The hysteresis decision is symbol-independent (each symbol has its own pending count).

## Classification branches (verbatim from lines 133-156)

```
adx > trending_adx_threshold AND plus_di > minus_di AND choppiness < 45  → TRENDING_UP, conf = min(adx/50, 1.0)
adx > trending_adx_threshold AND minus_di > plus_di AND choppiness < 45  → TRENDING_DOWN, conf = min(adx/50, 1.0)
atr_percentile > volatile_atr_percentile OR volume_ratio > 2.0           → VOLATILE, conf = min(atr_percentile/200, 1.0)
adx < ranging_adx_threshold AND choppiness > ranging_choppiness_threshold → RANGING, conf = min(choppiness/80, 1.0)
adx < dead_adx_threshold AND volume_ratio < dead_volume_ratio AND atr_percentile < 50 → DEAD, conf = 0.8
ELSE                                                                       → RANGING, conf = 0.4
```

With the configured thresholds (trending=25, volatile=150, ranging_adx=20, ranging_chop=60, dead_adx=15, dead_vol=0.5), the only ranging condition that hits the explicit branch is `(adx < 20 AND chop > 60)`. The `volatile_atr_percentile = 150` is unreachable from the NATR-derived `atr_percentile`, so VOLATILE is gated only by `volume_ratio > 2.0`. Everything between the trending criteria and the strict-ranging criteria falls into the `else` fallback as RANGING with `confidence = 0.4`.

## Hysteresis state machine (lines 176-223)

For each per-symbol classification:

- If no confirmed regime yet → confirm immediately, clear pending, set last.
- Else if new regime == confirmed regime → update confirmed in-place with new metrics, clear pending, set last.
- Else if new regime != confirmed regime:
  - Increment pending count (if same candidate as before) or reset to 1 (new candidate).
  - If pending count >= `hysteresis_count` (configured 2) → confirm new regime, emit `REGIME_CHG` warning, set last to the new state.
  - Else → return previously confirmed state, emit `REGIME_PENDING` info, set last to the confirmed state.

Effect: a single anomalous reading does not cause a regime flip in any consumer; the new label must persist for 2 consecutive ticks before consumers see it.

## Important observation about confidence

The `confidence` field varies by branch:

- TRENDING_UP/DOWN: `min(adx/50, 1.0)`. At ADX 25 = 0.50; at ADX 50 = 1.00.
- VOLATILE: `min(atr_percentile/200, 1.0)`. Typically below 0.5 since atr_percentile maxes near 100.
- RANGING (strict): `min(choppiness/80, 1.0)`. At chop 60 = 0.75; at chop 80 = 1.00.
- DEAD: hardcoded 0.8.
- ELSE → RANGING: hardcoded **0.4**.

This means: every `confidence = 0.4` regime emission is a fallback classification. Operators (and Phase 2) can use `conf=0.40` as a signature of the `else` branch.
