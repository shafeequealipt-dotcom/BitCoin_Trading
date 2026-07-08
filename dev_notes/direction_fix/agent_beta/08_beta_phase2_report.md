# BETA Phase 2 Report — APEX_DIR_LOCK and XRAY Override Threshold

This is the operator-facing report for the BETA investigation. It contains two decision requests at the end. Headings use h2 and h3 only; prose is plain.

## What the spec assumed versus current code

The spec described the APEX_DIR_LOCK as a regime-only decision tree at `optimizer.py:1270-1311`. Current code is at `optimizer.py:1265-1311` — off by five lines at the start, but the decision tree body is exactly as the spec described.

The spec described `xray_lock_override_ratio_threshold = 10.0` as the dead-zone driver. Confirmed at `src/config/settings.py:831` and read at `strategy_worker.py:1671-1675`.

One important alignment that the spec did not name explicitly: the asymmetric `apex_min_flip_confidence_buy_to_sell = 0.95` and `_sell_to_buy = 0.70` (settings.py:2203-2204) already encode the operator's aim bias inside APEX. But these thresholds only apply for ranging, dead, and unknown regimes — exactly the regimes the lock does NOT fire on. The asymmetric design exists but never engages on the regimes that produce the Sell bias.

No drift in the file:line references that BETA needs to act on.

## Production evidence

On the 2026-05-16 13:40-18:30 session log:

- 80 APEX_DIR_LOCK events. 71 forced Sell, 9 forced Buy. The Sell share is 89 percent.
- 66 of 80 (83 percent) were the trending_down + Sell alignment lock.
- 11 APEX_DIR_LOCK_OVERRIDE events. DeepSeek tried to flip. The lock vetoed all 11.
- 10 of 11 (91 percent) were Qwen-tried-Buy attempts. SOLUSDT alone produced 8 of those 10 Buy-flip attempts over 1.5 hours — a repeated, model-consistent disagreement that the lock systematically rejected.
- 8 XRAY_FLIP_SUPPRESSED_BY_LOCK events. The strategy_worker structural-RR override said "the other direction has 3.0× to 7.3× better R:R" but the 10× override threshold was not cleared. All 8 trades proceeded in the locked direction. Aggregate PnL on these 8 trades was -$111.98, dominated by BSBUSDT at -$70.08.
- 6 XRAY_OVERRIDE_LOCK events (the override path that DID fire). 4 were ORCAUSDT repeats at 11-498× ratios. The override fires reliably when evidence is extreme; it admits nothing in the 3-10× band.
- 68 of 79 APEX_FLIP_DECISION events had decision_reason `no_flip_attempt`. DeepSeek did not even try to flip in those 68 cases. The remaining 11 were all `lock_override`. Zero `flip_accepted` events — no flip survived the gates in this session.

The BSBUSDT decision chain is reproduced verbatim in deliverable 04. Summary: brain proposed Sell, lock fired under the volatile branch, DeepSeek tried Buy with 0.85 confidence, lock reverted to Sell, structural data showed rr_long=3.7 vs rr_short=0.5 (ratio 7.3 favoring Long), strategy_worker emitted XRAY_FLIP_SUPPRESSED_BY_LOCK, trade entered Sell, price moved up, SL hit at -1.40 percent (-$70.08).

## For R2 — top three options ranked

### Option B — Lock consults structural R:R before firing

Modify `_check_direction_lock()` at optimizer.py:1265-1311 to read `package.structural_data.rr_long` / `rr_short` and bail out when the structural ratio favors the OPPOSITE direction by an asymmetric threshold (3.0× for Sell→Buy direction, 10.0× for Buy→Sell direction). The lock retains its hard-veto when it does fire; it just fires less often. The structural data is already on the package — no new I/O.

Aim-bias score: all five questions YES. Risk: LOW. Complexity: 3 of 5. BSBUSDT case prevented.

### Option D — Advisory lock plus asymmetric confidence

Modify the post-parse override gate at optimizer.py:359-371 to let DeepSeek's flip stand when the parsed confidence clears the asymmetric `apex_min_flip_confidence_buy_to_sell` (0.95) or `_sell_to_buy` (0.70). The lock still fires and is still logged for observability, but it no longer hard-vetoes. The existing asymmetric thresholds already encode the operator's aim-bias intent — this option just routes them through the regimes they currently do not apply to.

Aim-bias score: all five YES. Risk: MEDIUM (existing tests assume hard-veto). Complexity: 4 of 5. BSBUSDT case prevented.

### Option A — Lock fires only when regime confidence above floor

Add a `regime_confidence > 0.60` guard at the top of `_check_direction_lock()` so the lock relaxes for marginal regime classifications. Does NOT address the BSBUSDT case (BSBUSDT regime_conf was 0.86, high). Useful only as an additive.

Aim-bias score: 4 of 5 YES (decision quality is partial). Risk: LOW. Complexity: 2 of 5.

### BETA recommendation for R2

**Option B.** It directly addresses the root cause — the lock function is regime-only and ignores the structural data that is already on the package. Option B promotes the structural-RR check into the lock decision, with asymmetric thresholds that preserve protection for the harder Buy→Sell direction while restoring agency for Sell→Buy. The risk profile is the lowest of the strong options. Composes cleanly with R3 Option C. Prevents BSBUSDT.

