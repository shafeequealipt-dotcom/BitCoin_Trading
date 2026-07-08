# Phase 6 — Signal Classifier (SIG_CLASSIFY)

The classifier produces one of `strong_buy / buy / neutral / sell / strong_sell` per coin per cycle.

## Pipeline

Per-coin per-cycle, `SignalWorker` (signal_worker.py:119) delegates to `SignalGenerator.generate_signal(symbol)` in `src/intelligence/signals/signal_generator.py:60-262`.

`generate_signal` (`:60-262`):
1. `_gather_inputs(symbol)` (`:97-127`) — collects sentiment, F&G, funding rate, OI change, volume-surge ratio.
2. `_evaluate_signal(...)` (`:393-545`) — produces the categorical label.
3. `ConfidenceCalculator.calculate(components)` (`:139-181`) — produces the 0-1 confidence.
4. `_apply_confidence_floor(signal_type, confidence)` (`:194-224`) — non-destructive downgrade.
5. `_save_signal(signal)` (`:251`) — persists to `signals` table.

## The Classifier Math (`_evaluate_signal`)

Step 1 — clamp four component scores to `[-1, +1]`:
```python
s_sentiment = clamp(float(sentiment), -1.0, 1.0)                                # :454
s_fg        = clamp((50.0 - fear_greed) / cfg.fg_normalize_range, -1.0, 1.0)    # :455-457 — CONTRARIAN
s_funding   = clamp(-funding_rate / cfg.funding_normalize, -1.0, 1.0)           # :458-460 — INVERTED
s_oi        = clamp(oi_change / cfg.oi_normalize_pct, -1.0, 1.0)                # :461
```

Where (settings.py:3045-3049):
- `fg_normalize_range = 30.0` (so fg=20 → +1.0, fg=80 → −1.0)
- `funding_normalize = 0.005` (so funding=+0.005 → −1.0 contribution, funding=−0.005 → +1.0)
- `oi_normalize_pct = 5.0` (so OI change ±5% saturates)

Step 2 — activity gating:
```python
if not self._sentiment_consumption_enabled:                # :474-477
    active["sentiment"] = False
else:
    active["sentiment"] = abs(s_sentiment) >= cfg.sentiment_min_active   # 0.05
active["fg"]      = abs(s_fg)      >= cfg.fg_min_active                  # 0.10
active["funding"] = abs(s_funding) >= cfg.funding_min_active             # 0.10
active["oi"]      = abs(s_oi)      >= cfg.oi_min_active                  # 0.10
```

Step 3 — weighted sum over active set only:
```python
weights = {"sentiment": 0.40, "fg": 0.25, "funding": 0.20, "oi": 0.15}   # settings.py:3041-3044
active_weight_sum = sum(weights[c] for c in active if active[c])
if active_weight_sum <= 0.0:
    direction_score = 0.0; label = NEUTRAL
else:
    direction_score = sum(weights[c] * scores[c] for c in active if active[c]) / active_weight_sum
```

Step 4 — threshold mapping:
```python
strong_threshold = 0.55       # settings.py:3045
buy_threshold    = 0.18       # settings.py:3046 — was 0.25, LOWERED to "match BUY-leaning observed data"

if direction_score >= strong_threshold:     label = STRONG_BUY
elif direction_score >= buy_threshold:      label = BUY
elif direction_score <= -strong_threshold:  label = STRONG_SELL
elif direction_score <= -buy_threshold:     label = SELL
else:                                       label = NEUTRAL
```

Validator at settings.py:3063-3067 enforces `0 < buy_threshold < strong_threshold <= 1` — so the asymmetric `buy=0.18` is structural; only constrained to be less than `strong=0.55`.

## Confidence Floor Downgrade

Code: signal_generator.py:194-224. Applied AFTER label assignment.

```python
CONFIDENCE_THRESHOLDS = {"strong_buy": 0.60, "buy": 0.40, "sell": 0.40, "strong_sell": 0.60}  # signal_models.py:44-50

if label in (STRONG_BUY, STRONG_SELL) and confidence < 0.60:
    label → BUY or SELL                                 # downgrade
    if confidence < 0.40:
        label → NEUTRAL                                 # force neutral
elif label in (BUY, SELL) and confidence < 0.40:
    label → NEUTRAL
```

**Non-destructive (Phase 4B / CALL_B-Framing-Fix, 2026-05-06)**: the ORIGINAL label is preserved in `components.original_signal_type` (signal_generator.py:242). Also `confidence_floor_failed`, `confidence_below_strong`, `confidence_below_buy` boolean flags (signal_generator.py:243-245).

## Confidence Calculation

Code: src/intelligence/signals/confidence.py. Formula at `:65-68`:
```python
confidence = agreement*0.40 + magnitude*0.25 + volume*0.20 + freshness*0.15
```

- `agreement` (confidence.py:75-90) — fraction of components agreeing on dominant direction. Direction-counting threshold ±0.05. If `len(scores) < 2`: default 0.5.
- `magnitude` — `mean(abs(s) for s in scores)`.
- `volume` — bucketed from `components["volume_surge_ratio"]` (`:131-137`): `<0.5→0.3`, `<1.5→0.5`, `<2.5→0.7`, `>=2.5→1.0`. Missing → 0.5.
- `freshness` — bucketed from `components["data_age_hours"]` (`:146-154`): `<=1→1.0`, `<=6→0.8`, `<=12→0.6`, `<=24→0.4`, else 0.3. Missing → default 24h.

## Critical Discrepancies (already noted in Phase 1, recapped here)

### 1. Two competing normaliser ladders

Confidence path normalises:
- F&G by `(fg-50)/50.0` (signal_generator.py:141)
- funding by `*100` (`:142`)
- OI by `/20.0` (`:143`)

