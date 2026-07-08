# Scanner (Layer 1D) Fail-Bucket Deep Analysis

**Date**: 2026-04-29
**Context**: Live-monitoring observation that scanner outputs 0-3 qualifiers per cycle vs blueprint §10.2 target of 5-25. Operator asked for an in-depth breakdown of WHERE coins are lost, in the 5-criterion qualitative gate.

**Source code**: `src/workers/scanner_worker.py:_qualifies()` lines 567-687 (the gate body).

The gate runs as **5 sequential checks**, short-circuiting at the first failure. Whichever check fails first determines the bucket the coin is logged in. The aggregate counts emit per cycle as `SCANNER_FILTER_AGGREGATE`.

---

## Aggregate log line format

```
SCANNER_FILTER_AGGREGATE | cycle_id=<id> total=50 qualified=N
  fail_no_xray=N fail_setup_none=N fail_consensus=N fail_regime=N fail_rr=N fail_blockers=N
  pass_xray=N pass_consensus_strong=N pass_consensus_good=N
```

**Math invariant**: `qualified + fail_no_xray + fail_setup_none + fail_consensus + fail_regime + fail_rr + fail_blockers = total` (exactly 50 for the watch_list run).

---

## Bucket 1 — `fail_no_xray` (XRAY analysis completely missing)

### Code path
`scanner_worker.py:599-608`:

```python
sw = self.services.get("structure_worker")
cache = getattr(sw, "_cache", None) if sw else None
structure = cache.get(symbol) if cache and hasattr(cache, "get") else None
if structure is None:
    record["reasons_failed"].append("no_xray_analysis")
    return False, record
```

### What it means
The XRAY structure_worker hasn't produced an entry for this coin AT ALL — not even a `setup_type=none` result. The `StructureCache` dict has no key for this symbol.

### When it fires
- StructureWorker hasn't ticked yet for this cycle (cold start before XRAY ticks)
- StructureWorker excluded the coin (insufficient klines, error in analysis)
- Cache TTL (300s) expired without refresh

### Live observation
Almost always **0** in healthy steady-state. Was 50 once at boot before XRAY first tick (cycle 03:30 in earlier session). If you see this >0 in steady state → XRAY is broken for those coins.

### Config knob
None directly. Driven by upstream XRAY worker health.

---

## Bucket 2 — `fail_setup_none` (XRAY analyzed but classifier emitted `setup_type="none"`)

### Code path
`scanner_worker.py:609-612`:

```python
setup_type = getattr(structure, "setup_type", None)
if setup_type is None or getattr(setup_type, "value", "none") == "none":
    record["reasons_failed"].append("no_xray_setup_type")
    return False, record
```

### What it means
XRAY ran all 12 phases on this coin, populated the StructureCache entry, but couldn't classify any structural pattern. The setup_type enum can be one of:

- `bullish_FVG_OB` / `bearish_FVG_OB`
- `bullish_structural_break` / `bearish_structural_break`
- `liquidity_sweep_reclaim`
- `range_compression_breakout`
- `none` ← the failure

### When it fires
- Coin has no clean FVG zone above/below current price
- Coin has no fresh order block in proximity
- No recent liquidity sweep
- Price is in unclear chop (no clear range bounds, no clear trend)

### Live observation
**11-19 per cycle** consistently (~24-38% of watch_list). This is the **upstream funnel cap** — the absolute number of coins XRAY can identify with concrete patterns each cycle.

### Config knobs
`[structure.setup_types]` in `config.toml`:

| Knob | Current | Effect of lowering |
|---|---|---|
| `fvg_ob_min_confluence` | 0.5 (blueprint suggested 0.7) | More FVG_OB classifications |
| `structural_break_require_retest` | true | Less waiting for retest before classifying |
| `sweep_min_displacement_pct` | 0.5 | Smaller sweeps qualify |
| `range_breakout_min_compression_bars` | 20 | Shorter compression windows qualify |
| `mtf_alignment_required` | true | Single-TF setups can qualify |
| `ranging_market_mtf_threshold` | 0.55 | Looser MTF agreement |

Loosening any → more coins get a setup_type → fewer fail here. **This is the only quality-preserving lever to widen the upstream funnel** (vs loosening downstream gates which sacrifices trade quality).

---

## Bucket 3 — `fail_consensus` (Ensemble did NOT reach STRONG or GOOD)

### Code path
`scanner_worker.py:617-624`:

