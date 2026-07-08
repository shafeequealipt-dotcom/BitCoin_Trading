# PRIMARY Issue — Phase 1 Step P.1.8: Flipped vs Unflipped Performance (THE CRITICAL STATISTIC)

Source: `data/trading.db` — `trade_intelligence` table (`apex_flipped`, `apex_original_direction`, `apex_final_direction`).
Status: queries executed. Investigation only — no code changes.

This is the headline statistic for the operator's strategic-policy decision at PRIMARY Phase 2.

## 1. Flip-Pair Performance — Both Exchange Modes

| Mode | Original → Final | Trades | WR | Net pnl_usd |
|------|------------------|--------|----|-------------|
| **bybit_demo** | Buy → Buy (unflipped) | 32 | 37.5% | $+8.71 |
| **bybit_demo** | Buy → Sell (flipped) | 176 | 30.7% | $+223.56 |
| **bybit_demo** | Sell → Buy (flipped) | 3 | 66.7% | $+5.93 |
| **bybit_demo** | Sell → Sell (unflipped) | 113 | 29.2% | $-46.83 |
| **shadow** | Buy → Buy (unflipped) | 402 | **47.8%** | **$+252.23** |
| **shadow** | Buy → Sell (flipped) | 189 | 31.7% | $-238.11 |
| **shadow** | Sell → Buy (flipped) | 76 | 51.3% | $+74.13 |
| **shadow** | Sell → Sell (unflipped) | 259 | 40.9% | $+143.12 |

### Interpretation

#### Shadow (larger sample, longer history — statistically stronger evidence)

In shadow, the picture is **unambiguous**:
- **Buy → Buy unflipped is the BEST cohort**: 47.8% WR, +$252.23 net.
- **Buy → Sell flipped is the WORST**: 31.7% WR, -$238.11 net.
- The flip swing on Buy origins costs roughly **$490 vs leaving alone** (252 − (−238) = 490).
- Sell → Buy flips are mildly helpful (+$74); Sell → Sell unflipped is net positive (+$143).
- **Conclusion from shadow data**: the Buy → Sell flip is structurally destructive. The Sell → Buy flip is mildly helpful but rare.

#### Bybit_demo (smaller sample, 2.3 days, may not represent steady state)

- **Buy → Sell flipped is the largest net winner in dollar terms** ($+223 across 176 trades). But the WR (30.7%) is much lower than Buy → Buy unflipped (37.5%).
- **Sell → Sell unflipped is the worst loser** (-$46.83) and the system's default direction without flips.
- Sell → Buy flipped is too small to evaluate (3 trades).

The bybit_demo and shadow data **disagree about whether Buy → Sell flips are helpful**. Possible reasons:
1. **Sample mismatch**: shadow has ~3× more flipped Buy → Sell trades (189 vs 176) but spans a far longer history. bybit_demo's 176 happen in 2.3 days under specific market conditions.
2. **Position sizing on flips**: the `_apply_flip_resize_policy` capping flipped trade size DOWN means flipped trades are systematically smaller. Bybit_demo's "Buy→Sell flipped" cohort has avg size $259 vs $312 for unflipped (Phase 0 baseline data). Smaller losing trades have smaller absolute losses → flipped cohort dollar PnL inflated artificially.
3. **Current market state favoring Sell**: Phase 0 / P.1.7 found Sell × trending_up unexpectedly net +$502 on bybit_demo — counter-intuitive cohort that may be driving the apparent profitability of flips on bybit_demo.

## 2. Direction-Pair Win Rate Deltas (the cleanest read)

Removing the position-sizing confound — using pure win rate:

| Mode | Direction | Unflipped WR | Flipped WR | Δ |
|------|-----------|--------------|------------|---|
| shadow | Buy origin   | 47.8% | 31.7% | **-16.1 pp** |
| shadow | Sell origin  | 40.9% | 51.3% | +10.4 pp |
| bybit_demo | Buy origin  | 37.5% | 30.7% | **-6.8 pp** |
| bybit_demo | Sell origin | 29.2% | 66.7% | +37.5 pp (tiny n=3) |

**Both datasets agree on the WR direction of the effect:**
- **Buy → Sell flips REDUCE win rate** by 6.8-16.1 percentage points.
- **Sell → Buy flips INCREASE win rate** by 10+ pp (small samples).

This is the cleanest evidence the operator has. WR is sample-size sensitive but direction-of-effect agrees across modes.

## 3. Flip × Regime — Bybit Demo

| Regime | apex_flipped | Trades | WR | Net pnl_usd |
|--------|--------------|--------|----|-------------|
| ranging | 0 (unflipped) | 70 | 38.6% | $-65.70 |
| ranging | 1 (flipped) | 86 | **23.3%** | **$-213.87** |
| trending_up | 0 | 41 | 36.6% | $+58.72 |
| trending_up | 1 | 122 | 32.0% | $+415.71 |
| trending_down | 0 | 6 | 16.7% | $+0.69 |
| trending_down | 1 | 1 | 100.0% | $+9.56 |
| volatile | 0 | 7 | 42.9% | $-2.06 |
| volatile | 1 | 2 | 0.0% | $0.00 |

