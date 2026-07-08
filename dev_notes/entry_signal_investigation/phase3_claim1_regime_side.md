# Phase 3 — Claim 1 Verification: Regime × Side × Outcome

## The Claim (from Part A.1 of the protocol)

> Buy-side is broken in every regime. 57% loss rate on Buys vs 46% on Sells across 234 trades. Even in trending_up regime, Buys lose ~73% of the time.

Specific sub-claim numbers from the prior analysis output:

- volatile + Buy: 19 trades, 15 losses, 78.9% loss rate, net −$53.70
- trending_up + Buy: 11 trades, 8 losses, 72.7% loss rate, net −$4.24
- ranging + Buy: 25 trades, 60.0% loss rate, net −$41.86
- volatile + Sell: 60 trades, 56.7% loss rate, net −$126.28

## Method

Read-only SELECT against `trade_intelligence` for the analysis window 2026-05-20 05:46 → 2026-05-21 12:40. The DB has a dedicated `entry_regime` column (regime at entry time) which is the authoritative source.

Database is the truth source. The prior analysis sampled `REGIME_PERCOIN` events from the log files within a ±30 s window around `BRAIN_DO_TRADE`, which under-sampled the regime tag (80 of 234 prior-analysis trades had a "?" regime bucket because no matching log event was found in that window).

## Reconciliation Of Sample Size

- DB trades in window with `exchange_mode='bybit_demo'`: **225**.
- DB trades in window with `entry_regime` populated: **222** (3 missing).
- Prior analysis trade count: **234** (cross-source: workers + brain + general logs). Difference (12 trades) is shadow-mode trades or trades whose persistence rows skipped `trade_intelligence` — to be reviewed in Phase 0/8 if material; not material for the per-cell comparison.

## Result — DB-Verified Regime × Direction (bybit_demo, window)

| entry_regime | direction | n | L | W | loss% | net USD |
|---|---|---|---|---|---|---|
| dead | Buy | 5 | 3 | 2 | 60.0% | −7.76 |
| ranging | Buy | 39 | 22 | 17 | 56.4% | **−58.99** |
| ranging | Sell | 39 | 17 | 22 | 43.6% | −12.77 |
| trending_down | Buy | 1 | 1 | 0 | 100.0% | −3.08 |
| trending_down | Sell | 2 | 1 | 1 | 50.0% | −0.50 |
| trending_up | Buy | 17 | 11 | 6 | **64.7%** | **−76.59** |
| trending_up | Sell | 32 | 17 | 15 | 53.1% | +13.80 |
| volatile | Buy | 39 | 20 | 19 | **51.3%** | **+135.36** |
| volatile | Sell | 48 | 21 | 27 | 43.8% | +99.70 |

Overall window totals (all entry_regime, all directions):

| direction | n | L | W | loss% | net USD |
|---|---|---|---|---|---|
| Buy | 103 | 57 | 46 | **55.3%** | +115.59 |
| Sell | 122 | 57 | 65 | **46.7%** | +78.73 |

## Verification Result

| Sub-claim | Prior analysis | DB-verified | Verdict |
|---|---|---|---|
| Buy loss% headline | 57% | 55.3% | **Reproduced** (within 2 points) |
| Sell loss% headline | 46% | 46.7% | **Reproduced** |
| volatile + Buy loss% | 78.9% | 51.3% | **CONTRADICTED** |
| volatile + Buy net | −$53.70 | **+$135.36** | **CONTRADICTED** |
| trending_up + Buy loss% | 72.7% | 64.7% | **Partially reproduced** (high loss rate but milder) |
| trending_up + Buy net | −$4.24 | −$76.59 | **Bigger loss than claimed** |
| ranging + Buy loss% | 60.0% | 56.4% | **Reproduced** |

The headline "Buy-side worse than Sell-side" reproduces. **The single most damning per-cell claim — "volatile + Buy is a 79% loss rate disaster" — does NOT reproduce.** DB-verified, volatile + Buy in this window was actually a 51% loss-rate cell that netted +$135.36 on 39 trades.

## Why The Discrepancy

The prior analysis assigned regime to each trade by scanning the workers log for `REGIME_PERCOIN` events within a ±30 s window of the `BRAIN_DO_TRADE` event for that symbol. Trades whose `REGIME_PERCOIN` line did not fall in that window were assigned regime "?". The prior output showed **80 of 234 trades** in the "?" bucket — i.e., 34% of the sample had no regime tag at all.

The "?" bucket in the prior output happened to be biased toward winners (n=80, L=27, W=53, +$343 net). When that profitable subset was excluded, the remaining regime-tagged subset looked far worse than the true population. The DB has `entry_regime` written at trade-creation time directly from the brain's prompt-context map, so it does not have this sampling problem (222 of 225 populated; 99% coverage).

## Wider-Window Stability Check (last 7 days)

Adding the surrounding week to test whether the headline holds across more trades:

| entry_regime | direction | n | loss% | net USD |
|---|---|---|---|---|
| dead | Buy | 6 | 66.7% | −8.17 |
| dead | Sell | 1 | 100.0% | −2.90 |
| ranging | Buy | 69 | 60.9% | **−120.21** |
| ranging | Sell | 120 | 40.0% | **+158.49** |
| trending_down | Buy | 22 | 40.9% | +47.59 |
| trending_down | Sell | 179 | 59.8% | −19.58 |
| trending_up | Buy | 62 | 54.8% | **−162.05** |
| trending_up | Sell | 54 | 51.9% | +45.17 |
| volatile | Buy | 76 | 47.4% | **+425.01** |
| volatile | Sell | 111 | 59.5% | **−165.51** |

The 7-day picture is materially different from both the prior-analysis story and the in-window snapshot:

- **volatile + Buy is the BEST cell** (+$425 over 76 trades) — opposite of the prior-analysis claim.
- **volatile + Sell is a loser** (−$165). The prior analysis already showed volatile + Sell negative; the wider window confirms.
- **trending_up + Buy is the worst cell** (−$162 over 62 trades, 55% loss rate). The Buy-side weakness is real but it is concentrated in `trending_up + Buy` and `ranging + Buy`, not in `volatile + Buy`.
- **ranging + Sell is the second-best cell** (+$158 over 120 trades, 40% loss rate). This contradicts a regime-neutral "Sell is fine" reading; Sell is fine in ranging but a loser in volatile and trending_down.

## What This Means For The Headline

The headline "Buy is broken across every regime" is **only weakly supported**. Buy is bad in ranging and trending_up. Buy is genuinely good in volatile (over the longer window) and slightly profitable in trending_down. The directional bias is regime-conditional, not blanket.

The headline "volatile + Buy is the worst single cell" is **false**. Volatile + Buy is the best single cell over 7 days. Volatile + Sell is the loser the prior analysis was probably reading off the wrong axis of.

The per-trade regime data exists in the DB and is reliable. Future analyses should query `trade_intelligence.entry_regime` directly rather than reconstructing regime from log events.

## Status

Claim 1 verified with significant correction. Documented for the Phase 8 synthesis. No code or DB changed.