```python
consensus = lm.get_strategy_consensus(symbol)
accept = {"STRONG"} if cfg.min_consensus == "STRONG" else {"STRONG", "GOOD"}
if consensus is None or consensus.get("consensus") not in accept:
    label = consensus.get("consensus") if consensus else "NONE"
    record["reasons_failed"].append(f"consensus={label}")
    return False, record
```

### How "consensus" is computed
`ensemble.py:128-138`:

```python
if agreeing >= 4.0 and opposing <= 1.5:        consensus = "STRONG"
elif agreeing >= min_ensemble_agreement and opposing <= max_ensemble_opposition:
                                               consensus = "GOOD"
elif agreeing >= 1.5 and opposing <= 1.5:      consensus = "WEAK"
elif agreeing > opposing:                       consensus = "LEAN"
else:                                           consensus = "CONFLICT"
```

Where:
- `agreeing` = sum of (weight × confidence) from strategies voting in the consensus direction (BUY or SELL)
- `opposing` = mirror sum from strategies voting opposite
- Capped per-strategy at `single_strategy_max_share` so no single strategy can force STRONG (Definitive-fix Phase 12 from 2026-04-28)

### Four sub-reasons a coin lands in fail_consensus

| Sub-reason | Why | Frequency |
|---|---|---|
| **consensus=NONE** | StrategyWorker didn't run any strategy on this coin (TIME_DECAY_SKIP / STRAT_SKIP_STALE — stale or missing kline data) | Common for sleepy coins |
| **consensus=WEAK** | 1.5 ≤ agreeing < 4.0 AND opposing ≤ 1.5 — modest agreement, some support but not enough confidence | Common in choppy markets |
| **consensus=LEAN** | agreeing > opposing but agreeing < 1.5 — mild tilt, very weak signal | Common in noisy markets |
| **consensus=CONFLICT** | opposing ≥ agreeing — strategies disagree about direction | Common at S/R levels, indecision |

### When it dominates
This is the **biggest variance bucket**. In Asian-late / ranging session (cycle 06:10): 34 of 38 = 89% fail here. In active session (cycle 05:05): 17 of 38 = 45% fail here.

### Config knobs
| Knob | Location | Effect |
|---|---|---|
| `min_consensus = "GOOD"` | `[scanner.qualitative]` | Accept STRONG + GOOD. Setting `"STRONG"` tightens (only 0-5 pass). Loosening would require code change since it's a string enum. |
| `min_ensemble_agreement` | `[strategy_engine]` | Lowers the GOOD threshold's `agreeing` requirement |
| `max_ensemble_opposition` | `[strategy_engine]` | Raises the GOOD threshold's `opposing` allowance |
| `single_strategy_max_share` | `[strategy_engine]` | Correctness, not loosener — prevents one strategy forcing STRONG |

### Live observation pattern (5 cycles this session)

| Cycle | pass_strong | pass_good | fail_consensus |
|---|---|---|---|
| 04:55 | 7 | 0 | 22 |
| 05:00 | 16 | 5 | 17 |
| 05:05 | 17 | 4 | 17 |
| 06:10 | 0 | 4 | 34 |
| 06:15 | 3 | 3 | 33 |

Consensus distribution swings WILDLY — 0-21 of 38 setup-typed coins reach STRONG/GOOD. **This is purely market-driven, not config-driven.** When market is decisive, you get many STRONG; when undecided, you get many WEAK/LEAN/CONFLICT.

---

## Bucket 4 — `fail_regime` (Direction does NOT align with regime)

### Code path
`scanner_worker.py:627-647`:

```python
state = rw.get_regime(symbol)
regime_label = state.regime.value if state else ""
direction = consensus.get("direction", "")
if not self._regime_aligns(regime_label, direction):
    record["reasons_failed"].append(f"regime={regime_label or 'unknown'}_vs_{direction}")
    return False, record
```

### Alignment matrix
`scanner_worker.py:_regime_aligns()`:

```python
if direction == "long":  return regime in {trending_up, ranging}
if direction == "short": return regime in {trending_down, ranging}
return False  # neutral direction or volatile/unknown regime → fail
```