Option D is also aim-aligned and arguably more elegant. The reason BETA does not lead with D is that D changes the lock's semantic (from hard veto to advisory) — that change ripples through the existing test suite and the layer_manager / strategy_worker plumbing that depends on `is_locked`. Option B keeps the lock semantic intact and only changes when it fires.

## For R3 — top three options ranked

### Option C — Direction-asymmetric override threshold

Replace the single `xray_lock_override_ratio_threshold = 10.0` with two:
- `xray_lock_override_ratio_threshold_buy_to_sell = 10.0` (unchanged — preserve protection for the worse-performing direction).
- `xray_lock_override_ratio_threshold_sell_to_buy = 3.0` (matches the flip threshold; eliminates dead zone for the better-performing direction).

Resolve the threshold in strategy_worker.py:1671-1675 based on `_flipped_dir`. Mirrors the existing asymmetric APEX flip-confidence design. Prevents all 8 May 16 suppression cases (all 8 were Sell→Buy direction flips that the new 3.0× threshold admits).

Aim-bias score: all five YES. Risk: LOW. Complexity: 2 of 5. BSBUSDT case prevented.

### Option A — Lower the 10× to 3× symmetrically

Single config value change. Collapses the dead zone for both directions. Simpler than Option C but symmetrically weakens the lock in both directions — does not encode the operator's aim-bias asymmetry.

Aim-bias score: all five YES. Risk: LOW-MEDIUM. Complexity: 1 of 5. BSBUSDT prevented.

### Option B — Lower the 10× to 5× compromise

Middle ground. Narrows the dead zone to 3.0-4.99×. Prevents BSBUSDT (7.3×) and ONDOUSDT (6.4×) and PLUMEUSDT (5.0×) but NOT the 3-4× cases (ARBUSDT, SKRUSDT, DYDXUSDT, LDOUSDT, OPUSDT).

Aim-bias score: 4 of 5 (decision quality partial). Risk: LOW. Complexity: 1 of 5. BSBUSDT prevented.

### BETA recommendation for R3

**Option C.** The asymmetric design directly encodes the aim-bias evidence (Buys win 55.6 percent vs Sells 41.8 percent). It composes naturally with the existing asymmetric APEX flip-confidence thresholds and with R2 Option B (which BETA also recommends with the same asymmetric pattern). Prevents all 8 May 16 suppression cases without weakening the Buy→Sell direction protection that the original 10× provided.

Option A is the simpler runner-up if the operator prefers a single-value change. It is more aggressive — it relaxes the lock in both directions — which carries marginally higher risk of admitting Buy→Sell flips that the asymmetric design would still hold.

Option B (the 5× compromise) is acceptable as a more conservative stepping stone but leaves five of the eight May 16 suppression cases unaddressed.

## How R2 and R3 fixes interact

The two fixes are at different layers but share the same data and the same ratio computation.

When BOTH ship:
- Structurally-clear Sell→Buy cases (ratio ≥ 3.0×): R2 prevents the lock from firing. Trade enters Buy via DeepSeek's natural flip. R3 threshold is never tested because no lock exists to override.
- Structurally-clear Buy→Sell cases (ratio ≥ 3.0×): R2 ALSO prevents the lock from firing (its threshold is symmetric in Option B as defined). But R3's Buy→Sell threshold remains 10×. To keep consistency between R2 and R3 thresholds, R2 should use the same asymmetric values as R3.
- Marginal cases (ratio 2.0-3.0×): neither relaxes. Lock fires; trade enters locked direction.

If only R3 ships:
- Lock still fires under regime alone (no R2 relaxation).
- Override threshold is asymmetric. All 8 May 16 Sell→Buy suppressions clear. Direction shifts ~8 trades toward Buy.

If only R2 ships:
- Lock fires less often. When it does fire, the 10× override threshold still gates.
- Direction shifts because the lock relaxation upstream is more impactful than the post-execute override.

**Operator can ship either alone for partial benefit; the combination provides redundancy and the strongest result.**

The trial behavior in deliverable 07 walks through the BSBUSDT case under the combined fix. Net result: the trade enters Buy, the SL location is the structural placement's long_sl_price, and the trade likely captures the move that the post-entry data already showed (+0.23 % at minute 15 of the original trade).

## Decision request one — R2

Which option does the operator approve for R2 — the APEX_DIR_LOCK?

- **Option A**: regime-confidence floor. Marginal improvement. Does not prevent BSBUSDT.
- **Option B**: lock consults structural R:R with asymmetric thresholds. **BETA recommendation.** Prevents BSBUSDT.
- **Option C**: conviction-history-aware lock. Medium complexity. Partial benefit.
- **Option D**: advisory lock with asymmetric confidence gate. Most elegant; higher implementation risk.
- **Option E**: composition of A+B+C+D. Maximum but tunable burden.

## Decision request two — R3

Which option does the operator approve for R3 — the override threshold?

- **Option A**: lower flat 10× → 3×. Simplest. Symmetrically weakens the lock.
- **Option B**: lower flat 10× → 5×. Compromise. Prevents the worst three of eight cases.
- **Option C**: direction-asymmetric (10× Buy→Sell, 3× Sell→Buy). **BETA recommendation.** Encodes aim bias.
- **Option D**: conviction-aware (threshold scales with xray_conf). Principled; medium complexity.
- **Option E**: aim-bias-evidence-aware (per-direction WR). Subsumes Option C; useful if dynamic self-tuning is desired.

BETA awaits the operator's two decisions before drafting implementation commits on the `fix/r2-r3-apex-direction-lock` branch.
