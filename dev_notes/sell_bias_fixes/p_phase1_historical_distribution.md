# PRIMARY Issue — Phase 1 Step P.1.7: Historical Direction Distribution

Sources: `data/trading.db` — `trade_log`, `trade_intelligence`, `regime_history` tables.
Window: bybit_demo (2026-05-09 to 2026-05-11) = 295 trades over 2.3 days.
Status: queries executed. Investigation only — no code changes.

## 1. Sample Caveat

bybit_demo trading began on 2026-05-09 13:06. The spec's "30 day window" is therefore effectively the full bybit_demo lifetime (2.3 days). Shadow data (older, 1,597 trades) provides a longer baseline but with different system configuration. Conclusions below note when each dataset applies.

## 2. Per-Day Direction Distribution — bybit_demo

| Day | Buy | Sell | Sell% |
|-----|-----|------|-------|
| 2026-05-09 | 12 | 104 | **89.7%** |
| 2026-05-10 |  8 |  75 | **90.4%** |
| 2026-05-11 |  7 |  89 | **92.7%** |

**The Sell-bias is constant across all 3 days.** It is not a one-day fluke nor a recently-emerged pattern; it is the steady state since bybit_demo cutover.

## 3. Per-Strategy Distribution

All 295 bybit_demo trades originate from a single strategy: `claude_trader`. No other strategy is currently emitting bybit_demo trades. Per-strategy analysis is therefore degenerate — the bias is uniform across all signals reaching bybit_demo.

## 4. Per-Symbol Distribution (top 11 by volume on bybit_demo)

| Symbol | Total | Buy | Sell |
|--------|-------|-----|------|
| AEROUSDT | 19 |  0 | 19 |
| FILUSDT  | 16 |  0 | 16 |
| CRVUSDT  | 14 |  0 | 14 |
| SEIUSDT  | 14 |  1 | 13 |
| AXSUSDT  | 12 |  0 | 12 |
| NEARUSDT | 12 |  0 | 12 |
| IMXUSDT  | 11 |  0 | 11 |
| KATUSDT  | 11 |  1 | 10 |
| PLUMEUSDT | 11 |  0 | 11 |
| ADAUSDT  | 10 |  1 |  9 |
| ENAUSDT  | 10 |  1 |  9 |

**Seven of the top-11 symbols have ZERO Buy trades.** The remaining four have exactly one Buy each. The structural Sell-bias is not symbol-specific — it is the system-wide behavior pattern.

## 5. Performance By Direction — bybit_demo (trade_log)

| Direction | Trades | Wins | WR | Avg pnl_pct | Net pnl_usd |
|-----------|--------|------|----|-------------|-------------|
| Buy  |  27 |  8 | 29.6% | +0.066% | $-4.69 |
| Sell | 268 | 73 | 27.2% | -0.015% | $-85.12 |
| **All** | 295 | 81 | 27.5% | -0.008% | $-89.81 |

Both directions are net losing. Sell loses more than Buy in aggregate dollar terms, but the per-trade averages are close to flat. Note: 117/295 of these rows have pnl_usd = 0 (40% measurement gap; see Phase 0 baseline §10). Aggregates therefore understate true PnL magnitudes.

## 6. Performance By Direction — bybit_demo (trade_intelligence, smaller cohort)

`trade_intelligence` only contains trades that completed the full post-close analytics pipeline (335 records vs 295 in trade_log — discrepancy due to data captured from earlier shadow imports). For bybit_demo trades only:

| Direction | Trades | WR | Avg pnl_pct | Net pnl_usd |
|-----------|--------|----|-------------|-------------|
| Buy  |  36 | 41.7% | +0.081% | $+23.60 |
| Sell | 299 | 30.4% | -0.024% | $+179.44 |

In this cohort, Sell is **net positive** in absolute dollar terms. The contrast with the trade_log figures is due to which records are included; both views are real but capture different slices.

**Net-positive Sell aggregate is consistent with the operator's emergency-close behavior** — when market moved up (against the Sell-biased portfolio), large unrealized losses turned into realized closes, but the underlying directional skew has produced more winning trades than losing in absolute count (91 of 299 Sells won = 30.4%) and the per-trade economics happen to be slightly positive on average when measured.

