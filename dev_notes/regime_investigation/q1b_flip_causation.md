# Q1b — Flip Causation Trace (10 XRAY Buy → Sell Events)

## Method

For each of 10 `DIRECTION_DECISION reason=xray_flip` events with `brain_dir=Buy final_dir=Sell` in the 2026-05-11 17:35 to 23:30 window:

- Pulled the most-recent `REGIME |` emission for the same symbol prior to the flip timestamp.
- Pulled any `APEX_FLIP_DECISION` event for the same symbol within ±5 minutes (unified log only exists post-22:23).

## Trace table

| # | Time (UTC) | Symbol | XRAY ratio | Regime label | Detector conf | ADX | Chop | APEX outcome | APEX dir_locked |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 17:47:49 | NEARUSDT | 668.0x | ranging | 0.40 | 13.6 | 44.6 | pre-unified-log | N |
| 2 | 18:23:29 | INJUSDT | 6.4x | ranging | 0.40 | 18.3 | 30.4 | pre-unified-log | N |
| 3 | 18:50:16 | BNBUSDT | 11.1x | **dead** | 0.80 | 13.9 | 29.8 | pre-unified-log | N |
| 4 | 18:59:20 | CRVUSDT | 5.7x | ranging | 0.40 | 18.2 | 36.1 | pre-unified-log | N |
| 5 | 18:59:21 | MANAUSDT | 27.1x | ranging | 0.40 | 9.4 | 38.8 | pre-unified-log | N |
| 6 | 19:18:12 | CRVUSDT | 359.0x | ranging | 0.40 | 18.2 | 36.1 | pre-unified-log | N |
| 7 | 19:18:13 | XRPUSDT | 16.5x | ranging | 0.40 | 14.6 | 34.0 | pre-unified-log | N |
| 8 | 22:50:40 | HBARUSDT | 34.2x | ranging | 0.40 | 13.0 | 54.1 | **flip_attempted=Y, flip_accepted=N, conf_below_threshold** (eff_conf=0.90 vs 0.95 threshold) | N |
| 9 | 22:51:00 | MANAUSDT | 24.0x | ranging | 0.40 | 9.4 | 38.8 | **flip_attempted=Y, flip_accepted=N, conf_below_threshold** (eff_conf=0.90 vs 0.95 threshold) | N |
| 10 | 23:00:00 | BNBUSDT | 25.7x | **dead** | 0.80 | 13.9 | 29.8 | (none in window) | N |

## Patterns

### Pattern 1 — All flips on non-trending labels with `apex_locked=N`

In every single one of the 10 flips, `apex_locked=N` in the DIRECTION_DECISION log. This is the structural reason the flip was permitted: APEX direction lock only fires for `trending_up`/`trending_down` regimes. Since none of these symbols had a trending label, APEX did not pre-empt the flip with a hard lock.

### Pattern 2 — 80% of flips on ELSE fallback (`conf=0.40 ranging`)

8 of 10 flips occurred on a `ranging` label with confidence `0.40` — the unique signature of the `else` fallback branch in `RegimeDetector.detect()` at lines 153-156. These coins had ADX in [9, 18] and choppiness in [30, 55] — values that don't meet the strict ranging criteria (`adx < 20 AND chop > 60`) but also don't meet the trending criteria (`adx > 25 AND chop < 45`). The detector defaults them to `ranging` without conviction. None of the 8 are clear-cut mean-reversion candidates.

### Pattern 3 — 2 of 10 flips on `dead` regime (BNBUSDT)

Both BNBUSDT events show `dead` (ADX 13.9, choppiness 29.8). The `dead` regime activates only `funding_arb` + `microstructure` strategies in the ensemble, and APEX does not direction-lock for dead. Brain still made a Buy decision (perhaps via a microstructure signal), but XRAY's structural R:R said Sell with high confidence. The 11x and 25x ratios are still well above the 3.0 threshold.

### Pattern 4 — APEX correctly preserved Buy, XRAY overrode

Events 8 and 9 (HBARUSDT, MANAUSDT at 22:50) are the clearest illustration of the bug surface. The sequence:

1. Brain decided Buy.
2. Qwen (initial direction) was Sell.
3. APEX `_enforce_flip_confidence` blocked the flip because `effective_confidence = 0.90 < 0.95` (Buy → Sell threshold).
4. APEX wrote `apex_dir = Buy` (preserving the brain decision).
5. APEX emitted `APEX_FLIP_DECISION decision_reason=conf_below_threshold flip_accepted=N`.
6. **Downstream, XRAY saw the structural R:R: `rr_chosen = 0.10/0.17` (Buy direction) vs `rr_flipped = 3.42/4.08` (Sell direction).**
7. Ratio = 34.2x and 24.0x — both far above the 3.0 threshold.
8. XRAY flipped Buy → Sell, ignoring APEX's preservation.

The same structural picture that drove Qwen to want Sell at the APEX layer also drove XRAY to flip post-APEX. Both saw the same R:R asymmetry. APEX rejected the flip because the model's confidence wasn't high enough; XRAY accepted it because structural R:R was decisive. **Two consecutive layers disagreeing on the same input.**

## Causation chain (synthesized)

```
1. Detector classifies low-ADX / moderate-choppiness coin as "ranging conf=0.40" (ELSE fallback)
                                  |
                                  v
2. Stage 2 prompt receives [RANGING 40%] tag for the coin
                                  |
              (regime-driven directional bias is "both directions ok")
                                  v
3. Brain decides Buy based on its own signal mix
                                  |
                                  v
4. APEX direction lock does NOT fire (lock requires trending regime)
                                  |
                                  v
5. Qwen secondary model proposes Sell flip
                                  |
                                  v
6. APEX _enforce_flip_confidence checks effective confidence (Buy→Sell needs 0.95)
                                  |
                +----- conf < 0.95 ------+
                |                        |
                v                        v
        conf_below_threshold        conf >= 0.95
        APEX preserves Buy          APEX accepts Sell flip
                |                        |
                v                        v
7a. APEX writes apex_dir=Buy     7b. APEX writes apex_dir=Sell
                |                   (counted in APEX flips)
                v
8. Trade exits APEX with brain_dir=Buy and final_dir set to Buy (apex_dir match)
                |
                v
9. XRAY block at strategy_worker.py:1604-1779 sees the trade
                |
                v
10. XRAY computes _ratio = rr_opposite / rr_chosen
                |
                v
11. If _ratio > xray_dir_flip_threshold_ratio (3.0):
                |
                v
12. XRAY flips final_dir to opposite of brain_dir
                |
                v
13. DIRECTION_DECISION emits flip_source=xray reason=xray_flip
                |
                v
14. Trade places as Sell
```

## What this tells us about the three paths

- **Path A (XRAY threshold tune)** directly addresses step 11. Raising the threshold to (say) 10.0 would prevent flips at 6.4x and 5.7x (events 2 and 4) but still allow 11.1x, 16.5x, 24x, 27x, 34x, 359x, 668x flips. So Path A alone would keep most of the high-ratio flips but eliminate the marginal ones. The Buy share would rise modestly but the system would still see most large-ratio flips.

- **Path B (regime detector fix, especially B1 closing the ELSE fallback)** addresses steps 1-2 directly. If the detector can confidently label these coins as `weak_trending_up` or `weak_trending_down` instead of defaulting to `ranging conf=0.40`, then APEX direction lock can fire on the weak trends and pre-empt the entire chain at step 4. The coin never reaches XRAY's flip block.

- **Path C (hybrid)** does Path B first, then evaluates whether Path A is still warranted. Given that 80% of the traced flips happened on the ELSE fallback, Path B alone might resolve most of them. The remaining high-ratio non-fallback flips (e.g., events 3 and 10 on `dead` BNBUSDT) would need separate treatment.

## Caveats

- This is 10 events. Phase 2 widens the sample to 30+ for accuracy quantification.
- Pre-unified-log events (1-7) lack APEX_FLIP_DECISION fields, so we cannot reconstruct whether APEX would have caught the flip if the unified log had been live. The PRIMARY fix likely changes counterfactual behavior; events 8-9 illustrate the post-fix state.
- "Structural R:R legitimately favoring Sell" is an open question — we cannot verify whether the StructureCache analysis is correct without inspecting structural inputs. That is out of scope for this investigation per the spec.
