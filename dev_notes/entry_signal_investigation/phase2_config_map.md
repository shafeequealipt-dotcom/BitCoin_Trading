# Phase 2 — Entry Configuration Map

Every configuration value governing entry, where it lives, whether hardcoded or data-derived, and whether regime-conditional. Sources: `config.toml`, `src/config/settings.py`, plus inline hardcoded literals already catalogued in Phase 1.

## Configuration Architecture

- `src/config/settings.py` (4,334 lines) defines a tree of `@dataclass` settings. Default values live there.
- `config.toml` (2,043 lines) overrides defaults at boot. Env vars override toml.
- A subset of values lives ONLY in code (no config exposure). These are noted as "hardcoded" in the tables below.

---

## The 36 Strategies — Weights And Categories

### Live weight source

Per `signal_types.py:195` (referenced in Phase 1): `ensemble_weight: float = 1.0`. Every strategy starts at **1.0 uniform**.

The Optimizer module (src/strategies/optimizer.py) mutates weights via `registry.set_ensemble_weight(name, weight)` (registry.py:96-104) which clamps to `[0.1, 3.0]`. The optimizer's adjustments are computed but **NOT PERSISTED TO DB**. The registry holds weights in-memory only. On process restart, every strategy resets to 1.0.

`strategy_performance` table contains only `claude_trader` rows (145 of them, one per symbol+timeframe). The per-strategy A1-K4 rows that the registry produces in-memory are not written here. Per-strategy weight differentiation is therefore ephemeral — it persists only as long as the process runs.

### Category classification (from Phase 1 enumeration)

| Family | Strategies | Phase 3 Net PnL |
|---|---|---|
| Momentum / trend / breakout | A2, A3, A4, B1, B3, B4, F2, H4, H3 | All NEGATIVE on supported trades |
| Trend (confirmed) | B2 | Positive (+$71) |
| Contrarian | A1, C1, C2, D1, D2, E1, G2 | Mixed (most neutral in window) |
| Event-driven | E2, F3, G1, G3, G4, H1, I1, I2, I3 | Net POSITIVE (G1, G4, I4 strongly positive) |
| Mean-reversion / time | I4 | Positive (+$107) |
| Structural | F1, J1, J3, J4 | Net neutral |
| Pattern-driven (AI) | K1, K2, K3, K4 | Non-voters or never supported in sample |
| Sentiment | E1, E2, E3, G2 | Sparse activity in window |

The category labels are SELF-DECLARED in each strategy file (e.g. `category = SignalCategory.MOMENTUM`). The ensemble does NOT read category — it only sums weighted votes. The category metadata is for telemetry only.

---

## Ensemble Consensus Thresholds

Code path: `src/strategies/ensemble.py:261-275`. Two threshold sources:

### Hardcoded (cannot be tuned without code change)

- STRONG consensus: `agreeing >= 4.0 AND opposing <= 1.5` (ensemble.py:263)
- WEAK consensus: `agreeing >= 1.5 AND opposing <= 1.5` (ensemble.py:267)
- LEAN consensus: `agreeing > opposing` (ensemble.py:269)
- CONFLICT consensus: else (ensemble.py:272)
- CONSENSUS_SIZE map: `{STRONG: 1.0, GOOD: 0.75, LEAN: 0.50, WEAK: 0.30, CONFLICT: 0.15}` (ensemble.py:261)

### Tunable via `[strategy_engine]`

- `min_ensemble_agreement` — controls GOOD `agreeing` bound. Dataclass default 5.0 (settings.py:1585). **`config.toml:983` overrides to 2.5**.
- `max_ensemble_opposition` — controls GOOD `opposing` bound. Dataclass default 1.0 (settings.py:1586). **`config.toml:984` overrides to 2.5**.
- `vote_trace_enabled` — gates STRAT_VOTE_TRACE log emission. Default True (settings.py:1594). Toml-overridable.
- `single_strategy_max_share` — caps any single strategy's contribution as a fraction of the side total. Default 1.0 = cap disabled (settings.py:1601). Toml-overridable.