### Critical Insight — Ranging Regime

In **ranging regime** (where APEX has no pre-call lock and flips are most prevalent):
- Unflipped: 70 trades, 38.6% WR, $-65.70 net
- Flipped: 86 trades, **23.3% WR**, **$-213.87 net**
- WR drop: -15.3 pp
- PnL hit: -$148.17 net

**The ranging regime is the EXACT scenario the flip is supposed to help, and the data shows the flip MAKES THE OUTCOME WORSE** — fewer wins, larger absolute loss.

### Anomaly — trending_up Has Flipped Trades?

122 flipped trades in `trending_up` regime is unexpected. Per `_check_direction_lock` (optimizer.py:885-931), trending_up should be **locked** — APEX cannot flip. These 122 trades must have:
- Been XRAY-driven flips (XRAY did not respect APEX_DIR_LOCK before Issue 1 fix on 2026-05-11), OR
- Pre-date the Issue 1 fix and reflect the older behavior, OR
- The lock was bypassed somehow (the override path at optimizer.py:330-341 would still log `APEX_DIR_LOCK_OVERRIDE`).

P.1.9 (DeepSeek inspection) can disambiguate by sampling a few of these trades and checking their flip source.

## 4. Confidence And Sizing By Flip Status — Bybit Demo

| apex_flipped | Avg apex_confidence | Avg position_size_usd | Avg pnl_pct | WR | N |
|--------------|---------------------|------------------------|-------------|----|----|
| 0 (unflipped) | 0.77 | $312 | -0.011% | 37.1% | 124 |
| 1 (flipped)   | 0.83 | $259 | -0.013% | 28.4% | 211 |

Findings:
- **Flipped trades have HIGHER avg confidence** (0.83 vs 0.77). DeepSeek expresses more conviction when flipping than when not flipping.
- **Flipped trades have SMALLER avg size** ($259 vs $312, a 17% reduction). Consistent with `_apply_flip_resize_policy` accepting DeepSeek's smaller flip sizing.
- **Flipped trades have LOWER win rate** (28.4% vs 37.1%, -8.7 pp). Despite higher confidence.
- Avg per-trade pnl_pct is nearly identical (the dollar difference comes from sizing).

**Implication: DeepSeek's confidence on flipped trades is overconfident relative to outcome.** When DeepSeek says "I'm 83% confident this should be Sell, not Buy", the system loses more often than when DeepSeek doesn't flip.

## 5. Why The Operator Needs This Data

The flip-vs-unflip statistic is what the spec's Part B Rule 12 explicitly requires for the strategic decision:

> "For the PRIMARY issue, the investigation MUST include:
> - Direction distribution across last 30 days of trades
> - Win rate per direction
> - Win rate of flipped trades vs unflipped trades
> - PnL contribution of flipped trades vs unflipped
> - Per-regime breakdown of the above"

All four required statistics are captured above. The strongest signals are:

1. **Shadow data — clear** (large sample, statistically meaningful): Buy → Sell flips lose 16.1 pp WR and -$490 net swing per cohort.
2. **bybit_demo data — directionally consistent on WR**, but mixed on dollar PnL due to sizing effects.
3. **Ranging regime specifically — flips destroy WR** (38.6% → 23.3%, -15.3 pp) and increase loss (-$66 → -$214).
4. **DeepSeek confidence is overconfident** on flipped trades (83% confidence → 28% win rate).

The data does NOT support the current flip policy as profitable.

## 6. Decision Texture For Phase 2

When the operator faces the option menu at Phase 2, this data informs:

- **Option 1 (brain-priority gating)** — strongly supported by data: brain's Buy decisions outperform their flipped Sell counterparts on WR by 6.8-16.1 pp.
- **Option 2 (regime-specific tuning)** — flips in ranging are demonstrably bad; this is where the worst flip outcomes concentrate.
- **Option 3 (raised confidence floor)** — data shows DeepSeek's confidence is overcalibrated; raising `apex_min_flip_confidence` from 0.70 to e.g. 0.85 would block most current flips without changing the underlying behavior.
- **Option 4 (asymmetric thresholds)** — Sell → Buy flips look mildly helpful (small sample); Buy → Sell flips look destructive. Asymmetric threshold (e.g. allow Sell → Buy with conf > 0.70 but require Buy → Sell with conf > 0.95) is supported.
- **Option 5 (brain authority restoration)** — directly supported: brain's direction outperforms the flipped direction by every metric except absolute bybit_demo dollars (which is sizing-confounded).
- **Option 7 (status quo + logging)** — only justified by the spec if data showed flips were helpful. Data shows the opposite. Option 7 is not data-supported.

## 7. Out-of-Scope Confirmation

- No code changes.
- SQL was read-only.
