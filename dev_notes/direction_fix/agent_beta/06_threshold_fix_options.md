# BETA 06 — R3 Threshold Fix Options (A through E)

This document evaluates five candidate fixes for R3 — the 10× `xray_lock_override_ratio_threshold` that creates the 3.0×-9.99× dead zone where 8 trades were suppressed on May 16. Each option modifies `src/workers/strategy_worker.py:1671-1717` and/or `src/config/settings.py:831`.

The aim-bias questions answered for each option are:

1. **Frequency**: Does this preserve trade frequency?
2. **Aggression**: Does this preserve decisive brain proposals?
3. **Decision quality**: Does this measurably improve outcomes?
4. **Passive close**: Does this preserve the data-lake watchdog advantage?
5. **Separation of concerns**: Does this respect layer boundaries?

## Option A — Lower 10× to 3× (eliminate the dead zone)

### Mechanism change
- File: `src/config/settings.py:831` — change default `xray_lock_override_ratio_threshold: float = 10.0` → `3.0`.
- File: `config.toml` — surface the value (currently relies on dataclass default). New entry under `[risk]`: `xray_lock_override_ratio_threshold = 3.0`.
- File: `src/workers/strategy_worker.py:1671-1681` — no code change; only the runtime value changes.
- The condition `_lock_override_threshold > _flip_threshold` at line 1679 must still hold. With both at 3.0, the override path is unreachable. The override path requires `_lock_override_threshold > _flip_threshold`; equality fails. Either the comparison must change to `>=` or the values must differ by at least epsilon. Recommended: change the comparison to `>=` so that override-at-threshold composes correctly.

### Observable behavior change
- Dead zone collapses entirely. Any ratio > 3.0× clears both thresholds at once. The override always fires when the flip threshold fires.
- The lock becomes structurally weak: structural evidence ≥ 3.0× will always over-rule it.
- BSBUSDT (7.3×): override fires, trade flips to Buy. PREVENTED.
- All 8 XRAY_FLIP_SUPPRESSED_BY_LOCK cases (3.0× to 7.3×): override fires for each. All 8 trades flip to the structural winner.
- The R3 spec calls this a "forbidden band-aid" boundary (flat-1.0 override threshold) — Option A is at 3.0, not 1.0, so it is NOT in the forbidden zone.

### Aim-bias evaluation
1. Frequency: YES — more flips stand, more aim-aligned trades pass through.
2. Aggression: YES — brain still proposes; structural override is decisive.
3. Decision quality: YES — strong structural evidence (3-7×) directly translates to the structurally-correct direction.
4. Passive close: YES — no watchdog change.
5. Separation: YES — single-line config; no architectural shift.

### Expected impact
- Suppressed-trade count: 0 (was 8).
- Override-fire count: rises from 6 to ~14 (all suppressed cases become overrides).
- Direction distribution: shifts toward balance by 8 trades' worth in this session.
- BSBUSDT-style trades: prevented.

### Risk
- LOW-MEDIUM. The lock effectively becomes equivalent to "lock fires only when structure agrees" — a strong relaxation. Risk: aggressive flips on noisy structural signals.
- Mitigation: the 3.0× threshold is the same threshold strategy_worker uses for unlocked flips; using it for locked-flip override is consistent.

### Complexity
- 1 of 5. Single value change + the comparison `>` to `>=` to keep the path reachable.

### Verdict
The simplest, most direct fix. Eliminates the dead zone by construction. The downside is that it makes the lock less effective overall — anytime structure says ≥ 3× the other way, the lock relaxes.

## Option B — Lower 10× to 5× (compromise)

### Mechanism change
- File: `src/config/settings.py:831` — default 10.0 → 5.0.
- File: `config.toml` — `xray_lock_override_ratio_threshold = 5.0`.

### Observable behavior change
- Dead zone narrows: 3.0× to 4.99×.
- BSBUSDT (7.3×): override fires. PREVENTED.
- ARBUSDT (3.7×), SKRUSDT (4.2×), DYDXUSDT (4.2×): still in dead zone, still suppressed.
- PLUMEUSDT (5.0×): at boundary. With `>` comparison, NOT cleared. With `>=`, cleared. Recommend `>=` for clarity.
- LDOUSDT (3.0×), OPUSDT (3.0×): in dead zone, suppressed.
- ONDOUSDT (6.4×): cleared. PREVENTED.

Of the 8 May 16 suppressions, 3 (BSBUSDT 7.3×, PLUMEUSDT 5.0×, ONDOUSDT 6.4×) would clear at 5.0×.

