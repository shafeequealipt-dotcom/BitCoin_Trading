# C2 — SignalWorker (Forensic Data)

CAPTURE TIMESTAMP (UTC): 2026-04-27 22:58:35
Log file: `/home/inshadaliqbal786/trading-intelligence-mcp/data/logs/workers.log`
DB snapshot: `_trading_db_snapshot.db` (mtime 22:56)

---

## C.2.1 — Signal generation pipeline

**Worker file:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/signal_worker.py` — 178 lines (verified).
**Generator file:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/intelligence/signals/signal_generator.py` (`SignalGenerator.generate_signal`, lines 70-214).

### Inputs (where each is read from)

Inside `SignalGenerator.generate_signal()` (`signal_generator.py:84-99`):

```python
sentiment = await self._aggregator.aggregate_for_symbol(symbol)
overall_score = sentiment.get("overall_score", 0.0)
fg = await self._altdata_repo.get_latest_fear_greed()
fg_value = fg.value if fg else 50
fr = await self._altdata_repo.get_latest_funding_rate(symbol)
funding_rate = fr.funding_rate if fr else 0.0
oi = await self._altdata_repo.get_latest_open_interest(symbol)
oi_change = oi.get("change_24h_pct", 0.0) if oi and isinstance(oi, dict) else 0.0
```

| Input | Source |
|-------|--------|
| `sentiment.overall_score` | `SentimentAggregator.aggregate_for_symbol(symbol)` (per-symbol weighted news/reddit/F&G mix) |
| `fg_value` | `AltDataRepository.get_latest_fear_greed()` → `fear_greed_index` table |
| `funding_rate` | `AltDataRepository.get_latest_funding_rate(symbol)` → `funding_rates` table |
| `oi_change` | `AltDataRepository.get_latest_open_interest(symbol)` → `open_interest` table |
| `volume_surge_ratio` | `_compute_volume_surge_ratio(symbol)` reads M5 klines from `MarketRepository.get_klines(symbol, "5", 21)` (`signal_generator.py:284-312`) |
| `data_age_hours` | `_compute_data_age_hours(fg, fr, oi)` — oldest of fg/fr/oi timestamps (`signal_generator.py:216-282`) |

**No structure/X-RAY input** — `SignalGenerator` does NOT consume `StructureCache`. The "TA + sentiment + funding + structure" formulation in the prompt is partially incorrect with respect to the live wiring: TA and structure are not direct inputs to the signal classifier. SignalWorker does call `aggregator.aggregate_for_symbol()` (sentiment side-effect) and then `signal_generator.generate_signal()`.

### Aggregation formula (verbatim)

`signal_generator._evaluate_signal()` at `signal_generator.py:349-490`:

```python
# 1. Compute four component scores in [-1, +1].
s_sentiment = clamp(float(sentiment), -1.0, 1.0)
s_fg = clamp((50.0 - float(fear_greed)) / cfg.fg_normalize_range, -1.0, 1.0,)
s_funding = clamp(-float(funding_rate) / cfg.funding_normalize, -1.0, 1.0,)
s_oi = clamp(float(oi_change) / cfg.oi_normalize_pct, -1.0, 1.0)

# 2. Mark each component active iff abs(score) >= its threshold.
active = {
    "sentiment": abs(s_sentiment) >= cfg.sentiment_min_active,
    "fg":        abs(s_fg)        >= cfg.fg_min_active,
    "funding":   abs(s_funding)   >= cfg.funding_min_active,
    "oi":        abs(s_oi)        >= cfg.oi_min_active,
}
weights = {
    "sentiment": cfg.sentiment_weight,
    "fg":        cfg.fg_weight,
    "funding":   cfg.funding_weight,
    "oi":        cfg.oi_weight,
}

# 3. Weighted sum over active components, renormalised.
active_weight_sum = sum(weights[c] for c in active if active[c])
if active_weight_sum <= 0.0:
    direction_score = 0.0
    signal_type = SignalType.NEUTRAL
    reason = (...)
else:
    direction_score = sum(
        weights[c] * scores[c] for c in active if active[c]
    ) / active_weight_sum
    if direction_score >= cfg.strong_threshold:    signal_type = SignalType.STRONG_BUY
    elif direction_score >= cfg.buy_threshold:     signal_type = SignalType.BUY
    elif direction_score <= -cfg.strong_threshold: signal_type = SignalType.STRONG_SELL
    elif direction_score <= -cfg.buy_threshold:    signal_type = SignalType.SELL
    else:                                          signal_type = SignalType.NEUTRAL
```

Default threshold/weight values (`SignalGeneratorMultiSourceSettings`, `settings.py:1645-1657`):

