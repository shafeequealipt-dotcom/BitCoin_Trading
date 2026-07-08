# Phase 0.F — XRAY State Inputs (for State Labeler)

## Source: `structure_worker._cache.get(symbol)` → StructuralAnalysis

Defined at `src/analysis/structure/models/structure_types.py:488-600`. Populated each cycle by `StructureWorker.tick()` (`src/workers/structure_worker.py`).

## Fields the State Labeler consumes

### Core classification

| Field | Type | Source line | Use in labeler |
|---|---|---|---|
| `setup_type` | `SetupType` enum | `structure_types.py:13-48` | Match against label triggers |
| `setup_type_confidence` | float (0..1) | `structure_types.py:572` | Cleanness component of interestingness |
| `setup_score` | int (0..100) | `structure_types.py:512` | Structural quality component |
| `suggested_direction` | str ("long"/"short"/"") | `structure_types.py:514` | Label trigger filtering |
| `trade_direction` | str | `structure_types.py:589` | Counter-trade label trigger; opposite of suggested for `*_COUNTER` |

### SetupType enum values (with COUNTER variants from xray-counter fix)

```
NONE
BULLISH_FVG_OB                     BEARISH_FVG_OB
BULLISH_FVG_OB_COUNTER             BEARISH_FVG_OB_COUNTER
BULLISH_STRUCTURAL_BREAK           BEARISH_STRUCTURAL_BREAK
BULLISH_LIQUIDITY_SWEEP            BEARISH_LIQUIDITY_SWEEP
BULLISH_RANGE_BREAKOUT             BEARISH_RANGE_BREAKOUT
```

### FVG/OB inventory (for label classification)

| Field | Type | Source line |
|---|---|---|
| `nearest_fvg` | `FairValueGap \| None` | `structure_types.py:518` |
| `nearest_fvg_counter` | `FairValueGap \| None` | `structure_types.py:525` |
| `nearest_ob` | `OrderBlock \| None` | `structure_types.py:529` |
| `nearest_ob_counter` | `OrderBlock \| None` | `structure_types.py:534` |

`FairValueGap`: `direction, top, bottom, midpoint, filled, partially_filled, fill_percentage, gap_size_pct, displacement_strength, displacement_ratio`.
`OrderBlock`: `direction, high, low, midpoint, retests, fresh, displacement_strength, has_fvg, broke_structure, strength_score`.

### XRAY_NONE_REASON enrichment (when setup_type=NONE)

Used by `OB_MITIGATED_FVG_ONLY_*` and `MANIPULATION_WINDOW` labels. Emitted by `engine.diagnose_none(result)` at `structure_worker.py:170-206`. Fields: `closest_type, missed_by, weakest_input, mtf_score_01, smc_01, direction, structure, in_direction_fvg, in_direction_ob, counter_direction_fvg, counter_direction_ob, last_bos_significance, last_bos_age_bars, recent_sweep, range_compression, atr_pct_h1, window_pct_fvg, window_pct_ob, first_failure_branch`.

### Volatility

| Field | Type | Source line | Label use |
|---|---|---|---|
| `atr_pct_h1` | float | `structure_types.py:599` | Extremity component; risk_envelope sizing |
| `range_compression` | bool | (in NONE_REASON or MTFConfluence) | `BREAKOUT_PENDING` trigger |

### Multi-Timeframe Confluence

`MTFConfluence` at `structure_types.py:391-411`:
- `timeframe_analyses: dict` (per-TF: bias, at_level, trigger)
- `direction_alignment: str` ("fully_aligned"|"mostly_aligned"|"mixed"|"conflicting")
- `aligned_direction: str | None`
- `score: int (0-10)`
- `quality: str` ("maximum"|"good"|"weak"|"none")

Convenience fields on StructuralAnalysis: `mtf_confluence_score, confluence_quality, total_confluence_factors` (lines 556-559).

### Structural levels (for risk envelope)

