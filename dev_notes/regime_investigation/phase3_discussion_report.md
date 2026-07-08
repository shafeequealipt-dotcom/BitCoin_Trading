# Phase 3 — Discussion Report

Investigation date: 2026-05-12. Investigator: Claude. Working tree: `fix/sell-bias-fixes-2026-05-11` at HEAD `848fe40`. PRIMARY APEX sell-bias fix verified present in source and in production logs.

# Section 1 — Headline Finding

The regime detector is per-coin in code and in data (92.5% of 5-min log buckets show distinct labels across symbols). The detector's accuracy on the immediate 30-minute price-action horizon is **14.6% overall and 11.8% on its `ranging` label**, with a **false-ranging rate of 88.2%**. The root cause is a single fallback branch at `src/strategies/regime.py:153-156` (`else: regime = RANGING`) that absorbs **73.9% of all regime emissions** in the 48-hour sample. The downstream effect: APEX's regime-conditional direction lock fails to fire on most trades, leaving XRAY's structural R:R threshold (currently 3.0×) as the only gate against Buy → Sell flips. Of 10 traced Buy → Sell flips, 8 occurred on coins carrying the fallback `ranging conf=0.40` label. None had `apex_locked=Y`.

# Section 2 — Question 1 Answer (per-coin vs market-aggregate)

The detector is **per-coin in both code and data**.

Code evidence:

- `RegimeDetector.detect(symbol)` and `detect_per_coin(symbols)` at `src/strategies/regime.py:78-233` run a fresh per-symbol pipeline for every call. Inputs (ADX, Plus-DI, Minus-DI, choppiness, NATR, volume SMA ratio) all derive from `market_repo.get_klines(symbol, "60", 200)`, scoped per symbol.
- No cross-coin signals are fed into the classifier. No BTC override, no fear-greed input, no funding-rate input.
- Per-symbol cache: `_per_coin_regimes`, `_confirmed_regimes`, `_pending_regime` — all keyed by `symbol`.

Empirical evidence (48h window):

- 7508 REGIME emissions across 159 5-min time buckets.
- **92.5% of buckets show >1 distinct regime label across the 50 watch_list symbols**.
- 62.9% of buckets show all 5 possible labels simultaneously.
- Per-symbol ranging share ranges from 36% (ETHUSDT) to 99% (ARBUSDT). Per-symbol behavior is systematically different.

The detector is unambiguously per-coin.

# Section 2b — Question 1b Answer (flip-causation chain)

Of 10 traced Buy → Sell XRAY flips in the post-1735 window:

- 10/10 had `apex_locked=N` (APEX direction lock did not fire).
- 8/10 occurred on the `ranging` label with `conf=0.40` (the ELSE fallback signature).
- 2/10 occurred on the `dead` label.
- 0/10 occurred on a trending label.
- XRAY ratios spanned 5.7x to 668x; the median was approximately 25x.

The clearest illustration of the bug surface is events 8-9 (HBARUSDT and MANAUSDT at 2026-05-11 22:50):

1. APEX detected a flip attempt from brain Buy to Qwen Sell.
2. APEX `_enforce_flip_confidence` correctly blocked the flip because `effective_confidence = 0.90 < 0.95` (Buy → Sell threshold).
3. APEX wrote `apex_dir = Buy` — the PRIMARY fix preserving the brain decision.
4. XRAY downstream saw the same structural picture (`rr_chosen ≈ 0.10`, `rr_flipped ≈ 3.42`).
5. XRAY ratio computed as 34.2x and 24.0x, both far above the 3.0 threshold.
6. XRAY flipped Buy → Sell, overriding APEX's preservation.

APEX and XRAY are two consecutive layers reading correlated inputs (Qwen confidence and structural R:R both derive from the same setup analysis) but applying different gates. APEX's confidence gate succeeds; XRAY's R:R-ratio gate is permissive enough to allow the flip anyway.

# Section 3 — Question 2 Answer (regime accuracy)

Empirical accuracy measured by `scripts/regime_accuracy_probe.py` over 96 stratified samples across 12 top symbols and the 48-hour window.

Headline confusion matrix (rows = detector label, columns = objective regime on 30-min before window):

| | trending_up | trending_down | ranging | other | weak_trending_up | weak_trending_down |
|---|---|---|---|---|---|---|
| ranging (n=85) | 2 | 8 | 10 | 18 | 21 | 26 |
| other (n=11) | 0 | 2 | 1 | 4 | 3 | 1 |

