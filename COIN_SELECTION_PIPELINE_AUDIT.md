# Coin Selection Pipeline — Complete Forensic Audit

How coins are narrowed from the full exchange universe down to the handful the brain trades, every stage, every filter, every factor, every value — proven from the code and cross-checked against the live logs of 2026-06-15. Read-only audit. No code or configuration was changed.

## How to read this audit

This audit traces the selection funnel end to end. Each stage names the code that runs it, the exact factors and thresholds it uses, the current configuration value (read first-hand from config.toml), and the real per-cycle counts proven from the scanner log lines in the 01:44 to 02:44 UTC window of 2026-06-15. Where the live behaviour differs from what the code could do (for example a gate that exists but is dormant in the current mode), that is stated plainly. An audit-findings section at the end lists the flaws, gaps, and calibration concerns the trace surfaced.

The configuration values, the funnel counts, and the candidate-prompt structure were verified by me directly. The internal code line locations of the two scoring formulas and the state labeller were mapped by read-only code-reading agents and are corroborated by the fact that every weight they reported matches config.toml exactly and every count they implied matches the logs.

## The funnel in one view

The live funnel, proven from the logs, has five narrowing stages:

The exchange exposes about 582 linear USDT tickers. The operator watch list narrows that to 50 coins. The Layer 1D scanner scores all 50 and selects 15 as the tradeable set for the cycle. The briefing surfaces the top 5 of those 15 as fully-evidenced candidates to the brain. The brain then opens 2 to 4 trades. A separate, parallel narrowing (the market scanner's 30-coin active subset) feeds the upstream technical-analysis workers but is not the candidate funnel the brain sees.

In the live window: 582 tickers, filtered to 50 watch-list coins, 48 to 50 scored, 15 selected every cycle, 5 briefed, and 2 to 4 traded.

## Stage 0 — The exchange universe to the watch list

The market scanner in src/strategies/scanner.py fetches every linear ticker from the exchange (market_service.get_all_linear_tickers, around line 391) and immediately filters to the operator watch list. The HR-1 filter at lines 402 to 418 keeps only symbols in the watch list together with any symbol that currently has an open position, and emits SCANNER_INPUT.

The watch list is the single source of truth for the universe. It is config.toml [universe].watch_list at lines 1257 onward: 50 coins, described in the file as 12 major coins, 23 mid-caps, and 15 aggressive opportunity hunters, curated for roughly 100 dollars of capital.

Proven in the log: "SCANNER_INPUT | watch_list=50 protected=3 input_set=50 all_tickers=582 filtered=50". The 582 exchange tickers are cut to exactly the 50 watch-list coins (plus open positions, which here were already inside the list).

## Stage 1 — The market scanner's opportunity score and the 30-coin active subset

This stage runs in src/strategies/scanner.py and produces the active subset that the technical-analysis workers operate on. It is parallel to, and distinct from, the briefing selection that feeds the brain.

Each surviving coin is scored on a zero-to-one-hundred opportunity score from five components (lines roughly 451 to 533):

Momentum contributes up to 30 points on a step ladder of the absolute 24-hour change: 10 percent or more scores 30, down through 0.8 percent scoring 10, below that zero. Volatility contributes up to 25 points on the daily range: 8 percent or more scores 25, down to 1.5 percent scoring 10. Trend strength contributes up to 15 points on the ratio of the 24-hour change to the daily range. Volume contributes up to 20 points by dollar volume, from 500 million down to 5 million. Spread contributes up to 10 points, tightest spreads scoring highest. A regime-alignment bonus adds plus 10 for a clean trend, plus 5 for volatile, minus 10 for a dead regime; and a chop penalty subtracts 15 when the daily range is wide but the trend ratio is low. The score is floored at zero.

Hard disqualifiers run first (lines 426 to 437): a coin is dropped if 24-hour volume is below 5 million dollars, if price is below 0.0001, or if the spread exceeds 0.5 percent. The relevant config is config.toml [scanner].min_volume_24h equal to 5,000,000 and max_spread_pct equal to 0.15.

The scored list is sorted and cut to the top max_coins, which is config.toml [scanner].max_coins equal to 30 (line 1096; note the dataclass default in settings.py is 15, but config.toml overrides it to 30). The [universe] comment at line 1238 confirms the intent: all downstream workers operate on this 30-coin active subset.

A hysteresis layer (config.toml [scanner.hysteresis], lines 1108 to 1113) stabilises membership so coins do not flap in and out each scan: a new coin must score at least the cutoff plus 5 points for 2 consecutive scans to enter, and an incumbent must score at most the cutoff minus 5 points for 3 consecutive scans to exit. A coin removed from the active subset cannot re-enter for reentry_cooldown_seconds equal to 600. Coins with open positions are never removed.

## Stage 2 — The Layer 1D briefing: 50 scored to 15 selected

This is the operative narrowing for the brain, and it runs in src/workers/scanner_worker.py in briefing mode (config.toml [scanner].mode equal to "briefing", line 1083). Critically, it scores the full 50-coin watch list directly, not the 30-coin active subset. The live log proves it: "SCANNER_TICK_SUMMARY | watch_list=50 ... scored=50 selected=15 top_n=15 forced_in=3".

Two separate scores are computed for every coin, and the cut to 15 draws from both.

### The opportunity composite score

The first score is the composite opportunity score in _compute_opportunity_score (lines roughly 416 to 486), a weighted sum of six normalised components. The weights are config.toml [scanner.scoring_weights] (lines 1128 to 1134), and they sum to 1.0:

Structure weight 0.27, the X-RAY setup score from 0 to 100 normalised and multiplied by the setup-type confidence. Strategy weight 0.27, the strategy worker total score normalised to zero-to-one. Signal weight 0.13, the signal-worker confidence. Regime weight 0.13, the regime-alignment factor mapped from minus-one-to-plus-one into zero-to-one. Funding weight 0.10, the absolute funding rate saturated at 0.1 percent. Reward-to-risk weight 0.10, the direction-aware reward-to-risk saturated at 3.0. This is the "score" field shown in the candidate blocks and the SCANNER_SELECTED log.

### The interestingness score

The second score is the interestingness score in src/workers/scanner/interestingness.py, computed inside _build_package and emitted as BRIEFING_INTERESTINGNESS. The weights are config.toml [scanner.briefing.interestingness_weights] (lines 1184 to 1191), summing to 1.0:

Cleanness weight 0.20, computed as 0.40 times regime confidence plus 0.30 times setup confidence plus 0.20 times a direction-agreement score plus 0.10 times a sanity bonus (ADX above 20 or choppiness below 60). Confluence weight 0.20, the fraction of directional anchors (consensus, trade direction, signal, the three multi-timeframe biases, funding tilt, regime trend) that point the same way. Extremity weight 0.15, the maximum of the funding extremity, fear-and-greed extremity, range-position extremity, open-interest-change extremity, and volume-surge extremity. Label strength weight 0.20, the primary state-label base weight plus a decayed contribution from secondary labels. Structural quality weight 0.15, computed as 0.45 times the normalised setup score plus 0.35 times the normalised reward-to-risk plus 0.20 times the multi-timeframe score. Multi-timeframe alignment weight 0.07, the aligned-timeframe count over four. Open-position floor weight 0.03, a flat bump for a held coin. This is the "interestingness" field shown first in each candidate block.

### The cut to 15

The selection logic (lines roughly 1529 to 1570) works as follows. Open-position coins are force-included first (the forced list). The remaining budget is top_n_packages minus the forced count, where config.toml [scanner.briefing].top_n_packages equals 15 (line 1174). That budget is filled by reserve_slots_union, which alternates drawing from the coins ranked highest by opportunity score and the coins ranked highest by interestingness, so both scores contribute roughly equally. If fewer than min_briefing_packages (12, line 1175) are selected, it pads to that floor. The result is emitted as SCANNER_RESERVE_SLOTS and the per-coin SCANNER_SELECTED and SCANNER_LABELED lines.

Proven in the log: "SCANNER_RESERVE_SLOTS | budget=12 from_opportunity=6 from_interestingness=6 forced=3 selected=15". The 15 is therefore three open positions plus six coins chosen by the opportunity score and six by the interestingness score.

A recent-loss cooldown is applied before scoring in briefing mode (lines roughly 1435 to 1462): a coin that took a real loss within the apex cooldown (config.toml [apex].reentry_cooldown_seconds equal to 1200) is held out so a fresh coin takes its slot, emitting SCANNER_LOSS_COOLDOWN_EXCLUDED. Open positions bypass this. The log shows "loss_cooldown_excluded=2" on some cycles.

### What the 15 actually contain

The briefing summary reveals the quality of the 15. Proven in the log: "SCANNER_BRIEFING_SUMMARY | ... total=15 with_label=4 advisory_only=3 mean_interestingness=0.674 top_label=TREND_PULLBACK_LONG loss_cooldown_excluded=0". Of the 15 tradeable coins, only about 4 carry a genuine tradeable state label, about 3 are advisory-only (such as recent-loser cooldown), and the remainder carry no actionable label. The mean interestingness of 0.674 is well above the documented top-15 landing point of about 0.45, because the watch list is small and most of it is selected.

## Stage 3 — The 15 to 5 candidate cut to the brain

The brain prompt shows two tiers. The full tradeable set, "TRADEABLE COINS THIS CYCLE (15 coins)", is the 15 selected coins, presented as names the brain may trade from. The fully-evidenced candidates, "TRADE CANDIDATES (5 candidates; full Layer 1B/1C evidence)", are the top 5, ordered by interestingness descending. The cut is config.toml [stage2].top_n_to_brain equal to 5 (line 591), lowered from 10 on 2026-05-31 specifically to shrink the Call-A prompt.

Proven in the captured prompts: every recent Call-A dump shows a 15-coin tradeable list and exactly 5 fully-briefed candidate blocks. The 5 are ordered by interestingness; the highest-interestingness coin is briefed first.

The state labeller in src/workers/scanner/state_labeler.py assigns each coin a primary and secondary labels that feed the label-strength component and appear in the candidate header. There are about 22 labels with fixed base weights, the strongest being the trend-pullback and liquidity-sweep-reversal labels at 0.85, range-fade at 0.65, momentum-burst at 0.55, counter-trade at 0.45, and the advisory labels manipulation-window 0.20, recent-loser-cooldown 0.15, and no-tradeable-state 0.05. Eight directional triggers carry a regime gate that, on a mismatched regime, multiplies confidence by config.toml [scanner.labeller].counter_regime_confidence_haircut equal to 0.5 rather than killing the label outright. The range-fade and funding-fade labels are suppressed on a genuine range break (range_fade_breakout_guard_enabled equal to true), and the extreme-sentiment labels are scaled by the coin's own structural conviction with a floor of 0.35.

## Stage 4 — Package validation

Before a selected coin reaches the brain, its package is validated for completeness in src/core/coin_package_validator.py (called around scanner_worker line 1604). A package below 0.50 completeness is quarantined and dropped (PACKAGE_QUARANTINED); below 0.85 it is kept with a warning; otherwise it passes. Staleness beyond 300 seconds fails the relevant block. The aggregate is PACKAGE_VALIDATE_SUMMARY. In the audited window every selected package validated, so this gate did not cut anyone, but it is the final integrity guard on the data the brain sees.

## Stage 5 — Data freshness gates upstream

The data each coin carries is bounded by freshness windows that, if missed, drop the coin from analysis: klines older than 300 seconds skip technical analysis, a minimum of 50 candles is required, consensus older than 360 seconds is labelled stale, regime recalculates every 600 seconds with a 2-reading confirmation, local price is stale beyond 10 seconds, funding refreshes within the 5-minute window, open interest every 5 minutes, and fear-and-greed hourly. These determine whether a coin's block is fresh or partially blank when it reaches the briefing.

## Audit findings

These are observations the trace surfaced, each framed for the operator's decision, not applied.

First, the qualitative gate is dormant in the live mode. The five-criterion gate in config.toml [scanner.qualitative] (minimum reward-to-risk 1.1, minimum consensus GOOD, regime alignment required, funding blocker, recent-failure blocker, lines 1147 to 1161) exists in code but is bypassed in briefing mode. The live SCANNER_FILTER_AGGREGATE lines show every failure counter at zero with 44 to 48 of 50 coins "qualified," because in briefing mode "qualified" means only that interestingness is above 0.30. The practical effect is that selection is essentially "rank the whole 50-coin watch list and take 15"; the reward-to-risk, consensus, and regime gates that could refuse a coin are not refusing anyone. This is a design choice (the briefing rewrite replaced the gate with a graded preference), but it means the only real narrowing from 50 to 15 is the two ranking scores and the 15-cap, not any quality floor.

Second, the interestingness ranking over-promotes untradeable coins. LINKUSDT was ranked the number-one candidate by interestingness in every captured Call-A prompt (interestingness 0.80) yet had a low opportunity score (about 0.35) and was avoided by the brain every time. The interestingness formula rewards label strength and extremity heavily (0.20 plus 0.15 of the weight), so a coin can rank first on a strong label and an extreme reading while having no tradeable reward-to-risk. Because the top 5 candidates are ordered by interestingness, the most prominent candidate slot is repeatedly spent on a coin the brain cannot trade. This is the same LINK that the exit investigation found to be a dead drifter.

Third, two parallel selection systems narrow on different bases and may diverge. The market scanner in strategies/scanner.py selects a 30-coin active subset by the 0-to-100 opportunity score for the analysis workers, while the Layer 1D scanner in scanner_worker.py selects 15 from the full 50 by the composite and interestingness scores for the brain. If the analysis workers only compute fresh X-RAY, signal, and regime data on the 30-coin subset, then up to 20 watch-list coins the Layer 1D scanner scores could carry stale or partial analysis data. The audit did not prove divergence in the window (most coins passed the X-RAY presence check), but the two-system design is worth confirming, because a coin briefed to the brain with stale analysis would be a silent data-quality gap.

Fourth, forced open positions consume candidate slots. With up to 10 open positions allowed and all force-included ahead of fresh candidates, a busy book crowds the 15. In one audited cycle five forced positions left only ten budget slots, so only ten fresh coins competed for selection. This is intentional (open positions must be visible for management), but it shrinks the fresh-opportunity funnel when the book is full.

Fifth, most of the tradeable 15 are unlabeled or advisory. With about 4 of 15 carrying a tradeable label and about 3 advisory-only, the brain is told it may trade from 15 coins, but roughly half carry no actionable state. The 15-coin "tradeable" framing is therefore wider than the genuinely actionable set, which is closer to the 4 to 5 labeled coins.

## Configuration reference

The selection-funnel values, all current and read from config.toml:

Universe: [universe].watch_list is 50 coins. [scanner].min_volume_24h is 5,000,000, max_coins is 30, max_spread_pct is 0.15, scan_interval_seconds is 300, mode is "briefing", reentry_cooldown_seconds is 600.

Hysteresis: entry_consecutive_scans 2, exit_consecutive_scans 3, entry_threshold_above_min 5, exit_threshold_below_min minus 5.

Opportunity score weights: structure 0.27, strategy 0.27, signal 0.13, regime 0.13, funding 0.10, reward-to-risk 0.10.

Qualitative gate (dormant in briefing mode): min_rr_ratio 1.1, min_consensus GOOD, require_regime_alignment true, funding_blocker_threshold_pct 0.001, recent_failure_blocker_hours 1, max_selection 15.

Briefing: top_n_packages 15, min_briefing_packages 12, qualified_threshold 0.30, prompt_floor_interestingness 0.20.

Interestingness weights: cleanness 0.20, confluence 0.20, extremity 0.15, label_strength 0.20, structural_quality 0.15, mtf_alignment 0.07, open_position_floor 0.03.

State labeller: counter_regime_confidence_haircut 0.5, extreme_sentiment_conviction_floor 0.35, extreme_sentiment_offtrend_haircut true, range_fade_breakout_guard_enabled true.

Final cut and trading: [stage2].top_n_to_brain 5, max open positions 10, apex recent-loss reentry_cooldown_seconds 1200.

## Limitations

The internal code line numbers for the two scoring formulas and the state labeller were mapped by read-only code-reading rather than line-by-line by me personally; they are corroborated because every weight and threshold reported matches config.toml exactly and every funnel count matches the live logs. The divergence-between-the-two-scanners finding is a hypothesis the logs did not fully settle; confirming whether the Layer 1D scanner ever briefs a coin with stale analysis would require correlating each briefed coin's X-RAY timestamp against the active-subset membership, which the current logs do not directly expose. The funnel counts are from the 01:44 to 02:44 UTC window of 2026-06-15 and the captured Call-A prompts of the same day; other market conditions could change the per-stage survivor counts but not the mechanism.
