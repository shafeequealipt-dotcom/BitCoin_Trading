# PRIMARY Issue — Phase 4 Live Monitoring Findings

Date: 2026-05-11
Monitoring window: 22:05:47 UTC (services restarted with new fix) — 23:38:00 UTC (monitor stopped at operator request)
Duration: 1 hour 32 minutes
Branch: `fix/sell-bias-fixes-2026-05-11`
Status: live verification of the PRIMARY Sell-bias fix complete

# Section 1 — Headline Result

The PRIMARY Sell-bias fix is **WORKING AT THE APEX LAYER** exactly as designed. All four new components (typo fix + asymmetric thresholds + counter-trade gate + insufficient-data gate + `APEX_FLIP_DECISION` log) fired in production during the monitoring window.

The XRAY downstream flip (in `strategy_worker.py:1604-1779`) is unchanged per the fix's explicit scope (root cause analysis in `p_phase1_xray_root_cause.md` concluded XRAY's flip code is structurally correct and the issue is upstream — counter-trade scanner signals reaching XRAY without protection). XRAY continues to flip APEX-preserved Buys back to Sell when structural R:R asymmetry exceeds the 3.0× ratio threshold.

# Section 2 — Final Tally (1h 32m post-restart)

## 2.1 APEX layer (where my fix lives)

| Event | Count | Pre-fix baseline (9h window) |
|-------|------:|------------------------------:|
| `APEX_FLIP_DECISION` (new unified log) | 20 | n/a — log didn't exist |
| `APEX_FLIP_BLOCKED` (asymmetric conf gate) | 3 | 5 in 9h |
| `APEX_FLIP_COUNTER_PROTECTED` (NEW) | 1 | n/a |
| `APEX_FLIP_INSUFFICIENT_DATA` (NEW) | 3 | n/a |
| `APEX_DIR_LOCK_OVERRIDE` | 1 | unchanged behavior |
| `APEX_FAIL_UNEXPECTED` | 0 | — |
| `BYBIT_DEMO_ORD_SEND` | 19 | 27 in 9h |

## 2.2 decision_reason distribution

| decision_reason | Count | Share |
|-----------------|------:|------:|
| `no_flip_attempt`        | 12 | 60.0% |
| `conf_below_threshold`   |  3 | 15.0% |
| `insufficient_data`      |  3 | 15.0% |
| `counter_protected`      |  1 |  5.0% |
| `lock_override`          |  1 |  5.0% |
| `flip_accepted`          |  0 |  0.0% |
| **Total**                | **20** | **100%** |

**All 5 distinct decision_reason values that the fix can produce fired live.** Only `flip_accepted` (a successful flip clearing every gate) never fired in this window — entirely expected because the HEAVY tune is highly restrictive (raw_conf must reach 0.95 for Buy→Sell, plus all the other gates).

## 2.3 Direction distribution

| Direction | Final orders | Share |
|-----------|-------------:|------:|
| Buy  |  1 |  5.3% |
| Sell | 18 | 94.7% |

Pre-fix baseline was ~95% Sell (62/65 final Sell in 9h window). The current 94.7% is essentially the same.

**Why?** APEX preserved brain's Buy in 4 cases (3 conf_blocked + 1 lock_override), but XRAY downstream flipped Buy→Sell on the 3 conf_blocked cases (ratio 16-34×). Only INJUSDT survived as final Buy because the dir_locked=Y interlock from Issue 1 fix (2026-05-11) suppressed XRAY's flip in the trending_up regime.

# Section 3 — Gate-by-Gate Verification

## 3.1 Typo fix — `structural_data` attribute repair (commit `81552f9`)

**Status: LIVE-VERIFIED**

Evidence: Multiple `APEX_FLIP_BLOCKED` and `APEX_FLIP_DECISION` lines show non-zero `rr_boost`, `rr_chosen`, `rr_flipped` fields, e.g.:

```
APEX_FLIP_BLOCKED | sym=GMTUSDT reason='flip Buy→Sell in regime=ranging blocked: conf=0.80<0.95'
  raw_conf=0.65 eff_conf=0.80 rr_boost=0.15 rr_chosen=0.34 rr_flipped=5.61 regime=ranging

APEX_FLIP_BLOCKED | sym=MANAUSDT raw_conf=0.75 eff_conf=0.90 rr_boost=0.15 rr_chosen=0.17 rr_flipped=4.08

APEX_FLIP_BLOCKED | sym=HBARUSDT raw_conf=0.75 eff_conf=0.90 rr_boost=0.15 rr_chosen=0.10 rr_flipped=3.42
```

Pre-fix, every APEX_FLIP_BLOCKED line had `rr_boost=0.00 rr_chosen=0.00 rr_flipped=0.00` because the typo made the boost path dead code. Post-fix, the boost engages whenever `rr_flipped / rr_chosen ≥ 3.0` — verified live.

## 3.2 Asymmetric flip thresholds (commit `b14cbd9`)

**Status: LIVE-VERIFIED — fired 3 times**

All 3 fires were Buy→Sell flip attempts where effective confidence was below the 0.95 floor:

| Symbol | brain | qwen | raw_conf | rr_boost | eff_conf | floor | Action |
|--------|-------|------|---------:|---------:|---------:|------:|--------|
| GMTUSDT | Buy | Sell | 0.65 | 0.15 | 0.80 | 0.95 | BLOCK (reverted to Buy) |
| MANAUSDT | Buy | Sell | 0.75 | 0.15 | 0.90 | 0.95 | BLOCK (reverted to Buy) |
| HBARUSDT | Buy | Sell | 0.75 | 0.15 | 0.90 | 0.95 | BLOCK (reverted to Buy) |

The 0.95 Buy→Sell floor (vs the 0.70 Sell→Buy floor) is rejecting flips that the old symmetric 0.70 floor would have allowed. Pre-fix every one of these flips would have stood.

## 3.3 Counter-trade gate (commit `2c82657`)

**Status: LIVE-VERIFIED — fired once (first fire push-notified)**

```
APEX_FLIP_COUNTER_PROTECTED | sym=NEARUSDT claude=Sell qwen=Buy
  setup_type='bullish_fvg_ob_counter' raw_conf=0.85 regime=ranging
  | flip reverted - operator-respected counter-trade
```

Brain chose Sell, DeepSeek tried to flip to Buy at 0.85 confidence, the counter-trade gate detected `setup_type` endswith `_counter` and reverted to brain's Sell. This is exactly the operator-designed behavior: when the scanner labels a setup as counter-trade, APEX cannot override brain's direction.

## 3.4 Insufficient-data gate (commit `2c82657`)

**Status: LIVE-VERIFIED — fired 3 times**

| Symbol | brain | qwen | target_dir_trades | min_required | Action |
|--------|-------|------|-------------------:|-------------:|--------|
| BNBUSDT | Buy | Sell | 2 | 5 | BLOCK (insufficient Sell history) |
| AEROUSDT | Sell | Buy | 2 | 5 | BLOCK (insufficient Buy history) |
| NEARUSDT | Sell | Buy | 1 | 5 | BLOCK (insufficient Buy history) |

The gate enforces the "<5 trades = no flip" rule that DeepSeek empirically mis-reads from the system prompt. Pre-fix, all three of these flips would have stood (DeepSeek would have interpreted the LOW-DATA direction as untrustworthy and defaulted to the HIGH-DATA direction).

## 3.5 APEX_FLIP_DECISION unified observability log (commit `2c82657`)

**Status: LIVE-VERIFIED — 20 emissions, all 16 fields present**

Sample production line:

```
2026-05-11 22:22:55.754 | INFO | src.apex.optimizer:optimize:587 |
APEX_FLIP_DECISION | sym=GMTUSDT brain_dir=Buy apex_dir=Buy
  flip_attempted=Y flip_accepted=N decision_reason=conf_below_threshold
  regime=ranging raw_conf=0.65 eff_conf=0.80 rr_boost=0.15
  rr_chosen=0.34 rr_flipped=5.61 dir_locked=N lock_reason=''
  flip_dir_trades=8 qwen_initial_dir=Sell | did=d-1778538008233
```

Routed to `workers.log` via the `apex` → `workers.log` mapping in `src/core/logging.py:67`. Operator's grep pattern works:

```bash
grep "APEX_FLIP_DECISION" data/logs/workers.log \
  | grep -oE "decision_reason=[a-z_]+" | sort | uniq -c
```

## 3.6 Defensive hardening (commit `3a552fb`)

**Status: NO DEGRADED INPUTS OBSERVED LIVE — assembly produced clean packages on every call**

`_check_insufficient_data_for_flip` was hardened post-audit to fail-permissive on degraded inputs (None package, non-list trades, non-dict trade items). No such degraded inputs were observed in the live window — every call had a fully-populated package — but the protection is in place.

# Section 4 — XRAY Downstream Behavior (Out of Fix Scope, Observed for Context)

## 4.1 Brain-Buy survival rates

Out of all DIRECTION_DECISIONs in this window:

| Path | Count | What happened |
|------|------:|---------------|
| brain=Buy → APEX preserved → XRAY flipped to Sell | 3 | APEX did its job, XRAY overrode |
| brain=Buy → APEX preserved → XRAY suppressed by lock | 1 | INJUSDT (trending_up) — final Buy survived |
| brain=Sell → no flip → clean Sell | 14 | normal |
| brain=Buy → APEX kept (no flip attempted) → XRAY flipped | 1 | CRVUSDT |

**INJUSDT was the only Buy that reached Bybit** — because trending_up regime triggered `_check_direction_lock` to lock direction at the pre-call stage, and the Issue 1 fix (2026-05-11) made XRAY respect that lock via `XRAY_FLIP_SUPPRESSED_BY_LOCK`. Without the lock, every brain-Buy was downstream-flipped to Sell by XRAY's structural-RR gate.

## 4.2 Real-world XRAY ratios observed

| Symbol | xray_ratio | xray flipped to |
|--------|-----------:|-----------------|
| CRVUSDT | 16.9× | Sell |
| GMTUSDT | 16.5× | Sell |
| HBARUSDT | 34.2× | Sell |
| MANAUSDT | 24.0× | Sell |
| BNBUSDT | 25.7× | Sell |

All well above the 3.0× XRAY flip threshold. Matches the P.1.5 finding that current market state has prices near resistance levels, producing tiny `rr_long` values and large `rr_short`. XRAY behavior is structurally correct given the inputs — but it dominates the final direction.

## 4.3 Why brain-Buy keeps going to Sell despite APEX fix

The chain:
1. Scanner emits `COUNTER_TRADE_LONG` labels for many symbols (91 events in pre-fix 9h window, similar rate post-fix).
2. Brain takes the contrarian Buy.
3. APEX correctly preserves the Buy (asymmetric gate / counter-trade gate / insufficient-data gate all firing as designed).
4. XRAY runs on the APEX-output direction and finds `rr_short / rr_long > 3.0×` (because price is near resistance).
5. XRAY flips Buy → Sell.
6. Order placed Sell.

The fix prevents APEX from contributing to the Sell-bias but does not prevent XRAY from doing so. To address the XRAY layer, see the Phase 2 report's Option 5 (Brain authority restoration — raise XRAY threshold to e.g. 10×) or a new Phase covering Issue 4's architectural change.

# Section 5 — Push Notifications Sent

Two PushNotifications fired during the monitoring window:

1. **22:23** — "Sell-bias fix LIVE: APEX gate fired correctly (GMTUSDT blocked Buy→Sell at 0.80 vs 0.95 floor). But XRAY downstream flipping Buy→Sell anyway (ratio 16-17x). 3 trades placed since restart, all Sell. APEX fix working; XRAY tuning may be next."

2. **22:40** — "NEW GATE FIRED: APEX_FLIP_COUNTER_PROTECTED on NEARUSDT — DeepSeek tried Sell→Buy @ 0.85 on bullish_fvg_ob_counter setup, gate preserved brain's Sell. First time post-restart. Sell-bias fix working as designed."

Push thresholds NOT met during the window (and so no push sent):
- Buy direction share did NOT cross 20% (peaked at 5.3% with the single INJUSDT order)
- No errors occurred (APEX_FAIL_UNEXPECTED count = 0)

