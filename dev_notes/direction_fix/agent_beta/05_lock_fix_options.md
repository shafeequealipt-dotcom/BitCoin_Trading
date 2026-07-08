# BETA 05 — R2 Lock Fix Options (A through E)

This document evaluates five candidate fixes for R2 — the APEX_DIR_LOCK that forces direction from regime alone. Each option is described in mechanism, aim-bias evaluated, and assigned impact, risk, and complexity ratings.

The aim-bias questions answered for each option are:

1. **Frequency**: Does this preserve trade frequency?
2. **Aggression**: Does this preserve decisive brain proposals?
3. **Decision quality**: Does this measurably improve outcomes?
4. **Passive close**: Does this preserve the data-lake watchdog advantage?
5. **Separation of concerns**: Does this respect layer boundaries?

## Option A — Lock fires only when regime confidence > X %

### Mechanism change
- File: `src/apex/optimizer.py:1285-1311` — modify `_check_direction_lock()` to read `package.situation_data.regime_confidence` and bail out (`return False, ""`) when confidence is below a tunable floor.
- Implementation: prepend a guard at the top of the trending branch (line 1290) checking `regime_conf > X`. Default X likely 0.60 or 0.70 based on regime score distribution.
- New setting: `apex_lock_min_regime_confidence: float = 0.60`. Surfaced in config.toml.

### Observable behavior change
- Locks no longer fire when the regime was classified with low confidence (e.g., ADX = 21 just above the trending threshold).
- High-confidence regimes (ADX = 35+) still lock as before.
- Suppresses the "borderline regime" lock cases. BSBUSDT volatile had regime_conf 0.86 (high), so this option would NOT have prevented BSBUSDT.
- Estimated impact on May 16 lock count: ~15-20 % reduction. Most locks were in high-confidence regimes (the dataset shows ADX of 25-40 for the trending_down population).

### Aim-bias evaluation
1. Frequency: YES — locks fire less often, more flips can stand, more trades pass through APEX with the structurally-correct direction.
2. Aggression: YES — brain proposes decisively; only the lock relaxes.
3. Decision quality: PARTIAL — improves quality only in low-confidence regime cases. Does NOT fix the strong-regime + bad-structure case (BSBUSDT).
4. Passive close: YES — no watchdog or data-lake change.
5. Separation: YES — change is local to the lock function inside APEX.

### Expected impact
- Lock block rate: -15 to -20 % from baseline 89 % Sell.
- Direction distribution: small shift toward balance; depends on confidence distribution.
- Buy/Sell ratio: unchanged for the regime-confident sessions.

### Risk
- LOW. Tightening on confidence cannot let bad trades through; it can only relax in marginal cases.
- Risk of letting bad trades through: low.

### Complexity
- 2 of 5. Single function, single new setting, single conditional.

### Verdict
Marginal improvement. Does NOT address the BSBUSDT-style cases. Useful as an additive but insufficient alone.

## Option B — Lock fires only when XRAY ratio supports same direction

### Mechanism change
- File: `src/apex/optimizer.py:1285-1311` — modify `_check_direction_lock()` to consult `package.structural_data.rr_long`, `rr_short`, compute the same `_ratio = rr_opposite / rr_chosen` that strategy_worker uses, and bail out when the ratio exceeds a tunable threshold favoring the OPPOSITE direction.
- Implementation: in the trending alignment branch (line 1292-1293), add a guard `if structural_data and ratio > N: return False, ""`. Default N likely 3.0 (matching the flip threshold).
- New setting: `apex_lock_structural_override_ratio: float = 3.0`. Surfaced in config.toml.

### Observable behavior change
- When the regime says Sell but the structural data shows the opposite direction has 3× or more R:R, the lock does NOT fire. APEX is free to follow DeepSeek's reasoning (which has access to TIAS and direction-breakdown).
- BSBUSDT scenario: rr_long=3.7, rr_short=0.5, ratio=7.4×. With N=3.0, the lock would NOT have fired. APEX would have considered DeepSeek's Buy flip; the post-parse confidence gate (with `apex_min_flip_confidence_sell_to_buy = 0.70` at 0.85 confidence) would PASS. The trade would have been Buy.
- Estimated impact on May 16: 8 of 80 locks would have been suppressed (the 8 XRAY_FLIP_SUPPRESSED_BY_LOCK cases). Lock count drops to ~72. Lock block rate drops to ~85 % Sell.
- For the 11 lock-override Qwen attempts, this option matters only if structural-RR also disagrees. SOLUSDT had no XRAY_FLIP_SUPPRESSED event so structural likely agreed with the lock; those 8 lock-overrides for SOLUSDT would STILL fire. The asymmetric flip-confidence threshold would then need to clear (0.95 Buy→Sell vs 0.70 Sell→Buy) — DeepSeek would need 0.95+ to flip; most SOL flips likely below.

