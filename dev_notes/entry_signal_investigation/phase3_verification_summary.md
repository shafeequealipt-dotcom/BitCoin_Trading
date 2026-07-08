# Phase 3 — Verification Summary

Five correlation claims from the prior analysis were independently verified against the DB and logs over the 2026-05-20 05:46 → 2026-05-21 12:40 window (225 closed bybit_demo trades in `trade_intelligence`).

## Verdicts At A Glance

| Claim | Headline | Verdict |
|---|---|---|
| 1 | Buy-side broken in every regime; volatile+Buy worst cell | **PARTIALLY reproduced.** Buy/Sell headline reproduces (Buy 55.3% L vs Sell 46.7% L). The "volatile+Buy is the worst cell at 79% L" CONTRADICTED — DB shows volatile+Buy at 51% L and +$135 net. Buy weakness is concentrated in `trending_up + Buy` and `ranging + Buy`, NOT in volatile. |
| 2 | bullish_structural_break × volatile is heavily negative | **PARTIALLY reproduced.** The cell exists and lost 100% in this window (3 trades, −$120). Sample is smaller than prior claim (3 not 8). Broader claim that `bullish_structural_break` overall is a losing setup REPRODUCES (14 trades total, −$200 net, 71% loss). The narrative "volatile is bad" does NOT generalize — `bullish_fvg_ob × volatile` is the BEST cell (+$184). |
| 3 | Ensemble herding inversely correlated with outcome | **FULLY reproduced.** ≤4 supporters: 107 trades, 44% loss, +$300 net. 5+ supporters: combined negative or barely positive. The system sizes UP on the cohort where edge is LOWER. WEAK consensus outperforms STRONG and GOOD on per-trade basis. |
| 4 | strong_buy label mis-calibrated | **REPRODUCED on loss rate, REFINED on PnL.** `strong_buy` loss% = 54.9%, `buy` loss% = 44.4% (10.5-point gap confirms threshold mis-calibration). But `strong_buy` net = **+$199** (positive) while `buy` net = +$4 (flat). The mis-calibration is about per-win magnitude, not edge direction. |
| 5 | Momentum strategies net-negative; contrarian/event-driven positive | **FULLY reproduced.** All 8 momentum/trend/breakout strategies (A4, A2, A3, B1, B3, B4, F2, H4) net-negative. All 4 contrarian/event-driven (G1, G4, I4, B2) net-positive. Per-strategy numbers within $5 of prior. Ensemble weights all uniform at 1.0 by default. |

## Net Pattern (My Refined Story From The Verified Data)

1. **The Buy/Sell directional asymmetry is real but regime-mediated.** Buy fails in `trending_up` and `ranging`. Buy succeeds in `volatile` and (slightly) `trending_down`. Sell succeeds in `trending_up` and `ranging`. Sell fails in `volatile` and `trending_down`. The losing cells are: `trending_up+Buy` (−$77), `ranging+Buy` (−$59), `dead+Buy` (−$8), `trending_up+Sell` is +$14 winner, `volatile+Buy` is +$135 winner. The prior claim oversimplified "Buy is broken everywhere"; the DB-verified picture is regime-specific.

2. **Setup quality matters more than regime.** `bullish_fvg_ob` works in 4 of 5 regimes; `bullish_structural_break` fails in all sampled regimes. The bug suspect is the bullish_structural_break entry criterion (`last_bos.direction == "bullish"` AND `direction == "long"` at structure_engine.py:1272-1294) which fires when a breakout has already played out.

3. **Consensus is anti-predictive above 5 supporters.** WEAK (40% loss) outperforms GOOD (58% loss) and STRONG (49% loss). The ensemble's sizing function `CONSENSUS_SIZE[STRONG]=1.0 vs WEAK=0.30` actively amplifies the worst sub-population.

