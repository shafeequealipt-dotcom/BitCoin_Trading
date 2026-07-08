# Q2 Step 2.6 — Trade-Outcome Correlation

## Method

Pulled `trade_history` rows from `data/trading.db` for the last 24 hours and split by side. Compared per-side win rate and PnL.

## 24-hour outcome summary

| Side | Trades | Avg PnL | Total PnL | Wins | Win rate |
|---|---|---|---|---|---|
| Buy | 9 | -$1.36 | -$12.21 | 4 | 44.4% |
| Sell | 110 | +$0.42 | +$45.70 | 51 | 46.4% |
| Total | 119 | +$0.28 | +$33.49 | 55 | 46.2% |

## Observations

- **Sell volume is ~12x Buy volume** (110 vs 9). Consistent with the 91.7% sell-share of executed trades.
- **Win rates are nearly identical** (44.4% Buy vs 46.4% Sell). The detector or XRAY layer is not making more accurate side calls when going Sell vs Buy — both sides have similar per-trade win probability.
- **Per-trade PnL is negative for Buy and positive for Sell** (-$1.36 vs +$0.42). The system loses on average when it goes Buy and wins on average when it goes Sell. This is the window's bearish-drift contribution: a Buy in a falling market is more often closed for loss than gain.
- **Aggregate PnL is positive** (+$33.49). The system is profitable in the window despite the heavy Sell-bias and despite the regime mis-classification.

## Critical caveat

The trade-outcome data is **window-specific to a bearish market drift**. In a bullish market, the per-side PnL would invert: Buy would be profitable, Sell would be the losing side. The system's Sell-bias would become harmful.

This is precisely the operator's concern. A system that has 91.7% sell-share is operating as a one-sided directional bet. If the market reverses, the sell-bias becomes the wrong directional bet, and aggregate PnL flips negative.

## Correlation between regime accuracy and outcome

For samples where a trade was executed within ±15 minutes of the regime classification (cross-reference `trade_history.entry_time` with sample timestamp), we cannot reliably establish a robust correlation due to:

1. Sample size: only ~5-10 of the 96 regime samples had a trade executed within the window for the same symbol.
2. The `trade_history` row stores `side` (final direction) but not whether XRAY flipped, so we cannot directly tie outcome to a specific regime mistake.

A more rigorous regime-vs-outcome correlation requires the upcoming verification phase (Phase 5) where post-deploy metrics are compared to baseline. For now, we note:

- Bearish-window aggregate PnL is positive — the sell-bias is not currently destroying capital.
- Per-trade Buy PnL is negative — the rare Buys that escape XRAY's flip are not, on average, the high-quality contrarian wins one would hope for.
- The directional bias is window-favorable but structurally fragile.

## What this means for path selection

- **Path A (XRAY threshold tune)** would likely reduce the Sell count and increase the Buy count modestly. If executed during a bearish drift, this would mean more losing Buys (current Buy avg PnL is negative). Aggregate PnL could initially worsen. Path A's value is mostly insurance: when the market reverses, the system has more Buy exposure to capture the move.
- **Path B (detector fix)** addresses the upstream cause. If implemented cleanly, the detector's labels become informative again, ensemble category gating works as intended, APEX direction lock fires when it should. The downstream effects ripple through every consumer.
- **Path C (hybrid)** does Path B first, then evaluates Path A.

The outcome-correlation analysis does not strongly favor one path over another; it does emphasize that any change must be verified in production with several days of monitoring (Phase 5), because the current "profitable in a bear window" state could be deceptive.
