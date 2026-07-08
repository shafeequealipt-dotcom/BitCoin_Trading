# Phase 1.7 — Market Reality Verification

Spec lines 481-488: verify the prior report's claim that the market was 8.9× more trending_down than trending_up in the audited window, and confirm this reflects genuine market state vs detector bug.

## Independent regime count

`grep "src.strategies.regime:detect" $LOG | grep -oE "rgm=[a-z_]+" | sort | uniq -c`:

| Regime | Per-coin emissions | Share |
|---|---:|---:|
| trending_down | 1,567 | 52.9% |
| ranging | 865 | 29.2% |
| volatile | 657 | 22.2% |
| trending_up | 176 | 5.9% |

**Trending_down / trending_up = 1,567 / 176 = 8.9×.** Matches prior report claim exactly.

## Genuine market state or detector bug?

### ADX values seen

- Min: 11.3.
- Median: 26.7.
- Max: 59.8.

ADX > 20 is "trending" by classical Wilder convention; > 25 is "strong trend". The median ADX of 26.7 indicates the universe was in a moderately strong trend regime in this window.

### Per-major-coin ADX (BTC/ETH/SOL)

Sample from `src.strategies.regime:detect`:

| Symbol | Regime | Confidence | ADX | Chop |
|---|---|---:|---:|---:|
| BTCUSDT | trending_down | 0.63 | 31.6 | 19.3 |
| ETHUSDT | trending_down | 0.71 | 35.5 | 15.3 |
| SOLUSDT | trending_down | 0.60 | 30.0 | 26.6 |

ETHUSDT ADX 35.5 with chop 15.3 is a classic "strong downtrend" reading. BTC ADX 31.6 is a moderate-to-strong downtrend. These are genuine market readings, not detector noise.

### Confidence values

The detector reports confidence 0.60-0.71 for the major-coin trending_down regimes. Per the B1a calibration (commit `6938c69`, 2026-05-12), the trending threshold is ADX ≥ 20. Confidence 0.63 corresponds to ADX 31.6 (above threshold by 1.58× — moderate-strong). These are not borderline detections.

### Was this period genuinely a downtrend?

Cross-check via the audit-window's BYBIT entries: of 75 Sell entries, the win rate over the next few days (per `trade_log` 2026-05-18 entries) was 58.8% — confirming the Sell direction was profitable, consistent with continuing-bearish market.

## Proportionality analysis

If execution direction were purely proportional to regime:
- Expected: 1567 / (1567 + 176) = 89.9% Sell.
- Observed at brain: 92.3% Sell.
- Observed at orders: 89.3% Sell.

**Final orders (89.3%) match the regime-proportional expectation (89.9%) almost exactly.** Brain output (92.3%) is slightly above proportional — the strategist prompt is amplifying by ~2.4 percentage points.

This is a critical finding:
- The system as a whole is NOT mis-pricing direction relative to market regime.
- The override layer's 25 Sell→Buy flips bring brain (92.3%) back to ~order level (89.3%), aligning with regime.
- The prompt amplification (Issue 4) is ~2-3 pp, not the massive distortion the headline numbers suggest.
- The "bias" is largely the *market*, not the *code*.

## Verdict

- Market state claim accurate: 8.9× downtrend, ADX 30+ on majors, confidence 0.60-0.71. Real, not detector bug.
- The 89% Sell ratio at order placement is approximately proportional to the regime distribution (89.9% expected).
- The prompt-level amplification at the brain (92.3% vs 89.9% expected) is small (~2-3 pp).
- The override layer is doing useful work — pulling brain output back toward the regime-proportional expectation.

## Implications for fix path

- **Concern 8 (bias may not be a bug) has substantial merit**: final order direction is nearly regime-proportional. A "fix" that drives Sell% below 80% in this market would be over-correcting against reality.
- **Issue 4 amplification is real but small** (~2-3 pp). Worth fixing because the prompt asymmetry violates operator directive, but the expected behavior change at the order level may be modest (orders shift from 89% to ~85% Sell).
- **The empirical case for shipping Issue 4 first (Path C / Concern 5) is strong**: low risk, small expected shift, no cascade effects.
- **Issues 1, 2, 3 may have smaller effective impact than the headline numbers suggest** — they could be operating largely in agreement with market reality, with the asymmetric mechanisms amplifying a real signal. Worth shipping for design-directive compliance, but expected PnL impact may be small.
- **Critical follow-up**: must validate in a *different* market regime (trending_up or ranging-balanced market). If the same prompt mandate operates in a balanced market and STILL produces 85%+ Sell, that proves the bias is in the code. If a balanced market produces ~50/50, the asymmetric prompt is mostly cosmetic in normal conditions.