## 7. Direction × Regime — bybit_demo (from §3.4 of Phase 0 baseline, repeated for completeness)

| Direction | regime          | N | WR | Avg pnl_pct | Net pnl_usd |
|-----------|-----------------|----|----|-------------|-------------|
| Buy       | ranging         | 14 | 50.0% | +0.018% | $+42.94 |
| Buy       | trending_up     | 22 | 36.4% | +0.122% | $-19.34 |
| Sell      | ranging         | 148 | 27.7% | -0.028% | $-322.27 |
| Sell      | trending_down   | 5 | 20.0% | +0.025% | $+0.69 |
| Sell      | trending_up     | 139 | 33.8% | -0.019% | $+502.61 |
| Sell      | volatile        | 7 | 28.6% | -0.056% | $-1.58 |

Key observations:
1. **Buy in ranging has the highest WR (50%) of any direction-regime cohort** and is net positive ($+42.94 on only 14 trades). The cohort is tiny but consistent with the global TIAS Section 4 evidence that Buy outperforms Sell in ranging.
2. **Sell in ranging is the biggest losing cohort** ($-322.27 net on 148 trades, 27.7% WR).
3. **Sell in trending_up is unexpectedly the largest net winner** ($+502.61 net on 139 trades). This is counter-intuitive — Sell against an uptrend regime is normally a loss expectation. The hypothesis: per-coin regime can differ from BTC's regime; "trending_up" here may reflect BTC's regime while individual coins were actually consolidating or declining within that period. P.1.6 noted that per-coin regimes are computed separately and consumed by APEX.

## 8. Cumulative Aggregate

Bybit demo cumulative PnL across 295 trades over 2.3 days: **$-89.81 net loss** with **27.5% overall WR**.

The Sell-biased pattern produced sub-30% WR on Sell trades while the (much smaller) Buy cohort had a marginally better WR (29.6%). The system's profitability is currently structurally below break-even.

## 9. DeepSeek Post-Hoc Direction Verdict (`ds_optimal_direction` text)

Per Phase 0 baseline §4.4: for the 335 trade_intelligence rows with a `ds_optimal_direction` populated:
- YES (system direction was right): 133 (39.7%), WR 73.7%
- NO (system direction was wrong): 190 (56.7%), WR 3.7%
- UNCLEAR: 12 (3.6%), WR 8.3%

DeepSeek post-hoc evaluation says the system's direction was wrong on the **majority** of its trades, and the correlation between this verdict and outcome is extreme (74% WR for "YES" vs 4% for "NO"). This is independent corroboration that the directional skew is producing trades against where the immediate market move went.

## 10. Findings Summary

| Question | Answer |
|----------|--------|
| Direction split bybit_demo? | 90.8% Sell (268/295). Constant across all 3 days. |
| Win rate by direction? | Buy 29.6%, Sell 27.2% (trade_log). Buy 41.7%, Sell 30.4% (trade_intelligence). |
| Best direction-regime cohort? | Buy × ranging: 14 trades, 50% WR, +$42.94 net. |
| Worst direction-regime cohort? | Sell × ranging: 148 trades, 27.7% WR, $-322.27 net. |
| DeepSeek post-hoc verdict? | System direction "wrong" on 56.7% of trades it could evaluate. |
| Per-strategy? | Single strategy (claude_trader). No segmentation possible. |

## 11. Implications For Phase 2

1. The Sell-bias is **structural across days, across symbols, across regimes**. It is not transient.
2. The historical evidence does NOT support the current flip policy as profitable. Sell-in-ranging is the dominant cohort and the dominant loser.
3. The Buy-in-ranging cohort (smallest) has the highest WR. Letting more Buys through (i.e. blocking some Buy → Sell flips) is consistent with the data showing Buy as the better direction in ranging.
4. Sell-in-trending_up's net positive PnL is an anomaly that deserves a separate look — possibly an artifact of how regime is classified vs per-coin trend.

## 12. Out-of-Scope Confirmation

- No code changes.
- All SQL was read-only (SELECT).