### Effective live consensus thresholds (production)

With `config.toml` active:
- STRONG: `agreeing >= 4.0 AND opposing <= 1.5` (HARDCODED unchanged)
- GOOD: `agreeing >= 2.5 AND opposing <= 2.5` (config-relaxed from 5.0/1.0)
- WEAK: `agreeing >= 1.5 AND opposing <= 1.5` (HARDCODED unchanged)
- LEAN: `agreeing > opposing` (HARDCODED unchanged)
- CONFLICT: else (HARDCODED)

**Finding**: The production toml *relaxes* the GOOD threshold significantly. With `min_ensemble_agreement=2.5`, a trade with `agreeing=2.5, opposing=2.5` is GOOD. Previously this would have been CONFLICT (agreeing equals opposing) or WEAK. The bar for "moderate confidence" has been lowered.

### Branch ordering quirk

The first-match-wins evaluation order is STRONG → GOOD → WEAK → LEAN → CONFLICT. STRONG hardcodes a 4.0/1.5 floor; GOOD's toml-effective 2.5/2.5 floor is LOOSER on the `agreeing` axis but TIGHTER (less permissive) on the `opposing` axis. A trade at `agreeing=4.5, opposing=2.0` is STRONG (lines 263 satisfies). A trade at `agreeing=3.0, opposing=2.0` is GOOD (`>=2.5 and <=2.5`). A trade at `agreeing=3.0, opposing=2.6` is WEAK (`>=1.5 and <=1.5` does NOT match because opposing=2.6; falls to LEAN if agreeing > opposing).

### Regime-conditional? NO

No threshold above varies with regime. Same bars apply in trending, ranging, volatile, dead.

---

## SIG_CLASSIFY Thresholds

Code path: `src/intelligence/signals/signal_generator.py:_evaluate_signal` (`:393-545`). All thresholds tunable via `[signal_generator.multi_source]`:

| Name | Default | config.toml override | Role |
|---|---|---|---|
| `sentiment_min_active` | 0.05 | (toml matches) | min `\|s_sentiment\|` for sentiment to participate |
| `fg_min_active` | 0.10 | (toml matches) | min `\|s_fg\|` for F&G to participate |
| `funding_min_active` | 0.10 | (toml matches) | min `\|s_funding\|` for funding to participate |
| `oi_min_active` | 0.10 | (toml matches) | min `\|s_oi\|` for OI to participate |
| `sentiment_weight` | 0.40 | (toml matches) | weight in weighted sum |
| `fg_weight` | 0.25 | (toml matches) | weight |
| `funding_weight` | 0.20 | (toml matches) | weight |
| `oi_weight` | 0.15 | (toml matches) | weight |
| `strong_threshold` | 0.55 | 0.55 | `\|direction_score\| >= → STRONG_BUY/SELL` |
| `buy_threshold` | **0.18** | **0.18** | `\|direction_score\| >= → BUY/SELL` |
| `fg_normalize_range` | 30.0 | 30.0 | F&G normaliser `(50-fg)/range` |
| `funding_normalize` | 0.005 | 0.005 | funding normaliser `-fr/norm` |
| `oi_normalize_pct` | 5.0 | 5.0 | OI normaliser `oi/norm` |

**Critical finding — the BUY-bias is deliberate**. `buy_threshold = 0.18` was lowered from 0.25 (settings.py:3034-3038 comment: "buy_threshold 0.25 → 0.18 to match the typical BUY-leaning direction_score observed in forensic data"). The classifier now labels weakly-positive `direction_score` values as `BUY` that previously would have been NEUTRAL. This is a **structural asymmetry** baked into the classifier — BUY fires earlier than SELL would fire in the mirror case (because the threshold compares the absolute value but the input is skewed BUY-positive in the post-fix data).

