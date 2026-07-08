# BETA Phase 2 — Operator Decision Record

## R2 decision

Operator note for R2: "properly do indepth analysis and do deep thinking and do best" — operator delegated the selection to my best judgment after deeper consideration.

**Decision: Option B (structural R:R consultation) with static asymmetric thresholds.**

### Specification

In `_check_direction_lock()` at `src/apex/optimizer.py:1265-1311`, before the existing regime-only veto, consult `package.structural_data` (which now also carries ALPHA's plumbed `trade_direction`):

- For trending_up / trending_down / volatile regimes where the lock would normally fire:
  - Read `structural_data.rr_long`, `rr_short`, and the new `trade_direction`
  - If brain proposed Buy and regime says Sell (Sell→Buy override direction): bail out of lock when `rr_long / rr_short >= 3.0` OR `trade_direction == "long"`
  - If brain proposed Sell and regime says Buy (Buy→Sell override direction): bail out of lock when `rr_short / rr_long >= 10.0` AND `trade_direction == "short"` (require both — protects the worse-WR direction harder)
- Otherwise lock fires as before
- Emit `APEX_LOCK_DECISION_EXPLAINED` event with `regime`, `ratio_long_to_short`, `trade_direction`, `verdict` ("fired" or "bailed_structural")

### Why this (and not Option D advisory or Option E compose-all)

R2 fires FIRST in the chain (lock decision); R3 fires SECOND (override of an already-locked direction). With R3 Option E covering aim-bias-evidence-aware auto-tuning, layering aim-bias evidence redundantly on R2 is over-engineering. The natural pairing is:

- R2 Option B = structural R:R consultation with STATIC asymmetric thresholds (first-line defense; preserves hard-veto semantic; minimal ripple)
- R3 Option E = WR-aware auto-tuning override threshold (second-line defense; encodes aim-bias-evidence)

The two layers compose without redundancy.

Static thresholds (10x Buy→Sell, 3x Sell→Buy) encode the same aim-bias asymmetry as R3 Option E does dynamically. Once R3's WR-aware mechanism is in place, R2's static thresholds can be tuned manually in config based on observed post-fix data; auto-tuning R2 is deferred to a future enhancement (avoid double-feedback loops).

Option D (advisory lock) was rejected because the lock semantic change ripples through layer_manager, strategy_worker, and many existing tests — high implementation risk that does not improve outcome over Option B for the BSBUSDT case.

Option E (compose A+B+C+D) was rejected as over-engineering for first iteration; the project's test-velocity guardrail favors focused fixes over comprehensive ones.

## R3 decision

Operator selected: **Option E — per-direction WR auto-tuning** (the most advanced option).

### Specification

In `src/workers/strategy_worker.py:1671-1717`, the `xray_lock_override_ratio_threshold` resolution becomes dynamic:

- Read measured per-direction WR from the trade_log (last N trades, N TBD — likely 200 per COMPLETE_FINDINGS baseline)
- Compute threshold as a function of WR:
  - For Sell→Buy override (brain wants Buy in locked-Sell direction): threshold = `base * (1 - buy_wr / 100)` — when Buy WR is high, threshold is low (easier override)
  - For Buy→Sell override (brain wants Sell in locked-Buy direction): threshold = `base * buy_wr / 100` — when Buy WR is high, threshold is high (harder override)
- Caps: floor at 2.0x, ceiling at 15.0x to prevent extreme tuning
- Emit `XRAY_OVERRIDE_RATIO_DETAIL` event with `direction`, `buy_wr`, `sell_wr`, `derived_threshold`, `xray_ratio`, `verdict`

### Configuration surface

New settings:
- `xray_lock_override_wr_base` (default 10.0)
- `xray_lock_override_wr_floor` (default 2.0)
- `xray_lock_override_wr_ceiling` (default 15.0)
- `xray_lock_override_wr_window_trades` (default 200 — matches COMPLETE_FINDINGS baseline)

Existing static settings retained for backward-compat fallback when WR data unavailable (e.g., cold start with <30 trades).

## Branch

`fix/r2-r3-apex-direction-lock` (created off HEAD 7320266 at Phase 0).

## Sequencing

BETA Phase 3 begins AFTER ALPHA Phase 4 verification passes — BETA's optimizer consumes ALPHA's plumbed `trade_direction` field.

## Cross-agent interactions

- ALPHA Option E: plumbs `trade_direction` into `StructuralData`. R2 Option B consumes this field in `_check_direction_lock`.
- GAMMA R4: ships LAST after R1+R2+R3 per GAMMA's sequencing recommendation; R4 acts as back-stop.

## Reference deliverables

- `05_lock_fix_options.md` — Option B full specification
- `06_threshold_fix_options.md` — Option E full specification
- `07_beta_synthesis.md` — combined R2+R3 trial behavior
- `08_beta_phase2_report.md` — operator-facing presentation