`structural_placement` field carries:
- `structural_sl: float` (invalidation level)
- `rr_long: float`, `rr_short: float` (Phase 4 direction-aware)
- `rr_ratio: float` (legacy fallback)
- `rr_best, rr_best_direction, rr_quality`

### Session context (manipulation flag)

`session_context: SessionContext | None` at `structure_types.py:562`:
- `current_session: str` ("asian"/"london"/"new_york"/"late_ny")
- `session_phase: str` ("early"/"mid"/"late")
- `asian_range_high, asian_range_low, asian_range_broken: str | None`
- `manipulation_likely: bool`

`manipulation_likely=True` when (per `session_timing.py:103-108`):
- `session == "london"` AND `phase == "early"` AND `asian_range_broken` AND `not last_bos`

## Per-coin regime (RegimeState)

`regime_worker._per_coin_regimes[sym]` populated by `RegimeDetector.detect_per_coin(symbols)` at `src/strategies/regime.py:225-233`.

`RegimeState` dataclass at `src/strategies/models/regime_types.py:41-54`:
- `regime: MarketRegime` (TRENDING_UP/TRENDING_DOWN/RANGING/VOLATILE/DEAD)
- `confidence: float (0..1)`
- `adx: float (0..100)`
- `atr_percentile: float (0..100)`
- `choppiness: float (~0..100)`
- `volume_ratio: float`
- `trend_direction: int (+1/-1/0)`
- `active_strategy_categories: list[str]`

Restored from `coin_regime_history` table on first tick.

## Signal worker output

`signal_worker._cache[sym]`. `Signal` dataclass:
- `signal_type: SignalType` ({STRONG_BUY, BUY, NEUTRAL, SELL, STRONG_SELL})
- `confidence: float (0..1)`
- `components: dict` with keys: `overall_sentiment, fear_greed, funding_rate, oi_change_pct, news_count, reddit_count, data_age_hours, volume_surge_ratio`

Confidence thresholds (signal_models.py:44-50):
- STRONG_*: conf >= 0.60
- BUY/SELL: conf >= 0.40
- below → downgraded to NEUTRAL

## Altdata

| Method | Returns | Sign convention |
|---|---|---|
| `altdata_worker.get_funding(sym)` | float | positive = longs pay shorts |
| `altdata_worker.get_oi(sym)` | dict (incl. `change_24h_pct`) | — |
| `altdata_worker.get_fg()` / `fear_greed.get_latest()` | int 0..100 | — |

## Sentiment aggregator

`aggregate_for_symbol(symbol, hours=24)` returns:
```python
{
    "overall_score": float (-1..+1),
    "level": SentimentLevel enum,
    "news_score": float,
    "reddit_score": float,
    "news_count": int,
    "reddit_count": int,
    "fear_greed": int (0..100),
    "momentum": float,
}
```

Component weights (aggregator.py:19-22): NEWS 0.35, REDDIT 0.30, F&G 0.20 (amplified to 0.40-0.60 when extreme), MOMENTUM 0.15.

Reddit currently inactive (no `settings.reddit.client_id`) — degraded mode logged once at init.

## Position-in-range (for range_fade labels)

Need to compute from price + structural high/low. `structural_placement` may carry this; fallback compute as `(price - range_low) / (range_high - range_low)`. To verify in Phase 3.

## H4/D1 bias (Gap from audit)

Currently NOT computed per coin. Required for `MTFBiasBlock`. Phase 5 adds lightweight EMA-cross (50/200) over H4 and D1 candles from existing kline cache. Plan trade-off: full TA on H4/D1 too expensive; EMA cross is sufficient for directional bias.

## What's NOT available, must NOT block labeler

- Per-strategy performance per coin per cycle (cached in registry, not per-coin per-cycle)
- Sentiment SOURCE breakdown active flag per source (computable from non-zero counts)
- Hysteresis countdown for pending regime changes (would require new field on RegimeState)
- Component-level signal staleness (current `data_age_hours` is aggregate)

Labeler reads what's available, defaults missing fields to safe values. **Labeler must never raise** — it always returns a valid label list (possibly just `[NO_TRADEABLE_STATE]`).