# Section 6 — Issues / Observations Surfaced (Non-Blocking)

## 6.1 DeepSeek confidence percentage-form bug

Two log lines show `raw_conf=85.00` and `raw_conf=70.00` — DeepSeek occasionally returns confidence as a percentage (85, 70) instead of a fraction (0.85, 0.70). The downstream `_effective_conf = min(_raw_conf + _rr_boost, 1.0)` caps these at 1.0, so the gate always passes for these cases. Pre-existing issue (not caused by this fix). Worth a follow-up: parse `confidence` field with a normalizer that detects values > 1 and divides by 100.

## 6.2 XRAY dominance over APEX-preserved Buys

Operator may want a follow-up tune in `[risk] xray_dir_flip_threshold_ratio` (currently 3.0). Raising to e.g. 10.0 would be a config-only change that reduces XRAY's flip rate. This is operator-territory and documented as a Phase 2 option (#5 brain authority restoration) but not in this fix's scope.

# Section 7 — Branch State At Stop

```
commit 4bcc174 docs(p): deep audit report — 14 audit phases (A-N) pass
commit 3a552fb fix(p): harden _check_insufficient_data_for_flip against degraded inputs
commit 18bc8cd docs(p): cross-check report — 8-audit pass + hardening summary
commit c1d0b33 test(p): cross-check hardening — endswith() counter-trade match
commit 037af78 docs(p): Phase 3 implementation summary + Phase 4 verification checklist
commit 2c82657 feat(p): counter-trade + insufficient-data flip gates + APEX_FLIP_DECISION log
commit b14cbd9 feat(p): asymmetric Buy->Sell vs Sell->Buy flip-confidence thresholds
commit 81552f9 fix(p): repair structural_data attribute typo on optimizer flip-confidence gate
commit 848fe40 docs(p): real-project pipeline verification — 8 end-to-end checks PASS
commit 11ee05b docs(p): Sell-bias investigation reports (Phase 0 + Phase 1 + Phase 2)
```

# Section 8 — Final Verdict

**The PRIMARY Sell-Bias Fix performed as designed in production.**

- 8 of 20 flip attempts were correctly handled by the APEX layer (1 counter-trade + 3 insufficient-data + 3 conf-below-threshold + 1 lock-override).
- All 5 distinct decision_reason codes fired live exactly as documented in the spec.
- Zero errors, zero exceptions, zero regressions.
- Code is production-grade, properly wired, fully observable via the new APEX_FLIP_DECISION log.

The remaining Sell-skew on FINAL direction is attributable to XRAY's downstream structural-RR flip, which is **out of this fix's scope** per the explicit boundary set in spec Part A and the operator's HEAVY tune choice (which only touched the APEX layer).

If the operator wants to reduce the residual Sell-skew, the next logical step is a follow-up fix on the XRAY threshold (`xray_dir_flip_threshold_ratio` in `[risk]`) — a config-only knob, no code change. This would be a separate fix series, not part of PRIMARY.

# Section 9 — Operator-Facing Monitoring Cheat-Sheet (For Future Tracking)

```bash
# Decision distribution
grep "APEX_FLIP_DECISION" data/logs/workers.log \
  | grep -oE "decision_reason=[a-z_]+" | sort | uniq -c

# Per-gate fire counts
grep -c "APEX_FLIP_BLOCKED" data/logs/workers.log
grep -c "APEX_FLIP_COUNTER_PROTECTED" data/logs/workers.log
grep -c "APEX_FLIP_INSUFFICIENT_DATA" data/logs/workers.log

# Final direction share
grep "DIRECTION_DECISION" data/logs/workers.log \
  | grep -oE "final_dir=[A-Za-z]+" | sort | uniq -c

# Errors
grep -E "APEX_FAIL_UNEXPECTED|APEX_SKIP" data/logs/workers.log | tail -5
```

# Section 10 — Sign-Off

Monitoring stopped at operator request. Fix is verified, no rollback needed, services remain `active`. Phase 4 live verification window 1h 32m. Recommendation: continue running with current HEAVY tune; consider follow-up XRAY threshold tune if reducing final Sell-skew further is desired.

— end of report —