Quantitative findings:

- Overall accuracy: **14.6%**.
- **False-ranging rate: 88.2%**. Of 85 samples labeled `ranging`, only 10 (11.8%) are objectively ranging.
- 47 of 85 ranging-labeled samples (55%) had directionally consistent moves (weak or strong trending).
- 10 of 85 ranging-labeled samples (12%) had strong-trending moves (1+ ATR in a single direction with limited pullback).
- Directional asymmetry: of mislabeled-as-ranging samples, **34 are bearish (45%)** vs **23 bullish (31%)**, a 1.48× bearish skew matching the 48h market drift.
- **ELSE-fallback subset (conf=0.40)** captures 80 of the 96 samples and has 12.5% accuracy on the ranging-correct criterion.

Per-symbol accuracy varies from 0% (BTCUSDT, ADAUSDT, NEARUSDT, XRPUSDT) to 62% (ETHUSDT). The best-performing symbols are those whose H1 indicators occasionally land in the `dead` branch rather than the ELSE fallback.

Confidence stratification: every `conf=0.40` ranging label is a fallback emission. Detector emissions with `conf > 0.50` come from explicit branches (trending, volatile, dead, or strict-ranging) and have proportionally higher accuracy where the explicit criteria align with 30-min behavior.

# Section 4 — Trade-Outcome Correlation

24-hour `trade_history` summary:

| Side | Trades | Avg PnL | Total PnL | Wins | Win rate |
|---|---|---|---|---|---|
| Buy | 9 | -$1.36 | -$12.21 | 4 | 44.4% |
| Sell | 110 | +$0.42 | +$45.70 | 51 | 46.4% |
| Total | 119 | +$0.28 | +$33.49 | 55 | 46.2% |

Observations:

- Sell volume is approximately 12x Buy volume, consistent with the 91.7% sell-share of executed trades.
- Per-side win rates are nearly identical (44% vs 46%), so the system is not making better side calls when going Sell.
- Per-trade Buy PnL is negative, per-trade Sell PnL is positive. Aggregate PnL is positive ($33.49). The bearish drift makes the directional bias temporarily profitable.
- The directional bias is window-favorable but structurally fragile. If the market reverses, the same bias becomes the wrong directional bet.

Direct correlation of detector accuracy to trade outcome is weak because few regime samples coincided with trades in the window. The Phase 5 verification (4-6h post-deploy monitoring) is the right horizon to measure outcome shifts after a fix.

# Section 5 — Three Paths With Trade-Offs

## Path A — Tune XRAY threshold