```
sentiment_min_active = 0.05
fg_min_active        = 0.10
funding_min_active   = 0.20
oi_min_active        = 0.20
sentiment_weight     = 0.40
fg_weight            = 0.25
funding_weight       = 0.20
oi_weight            = 0.15
strong_threshold     = 0.55
buy_threshold        = 0.25
fg_normalize_range   = 30.0
funding_normalize    = 0.005
oi_normalize_pct     = 5.0
```

Confidence is computed by `ConfidenceCalculator.calculate(components)` at `confidence.py:19-71`:

```python
confidence = (
    agreement * 0.40
    + magnitude * 0.25
    + volume * 0.20
    + freshness * 0.15
)
```

with `_freshness_factor` returning 0.3 when `age_hours > 24`, 0.4 when `<= 24`, 0.6 when `<= 12`, 0.8 when `<= 6`, 1.0 when `<= 1`. `_volume_factor` returns 0.3 when `volume_surge_ratio < 0.5`, 0.5 if `< 1.5`, 0.7 if `< 2.5`, 1.0 if `>= 2.5`.

---

## C.2.2 — Phase-29 confidence gate

**Location:** `signal_generator.py:158-189`. Verbatim:

```python
# Phase 29 (Y-28): enforce CONFIDENCE_THRESHOLDS as a hard gate.
_orig_type = signal_type
try:
    t_strong = float(CONFIDENCE_THRESHOLDS.get("strong_buy", 0.60))
    t_buy = float(CONFIDENCE_THRESHOLDS.get("buy", 0.40))
except Exception:
    t_strong, t_buy = 0.60, 0.40
if signal_type in (SignalType.STRONG_BUY, SignalType.STRONG_SELL):
    if confidence < t_strong:
        if confidence >= t_buy:
            signal_type = (
                SignalType.BUY if signal_type == SignalType.STRONG_BUY
                else SignalType.SELL
            )
        else:
            signal_type = SignalType.NEUTRAL
elif signal_type in (SignalType.BUY, SignalType.SELL):
    if confidence < t_buy:
        signal_type = SignalType.NEUTRAL
if signal_type != _orig_type:
    log.info(
        f"SIG_DOWNGRADE | sym={symbol} from={_orig_type.value} "
        f"to={signal_type.value} conf={confidence:.2f} "
        f"strong_min={t_strong:.2f} buy_min={t_buy:.2f} | {ctx()}"
    )
```

`CONFIDENCE_THRESHOLDS` (`signal_models.py:44-50`):

```
strong_buy  = 0.6
buy         = 0.4
neutral     = 0.0
sell        = 0.4
strong_sell = 0.6
```

**Why ~100% demote to NEUTRAL today:** the upstream classifier itself returns NEUTRAL because only the `fg` component is "active" (`SIG_GEN_INPUT … sent_active=False fg_active=True fund_active=False oi_active=False` — see live trace below). The single active component produces `direction_score = +0.100`, which is **below** `buy_threshold = 0.25`, so the classifier emits `NEUTRAL` directly — the Phase-29 gate then has nothing to demote (`_orig_type == NEUTRAL`). No `SIG_DOWNGRADE` events appear in the recent log because the original classification is already NEUTRAL.

```
$ grep -c "SIG_DOWNGRADE" workers.log    # 0 in the captured window
```

So the 100% NEUTRAL outcome is driven by the **classifier's** active-component gate (sentiment/funding/OI all under their `min_active` thresholds), not by the confidence gate.

**Active vs inactive (live BTCUSDT trace, `SIG_GEN_INPUT @ 22:26:00`):**

```
sent_active=False  fg_active=True  fund_active=False  oi_active=False
sentiment=-0.012   fg=47           funding=+0.00003   oi_change=+0.00
```

---

## C.2.3 — `_signal_cache` structure

**Location:** `signal_worker.py:67`:

```python
self._signal_cache: dict[str, Signal] = {}
```

**Key:** symbol string (e.g. `"BTCUSDT"`).
**Value:** `Signal` dataclass (`src/core/types.py`). `Signal` is constructed at `signal_generator.py:190-205`:

```python
signal = Signal(
    symbol=symbol,
    signal_type=signal_type,
    confidence=confidence,
    source="intelligence_aggregator",
    components={
        "overall_sentiment": overall_score,
        "fear_greed": fg_value,
        "funding_rate": funding_rate,
        "oi_change_pct": oi_change,
        "news_count": sentiment.get("news_count", 0),
        "reddit_count": sentiment.get("reddit_count", 0),
    },
    reasoning=reasoning,
    created_at=now_utc(),
)
```

**Live snapshot — 5 representative entries (last `SIG_GEN` events @ 22:26):**

