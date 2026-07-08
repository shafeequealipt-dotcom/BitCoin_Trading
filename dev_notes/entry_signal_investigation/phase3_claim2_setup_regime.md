# Phase 3 — Claim 2 Verification: Setup × Regime × Outcome

## The Claim

> bullish_structural_break in volatile regime is heavily negative. 8 trades, 6 losses, −$145 — roughly half of one day's losses in a single setup-regime cell.

## Method

`setup_type` is not stored in `trade_intelligence`. It is only recorded in the live log via `TIME_DECAY_STRUCT_GUARD` lines that fire DURING the hold (each line carries `entry_xray`, `entry_setup`, `entry_regime` snapshots). For each closed trade in window, the last STRUCT guard line before close was matched to the trade.

Regime source: DB `trade_intelligence.entry_regime` (the strategist's anchor at trade creation), with fallback to the STRUCT guard's `entry_regime` if DB value missing.

Window: 2026-05-20 05:46 → 2026-05-21 12:40. DB trades: 225. With STRUCT-tagged setup: 145.

## Result — Setup × Regime (145 trades with setup tag)

| Setup | Regime | n | L | W | loss% | net USD |
|---|---|---|---|---|---|---|
| bullish_structural_break | volatile | 3 | 3 | 0 | 100.0% | −$119.86 |
| bullish_structural_break | trending_up | 11 | 7 | 4 | 63.6% | −$80.29 |
| bearish_fvg_ob | ranging | 12 | 8 | 4 | 66.7% | −$71.68 |
| bullish_fvg_ob | ranging | 32 | 18 | 14 | 56.2% | −$39.38 |
| bearish_fvg_ob | volatile | 11 | 5 | 6 | 45.5% | −$26.23 |
| bullish_fvg_ob | dead | 2 | 2 | 0 | 100.0% | −$3.11 |
| bullish_fvg_ob | trending_up | 29 | 18 | 11 | 62.1% | −$0.11 |
| **bullish_fvg_ob** | **volatile** | **43** | **22** | **21** | **51.2%** | **+$183.93** |

## Verification Result

| Sub-claim | Prior analysis | DB-verified | Verdict |
|---|---|---|---|
| `bullish_structural_break × volatile` n | 8 | 3 | **Partial** (smaller sample) |
| `bullish_structural_break × volatile` loss% | 75% | **100%** | Worse than claimed |
| `bullish_structural_break × volatile` net | −$145 | **−$119.86** | Slightly less negative, same direction |
| `bullish_structural_break × volatile` is "worst cell" | yes | YES on net% basis, but only 3 trades | **Sample-size caveat** |
| `bullish_fvg_ob × volatile` directional read | (prior had this winning slightly) | **+$183.93 net, 51% loss** | **STRONG winner cell** |

The headline holds in direction: `bullish_structural_break` in volatile regime did lose all its trades in the window. But the sample is only 3 trades (the prior analysis claimed 8 — that count came from including `STRUCT_GUARD` logs that may have been counted across both directional variants or across overlapping windows).

## Wider Findings That Reproduce

- **`bullish_structural_break` overall is bad**: combining `volatile (3) + trending_up (11) + (any other)` = 14 trades, **net −$200** (matching the prior claim's bullish_structural_break headline). 10/14 = 71% loss rate.
- **`bullish_fvg_ob × ranging` is a money pit on volume**: 32 trades, 18 losses, −$39 net (small per-trade loss but consistent).
- **`bullish_fvg_ob × trending_up` is structurally flat**: 29 trades, 62% loss rate, but net only −$0.11 (winners exactly offset losers — no edge).
- **`bullish_fvg_ob × volatile` is the SINGLE BEST CELL** of the analysis window: 43 trades, +$184 net. This contradicts the narrative that "volatile is bad for longs" — when the structural setup is FVG/OB rather than structural break, volatile actually pays.

## What This Means

The data supports a refined claim:
- `bullish_structural_break` (in ANY regime) is the bad setup — average loss −$14 per trade across 14 trades.
- It is especially bad in `volatile` (small sample, 100% loss rate) and `trending_up` (n=11, 64% loss rate).
- The `volatile` regime as a whole is NOT bad. `bullish_fvg_ob × volatile` is the best-performing cell.

The bug-suspect is the `bullish_structural_break` setup_type's entry criterion, NOT the volatile regime broadly. The structural break label is fired when `last_bos.direction == "bullish"` AND `direction == "long"` (structure_engine.py:1272-1294). When that fires in a fast-moving volatile environment, the price has likely already broken out and chasing is too late. The FVG/OB setup, by contrast, locates a pullback to a structural zone — entries are at better prices and have time to play out.

## Status

Claim 2 verified with refinement. The single-cell `bullish_structural_break × volatile` headline holds in direction (100% loss in DB) but is undersized (3 trades, not 8). The broader claim that `bullish_structural_break` is structurally a losing setup reproduces cleanly. The implied claim that "volatile regime is bad" does NOT hold — volatile + bullish_fvg_ob is the best single cell.