### Confidence-floor downgrade thresholds (HARDCODED in signal_models.py:44-50)

- `CONFIDENCE_THRESHOLDS["strong_buy"] = 0.60`
- `CONFIDENCE_THRESHOLDS["buy"] = 0.40`
- Mirror for sell labels.

Trade with `signal_type=STRONG_BUY, confidence<0.60` is downgraded to BUY; if `confidence<0.40`, forced to NEUTRAL. Non-destructive — original retained in `components.original_signal_type` (signal_generator.py:242). Phase 4B / CALL_B-Framing-Fix shipped this non-destructive variant on 2026-05-06.

### Regime-conditional? NO

Zero references to regime in signal_generator.py, signal_models.py, or confidence.py.

---

## Regime Detection Thresholds

Code path: `src/strategies/regime.py:90-156`. Tunable via `[regime]`:

| Name | Default | config.toml | Role |
|---|---|---|---|
| `detection_interval_seconds` | 300 | (toml matches) | **UNUSED** — worker is sweet-spot-scheduled |
| `primary_symbol` | "BTCUSDT" | (toml matches) | global regime symbol |
| `trending_adx_threshold` | 20.0 | 20.0 | ADX > x for TRENDING (LOWERED from typical 25; see `project_regime_b1a_fix_status.md` 2026-05-12) |
| `ranging_adx_threshold` | 20.0 | 20.0 | ADX < x for RANGING |
| `ranging_choppiness_threshold` | 50.0 | 50.0 | choppiness > x for RANGING |
| `volatile_atr_percentile` | 70.0 | 70.0 | atr_percentile > x for VOLATILE |
| `dead_adx_threshold` | 12.0 | 12.0 | ADX < x for DEAD |
| `dead_volume_ratio` | 0.5 | 0.5 | volume_ratio < x for DEAD |
| `hysteresis_count` | 2 | 2 | consecutive readings to confirm change |

### Hardcoded literals inside `RegimeDetector.detect()` (cannot be tuned)

- `choppiness < 45` for TRENDING (regime.py:133, 137)
- `volume_ratio > 2.0` for VOLATILE second clause (regime.py:141)
- `atr_percentile < 50` for DEAD additional gate (regime.py:149)
- Confidence formulas: `min(adx/50, 1.0)`, `min(atr_percentile/200, 1.0)`, `min(choppiness/80, 1.0)`, DEAD=0.8 fixed, ELSE-fallback=0.4

---

## Setup Type / X-RAY Thresholds

Code path: `src/analysis/structure/structure_engine.py:classify_setup`. ALL HARDCODED in the engine (no toml or settings.py exposure for these specific thresholds beyond the `[structure]` block):

| Name | Default | Role |
|---|---|---|
| `fvg_ob_min` | 0.7 | min MTF score for FVG+OB setup |
| `require_retest` | True | BOS retest gate |
| `sweep_min_pct` | 0.5 | min sweep depth |
| `breakout_min_bars` | 20 | compression-bars proxy |
| `ranging_market_mtf_threshold` | 0.55 | ranging market MTF gate |
| `counter_enabled` | True | enable counter-trade variants |
| `counter_mult` | 0.7 | confidence multiplier for counter setups |
| `counter_mtf_min` | 0.40 | min MTF for counter |
| `bos_minor_mult` | 0.8 | minor BoS confidence multiplier |
| `position_in_range >= 0.95` | 0.95 | bullish range breakout trigger |
| `position_in_range <= 0.05` | 0.05 | bearish range breakdown trigger |

### Regime-conditional? NO

Setup_type is determined from structural data alone (`market_structure.structure` ∈ uptrend/downtrend/ranging, FVG/OB freshness, BOS direction). The per-coin regime is NOT consulted.

---

## State Labeler Thresholds (HARDCODED)

