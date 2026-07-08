# Phase 1.8 — Historical Performance Validation

Spec lines 490-500: query `data/trading.db trade_log` over multiple windows. Compare to prior report's 7-day numbers.

## DB queries (bybit_demo only)

### 7-day window

| direction | count | total PnL | WR |
|---|---:|---:|---:|
| Buy | 93 | +$87.91 | 45.2% |
| Sell | 392 | +$411.56 | 52.8% |

Matches prior report exactly.

### 14-day window

| direction | count | total PnL | WR |
|---|---:|---:|---:|
| Buy | 122 | +$106.51 | **41.8%** |
| Sell | 681 | +$366.16 | **42.4%** |

**Critical finding: over 14 days, BOTH directions are below 50% WR.** Sell WR drops from 52.8% (7d) to 42.4% (14d). Buy WR drops from 45.2% (7d) to 41.8% (14d). Both losing on average.

### All-time

Identical to 14-day numbers (the system has only been running ~10 days; earliest trade 2026-05-09 13:06:05). Total trades: 803. Total PnL: $472.67.

## What this means

The 7-day window was anomalously good for Sells. The 14-day view shows the system is essentially break-even, with Sell WR (42.4%) only marginally better than Buy WR (41.8%).

Per-trade economics:
- Total PnL: $472.67 over 803 trades = $0.59 per trade average.
- Per-direction:
  - Buy: 122 × $0.87 = $106.51 per trade $0.87 avg.
  - Sell: 681 × $0.54 = $366.16 per trade $0.54 avg.
- Buy trades have HIGHER per-trade average PnL ($0.87 vs $0.54) but lower count (122 vs 681), so total Sell PnL exceeds Buy total.

The "Sell is winning" narrative is sample-size driven, not edge-driven. A Sell-bias system trades MORE Sells, so even with similar per-trade economics, total Sell PnL dominates by sheer volume.

## Discrepancy vs prior report

Prior report cited "Sell trades are marginally profitable, Buy trades are below break-even" using 7-day numbers (Buy WR 45.2% < 50%, Sell WR 52.8% > 50%). This is correct for 7-day.

But over 14 days, the conclusion changes: **both directions are below break-even.** The 7-day window is not representative of the system's longer-term performance. The prior report did not surface this — it implicitly accepted the 7-day numbers as the baseline.

## Daily breakdown (last 5 days)

| Day | Buy n | Buy WR | Buy PnL | Sell n | Sell WR | Sell PnL |
|---|---:|---:|---:|---:|---:|---:|
| 2026-05-14 | 30 | 53.3% | -$11.68 | 33 | 39.4% | -$122.70 |
| 2026-05-15 | 17 | 47.1% | +$29.35 | 41 | 51.2% | +$75.07 |
| 2026-05-16 | 7 | 85.7% | +$5.09 | 81 | 44.4% | -$15.67 |
| 2026-05-17 | 14 | 28.6% | -$28.07 | 43 | 55.8% | +$42.99 |
| 2026-05-18 | 9 | 33.3% | -$6.51 | 85 | 58.8% | +$146.92 |

Variance is huge day-to-day:
- 2026-05-14: Both directions lost; Sells lost $122.70 in 33 trades.
- 2026-05-16: Buy WR 85.7% but only 7 trades (tiny sample, not significant).
- 2026-05-18: Strong Sell day (+$146.92, WR 58.8%) which drove the 7-day Sell WR up.

The 5-17 and 5-18 days dominate the 7-day Sell narrative. Earlier days (5-14, 5-16) show Sells underperforming.

## Verdict

- 7-day claims accurate (matches DB).
- 14-day reveals both directions below break-even — a load-bearing finding NOT in the prior report.
- The system is essentially break-even on bybit_demo over 14 days ($472.67 total / 803 trades).
- "Sell bias is profitable" is true only over a cherry-picked 7-day window.

## Implications for fix path

- **Concern 8 (bias might be correct) is weakened**, not strengthened. The 14-day numbers show both directions losing on average. The system isn't "responding correctly to a bearish market" — it's making low-conviction trades on both sides, with the high Sell volume coincidentally winning more days than Buy.
- **No direction has structural edge**. Fixing the asymmetric prompt and the counter-multiplier (Issues 4 + 2) may help in two ways: (a) more Buy trades = more sample = lower-variance Buy WR; (b) symmetric prompt = less wasted Sell trades when Buy is the better setup.
- **The expected PnL improvement is modest**. If the system is break-even at scale, fixes that rebalance direction shouldn't add or subtract much expected value. They primarily reduce variance and align with the operator's design directive (asymmetry from data, not code).
- **The Buy underperformance (41.8% WR over 14d) is suspicious**. Per the prior report Issue 2 analysis, Buy trades are being suppressed at the size/sizing layer. The low WR could be because the surviving Buys are the lowest-conviction subset (Issue 2 conviction-weight cut at gate). Fixing Issue 2 (removing or splitting the ×0.7 multiplier) could improve Buy WR by letting higher-quality Buys through unsuppressed.
- **Concern 5 (ship Issue 4 alone, measure) remains the safest first move.** If Issue 4 alone shifts brain to 70-85% Sell and Buy WR stays at 41-45% or rises, Issue 2/3 are worth shipping. If Buy WR drops below 40%, revert and reconsider.