```
sym=BTCUSDT   type=neutral conf=0.20 vol_surge=0.03 age_h=22.43
   rsn='Multi-source dir=+0.100 active=[fg] (s=-0.01, fg=+0.10, fund=-0.01, oi=+0.00)'

sym=ETHUSDT   type=neutral conf=0.20 vol_surge=0.06 age_h=22.43
   rsn='Multi-source dir=+0.100 active=[fg] (s=-0.01, fg=+0.10, fund=-0.01, oi=+0.00)'

sym=SOLUSDT   type=neutral conf=0.20 vol_surge=0.04 age_h=22.43
   rsn='Multi-source dir=+0.100 active=[fg] (s=-0.01, fg=+0.10, fund=-0.01, oi=+0.00)'

sym=BNBUSDT   type=neutral conf=0.20 vol_surge=0.05 age_h=22.43
   rsn='Multi-source dir=+0.100 active=[fg] (s=-0.01, fg=+0.10, fund=-0.01, oi=+0.00)'

sym=XRPUSDT   type=neutral conf=0.20 vol_surge=0.04 age_h=22.43
   rsn='Multi-source dir=+0.100 active=[fg] (s=-0.01, fg=+0.10, fund=-0.01, oi=+0.00)'
```

(Anonymisation note: these values are public market data — no sensitive content.)

The cache is read by ScannerWorker via `signal_worker.get_signal(coin)` (`signal_worker.py:169-177`):

```python
def get_signal(self, coin: str) -> Signal | None:
    return self._signal_cache.get(coin)
```

---

## C.2.4 — Why the BTCUSDT signal is `neutral` / `0.20` / `+0.100`

Live inputs RIGHT NOW for BTCUSDT (most recent cycle 22:26:00):

`SIG_GEN_INPUT @ 22:26:00.054` (verbatim):

```
sym=BTCUSDT
sent_active=False  fg_active=True  fund_active=False  oi_active=False
sentiment=-0.012   fg=47           funding=+0.00003   oi_change=+0.00
```

`SIG_CLASSIFY @ 22:26:00.054` (verbatim):

```
sym=BTCUSDT components=[s:-0.01,fg:+0.10,fund:-0.01,oi:+0.00]
            active=[s:False,fg:True,fund:False,oi:False]
            direction_score=+0.100  type=neutral
```

`SIG_GEN @ 22:26:00.062` (verbatim):

```
sym=BTCUSDT type=neutral conf=0.20 vol_surge=0.03 age_h=22.43
rsn='Multi-source dir=+0.100 active=[fg] (s=-0.01, fg=+0.10, fund=-0.01, oi=+0.00)'
```

### Trace — direction_score

Component computations (per `_evaluate_signal`, with live values):

```
s_sentiment = clamp(-0.012, -1, +1)               = -0.012  → abs(-0.012) < 0.05 → INACTIVE
s_fg        = clamp((50 - 47) / 30.0, -1, +1)     = +0.100  → abs(+0.100) >= 0.10 → ACTIVE
s_funding   = clamp(-0.00003 / 0.005, -1, +1)     = -0.006  → abs(-0.006) < 0.20 → INACTIVE
s_oi        = clamp(+0.00 / 5.0, -1, +1)          = +0.000  → abs(+0.000) < 0.20 → INACTIVE
```

Only `fg` is active. `active_weight_sum = fg_weight = 0.25`.

```
direction_score = (0.25 * +0.100) / 0.25 = +0.100
```

`+0.100 < buy_threshold (0.25)` → `signal_type = NEUTRAL`. The classifier already returns NEUTRAL.

### Trace — confidence = 0.20

`ConfidenceCalculator.calculate` consumes:

```
components = {
    "news_sentiment": sentiment.news_score,
    "reddit_sentiment": sentiment.reddit_score,
    "fear_greed": (fg-50)/50  = (47-50)/50 = -0.06,
    "funding_rate": clamp(funding*100, -1, 1) = clamp(0.003, -1, 1) = +0.003,
    "open_interest": clamp(oi/20, -1, 1) = 0.0,
    "data_age_hours": 22.43,
    "volume_surge_ratio": 0.03,
}
```

The first five non-None scalars feed `agreement` and `magnitude`:

- `_agreement_factor`: counting `>0.05` and `<-0.05` of {-0.012, 0.0 [reddit], -0.06, +0.003, 0.0} → positives=0, negatives=1 (only `fg`=-0.06 < -0.05; sentiment -0.012 > -0.05; funding +0.003 < 0.05). `dominant=1, total=5` → 0.2.
- `_magnitude_factor`: mean(|...|) = (0.012 + 0 + 0.06 + 0.003 + 0)/5 = 0.015.
- `_volume_factor`: vol_surge=0.03 → returns 0.3 (the `< 0.5` bucket).
- `_freshness_factor`: age=22.43 h → returns 0.4 (the `<= 24` bucket).