Per Phase 1 finding, EVERY threshold in `state_labeler.py` is a hardcoded module-level constant. The docstring at state_labeler.py:48-51 says a `[scanner.briefing.label_thresholds]` config block was promised in Phase 4 but never landed. Listing critical ones:

| Name | Hardcoded value | File:line |
|---|---|---|
| RANGE_FADE_LONG position cap | 0.40 | state_labeler.py:335 |
| RANGE_FADE_SHORT position floor | 0.60 | :354 |
| FUNDING extreme decimal | 0.0015 | :399 — **DIFFERS** from qualitative `funding_blocker_threshold_pct=0.001` |
| MOMENTUM burst min change | ±5.0% | :475, :491 |
| MOMENTUM burst min vol_ratio | 1.5 | :479, :495 |
| EXTREME_FEAR window | (0, 20) | :551 |
| EXTREME_GREED window | (80, 100] | :570 |
| Base-weight ranking table | (22 values 0.05-0.85) | :110-133 |

### Tunable

- `counter_regime_confidence_haircut = 0.5` (config.toml:803, scanner.labeller section) — soft attenuation for triggers whose regime doesn't match label expectation. 0.0 reproduces legacy hard-kill.

---

## Scanner Scoring Weights

Code path: `src/workers/scanner_worker.py:_compute_opportunity_score` reading `settings.scanner.scoring_weights`. Tunable via `[scanner.scoring_weights]`:

| Name | Default | config.toml | Role |
|---|---|---|---|
| `structure` | 0.27 | 0.27 | X-RAY weight in opportunity score |
| `strategy` | 0.27 | 0.27 | StrategyWorker L2 weight |
| `signal` | 0.13 | 0.13 | SignalWorker confidence weight |
| `regime` | 0.13 | 0.13 | regime-alignment weight |
| `funding` | 0.10 | 0.10 | funding weight |
| `rr` | 0.10 | 0.10 | reward-to-risk weight |
| `min_rr_ratio` | 1.3 | 1.3 | min RR for exclusion-mode qualification |
| `min_consensus` | "GOOD" | (toml matches) | exclusion-mode consensus floor |
| `funding_blocker_threshold_pct` | 0.001 | 0.001 | exclusion-mode funding cap (DIFFERS from labeler 0.0015) |
| `max_selection` | 15 | (toml matches) | exclusion-mode top-N |
| `top_n_packages` | 15 | (toml matches) | briefing-mode top-N |
| `min_briefing_packages` | 12 | (toml matches) | briefing-mode soft floor |
| `prompt_floor_interestingness` | 0.20 | (toml matches) | per-coin skip floor |

### Regime-conditional? YES (within scanner only)

- `_get_regime_alignment` (scanner_worker.py:185-211) maps regime → {+1, +0.5, 0, -1} as a scoring component.
- `_regime_aligns` (scanner_worker.py:378-392) is exclusion-mode HARD GATE (rejects mis-aligned direction/regime).

But the regime-alignment score is summed into a composite — it cannot veto a high-structure-score trade.

---

## Strategist (CALL_A) Settings

Code path: `src/brain/strategist.py`. Tunable via `[brain]` and `[stage2]`:

| Name | Default | config.toml | Role |
|---|---|---|---|
| `brain.use_packages` | True | (toml matches) | use CoinPackage feed (vs legacy) |
| `brain.surface_briefing_fields` | True | (toml matches) | enable briefing-suffix system prompt + extras |
| `brain.surface_top_n_voters` | 10 | (toml matches) | how many top voters shown per coin |
| `brain.recent_loss_lookback_hours` | 336 | (toml matches) | 14 days lookback for TIAS lessons |
| `brain.recent_loss_max_lessons` | 2 | (toml matches) | max lessons per flagged candidate |
| `brain.prompt_event_buffer_max_events` | 20 | (toml matches) | event-buffer cap |
| `stage2.top_n_to_brain` | 10 (raised from 6 on 2026-05-05) | (toml matches) | top-N CoinPackages embedded |
| `stage2.enable_zero_two_contract` | False | (toml matches) | use TRADE_SYSTEM_PROMPT_ZERO_TWO |
| `stage2.enable_full_layer_block` | False | (toml matches) | enable richer per-coin block |

