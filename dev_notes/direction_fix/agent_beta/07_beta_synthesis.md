# BETA 07 — Synthesis

This document synthesises the BETA investigation. It states where the lock and threshold decisions happen, BETA's recommendations for R2 and R3, how the recommended fixes interact, and the trial behavior the operator can use to verify the fix works.

## Where the lock decision happens

- **File:line**: `src/apex/optimizer.py:1265-1311`
- **Function**: `_check_direction_lock(package, claude_direction, regime) -> tuple[bool, str]`
- **Mechanism**: regime-only decision tree. Returns `(True, reason)` for `trending_up`, `trending_down`, and `volatile` (with a narrow opp-history opt-out for volatile). Returns `(False, "")` for ranging/dead/unknown.
- **Where it is enforced**: optimizer.py:359-371 — post-parse override gate that hard-reverts DeepSeek's flip when the lock is set.
- **Where it is plumbed downstream**: optimizer.py:658-663 (`is_locked`, `lock_reason`) → layer_manager → strategy_worker:1648 (`_apex_locked`).

## Where the threshold decision happens

- **File:line**: `src/workers/strategy_worker.py:1671-1717`
- **Threshold value**: `xray_lock_override_ratio_threshold` (default 10.0), defined at `src/config/settings.py:831`.
- **Flip threshold (3.0)**: `xray_dir_flip_threshold_ratio` (default 3.0), defined at settings.py:817.
- **Effect**: when `_apex_locked=True`, the structural-RR flip path requires `ratio > xray_lock_override_ratio_threshold`. The dead zone for locked trades is 3.0 ≤ ratio < 10.0.

## BETA's recommendation for R2

**Option B — Lock fires only when XRAY ratio supports same direction.**

### Reasoning grounded in aim
The lock currently consults regime alone. The structural-RR data is already on `package.structural_data` (computed by Layer 1B and consumed elsewhere in APEX/strategy_worker). Promoting it into the lock decision:

1. Preserves the project's aim of aggressive opportunity exploitation — locks fire less often, more aim-aligned trades pass through.
2. Directly fixes the BSBUSDT-style failure mode where the lock ignored a 7.3× structural mismatch.
3. Composes cleanly with the existing structural-RR override in strategy_worker (R3 path) — same ratio calculation, same data source.
4. Lowest risk among the strong R2 options: the lock retains its hard-veto when it does fire; it just fires less often.
5. Lowest complexity among the strong R2 options.