4. **Strong_buy label has 10-point higher loss rate than buy** but bigger per-win magnitude makes it net positive. The classifier threshold (`strong=0.55` vs `buy=0.18`) is asymmetric and BUY-biased by deliberate calibration (settings.py:3034-3038 comment). The label is used by downstream consumers as a proxy for conviction, but the underlying edge does not justify that confidence.

5. **The momentum/trend strategy family produces negative expectancy in this period.** Eight of the 36 strategies are net-negative on supported trades. Four are net-positive. The ensemble weights all 36 equally at boot. Even the Optimizer's adjusted weights (clamped [0.1, 3.0], registry.py:100) cannot down-weight a strategy to zero, and there is no regime-conditional weighting at all.

## Discrepancies & Data-Persistence Findings

These are not failures of the prior analysis or this verification; they are systemic data-collection gaps the operator should know about:

1. **`ensemble_votes` DB table is dead schema.** Declared at migrations.py:434-446; never written to. Per-trade per-strategy votes live ONLY in the log file `STRAT_VOTE_TRACE` event, which itself only fires when consensus=="STRONG". Non-STRONG-consensus trades have no per-strategy vote record anywhere.

2. **`trade_intelligence.ensemble_votes` JSON column is durably NULL.** All 2,345 rows. Designed to hold per-trade JSON-encoded votes; never populated.

3. **`brain_decisions` table is empty.** The active strategist-call record is `claude_decisions` (2,888 rows).

4. **`signals` DB table is partially broken in recent days.** Window data has only `buy` and `neutral` labels persisted; `sell`, `strong_buy`, `strong_sell` are absent. Logs for the same window show 1,522 strong_buy + 2,219 buy + 359 neutral. The DB persistence is dropping ~6,200 of ~10,500 log-emitted SIG_CLASSIFY events.

5. **`entry_score` column is durably zero.** All 230 in-window trade_intelligence rows have entry_score=0 (uninitialized).

6. **`coin_regime_history` restore loses `volume_ratio` and `atr_percentile`.** Persisted schema carries only regime/confidence/adx/choppiness; restore fabricates volume_ratio=1.0 and atr_percentile=0 (regime_worker.py:107-117). Trend_direction is heuristically derived from regime *string*.

7. **`cycle_metrics` columns `signal_buy_pct / sell_pct / neutral_pct / xray_setup_type_count / regime_distribution_json`** are durably NULL. The per-cycle aggregator that was supposed to populate them was a "wired in follow-up commit" promise that never landed.

8. **The prior log-based analysis had a 34% "regime=?" sampling bias** because it matched `REGIME_PERCOIN` events within ±30 s of `BRAIN_DO_TRADE` and missed any trades whose REGIME_PERCOIN event was outside that window. Those missed-regime trades happened to be 80 of 234 in the sample and were net-positive (+$343). Excluding them made the regime-tagged subset look worse than the population. The DB's `entry_regime` is the authoritative source.

## Sample Sizes

| Verification | Window trades | With needed feature | Coverage |
|---|---|---|---|
| Claim 1 (regime × side) | 225 | 222 (entry_regime populated) | 99% |
| Claim 2 (setup × regime) | 225 | 145 (STRUCT_GUARD tag) | 64% |
| Claim 3 (herding) | 225 | 212 (STRAT_VOTE_TRACE) | 94% |
| Claim 4 (SIG label) | 225 | 221 (SIG_CLASSIFY log) | 98% |
| Claim 5 (per-strategy) | 225 | 212 (STRAT_VOTE_TRACE) | 94% |

Coverage is strong for claims 1, 3, 4, 5. Claim 2's coverage is weaker because STRUCT_GUARD only fires DURING the hold — trades that close very quickly or trades that take the no-time-decay path may not have a STRUCT_GUARD line. The 64% coverage still gives a solid sample for the headline.

## Status

All five claims verified. Three reproduce in full (3, 5, headline of 4). Two require refinement (1's volatile+Buy sub-cell, 2's sample size). One systemic discovery (signals DB persistence gap) was made during verification and is logged for Phase 8 synthesis.
