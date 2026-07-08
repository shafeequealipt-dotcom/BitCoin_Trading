# Q2 Step 2.7 — Edge Cases

## INJUSDT: the rare survived-Buy

From the Phase 0 baseline, the only post-deploy Buy → final=Buy survivor on a non-trivial path was INJUSDT (2026-05-11 23:17:50 and 23:42:43). Both events show:

- `apex_locked=Y lock_reason='trending_up aligns with Buy'`
- `reason=xray_flip_suppressed_by_lock`
- `analysis_score=+0.27` (positive directional)

The regime label for INJUSDT at this time was `trending_up`. APEX direction lock fired because regime was trending, suppressing XRAY's structural flip. The Buy survived because the detector correctly labeled INJUSDT as trending.

**Implication**: when the detector correctly labels trending, the entire downstream protective chain (APEX lock → XRAY suppression) works as designed. The system can preserve Buys. The dysfunction is upstream — the detector doesn't label trending often enough.

In the wider window, the same INJUSDT was XRAY-flipped Buy → Sell at 2026-05-11 18:23:29 with `apex_locked=N`. That earlier timestamp must have had a different regime label (likely `ranging conf=0.40` fallback). Same symbol, different times, different labels, opposite outcomes — illustrating the per-coin nature of the detector AND the consequence of label correctness.

## BNBUSDT: dead-regime Sell flip

Event #10 (BNBUSDT 23:00:00) showed regime label `dead` (conf=0.80). BNBUSDT had ADX 13.9, choppiness 29.8, volume_ratio low. This passes the `dead` criteria (`adx < 15` AND `volume_ratio < 0.5` AND `atr_percentile < 50`).

The `dead` regime activates only `funding_arb` + `microstructure` strategies in the ensemble (per `REGIME_ACTIVE_CATEGORIES`). Brain still proposed Buy (likely via a microstructure-based signal). XRAY flipped to Sell with ratio 25.7x.

The 5-min objective-before regime was `weak_trending_down`. Brain's Buy was contrarian; XRAY's flip to Sell was aligned with the 5-min price action. The flip was probably correct for the very-short-term horizon.

**Implication**: the `dead` regime is also vulnerable to the same flip mechanism. The system doesn't protect Buys in `dead`, and XRAY's structural R:R may still favor a Sell flip even when both indicator-based regimes (`dead`) and brain's signal (Buy) say otherwise.

## Sample patterns by symbol class

Looking at the per-symbol detector labels from the wider 48h variance analysis (q1_empirical_variance.md):

- **Universally-ranging coins** (ARBUSDT 99%, SOLUSDT/ADAUSDT 92%, LINKUSDT/DOGEUSDT 90%): the detector almost always says ranging. These coins benefit least from regime-based gating because every consumer of regime treats them the same way regardless of underlying movement.
- **Variable-label coins** (ETHUSDT 36% ranging, BNBUSDT 67%, XRPUSDT 67%): these get more variance in their labels and consequently more variance in trade decisions. ETHUSDT's high-confidence DEAD labeling does cause its decisions to differ from a universally-ranging coin.
- **High-ranging-share coins are exactly the ones with the highest false-ranging rates in Q2**, because their labels are flat by construction.

## Hysteresis interaction with the fallback

The detector's hysteresis (2 confirmed readings before regime change) interacts with the ELSE fallback. Because the fallback is the "default" of the classification space, transitioning out of `ranging` requires:

1. Two consecutive readings landing in the explicit trending/volatile/dead branches.
2. Both readings must agree on the new label.

If the indicators noisily oscillate around the trending threshold (ADX 24-26), readings alternate between `trending_up` (when ADX crosses above 25) and `ranging` (when it dips below). Hysteresis prevents the trending label from confirming, keeping the symbol in `ranging` longer than it should be.

This is a known characteristic of hysteresis-on-noisy-signals: it protects against noise-driven flips at the cost of latency in genuine transitions. With the ELSE fallback as the default state, the cost is asymmetric — staying in `ranging` is much easier than transitioning out.

## Sample where regime label changed during position

The probe doesn't cross-reference positions over time to find samples where the regime changed mid-position. This would require querying `positions` table by symbol and time range, then mapping each position's entry and exit to the surrounding regime emissions. Out of scope for the current investigation but worth noting as a follow-up if Path B implementation surfaces concerns about hysteresis stability.

## Summary

- The system's protective chain (APEX lock → XRAY suppress) works correctly when regime is correctly labeled (INJUSDT trending_up).
- The same chain fails silently when regime mis-labels (INJUSDT at an earlier timestamp, BNBUSDT in dead regime).
- Symbols with universally-ranging labels are most exposed because their per-coin classification carries no information.
- Hysteresis exacerbates the fallback issue by making it harder to escape `ranging` once entered.
