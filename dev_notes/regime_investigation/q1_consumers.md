# Q1 Step 1.4 — Regime Consumer Matrix

Grepped `src/` and `tests/` for every read of regime label. Below is the consumer matrix, partitioned by how the regime label flows into trading decisions.

## Trade-decision-path consumers (control flow)

These consumers cause regime to influence the eventual order placement:

| File:Line | Consumer | Read method | Gate type | Effect |
|---|---|---|---|---|
| `src/strategies/ensemble.py:57` | `EnsembleVoter.vote` | `registry.get_active_for_regime(regime.regime)` | **Strategy category gate** | Selects which strategies vote (e.g., RANGING activates `mean_reversion` + `funding_arb`; TRENDING_UP/DOWN activate `momentum` + `predatory`) |
| `src/strategies/scorer.py:255` | `TradeScorer.score` | `signal.strategy_category in regime.active_strategy_categories` | **Filter** | Drops signals whose category isn't in the regime's active list |
| `src/strategies/scanner.py:516-527` | `MarketScanner._score_coin` | `self.regime_detector.get_coin_regime(symbol)` | **Score bonus** | +10 trending, +5 volatile, -10 dead added to per-symbol score |
| `src/strategies/smart_leverage.py:68-70` | `SmartLeverage` | `regime.regime == MarketRegime.VOLATILE / DEAD` | **Leverage multiplier** | Reduces leverage in volatile/dead regimes |
| `src/brain/strategist.py:989,996` | `_build_market_data` (Stage 2 CALL_A) | `regime_detector.get_last_regime()` | **Prompt injection** | Global regime injected into market_data section as `_regime_str` |
| `src/brain/strategist.py:1139-1141,1179-1181` | `_build_market_data` (CALL_A per-coin tags) | `_rd.get_coin_regime(symbol)` | **Prompt injection** | Per-coin `[REGIME_UPPER conf%]` tag attached to each coin in the prompt |
| `src/brain/strategist.py:1870` | `_build_market_data` (CALL_B reuse) | `pkg.price_data.regime` (from CoinPackage) | **Prompt injection** | Regime string interpolated into setup-review prompt |
| `src/brain/strategist.py:2286-2317` | `_build_setup_review_prompt` | `regime_detector.get_coin_regime(pkg.symbol)` | **Prompt injection** | Per-coin regime injected into setup review |
| `src/brain/strategist.py:2468,2475` | `_build_setup_review_prompt` (fallback) | `regime_detector.get_last_regime()` | **Prompt injection** | Global regime fallback |
| `src/brain/strategist.py:2746,2748,2788-2790,3482` | various brain prompt slots | `_rd.get_coin_regime(_sym)` | **Prompt injection** | Per-coin regime in additional prompt slots |
| `src/brain/brain_v2.py:201` | `BrainV2.evaluate_setup` | `regime.regime.value` | **Prompt injection** | Regime label fed into v2 evaluator |
| `src/apex/optimizer.py:1033-1079` | `_check_direction_lock` | regime string from setup | **Direction lock** | Locks direction when regime is `trending_up`/`trending_down`; partially locks `volatile`; does NOT lock `ranging`/`dead`/`unknown` |
| `src/risk/layer4_protection.py:251` | `Layer4ProtectionService` | `self.regime_detector.get_coin_regime(symbol)` | **Defensive gate** | Layer 4 protection adjusts behavior based on regime |
| `src/tias/collector.py:282-300` | TIAS Collector | `regime_detector.get_coin_regime(symbol)` then `get_last_regime()` | **Telemetry / package field** | Writes regime string into `TradeIntelligencePackage.price_data.regime`; consumed downstream by strategist, validators |

## Validation-path consumer

| File:Line | Consumer | Read method | Gate type | Effect |
|---|---|---|---|---|
| `src/core/coin_package_validator.py:33,168-169` | `CoinPackageValidator` | `pkg.price_data.regime` | **Completeness check** | Empty regime contributes to a missing-data score; affects whether a package is "complete enough" to forward |

## Analysis-path consumer

| File:Line | Consumer | Read method | Gate type | Effect |
|---|---|---|---|---|
| `src/analysis/volatility_profile.py:241-243` | Volatility profile | `self._regime_detector.get_coin_regime(symbol)` | **Profile branch** | Different volatility profile per regime |
| `src/core/rule_engine.py:136` | `RuleEngine` | `regime_state.regime.value` | **Rule branch** | Per-regime rule application |

## Telemetry / display consumers (do not affect decisions)

| File:Line | Consumer | Use |
|---|---|---|
| `src/telegram/bot.py:100,573` | Telegram bot | `/regime` command + market regime line in context briefing |
| `src/telegram/features/morning_briefing.py:40` | Morning briefing | Regime in user-facing brief |
| `src/telegram/handlers/analysis.py:87,90` | `/regime` handler | Regime display + active categories |
| `src/database/repositories/backtest_repo.py:56` | Backtest writer | Regime column in `backtest_trades` table |

## XRAY direction flip path — does it use regime?

**No, not directly.** `src/workers/strategy_worker.py:1604-1779` (`_execute_claude_trade` XRAY block) reads `_apex_locked` and `_apex_lock_reason` from the trade dict (set by APEX) plus `_structural.setup_quality`, `_structural.market_structure.structure`, `_sp.rr_long`, `_sp.rr_short`. The regime label is NOT a direct branch input.

However, regime affects XRAY indirectly via:

1. `_apex_locked` is set true at APEX when regime is `trending_up`/`trending_down` (and partially in `volatile`). If the detector mislabels a trending coin as ranging, `_apex_locked = N`, allowing XRAY to flip the direction based on structural R:R.
2. The same regime label propagated into Stage 2 prompts may have already biased the brain's `Buy`/`Sell` decision before XRAY ever sees the trade.

This is the Q1b causation chain to be traced in `q1b_flip_causation.md`.

## Summary

- **15 control-flow consumers** of regime (strategies, scorer, scanner, leverage, strategist, brain_v2, APEX direction lock, Layer 4 protection, TIAS).
- **1 validation consumer** (coin_package_validator).
- **2 analysis consumers** (volatility_profile, rule_engine).
- **5 display/telemetry consumers** (telegram, backtest_repo).
- **0 direct consumers** in XRAY flip path; regime affects XRAY only indirectly via the APEX direction lock and via brain's pre-existing decision.

The control-flow consumers most relevant to sell-bias are:

- **APEX direction lock** (`src/apex/optimizer.py:1033-1079`): only fires for trending regimes. False-ranging label means the Buy is NOT locked, so XRAY can flip it.
- **Stage 2 strategist prompt** (`src/brain/strategist.py:1139-1181`): regime injected as `[RANGING 40%]` tag for each coin. Brain prompted with "ranging" tag may favor mean-reversion/Sell decisions.
- **Ensemble category gate** (`src/strategies/ensemble.py:57`): `mean_reversion` + `funding_arb` active in ranging vs `momentum` + `predatory` active in trending. False-ranging selects different strategies.
- **Scanner score bonus** (`src/strategies/scanner.py:516-527`): trending coins get +10 score boost. False-ranging strips that boost.