Alternative: Option D (advisory lock + asymmetric confidence) is also aim-aligned and arguably more elegant, but requires substantial test changes (the lock's hard-veto semantic is what test_apex_direction_lock.py and test_apex_lock_propagation.py verify). For a single-shot fix with bounded blast radius, Option B is the safer pick.

### Aim-bias evaluation (BETA recommendation)
1. Frequency: YES — locks fire less often.
2. Aggression: YES — brain proposes decisively; the relaxation only happens when structure objectively disagrees.
3. Decision quality: YES — directly addresses the failure mode (lock ignoring structural truth).
4. Passive close: YES — no watchdog or data-lake change.
5. Separation: YES — same data already flows through APEX layer.

## BETA's recommendation for R3

**Option C — Direction-asymmetric threshold.**

### Reasoning grounded in aim
The current threshold is symmetric. The aim-bias evidence is asymmetric: Buys win 55.6 % vs Sells 41.8 % over the last 200 trades. The existing APEX confidence thresholds are asymmetric (Buy→Sell 0.95, Sell→Buy 0.70). Mirroring that asymmetric design at the strategy_worker override level:

1. Preserves frequency in the under-represented (Buy) direction.
2. Encodes the operator's aim-bias evidence into the gate.
3. Composes naturally with the existing asymmetric flip-confidence thresholds.
4. Low risk: the harder Buy→Sell direction (which performs worse) keeps the 10× protective threshold.
5. Prevents all 8 May 16 suppression cases (all 8 were Sell→Buy flips that the asymmetric 3× threshold admits).

Alternative: Option A (flat 3×) is simpler but symmetrically weakens the lock in both directions. The asymmetric Option C preserves the protective behavior for Buy→Sell flips while restoring agency for Sell→Buy flips.

### Aim-bias evaluation (BETA recommendation)
1. Frequency: YES — Sell→Buy frequency rises.
2. Aggression: YES — DeepSeek decisive in the easier direction.
3. Decision quality: YES — directly couples to historical win rate.
4. Passive close: YES.
5. Separation: YES — single function in strategy_worker.

### Concrete threshold values
- `xray_lock_override_ratio_threshold_buy_to_sell: float = 10.0` (unchanged, preserve protection for Buy→Sell direction).
- `xray_lock_override_ratio_threshold_sell_to_buy: float = 3.0` (matches the flip threshold; eliminates dead zone for Sell→Buy direction).
- Resolution rule: select based on `_flipped_dir` (the direction the override would mutate TO).

## How R2 and R3 fixes interact

The two fixes operate at different layers but on the same data:

- **R2 (Option B) lives in optimizer.py:1265-1311.** Prevents the lock from firing in the first place when structure disagrees by ≥ 3.0×.
- **R3 (Option C) lives in strategy_worker.py:1671-1717.** Lowers the threshold for clearing the lock when it has fired.

**Composition**: With both fixes in place:
- Structurally-clear cases (ratio ≥ 3.0× in Sell→Buy direction): R2 prevents the lock from firing AT ALL. Trade enters Buy via DeepSeek's natural flip. The R3 threshold never has to fire because there is no lock to override.
- Structurally-borderline cases (ratio between 2.0× and 3.0× in Sell→Buy direction): R2 does NOT relax the lock (ratio below R2's threshold). R3's asymmetric threshold also requires ratio ≥ 3.0× for the Sell→Buy direction — also does not fire. Trade enters Sell with the locked direction. The original safety behaviour holds for marginal cases.
- Buy→Sell direction (ratio ≥ 3.0×): R2 also relaxes the lock here (structural-RR override is symmetric in Option B). But R3's Buy→Sell threshold is 10.0 (kept high). When the structural ratio is between 3× and 10× in Buy→Sell direction, the trade flips to Sell at APEX (R2 path) — the R3 path is bypassed because the lock did not fire. This is the only composition edge: R2 relaxes Buy→Sell at 3× while R3 keeps it at 10×. The two thresholds for Buy→Sell are inconsistent.

**Decision**: If both R2 (Option B) and R3 (Option C) ship, R2's threshold should ALSO be direction-asymmetric to match R3. So R2 settings become:
- `apex_lock_structural_override_ratio_buy_to_sell: float = 10.0`
- `apex_lock_structural_override_ratio_sell_to_buy: float = 3.0`

This is a small extension to Option B and is the right composition.

If only one fix ships (R3 Option C alone), the lock continues to fire as today; the structural-RR override is the only relaxation path. The 11 lock-overrides and 8 suppressions on May 16 reshape: all 8 Sell→Buy suppressions clear at 3.0×, all 6 Buy→Sell overrides at 10× still fire. Direction shifts ~8 trades toward Buy.

If only R2 ships (Option B alone, with asymmetric thresholds), the lock fires less often. The strategy_worker override threshold stays at 10× — but the lock's reduced fire rate means fewer trades reach the override path. Direction shifts substantially toward balance.

**Both fixes together provide the cleanest outcome AND the strongest redundancy.** The lock fires less often (R2), and when it does fire, the override clears at the right asymmetric threshold (R3).

## Trial behavior specification

### For the BSBUSDT case (verbatim re-run)

Input state at 15:02:04 with R2 + R3 fixes:
- regime=volatile, claude_direction=Sell, structural rr_long=3.7, rr_short=0.5 (ratio 7.4 favoring Long).
- R2 (Option B asymmetric): `_check_direction_lock` reads structural_data, computes ratio for the Sell→Buy direction (7.4 ≥ 3.0). Returns `(False, "")`. **No APEX_DIR_LOCK event.**
- DeepSeek's reasoning: confidence 0.85 in Buy. Post-parse confidence gate (`_enforce_flip_confidence`) for volatile regime — currently this gate's bail-out at line 1486 is `if regime in ("trending_up","trending_down","volatile"): return False, ""`. If R2 + R3 alone don't change this gate, volatile regime still skips post-parse confidence checking. Recommendation: as part of R2 Option B implementation, the volatile bail-out should be retained only when the lock fired. Since the lock did not fire here, the trade enters Buy without the confidence gate having anything to revert.
- APEX returns optimized.direction=Buy. No lock state set.
- strategy_worker reads `_apex_locked=False`. The XRAY override check runs. Ratio 7.4× > 3.0× (the flip threshold). No lock — the flip path fires the BUY direction via the unlocked flip branch (line 1721 `not _apex_locked and _ratio > _flip_threshold`). Trade enters BUY at the structurally-optimal SL/TP.
- Expected outcome: trade in the correct direction. Price moved UP in the next 30 minutes (the May 16 data confirms — the trade went +0.23 % at minute 15). The fixed-behavior trade likely closes at TP rather than SL.

### For a Qwen-Buy attempt in trending_down regime (the SOLUSDT pattern)

Input state at one of the 8 SOLUSDT events:
- regime=trending_down, claude_direction=Sell.
- R2 (Option B): structural_data is consulted. If rr_long is high enough vs rr_short (e.g., 3.0+× toward Long), the lock does NOT fire. DeepSeek's Buy stands.
- If structural_data does NOT favor Long by ≥ 3.0× (e.g., the regime alignment is also structurally supported), the lock FIRES.
- If the lock fires and DeepSeek tries Buy: R3 (Option C) governs. The override threshold for Sell→Buy is 3.0×. If the actual structural ratio is below 3.0×, the override does NOT fire — the trade stays Sell.
- If the actual structural ratio is ≥ 3.0× favoring Long: the override fires; trade flips to Buy.

**Net behavior**: A regime-aligned trade (Sell in trending_down) where the structure also aligns continues to enter as Sell. A regime-aligned trade where the structure DISAGREES at ≥ 3.0× enters as Buy (the structurally-correct direction). The lock no longer over-rules structural truth in either direction.

## Verification queries

After the fix ships, the operator can verify with these grep patterns on a 24-hour log slice:

| Query | Expected (pre-fix) | Expected (post-fix) |
|---|---|---|
| `grep -c "APEX_DIR_LOCK " logfile` | ~80 (May 16 baseline) | ~50-65 |
| `grep -c "APEX_DIR_LOCK_OVERRIDE" logfile` | ~11 | ~3-6 (most reverts replaced by no-lock or flip_accepted) |
| `grep -c "XRAY_FLIP_SUPPRESSED_BY_LOCK" logfile` | ~8 | ~0-2 |
| `grep -c "XRAY_OVERRIDE_LOCK " logfile` | ~6 | ~10-14 (more clears the asymmetric 3× threshold) |
| `grep "APEX_FLIP_DECISION" \| grep "decision_reason=flip_accepted"` count | 0 | ~5-10 (Sell→Buy flips standing) |
| `grep "STRAT_DIRECTIVE" \| awk Buy share` | ~14 % | ~30-40 % |
| `grep "BD_TRADE_HISTORY_PERSIST_OK" \| awk Buy share` | ~9 % | ~25-35 % |

Per-symbol verification:
- BSBUSDT-style cases (volatile + brain=Sell + structural rr_long ≥ 3× rr_short): expect APEX_FLIP_DECISION decision_reason=flip_accepted with apex_dir=Buy. Trade enters Buy. NO XRAY_FLIP_SUPPRESSED_BY_LOCK event.
- SOLUSDT-style cases (trending_down + DeepSeek-tried-Buy): expect either flip_accepted (when structural supports Buy at ≥ 3×) or lock_override (when structural confirms regime). Logs become decision-justified.

## What this fix does NOT do

To be honest about scope:

- Does NOT fix R1 (XRAY counter-trade inversion). The 691 `bullish_fvg_ob_counter` events labeling Long as Short remain a separate problem.
- Does NOT fix R4 (no portfolio direction concentration cap). Even after R2+R3 ship, a 76 % trending_down regime population will still produce a high Sell share; the lack of portfolio-level concentration cap remains.
- Does NOT address regime classification correctness. The lock is correct to defer to regime when structure and history both agree; the regime classifier itself is correct (per FINDINGS).

R2+R3 fix the gating layer that consumes regime — it does not fix the input distribution feeding it.

## Summary

- Recommend **R2 Option B** (lock consults structural-RR; asymmetric thresholds for composition) + **R3 Option C** (asymmetric override threshold).
- Both fixes are local to APEX and strategy_worker; no architectural changes.
- Together they prevent the BSBUSDT case AND eliminate the 8-case dead zone AND restore aim-bias-aligned direction asymmetry.
- Risk profile is LOW. Both fixes are conservative extensions of existing patterns.
- Complexity: 2 + 2 = 4 atomic commits expected (one per setting, one per call site).
- Trial verification: explicit per-pattern grep queries provided.