```
confidence = 0.40*0.2 + 0.25*0.015 + 0.20*0.3 + 0.15*0.4
           = 0.080  + 0.00375    + 0.060     + 0.060
           = 0.2038  ≈ 0.20  ✓
```

(The published value of `0.20` matches.)

### Identifying the EXACT input causing the demotion

The signal type is NEUTRAL because **3 of 4 components are inactive** (sentiment, funding, OI — all near zero) and the only active component (`fg = +0.100`) produces a direction_score (0.100) below the `buy_threshold` (0.25). The single largest contributor to keeping the signal NEUTRAL is the **inactive sentiment** path: `sentiment=-0.012` < `sentiment_min_active=0.05`. Sentiment carries the largest weight (0.40), so even a modest active sentiment in the BUY direction would boost direction_score above 0.25.

The confidence floor of ~0.20 is dominated by the `_volume_factor=0.3` and the `_freshness_factor=0.4` — both are at or near their floor. The `data_age_hours=22.43` indicates Fear & Greed (or funding rate) has not been refreshed in close to a day; **this directly puts freshness in the `<= 24` bucket of 0.4** instead of the `<= 1` bucket of 1.0.

---

## C.2.5 — Live distribution (last 100 signals)

**Source:** `SIG_BATCH_STATS` and `SIG_TICK_SUMMARY` events at 22:06, 22:11, 22:16, 22:21, 22:26 — each tick emits 50 signals → 250 signals across last 5 ticks (200 most recent ≈ "last 100" sample doubled). Verbatim:

```
22:06:01  SIG_BATCH_STATS | n=50 conf_min=0.203 conf_max=0.429 conf_mean=0.253 conf_std=0.054
22:11:00  SIG_BATCH_STATS | n=50 conf_min=0.203 conf_max=0.343 conf_mean=0.238 conf_std=0.048
22:16:01  SIG_BATCH_STATS | n=50 conf_min=0.203 conf_max=0.344 conf_mean=0.267 conf_std=0.058
22:21:00  SIG_BATCH_STATS | n=50 conf_min=0.203 conf_max=0.344 conf_mean=0.219 conf_std=0.035
22:26:03  SIG_BATCH_STATS | n=50 conf_min=0.203 conf_max=0.335 conf_mean=0.214 conf_std=0.025
```

### `signal_type` distribution

NOT FOUND in a single roll-up log; inferred from the SIG_GEN sample. All BTCUSDT samples shown above and the spot-check across BTC/ETH/SOL/BNB/XRP show `type=neutral`. Sample search:

```
$ grep -E "type=(buy|strong_buy|sell|strong_sell)" workers.log | tail -5
```

Returns 0 hits in the captured 21:55–22:27 window. Inferred distribution: **neutral=100%** of the last 250 signals. (Cross-verified: max conf observed `0.429` < `buy_threshold` confidence floor `0.40`, so even on confidence the classifier could only reach NEUTRAL or BUY; the classifier already chose NEUTRAL.)

### Confidence histogram (last 250)

From the five `SIG_BATCH_STATS` lines, raw stats only — no per-coin rows. Min across the window: 0.203. Max: 0.429. Mean: ~0.24. Std: 0.025–0.058. The distribution is essentially a narrow band [0.20, 0.43] centred near 0.22.

### `direction_score` histogram (last 100)

NOT FOUND as an aggregate log; per-coin direction_score lives in `SIG_CLASSIFY` lines. Sample (22:26 cycle, 5 majors all show `direction_score=+0.100`). The constancy across majors implies the F&G value (47) drives every coin to the same `s_fg=+0.100`, and with no other component active for those coins the per-symbol direction_score is identical. Per-symbol variation only enters when `fund` or `oi` cross their `0.20` activation thresholds (rare in the captured window).

---

## OBSERVED ANOMALIES

- 100% NEUTRAL outcome is structural: only F&G is active for the majors, and `direction_score=+0.100 < buy_threshold=0.25`.
- `data_age_hours = 22.43` for BTCUSDT — F&G or funding/OI has not been refreshed for nearly a day. This pegs `_freshness_factor` at 0.4 (the `<=24` bucket) for the entire 5-min cycle.
- `volume_surge_ratio = 0.03` for BTCUSDT — implies the most recent M5 kline has 3% of the 20-period average volume. Either a zero-volume bar landed in the read window or volume is genuinely collapsed.