### Hardcoded in TRADE_SYSTEM_PROMPT (cannot be tuned)

- "Return between 2 and 4 trades" (strategist.py:128) — non-zero floor mandate
- "SL should be at least 1.5% from entry" (:134)
- "Hold times: 15-45 min for scalps, up to 60 min for momentum" (:148)
- "Use leverage 3-5x on testnet" (:151) — Rule 11
- "Trending up + fear = strong buy" / "Extreme greed (F&G > 80): take profits on longs, look for short entries" (:90-97)

### Parser defaults (HARDCODED at strategist.py:4729-4736)

- max_positions=4, max_per_coin=1, default_sl_pct=2.0, default_tp_pct=2.5, default_hold_minutes=30, default_leverage=2, trailing_activation_pct=0.5

---

## APEX Settings

Code path: `src/apex/*.py`. Tunable via `[apex]`:

| Name | Default | Role |
|---|---|---|
| `max_position_size_usd` | 1200.0 | static cap |
| `max_leverage` | 5 | leverage cap |
| `min_tias_trades_for_optimization` | 3 | Tier 1 threshold |
| `min_regime_trades_for_fallback` | 10 | Tier 2 threshold |
| `gate_trail_activation_floor_pct_of_tp` | 15.0 | Check 9 |
| `gate_trail_distance_floor_pct` | 40.0 | Check 10 |
| `gate_confidence_floor` | 0.50 | Check 12 size-scale threshold |
| `gate_apex_size_cap_mult` | 1.5 | Check 0 size cap multiplier |
| `apex_size_cap_pct_of_equity` | 0.0 (disabled) | J5 dynamic cap |
| `apex_size_conviction_floor` | 0.5 | min conviction scale |
| `apex_tp_cap_hard_ceiling_pct` | 5.0 | TP cap absolute |
| `apex_min_flip_confidence` | 0.70 | symmetric default for confidence gate |
| `apex_min_flip_confidence_buy_to_sell` | **0.95** | asymmetric Buy→Sell flip floor |
| `apex_min_flip_confidence_sell_to_buy` | **0.70** | asymmetric Sell→Buy flip floor |
| `apex_flip_rr_boost_threshold` | 3.0 | RR threshold for boost |
| `apex_flip_rr_boost_amount` | 0.15 | RR boost amount |
| `apex_min_trades_for_flip` | 5 | insufficient-data flip gate |
| `apex_respect_counter_trade` | True | revert flip if setup is counter |
| `apex_block_flip_resize` | True | block upsize on flip |
| `apex_lock_score_threshold` | 0.0 | composite-score lock floor |
| `apex_lock_regime_weight` | 1.0 | composite-score regime weight |
| `apex_lock_structural_weight` | 1.0 | composite-score structural weight |
| `apex_lock_trade_dir_weight` | 1.0 | composite-score trade direction weight |
| `apex_lock_wr_weight` | 1.0 | composite-score win-rate weight |
| `apex_lock_symbol_evidence_weight` | 1.0 | composite-score symbol-evidence weight |
| `apex_lock_symbol_evidence_wr_floor_pct` | 70.0 | symbol-evidence WR floor |
| `reentry_cooldown_seconds` | 300 | gate Check 6 reentry cooldown |

### Critical asymmetry

- `apex_min_flip_confidence_buy_to_sell = 0.95` vs `apex_min_flip_confidence_sell_to_buy = 0.70`. **The asymmetric flip-confidence gate makes it MUCH HARDER to flip a Buy→Sell than Sell→Buy.** The system is configured to PROTECT BUYS from APEX flipping. Comment at settings.py:2287-2293 explicitly says this is a Buy-protection measure.