### Aim-bias evaluation
1. Frequency: YES — lock relaxes when structure disagrees, more trades pass through.
2. Aggression: YES — brain proposes decisively; only the lock relaxes.
3. Decision quality: YES — directly improves quality by surfacing structural evidence into the gate that currently ignores it.
4. Passive close: YES — no watchdog or data-lake change.
5. Separation: YES — structural_data is already on `package`; reading it in the lock function does not cross layers. The structure data is computed in Layer 1B and consumed by APEX (Layer 3); this is the same flow.

### Expected impact
- Lock fire count: -10 % (the 8 suppressed cases + a few more).
- Lock OVERRIDE rate: irrelevant for these cases — the lock simply does not fire.
- Direction distribution: shifts toward balance by approximately 10-15 % in trending markets.
- BSBUSDT-style trades: prevented.

### Risk
- LOW. The structure data is already trusted by the strategy_worker override path; promoting it to the lock decision is consistent. The R3 override would still serve as a safety net for cases where the lock does fire.
- Risk of letting bad trades through: low. Trades that have structural support in the OPPOSITE direction are exactly the trades the system currently rejects via SL — admitting the structurally-correct direction is a net win.

### Complexity
- 3 of 5. Requires reading rr_long/rr_short from the package, computing the ratio in a direction-aware way (mirror of strategy_worker.py:1618-1622), and adding a new setting. Manageable.

### Verdict
**Strongest pure-R2 option.** Directly addresses the root cause (lock ignores structural truth). Prevents the BSBUSDT case. Preserves the lock's regime-alignment role for cases where structural data is unavailable or agrees.

## Option C — Lock fires only when conviction history supports same direction

### Mechanism change
- File: `src/apex/optimizer.py:1285-1311` — modify `_check_direction_lock()` to consult per-direction TIAS history (similar to `_check_flip_evidence` but for the CURRENT direction, not the opposite).
- Implementation: in the trending alignment branch, add a guard "if conviction history for `claude_direction` is below a floor, AND opposite direction has stronger history, bail out". Or alternatively "if the symbol's per-direction WR for the locked direction is below 50 % AND the opposite is above 50 %, bail out".
- New setting: `apex_lock_min_direction_wr: float = 0.50` and `apex_lock_min_direction_trades: int = 5`.

### Observable behavior change
- When BSBUSDT's Sell history showed 1W/5L (20 % WR), the lock would not fire — the conviction history disagrees with the locked direction.
- For SOLUSDT (8 Buy-flip blocks), if SOL Buy history has ≥ 5 trades AND ≥ 50 % WR, the lock would not fire and DeepSeek's flip would stand.
- Per-coin direction-aware history is already partially available via `_check_flip_evidence` (which checks opposite-direction). This option extends to per-direction-WR-of-claude-direction.

### Aim-bias evaluation
1. Frequency: YES — locks fire less often when history says the locked direction is bad for this coin.
2. Aggression: YES — brain proposes decisively; only the lock relaxes.
3. Decision quality: YES — directly couples lock decision to per-coin per-direction empirical win rate. Closes the feedback loop the COMPLETE_FINDINGS noted (where prior Sell-biased losses inflate Sell history but should not license further Sells).
4. Passive close: YES — no watchdog or data-lake change.
5. Separation: YES — TIAS history already lives on the package.

### Expected impact
- Lock fire count: ~20-30 % reduction depending on per-coin history depth.
- For coins with sufficient history (≥ 5 trades each direction), the lock becomes a per-coin per-direction decision.
- For coins with insufficient history (first-time setup), the lock fires as before.

### Risk
- MEDIUM. Sample-size bias: a small streak of wins on one direction can erroneously license that direction. The `apex_min_trades_for_flip = 5` is already in play for the flip path; reusing the same minimum is consistent.
- Risk of letting bad trades through: medium. A few wins on Sell could let a Sell stand against a Buy-favoring regime. Mitigated by the 5-trade minimum.

### Complexity
- 3 of 5. Requires per-direction breakdown on the package. The data is already loaded (assembler reads trades); only the analysis is new.

### Verdict
Useful but not load-bearing alone. Most useful as a combination input (Option E).

## Option D — Lock becomes advisory; Qwen can override at threshold

### Mechanism change
- File: `src/apex/optimizer.py:352-371` — remove the hard override at line 365 (`optimized.direction = claude_direction`) and replace with a confidence threshold check. If `optimized.confidence > threshold`, keep DeepSeek's flip; emit a different event.
- The pre-call lock still fires (still emits `APEX_DIR_LOCK` for observability) but does NOT mutate direction post-parse if DeepSeek's confidence is high enough.
- Reuses the existing asymmetric `_resolve_flip_threshold()` — Buy→Sell needs 0.95, Sell→Buy needs 0.70.
- Critically: the existing post-parse `_enforce_flip_confidence()` becomes responsible for ALL regimes, not just ranging/dead/unknown. The trending/volatile bail-out at optimizer.py:1486-1488 would be removed.