- Change `[risk] xray_dir_flip_threshold_ratio` in `config.toml` from `3.0` to a higher value.
- Suggested values based on observed XRAY ratios: 6.0 (cuts the lowest-ratio flips), 10.0 (cuts about half), 25.0 (cuts most). The spec suggests 10.0.
- Implementation footprint: single-line config change. ~30 minutes including test scaffolding.
- File: `config.toml` (line containing `xray_dir_flip_threshold_ratio = 3.0`) and one test verifying load.
- Pros: trivial revert (one-line). Quick to deploy. Addresses the proximate flip mechanism.
- Cons: paper over the upstream issue (detector's high false-ranging rate). Leaves brain's Stage 2 prompt receiving uninformative `[RANGING 40%]` tags. Leaves scanner's score bonus operating on wrong labels.
- Estimated Buy-share recovery: 5-10% → 12-18% depending on threshold.
- Risk: too high a threshold (e.g., 25-30) eliminates legitimate large-asymmetry flips; too low (5-8) preserves most of the current bias.

## Path B — Fix the regime detector

Four sub-candidates. The investigation strongly recommends **B1** as the most impactful surgical fix; the others are supplementary or alternative.

### B1 — Eliminate or narrow the ELSE = RANGING fallback (highest leverage)

- The ELSE branch at `src/strategies/regime.py:153-156` produces 73.9% of all emissions. Closing the band that flows through it converts most fallback samples into informative labels.
- Two implementation options:
  - **B1a (config-tighten)**: adjust thresholds in the `[regime]` section so adjacent branches overlap and the ELSE band shrinks. Specifically, lower `trending_adx_threshold` from 25 to 20 (or even 18) so the trending branches catch coins in the [20, 25) zone. Also raise the strict-ranging band by lowering `ranging_choppiness_threshold` from 60 to 50 (matches typical crypto-norm definition).
  - **B1b (new label)**: introduce a `TRANSITIONAL` label that captures the [adx 15-25, choppiness 40-60] band explicitly. Consumers can then branch on transitional differently from ranging.
- B1a is simpler (config-only) and provides immediate measurability. B1b is the cleaner long-term fix but requires updating `MarketRegime` enum, `REGIME_ACTIVE_CATEGORIES`, and a few prompt-construction sites in strategist.py. Recommend B1a first as the surgical step; B1b is a follow-up.
- File surface for B1a: `config.toml` `[regime]` section + test verifying values.
- File surface for B1b: `src/strategies/models/regime_types.py` (enum + categories), `src/strategies/regime.py` (insert new branch), `src/brain/strategist.py` (already handles label.value uppercase, no change), `tests/test_strategies/test_regime.py` (new test).

### B2 — Per-coin threshold calibration

- Some symbols classify as ranging much more often than others (ARBUSDT 99%, ETHUSDT 36%). The current thresholds assume one set of cutoffs works for all symbols. A per-coin calibration would store per-symbol overrides.
- Implementation footprint: schema migration (new table `regime_thresholds` keyed by symbol), settings loader, regime detector consults the table per symbol.
- Pros: ultimately the most accurate. Long-term right call if Path B1 is insufficient.
- Cons: significantly more complex. Multi-day implementation. Risks stale calibration.
- Recommend deferring unless Path B1 + verification proves insufficient.

### B3 — Add structural signal as a regime input

- Currently the detector doesn't see `market_structure` from `StructureCache`, even though XRAY does. Cross-referencing could resolve regime / structure mismatches.
- Implementation footprint: detector dependency on structure cache, modified branch logic.
- Pros: ties the two signals together where they currently disagree.
- Cons: introduces a new dependency that affects regime startup ordering and worker lifecycle. Higher risk of regressions.
- Recommend deferring; revisit only if Path B1 + verification shows persistent disagreement between regime and structure.

### B4 — Confidence-weighted output (consumers respect detector's confidence)

- Currently consumers treat all regimes equally regardless of confidence. Making low-confidence regimes have softer downstream behavior:
  - Scanner score bonus scaled by confidence (currently +10 trending; would be `+10 * confidence`).
  - APEX direction lock requires `confidence > 0.7` to lock.
  - Stage 2 prompt tag includes confidence (already in code).
- Implementation footprint: scanner.py, APEX optimizer.py, possibly scorer.py.
- Pros: complements B1 — even with the fallback emitting conf=0.40, consumers downweight uncertain labels.
- Cons: requires touching multiple files. APEX direction lock change crosses into OUT OF SCOPE territory (APEX flip policy).
- Recommend deferring; Path B1 alone may resolve enough.

## Path C — Hybrid

Sequence:

1. Implement Path B1a (config-tighten ELSE fallback band).
2. Deploy.
3. Monitor 24 hours and re-run `scripts/regime_accuracy_probe.py`. Confirm false-ranging rate drops materially.
4. Re-measure Buy-share and XRAY flip count.
5. If XRAY flip count is still high (more than baseline / 2), then implement Path A's threshold tune.
6. Otherwise stop with Path B1a alone.

# Section 6 — Recommendation

**Recommended: Path C, with Path B1a as the first step.**

Reasoning:

1. **Path A alone treats symptom not cause.** The detector's 88% false-ranging rate makes regime an uninformative signal everywhere it is consumed. XRAY's flip is the most visible failure but not the only one. Stage 2 prompts, scanner score bonuses, and ensemble category gating are all operating on wrong labels.

2. **Path B1a is surgical and low-risk.** Changing `[regime]` config values touches no source code, requires no schema migration, has a single revert path. The current thresholds are not load-bearing in any other system; they only feed the classifier. Test scaffolding already exists (`tests/test_strategies/test_regime.py`).

3. **Verification gate is built in.** After Path B1a, the same accuracy probe re-runs. If the false-ranging rate drops to ~30-40% and APEX direction lock starts firing materially, Path A may be unnecessary. If false-ranging stays elevated or XRAY flips remain frequent, Path A is then applied with confidence.

4. **Path C preserves the operator's aggressive-exploitation aim.** Path A alone makes the system MORE conservative (fewer flips), and only conditionally aligns with aim. Path B fixes the upstream signal, restoring informed gating, and only then we ask whether the downstream gate also needs tuning.

The operator's choice prevails. If the operator prefers the quickest (Path A), or wants to combine paths simultaneously, or wants to defer Path B in favor of further investigation, the implementation phase will follow that choice.

# Section 7 — Specific Values For Recommended Path

## If Path C with Path B1a step 1

`config.toml` `[regime]` section, edit:

```
trending_adx_threshold        = 20          (was 25 — catches more weak trends)
ranging_adx_threshold         = 20          (unchanged)
ranging_choppiness_threshold  = 50          (was 60 — broader strict-ranging)
volatile_atr_percentile       = 70          (was 150 — make volatile reachable from NATR-derived percentile)
dead_adx_threshold            = 12          (was 15 — tighter dead)
dead_volume_ratio             = 0.5         (unchanged)
hysteresis_count              = 2           (unchanged)
```

Rationale per change:

- `trending_adx_threshold 25 → 20`: at the current 25, the trending branch fires only 8.8% of the time. Lowering to 20 catches the [20, 25) transition band currently absorbed by ELSE. Estimated trending-label share rises from 8.8% to approximately 25-30%.
- `ranging_choppiness_threshold 60 → 50`: at the current 60, only 3.1% of `ranging` labels come from the strict branch. Lowering to 50 expands strict-ranging to coverage roughly matching crypto-norm flat-market definition. Estimated strict-ranging share rises from 3.1% to approximately 25-35% of overall labels.
- `volatile_atr_percentile 150 → 70`: the NATR-derived value is capped near 100 in practice, so 150 makes the ATR-clause unreachable. Lowering to 70 (the original-design value) restores the volatile branch via ATR percentile. Estimated volatile-label share rises modestly (a few percentage points).
- `dead_adx_threshold 15 → 12`: tightens the dead branch so it doesn't absorb weakly-trending dead-like coins. Estimated dead share decreases by 1-3 percentage points.

Combined estimated impact (rough): the ELSE fallback share drops from 73.9% to approximately 25-35%. Trending-label share rises from 8.8% to 25-30%. APEX direction lock fires on 25-30% of trades instead of 8.8%. XRAY flip count drops by an estimated 50-70%.

The change is applied to `config.toml` only. No source code change for B1a. Tests in `tests/test_strategies/test_regime.py` get expanded to verify the new threshold-config values are loaded and that the classifier correctly hits the explicit branches more often.

After deploy, the same `scripts/regime_accuracy_probe.py` re-runs to confirm the false-ranging rate drops. Phase 5 verification report compares pre vs post.

## If Path A only

`config.toml` `[risk]` section:

```
xray_dir_flip_threshold_ratio = 10.0   (was 3.0)
```

Rationale: 10x is the spec's suggested value. Of the 10 observed flips, this would eliminate flips at 5.7x and 6.4x (and possibly 11.1x depending on exact margin), preserving the higher-ratio flips that may reflect legitimate setups. Estimated Buy-share recovery: 5-10% → 10-15%.

Test scaffolding lives in `tests/test_xray_dir_flip.py` (already present). No new test required for a value change.

## If Path B1 with B1b (new label)

Implementation surface:

- `src/strategies/models/regime_types.py`: add `MarketRegime.TRANSITIONAL = "transitional"`. Add `REGIME_ACTIVE_CATEGORIES[TRANSITIONAL] = [...]` with the strategy set appropriate for transitional markets (likely momentum + scalping + microstructure).
- `src/strategies/regime.py:153-156`: change the ELSE branch to assign `MarketRegime.TRANSITIONAL` with `confidence = 0.5`.
- `tests/test_strategies/test_regime.py`: add fixtures and a test that the transitional branch fires when ADX in [20, 25] AND choppiness in [40, 60].
- No other files need changes — Stage 2 prompts already use `.value.upper()`, scanner is OK with the new label (no specific bonus until later), APEX direction lock can be extended in a follow-up.

The B1b option is cleaner long-term but is a deferred follow-up to B1a. Recommend B1a now.

# Sign-off request

The operator must choose:

1. **Path A** (XRAY threshold tune, fast, treats symptom) — choose value (6.0 / 10.0 / 25.0 / other).
2. **Path B** with sub-candidates (B1a config tighten / B1b new label / B2 per-coin / B3 structural / B4 confidence-weighted).
3. **Path C** (Path B1a then re-evaluate Path A) — recommended.

Any additional constraints (e.g., maximum config delta, no schema changes, do both A and B1a simultaneously) should be specified.

After operator response, Phase 4 implementation begins per the chosen path. Phase 5 verification compares post-deploy metrics to the Phase 0 baseline. Until the operator responds, no code changes occur.