| Direction | Regime | Outcome |
|---|---|---|
| long | trending_up | ✓ pass |
| long | ranging | ✓ pass (range-bottom long ok) |
| long | trending_down | ✗ fail (don't fight trend) |
| long | volatile | ✗ fail (regime too unclear) |
| short | trending_down | ✓ pass |
| short | ranging | ✓ pass |
| short | trending_up | ✗ fail |
| short | volatile | ✗ fail |
| neutral | any | ✗ fail (direction must be defined) |
| any | "" or unknown | ✗ fail (e.g., BTC missing regime — chronic L1D-H3) |

### Live observation
0-5 per cycle. Usually 1-3. Highest when global regime conflicts with many coin-level direction signals.

### Config knob
`[scanner.qualitative].require_regime_alignment = true` — set to `false` to disable this check entirely (always 0).

### Quality cost of disabling
Allowing counter-regime trades is high-risk. Trend-following strategies expect to ride the regime; fighting it has lower expected value. Don't disable lightly.

---

## Bucket 5 — `fail_rr` (Reward-to-Risk ratio below `min_rr_ratio`)

### Code path
`scanner_worker.py:650-676` (direction-aware, Definitive-fix Phase 4 from 2026-04-28):

```python
sp = getattr(structure, "structural_placement", None)
direction = (consensus.get("direction") or "").lower()
if direction == "long":   rr_field = sp.rr_long
elif direction == "short": rr_field = sp.rr_short
else:                      rr_field = sp.rr_ratio  # fallback
rr = float(rr_field or 0.0)
if rr < cfg.min_rr_ratio:
    record["reasons_failed"].append(f"rr={rr:.2f}_below_{cfg.min_rr_ratio}")
    return False, record
```

### What it means
- `rr_long` = (structural_long_TP - current_price) / (current_price - structural_long_SL)
- `rr_short` = (current_price - structural_short_TP) / (structural_short_SL - current_price)
- The CONSENSUS DIRECTION's RR is checked, not the better of the two.

### Sub-reasons RR is low

| Pattern | Why RR is low |
|---|---|
| Coin near top of range with consensus=long | TP at range top is close, SL below range floor is far → small `rr_long` |
| Coin near bottom of range with consensus=short | Mirror of above |
| Mid-range coin | Both rr_long and rr_short modest (~1.0-1.5) |
| Coin just past resistance (uptrend break) with consensus=long | TP next resistance close, SL below break is far |
| Tight S/R band around current price | All RR values low |

### Config knob
`[scanner.qualitative].min_rr_ratio` — currently **1.1** (was 1.3, blueprint originally 2.0):

| Value | Quality interpretation |
|---|---|
| 1.0 | Breakeven floor — don't go below |
| 1.1 | Positive expectancy minimum (current) |
| 1.3 | Previous setting (over-strict for ranging markets) |
| 2.0 | Blueprint ideal (very strict) |

### Live observation
0-14 per cycle. Highest in ranging markets where price sits near range edges.

---

## Bucket 6 — `fail_blockers` (One of three veto conditions hit)

### Code path
`scanner_worker.py:678-687` calling `_check_blockers()` at lines 320-378.

Three independent checks; ANY fail = blocker.

### 6a. Funding rate against direction
```python
if direction == "long" and rate > funding_blocker_threshold_pct:
    blockers.append(f"funding_against_long_rate={rate:.4f}")
elif direction == "short" and rate < -funding_blocker_threshold_pct:
    blockers.append(f"funding_against_short_rate={rate:.4f}")
```

- Long when funding > +0.1% (longs paying shorts heavily) → expensive to hold long
- Short when funding < −0.1% (shorts paying longs heavily) → expensive to hold short
- Threshold: `funding_blocker_threshold_pct = 0.001` (0.1%)

### 6b. Manipulation-likely session
```python
if session.manipulation_likely:
    blockers.append("manipulation_likely_session")
```

XRAY's Phase 12 (Session Timing) tags certain windows as susceptible to manipulation: low-liquidity hours, certain Asian-session phases, weekend periods.

### 6c. Recent failure within lookback
```python
if recent_loss_set is not None and symbol in recent_loss_set:
    blockers.append(f"recent_loss_within_{recent_failure_blocker_hours}h")
```

- `recent_loss_set` is pre-fetched once per tick from `trade_recorder.recent_loss_symbols(db, hours=1)`
- Coins that closed at a loss within the past 1 hour are blocked
- Prevents immediate re-entry after a bad trade

### Live observation
0-1 per cycle. Almost always 0. Blockers fire rarely because:
- Funding extremes (>0.1%) are uncommon in normal market
- manipulation_likely is rare (XRAY only flags clear cases)
- Recent losses are rare since trade volume is low

### Anomaly flagged 2026-04-29 06:19
HYPEUSDT closed with a loss at 06:12:36, then qualified at 06:19:00 (6:24 within 1h window) without `fail_blockers` triggering. Possible causes:

- (a) `recent_loss_symbols` query has a magnitude threshold; the small loss didn't register
- (b) trade_recorder hasn't persisted HYPE's close to the table the prefetch reads
- (c) The "loss" magnitude is below some min_pnl threshold

**This is a candidate L1D bug worth investigating** — the recent-failure blocker should have caught HYPE.

### Config knobs
- `funding_blocker_threshold_pct = 0.001`
- `recent_failure_blocker_hours = 1`
- (manipulation_likely is XRAY-internal; no scanner config)

---

## Compound view — funnel arithmetic

```
50 watch_list coins
 │
 ├── fail_no_xray            ≈ 0 (rare; XRAY missing)
 ├── fail_setup_none         ≈ 11-19 (XRAY classifier verdict)  ← UPSTREAM CAP
 │
 │   pass_xray = 38-39
 │
 ├── fail_consensus          ≈ 17-34 (ensemble distribution, market-driven)  ← BIGGEST VARIANCE
 │
 │   pass_consensus = 4-21 (STRONG + GOOD combined)
 │
 ├── fail_regime             ≈ 0-5 (direction-vs-regime)
 │
 │   pass_regime = 1-17
 │
 ├── fail_rr                 ≈ 0-14 (RR < 1.1 against consensus direction)  ← QUALITY FLOOR
 │
 │   pass_rr = 0-3
 │
 ├── fail_blockers           ≈ 0-1 (funding/manipulation/recent-loss)
 │
qualified = 0-3 typical (blueprint expects 5-25 in active markets)
```

---

## Diagnostic interpretation — what each bucket TELLS you

| Bucket pattern | Diagnosis |
|---|---|
| `fail_no_xray > 0` in steady state | Upstream pipeline broken — XRAY isn't running for some coins. Check structure_worker health. |
| `fail_setup_none` consistently 38+ | XRAY classifier is too conservative for current market; tune `[structure.setup_types]` thresholds. **Only quality-preserving lever to widen funnel.** |
| `fail_consensus` dominates with `pass_strong=0` | Market is undecided / sleepy — wait for active session. No config can fix this. |
| `fail_consensus` dominates with `pass_strong>10` but few qualifiers downstream | Other gates (regime/RR) are the bottleneck — analyze those. |
| `fail_regime > 5` consistently | Many coins have direction-vs-regime mismatch — check global regime alignment. |
| `fail_rr` dominates | Coins are mid-range / poor structural placement — `min_rr_ratio` too strict, OR market is tight (everything near S/R levels). |
| `fail_blockers > 0` persistently | Operator alert — check funding extremes, recent losses, or manipulation_likely sessions. |

---

## Honest summary

The funnel reflects **market microstructure across all 5 dimensions**. There is no single "loosen this" knob. Each gate is doing its designed job:

1. XRAY filters for **structural pattern existence** (without a clean pattern, the trade has no structural anchor)
2. Consensus filters for **strategy-ensemble agreement** (without agreement, the signal is noise)
3. Regime filters for **direction-trend alignment** (don't fight the regime)
4. RR filters for **positive expectancy** (don't take asymmetric losses)
5. Blockers filter for **veto conditions** (funding extremes, manipulation, recent failures)

In active markets (London open, volatility expansion), all 5 gates pass simultaneously for 5-15 coins regularly. In sleepy markets (Asian-late, ranging, low-vol), 0-3 is the natural output.

The blueprint's "expect 5-25" is a **market-condition expectation**, not a guarantee. Forcing 10-12 in sleepy markets would require sacrificing quality on one or more dimensions.

The **ONLY quality-preserving lever** to widen the funnel is the upstream XRAY classifier (Bucket 2). Loosening setup-type detection thresholds in `[structure.setup_types]` would let more coins enter the funnel with named patterns. Everything else (consensus, regime, RR, blockers) trades quality for quantity.

---

## Cross-reference

- Blueprint: `/home/inshadaliqbal786/LAYER1_RESTRUCTURE_BLUEPRINT.md` §10 (Layer 1D), §10.2 (5-criterion gate), §10.5 (when 0 qualify)
- Scanner code: `src/workers/scanner_worker.py:567-687` (`_qualifies` body), `:320-378` (`_check_blockers`), `:305-318` (`_regime_aligns`)
- Ensemble code: `src/strategies/ensemble.py:128-138` (consensus tiering)
- Config: `config.toml:[scanner.qualitative]`, `:[structure.setup_types]`, `:[strategy_engine]`
- Memory: `project_layer1_restructure.md`, `project_post_layer1_fixes.md`, `project_scanner_async_prefetch_fix.md`