### `_cap_mult_map` mismatch (already noted in Phase 1)

- `optimizer.py:319`: `{dead:1.4, low:1.5, medium:1.6, high:1.8, extreme:2.0}`
- `models.py:149-153` (displayed to DeepSeek): `{dead:1.2, low:1.3, medium:1.3, high:1.4, extreme:1.5}`

---

## Regime-Blind Path Catalogue

Where regime is AVAILABLE but NOT USED in entry decisions:

1. **Setup_type classification** (structure_engine.py:1061-1362) — regime not consulted.
2. **SIG_CLASSIFY classifier** (signal_generator.py:393-545) — regime not consulted.
3. **Ensemble combination** (ensemble.py:149-305) — `registry.get_active_for_regime(regime)` called but the function ignores its argument (registry.py:44-53).
4. **Ensemble consensus thresholds** (ensemble.py:261-275) — same bars in every regime.
5. **Ensemble final_size_mult** (ensemble.py:275-293) — same CONSENSUS_SIZE map in every regime.
6. **Strategy weights** — uniform 1.0 across all regimes.
7. **State labeler trigger predicates** — most don't read regime; the eight that do (TREND/RANGE/FUNDING/FEAR/GREED) use it only as a soft confidence haircut, not a hard veto.
8. **Strategist CALL_A direction-decision** — no regime-conditional refusal in code; only LLM-instructional text.
9. **APEX optimizer composite lock** — regime contributes a SIGNAL (+1/-1/0) but is one of five weighted with equal weight 1.0 by default.
10. **APEX gate checks** — none of the 14 gate checks vary by regime.

Where regime IS USED:
1. **Scanner opportunity score** — regime-alignment is one of 6 weighted components (weight 0.13 of 1.00).
2. **Scanner exclusion-mode gate** — `_regime_aligns()` hard-rejects mis-aligned direction in exclusion mode (not the production default).
3. **Strategist user prompt text** — per-coin regime tags rendered to Claude.
4. **APEX optimizer Tier 2 fallback** — regime drives fallback selection.
5. **APEX optimizer composite lock signal** — regime contributes +1/-1.
6. **APEX optimizer RR-boost scope** — only applied when regime NOT in trending/volatile.
7. **APEX optimizer confidence-gate scope** — only applied when regime NOT in trending/volatile.
8. **APEX optimizer Tier 2 message** — regime embedded in pattern_summary text.
9. **State labeler haircut** — multiplies trigger confidence by `regime_haircut` (default 0.5) when regime doesn't match expected.

---

## Hardcoded Value Catalogue (cross-cutting summary)

The Phase 1 reports enumerate every hardcoded literal at file:line. Aggregated counts of decision-gating literals across the entry path:

| File | # hardcoded gates |
|---|---|
| structure_engine.py (classify_setup) | 10 |
| signal_generator.py | 13 |
| signal_models.py | 5 sets (multiple dead) |
| confidence.py | 9 |
| ensemble.py | 11 |
| regime.py | 9 |
| scanner_worker.py | 5 |
| state_labeler.py | 18 |
| strategist.py | 30+ |
| optimizer.py | 25+ |
| gate.py | 17 |
| prompts.py | 8 |

The high count in `state_labeler.py` (18 hardcoded) is the most prominent regime-blind hardcode cluster — the docstring at state_labeler.py:48-51 explicitly notes that a `[scanner.briefing.label_thresholds]` config block was planned but never landed.

## Status

Phase 2 complete. Every threshold, weight, multiplier, and label boundary in the entry pipeline has been catalogued with source location and tunability status. The single most impactful tuning gap is the absence of regime-conditional ensemble weighting, which the Phase 3 data shows is exactly the cell where the system loses money (momentum strategies in non-momentum regimes).