Classifier path normalises (settings-driven):
- F&G by `/30.0`
- funding by `/0.005` (= 1/200)
- OI by `/5.0`

The same raw funding rate `0.0025` becomes:
- For confidence: `0.0025 * 100 = 0.25` (small positive contribution)
- For classification: `-0.0025 / 0.005 = -0.5` (moderate bearish contribution)

The two paths see fundamentally different magnitudes of the same input. Confidence saturates funding at ±0.01; classifier saturates at ±0.005 — a 2× difference.

### 2. Dead constants in signal_models.py

Defined but unused by the post-Phase-1 classifier:
- `SENTIMENT_THRESHOLDS` (signal_models.py:7-14): strong_buy=0.5, buy=0.2, ...
- `FEAR_GREED_THRESHOLDS` (`:17-23`): extreme_fear=(0,20), fear=(21,40), ...
- `FUNDING_RATE_THRESHOLDS` (`:26-33`): extreme_positive=0.01, ...
- `OI_CHANGE_THRESHOLDS` (`:36-41`): significant=±10.0, moderate=±5.0
- `SOURCE_WEIGHTS` (`:53-60`): news=0.25, reddit=0.20, ...

Only `CONFIDENCE_THRESHOLDS` is consumed (signal_generator.py:19-21 import). The legacy 9-rule cascade was replaced by the weighted classifier; the old dicts remained in source as dead code.

### 3. `_sentiment_consumption_enabled` default mismatch

- signal_generator.py:76 defaults the local attribute to `True`.
- settings.py:1825 defaults the config field to `False`.

If `SignalGenerator()` is constructed without settings argument: sentiment is ENABLED. If constructed with default settings: sentiment is DISABLED. Effective behaviour depends on construction path.

### 4. Worker-level `_input_active` divergence

`signal_worker.py:138-145` checks `abs(x) > 0.0` to count an input as "active" for `SIG_INPUT_AVAILABILITY` summary. The classifier uses `abs(x) >= cfg.*_min_active` (0.05/0.10/0.10/0.10). The worker counts more inputs active than the classifier actually consumes.

### 5. Asymmetric BUY threshold (the deliberate one)

`buy_threshold = 0.18` vs `strong_threshold = 0.55`. The dataclass comment (settings.py:3034-3038) says: "buy_threshold 0.25 → 0.18 to match the typical BUY-leaning direction_score observed in forensic data."

This is a known, intentional asymmetry. The classifier labels a `direction_score = 0.20` as `BUY` (since 0.20 ≥ 0.18). For SELL to fire, `direction_score ≤ -0.18`. The thresholds compare absolute values but the inputs are skewed BUY-positive in observed data, so BUY fires earlier in absolute terms than SELL would in mirror conditions.

## Has The Label Calibration Ever Been Validated Against Outcomes?

Per Phase 3 Claim 4 verification:
- `strong_buy` label: 54.9% loss rate, +$199 net on 113 trades (50% of sample).
- `buy` label: 44.4% loss rate, +$4 net on 90 trades.
- `neutral` label: 55.6% loss rate, -$27 net on 18 trades.

The `strong_buy` label has 10.5 percentage points HIGHER loss rate than `buy`. This is the inverse of what the label hierarchy implies (strong_buy should be more confident, hence higher win rate). The label is calibrated against historical `direction_score` distribution (BUY-leaning) but NOT against trade outcomes. **There is no automated calibration loop** — no code re-tunes `strong_threshold` or `buy_threshold` based on observed PnL.

The Optimizer (src/strategies/optimizer.py) adjusts per-STRATEGY ensemble weights but not signal-classifier thresholds. The classifier thresholds are static constants in settings.py.

## Downstream Consumers

Per Phase 1 strategist map, the SIG_CLASSIFY label STRING does NOT reach Claude in the CALL_A prompt. The strategist surfaces only `pkg.signals.confidence` and `pkg.signals.direction` (strategist.py:2168-2171). The label name is captured by the prompt only when `[stage2].enable_full_layer_block=True` (default False), which then renders the live `signal_worker.get_signal(pkg.symbol)` and includes `type=…`.

Verified consumers of the label string:
- `alerts/formatter.py:32-38` — display emoji/text map.
- `mcp/tools/analysis_tools.py:139, 160` — exposes via MCP.
- `mcp/tools/memory_tools.py:98`.

The label is a **persistence and observability artifact**, not a runtime decision input to the trade pipeline.

## Persistence Gap (also noted in Phase 3 summary)

In the analysis window (2026-05-20+), the `signals` DB table contains only:
- `signal_type IN ('buy', 'neutral')`

Logs for the same window show 1,522 `strong_buy` + 2,219 `buy` + 359 `neutral` SIG_CLASSIFY events. The DB persistence path is dropping ~6,200 of ~10,500 log-emitted events. The cause is not in scope for this Phase 6 but is documented for Phase 8 synthesis as a data integrity issue.

Older `signals` rows show the full label set (`signals` table has 2,041 strong_buy + 8,370 buy + 1,656 sell + 2,095 strong_sell + 108,350 neutral total — implying earlier persistence worked).

## Summary

The classifier is a deterministic weighted-sum-with-thresholds. It is:
- **Tunable** via `[signal_generator.multi_source]` for weights/thresholds/normalisers.
- **Regime-blind** — no regime references anywhere in the classifier code.
- **Asymmetric** in its `buy_threshold` (deliberately lowered to 0.18 from 0.25).
- **Not calibrated against outcomes** — thresholds are static.
- **Discrepant** with the confidence calculator on input normalisation (two different ladders).
- **Partially persisted** in production (recent rows missing sell / strong_buy / strong_sell labels in DB).
- **Not directly consumed by the strategist's CALL_A** (only the per-coin confidence + direction reach Claude).