### Aim-bias evaluation
1. Frequency: YES — moderate increase in flip pass-through.
2. Aggression: YES — brain proposes, override fires at moderate-strong evidence.
3. Decision quality: PARTIAL — admits the strongest 3 of 8 cases. Misses the marginal ones (3-4×).
4. Passive close: YES — no watchdog change.
5. Separation: YES — single config.

### Expected impact
- Suppressed-trade count: 5 (was 8).
- Override-fire count: rises from 6 to ~9.
- Direction distribution: small shift toward balance.
- BSBUSDT-style trades: prevented.

### Risk
- LOW. More conservative than Option A. Lock retains more authority.

### Complexity
- 1 of 5.

### Verdict
A reasonable middle-ground. Prevents the worst case (BSBUSDT $70 loss). Less aggressive than Option A; less effective at restoring balance.

## Option C — Direction-asymmetric threshold

### Mechanism change
- File: `src/config/settings.py` — replace single `xray_lock_override_ratio_threshold` with two:
  - `xray_lock_override_ratio_threshold_buy_to_sell: float = 10.0` (keeping the harder bar for Buy→Sell flips).
  - `xray_lock_override_ratio_threshold_sell_to_buy: float = 3.0` (easier bar for Sell→Buy flips, matching the aim-bias data).
- File: `src/workers/strategy_worker.py:1671-1681` — resolve the threshold based on the would-be flipped direction. The current `_flipped_dir = "Sell" if direction == "Buy" else "Buy"` (line 1722) gives the answer; use this to pick the right threshold value.

### Observable behavior change
- Mirrors the existing asymmetric `apex_min_flip_confidence_*` design (0.95 Buy→Sell, 0.70 Sell→Buy).
- BSBUSDT (chosen=Sell, would flip to Buy = Sell→Buy direction): uses the 3.0× threshold. 7.3× clears. PREVENTED.
- ORCAUSDT (chosen=Buy, would flip to Sell = Buy→Sell direction): still uses 10.0×. 12.0× clears. Still works.
- The 5 of 6 May 16 overrides that were Buy→Sell would still fire at 10×.
- The 1 of 6 overrides that was Sell→Buy (OPUSDT 19.3×) would have fired at either threshold.
- All 8 May 16 suppressions were chosen=Sell (would flip to Buy = Sell→Buy direction) — all 8 cleared at 3.0×. All 8 PREVENTED.

### Aim-bias evaluation
1. Frequency: YES — frequency rises for Buy direction (the under-represented one).
2. Aggression: YES — brain proposes, asymmetric override admits the harder case.
3. Decision quality: YES — directly encodes the aim-bias evidence (Buys win more frequently); composes with existing asymmetric confidence design.
4. Passive close: YES.
5. Separation: YES — same file, same function, just two values instead of one.

### Expected impact
- Suppressed Sell-to-Buy flips: 0 (was 8).
- Suppressed Buy-to-Sell flips: unchanged.
- Direction distribution: shifts toward balance; Buy gains 8 trades in this session.
- BSBUSDT prevented.

### Risk
- LOW. The asymmetric design is already established in APEX (`apex_min_flip_confidence_buy_to_sell` 0.95 / `..._sell_to_buy` 0.70). Mirroring the design at the strategy_worker level is structurally consistent.

### Complexity
- 2 of 5. Two new settings, threshold resolution function, mirror of existing pattern.

### Verdict
**Strong candidate.** Directly addresses the aim-bias evidence. Composes naturally with the existing asymmetric flip-confidence thresholds. Prevents all 8 May 16 suppression cases without weakening the Buy→Sell direction (which has historically performed worse).

## Option D — Conviction-aware threshold

### Mechanism change
- File: `src/workers/strategy_worker.py:1671-1681` — adjust the threshold based on the XRAY confidence (`_xray_confidence` from the structure cache).
- Formula: `threshold = base - (xray_conf * adjustment)`. E.g., base=10.0, adjustment=7.0. At xray_conf=0.0 (low conviction) threshold=10.0. At xray_conf=1.0 (high conviction) threshold=3.0.
- New settings: `xray_lock_override_base: float = 10.0`, `xray_lock_override_conviction_adjustment: float = 7.0`.

### Observable behavior change
- BSBUSDT: xray_conf=0.55 (from SIZE_DERIVATION line). Threshold = 10.0 - (0.55 * 7.0) = 6.15. 7.3× > 6.15. Override fires. PREVENTED.
- For high-conviction structural signals, the lock relaxes more easily.
- For low-conviction structural signals (noisy data), the lock stays at 10×.