### Observable behavior change
- BSBUSDT: DeepSeek's 0.85 confidence on a Sell→Buy flip > 0.70 threshold. Flip stands. Trade is Buy.
- SOLUSDT: 8 of 8 Buy-flip attempts were at unknown confidence (not surfaced in log fields). If those flips had ≥ 0.70 confidence, they would stand. If < 0.70, they would be reverted.
- For trending_down regime with brain=Sell and DeepSeek tries Buy: needs 0.70+ confidence. Easier flip. Asymmetric in favor of Buy direction — aligns with the aim-bias data (Buys 55.6 % WR vs Sells 41.8 %).

### Aim-bias evaluation
1. Frequency: YES — more flips stand; brain proposals are not undone unconditionally.
2. Aggression: YES — DeepSeek's intelligence is actually consumed; reasoning is preserved.
3. Decision quality: YES — couples to confidence which DeepSeek had to justify. Aligns with the existing asymmetric design.
4. Passive close: YES — no watchdog or data-lake change.
5. Separation: YES — change is local to APEX flip discipline.

### Expected impact
- Lock OVERRIDE event count: drops dramatically (most overrides become decision_reason=flip_accepted instead of decision_reason=lock_override).
- Buy share of orders: rises significantly (SOL alone might add 5+ Buys; BSBUSDT becomes Buy; others follow).
- Direction distribution: substantial shift toward balance.

### Risk
- MEDIUM. The lock was designed to STOP DeepSeek from over-flipping. Making it advisory removes that brake. The asymmetric confidence threshold is the new brake; if DeepSeek's calibration drifts, more bad flips could land.
- Risk of letting bad trades through: medium. Mitigated by the asymmetric thresholds (which already encode aim bias).
- Existing test `test_apex_direction_lock.py` may need updates; the lock semantic changes from "hard lock" to "advisory lock with confidence gate".

### Complexity
- 4 of 5. Requires changing the post-parse override gate, threading the confidence gate through trending/volatile regimes, and updating tests.

### Verdict
**The cleanest aim-aligned option.** Consumes the existing asymmetric flip-confidence infrastructure (which encodes the operator's aim bias correctly). The lock continues to exist as an observability + safety belt but no longer hard-vetoes intelligence below a justified confidence.

## Option E — Combine A + B + C + D

### Mechanism change
- All four mechanisms compose. The lock fires only when:
  - Regime confidence > X% (Option A), AND
  - Structural-RR does NOT favor the opposite direction by ≥ N× (Option B), AND
  - Per-direction TIAS history does not contradict the locked direction (Option C).
- If the lock DOES fire under all those conditions, then DeepSeek can still override at the asymmetric confidence threshold (Option D).

### Observable behavior change
- The lock becomes a layered defense: only fires when regime AND structure AND history all agree. Even then, DeepSeek can override at the right confidence.
- Maximum reduction in spurious locks; maximum aim-bias alignment.

### Aim-bias evaluation
1. Frequency: YES — most locks become advisory; most flips stand.
2. Aggression: YES — brain and DeepSeek decisive.
3. Decision quality: YES — the lock no longer fires when ANY evidence stream disagrees.
4. Passive close: YES — no watchdog change.
5. Separation: YES — all changes inside APEX.

### Expected impact
- Lock fire count: -40 to -50 % from baseline.
- Direction distribution: substantial shift toward balance; near-natural in trending sessions.
- Risk: the lock might be too relaxed for the cases it was designed for (e.g., strong regime + DeepSeek hallucination).

### Risk
- MEDIUM-HIGH. Composition risk: each option is reasonable in isolation, but stacking them may relax the lock past a useful threshold.
- Many new settings (4 new tunables); higher cognitive load on the operator to tune.

### Complexity
- 5 of 5. Multiple coordinated changes.

### Verdict
The most thorough; also the most complex. Recommended only if the operator wants maximum aim alignment AND can carry the tuning burden.

## R2 ranking — recommendation summary

| Option | Aim alignment | Risk | Complexity | BSBUSDT-prevents? |
|---|---|---|---|---|
| A — regime confidence gate | LOW | LOW | 2 | NO |
| B — structural-RR gate | **HIGH** | LOW | 3 | **YES** |
| C — conviction history | MEDIUM | MEDIUM | 3 | PARTIAL |
| D — advisory lock + confidence | **HIGH** | MEDIUM | 4 | **YES** |
| E — composition of A+B+C+D | MAX | M-HIGH | 5 | YES |

The top two for BETA's R2 recommendation are Option B (structural-RR gate) and Option D (advisory + asymmetric confidence). Both prevent the BSBUSDT case. Option B is simpler and more conservative (lock still hard-vetoes when fired, just fires less often). Option D is the cleanest aim-bias-aligned redesign (lock becomes advisory and consumes existing asymmetric thresholds).

If a single option must be chosen, **BETA leans toward Option B** because (a) it directly addresses the lack of structural awareness, (b) the implementation reuses the same ratio computation already present in strategy_worker.py, (c) the risk profile is the lowest of the strong options, and (d) it can be safely composed with R3 changes without coordination issues.

If the operator wants the more aggressive redesign, **Option D** is the principled choice — but it carries higher implementation risk and may break existing tests in non-trivial ways.