### Aim-bias evaluation
1. Frequency: YES — high-conviction structural evidence is admitted.
2. Aggression: YES — brain proposes; structural override fires when justified.
3. Decision quality: YES — couples threshold to the signal's own confidence in itself. Robust against noisy structural data.
4. Passive close: YES.
5. Separation: YES — single function.

### Expected impact
- Depends on xray_confidence distribution. Most May 16 suppressed cases likely had xray_conf 0.5-0.7. With base=10, adjustment=7, the effective threshold would be 5.1-6.6 — admitting BSBUSDT (7.3×), ONDOUSDT (6.4×), PLUMEUSDT (5.0×) but not the 3-4× cases.

### Risk
- LOW-MEDIUM. The xray_conf is already a structured signal; using it to scale a related threshold is principled.
- Edge case: xray_conf can spike to 1.0 on noisy single-bar signals; if base is too low (e.g., base=10 adjustment=10), threshold could go negative.

### Complexity
- 3 of 5. Requires reading xray_conf and computing the threshold. Two new settings.

### Verdict
Principled and elegant. Useful but slightly indirect — Buy and Sell evidence get the same threshold even though aim-bias data favors easier Buy overrides.

## Option E — Aim-bias-evidence-aware threshold

### Mechanism change
- Similar to Option C (direction-asymmetric) but the threshold values are not hardcoded — they are derived from an aim-bias evidence signal. The signal can be:
  - The aggregate per-direction win rate over the last N trades (Buy 55.6 % / Sell 41.8 %).
  - The per-coin per-direction WR for the symbol in the current regime.
  - A configurable scalar tied to a "bias correction" intent declared by the operator.
- Implementation: a settings struct `xray_lock_override_aim_bias` with `buy_to_sell_threshold` and `sell_to_buy_threshold` fields. Optionally a "recalibrate from last N trades" knob.

### Observable behavior change
- Identical to Option C in static-threshold mode.
- In dynamic mode, the thresholds self-adjust over time as the trade history accumulates.

### Aim-bias evaluation
1. Frequency: YES.
2. Aggression: YES.
3. Decision quality: YES — directly couples to outcome data.
4. Passive close: YES.
5. Separation: YES — but adds a feedback loop (history → threshold → trade → history) that needs careful design to avoid oscillation.

### Expected impact
- Same as Option C for static thresholds; potentially more nuanced for dynamic.

### Risk
- MEDIUM. Dynamic-threshold mode introduces history-dependent behavior that is harder to test and may interact with the per-coin TIAS history that's already feeding multiple gates.
- Static mode is essentially Option C with a different label.

### Complexity
- 3 of 5 for static; 5 of 5 for dynamic.

### Verdict
Subsumes Option C. Worth considering if the operator wants the system to self-tune. For an initial fix, Option C is the safer subset.

## R3 ranking — recommendation summary

| Option | Aim alignment | Risk | Complexity | BSBUSDT-prevents? | Suppression count after fix |
|---|---|---|---|---|---|
| A — flat 3× | HIGH | LOW-MED | 1 | YES | 0 of 8 |
| B — flat 5× | MEDIUM | LOW | 1 | YES | 5 of 8 |
| C — asymmetric | **HIGH** | LOW | 2 | YES | 0 of 8 (Sell→Buy direction) |
| D — conviction-aware | HIGH | LOW-MED | 3 | YES | ~3-5 of 8 |
| E — aim-bias-aware static | HIGH | LOW | 2 | YES | Same as C |

The top candidates are Option A (simplest, most aggressive) and Option C (asymmetric — composes with existing design).

**BETA leans toward Option C.** It is:
- Structurally consistent with the existing asymmetric `apex_min_flip_confidence_*` thresholds inside APEX.
- Directly responsive to the aim-bias evidence (Buys win more often, so the bar to recover them from a lock should be lower).
- Low risk — the Buy→Sell direction (which historically performs worse) keeps the 10× protective threshold.
- Prevents all 8 May 16 suppression cases (all 8 were Sell→Buy flips that the asymmetric 3× threshold admits).

Option A is the runner-up — simpler implementation but symmetrically weakens the lock in both directions.

If the operator wants minimum-risk, **Option B (flat 5×)** is also reasonable as a stepping stone — it prevents the worst case (BSBUSDT 7.3×) and leaves room to tighten further if the aim-bias-balanced session does not materialize.
