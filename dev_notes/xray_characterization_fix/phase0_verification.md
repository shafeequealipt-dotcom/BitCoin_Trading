# Phase 0 — Pre-implementation Verification

**Date:** 2026-04-30 22:15 UTC
**Tag:** `pre-xray-counter-setup-fix` at commit `7c42a0c`
**Goal:** verify each issue X1–X6 against current code state and capture live baselines so Phases 1–6 can be measured against ground truth.

User directive: **do not depend on the markdown forensic files** — re-derive findings from current code, live logs, and probe.

---

## Live baseline (5 cycles 21:45 → 22:05 on 2026-04-30)

**SCANNER_FILTER_AGGREGATE (per cycle):**

| Cycle | qualified | pass_xray | fail_setup_none | fail_consensus | fail_rr |
|-------|-----------|-----------|-----------------|----------------|---------|
| 21:45 | 1 | 20 | 30 | 16 | 3 |
| 21:50 | 1 | 20 | 30 | 16 | 3 |
| 21:55 | 0 | 20 | 30 | 13 | 7 |
| 22:00 | 3 | 19 | 31 | 11 | 5 |
| 22:05 | 2 | 19 | 31 | 13 | 4 |
| **avg** | **1.4** | **19.6** | **30.4** | **13.8** | **4.4** |

**XRAY_CLASSIFY_SUMMARY (per cycle):**

| Cycle | none | bearish_fvg_ob | bullish_fvg_ob | bearish_structural_break | conf_p50 | conf_p95 |
|-------|------|----------------|----------------|--------------------------|----------|----------|
| 21:50 | 30 | 15 | 5 | 0 | 0.00 | 0.55 |
| 21:55 | 30 | 15 | 5 | 0 | 0.00 | 0.55 |
| 22:00 | 31 | 12 | 6 | 1 | 0.00 | 0.55 |
| 22:05 | 31 | 12 | 6 | 1 | 0.00 | 0.55 |
| 22:10 | 31 | 12 | 6 | 1 | 0.00 | 0.55 |
| **avg** | **30.6** | **13.2** | **5.6** | **0.6** | 0.00 | 0.55 |

**XRAY_NONE_REASON first-failure distribution (last 100 lines):**

| Bucket | Count | Share |
|--------|-------|-------|
| `no_fresh_bullish_fvg` | 47 | 47% |
| `no_fresh_bearish_ob` | 21 | 21% |
| `no_fresh_bearish_fvg` | 19 | 19% |
| `no_bullish_bos` | 9 | 9% |
| `no_fresh_bullish_ob` | 3 | 3% |
| `no_bearish_bos` | 1 | 1% |
| **Total** | **100** | **100%** |

→ **66% missing-FVG, 24% missing-OB, 10% missing-BoS.** Aligns with Bucket 2 prediction that ~84% of NONE failures are FVG/OB-related.

**Probe baseline:** `dev_notes/xray_characterization_fix/phase0_probe_baseline.log`. All 6 NONE coins (BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, DOGEUSDT, AAVEUSDT) confirmed missing the in-direction nearest zone in the engine-style filter. All 6 PASS coins (BNBUSDT, ADAUSDT, LINKUSDT, DYDXUSDT, BCHUSDT, NEARUSDT) confirmed having both in-direction zones present.

**Implication for Phase 4 lift:** the dominant failure mode (47% — `no_fresh_bullish_fvg` on uptrend coins like BTCUSDT/ETHUSDT/SOLUSDT) maps directly to candidates for `BEARISH_FVG_OB_COUNTER` (suggested=long, only bearish zones near price). The 19% mirror (`no_fresh_bearish_fvg` on downtrend coins) maps to `BULLISH_FVG_OB_COUNTER`. Together: 66% of NONE coins are counter-setup candidates by mechanism.

---

## Issue verification (X1 – X6)

### X1 — `_find_nearest_*` direction-locked AND distance-bounded

**Status:** ACTIVE. Confirmed verbatim.

| Function | File:line | Body |
|---|---|---|
| `_find_nearest_fvg` | `src/analysis/structure/structure_engine.py:554–569` | Static. `direction` parameter forces `expected = "bullish" if direction == "long" else "bearish"`. Returns first FVG matching `not filled AND fvg.direction == expected AND dist < 2.0%`. Returns `None` otherwise. Information about counter-direction zones is lost. |
| `_find_nearest_ob` | `src/analysis/structure/structure_engine.py:572–587` | Mirror. `dist < 3.0%`. |

**Callers:** `structure_engine.py:386–387` only. Two call sites in `analyze()`. → Phase 3 atomic call site update is safe.

### X2 — Fixed-percentage distance windows miscalibrated for volatility

**Status:** ACTIVE. Confirmed.

- FVG window hardcoded `2.0` at `structure_engine.py:568`.
- OB window hardcoded `3.0` at `structure_engine.py:586`.
- No ATR-aware logic anywhere in `_find_nearest_*` or surrounding code.
- `atr_pct` is **not** in StructuralAnalysis. It exists at `src/analysis/volatility_profile.py:38–39` (`atr_pct_5m`, `atr_pct_1h`) on the `VolatilityProfile` cache produced by a separate worker — not threaded into the structure pipeline.
- Probe data (last close, ATR%):

| Coin | ATR% | 2.0% as ATR multiple |
|---|---|---|
| BTCUSDT | 0.42% | 4.8 ATR (too loose) |
| BNBUSDT | 0.29% | 6.9 ATR (very loose) |
| DYDXUSDT | 1.30% | 1.5 ATR (too tight) |
| DOGEUSDT | 0.98% | 2.0 ATR (borderline tight) |

**Decision (Phase 2):** compute H1 NATR inline inside `engine.analyze()` using existing candles array. Do not depend on volatility_profile worker (avoids coupling + cold-start issues).

### X3 — `SetupType` enum missing counter variants

**Status:** ACTIVE. Confirmed.

`src/analysis/structure/models/structure_types.py:13–37`:

```python
class SetupType(str, Enum):
    NONE = "none"
    BULLISH_FVG_OB = "bullish_fvg_ob"
    BULLISH_STRUCTURAL_BREAK = "bullish_structural_break"
    BULLISH_LIQUIDITY_SWEEP = "bullish_liquidity_sweep"
    BULLISH_RANGE_BREAKOUT = "bullish_range_breakout"
    BEARISH_FVG_OB = "bearish_fvg_ob"
    BEARISH_STRUCTURAL_BREAK = "bearish_structural_break"
    BEARISH_LIQUIDITY_SWEEP = "bearish_liquidity_sweep"
    BEARISH_RANGE_BREAKDOWN = "bearish_range_breakdown"
```

**Type:** `(str, Enum)` — string mixin. New variants must follow `lower_snake = "lower_snake"` value pattern.

**Consumers verified (grep `SetupType\.`):**
- `src/analysis/structure/structure_engine.py` (return statements at lines 776, 788, 800, 809, 815, 818, 829, 834, 836)
- `src/analysis/structure/models/structure_types.py:496` (StructuralAnalysis default)
- `src/workers/structure_worker.py:147,151` (.value access)
- `src/core/coin_package.py:38` (string field)
- `src/brain/strategist.py:~1211` (display)
- Tests across `tests/test_phase2_layer1_restructure/`, `tests/test_setup_classifier_diagnose.py`

**Phase 1 plan:** add `BULLISH_FVG_OB_COUNTER = "bullish_fvg_ob_counter"` and `BEARISH_FVG_OB_COUNTER = "bearish_fvg_ob_counter"`. No consumer breaks (unknown variants simply don't match any switch).

### X4 — `classify_setup()` decision tree has no counter branches

**Status:** ACTIVE. Confirmed at `src/analysis/structure/structure_engine.py:676–836`.

**Current decision tree (ASCII):**

```
classify_setup(analysis)
    │
    ├─ 1. BULLISH_FVG_OB           if direction=long  AND nearest_fvg=bullish (unfilled)
    │                                              AND nearest_ob =bullish (fresh)
    │                                              AND _bull_alignment AND mtf>=fvg_ob_min
    │       confidence = min(mtf, max(smc, 0.5))
    │
    ├─ 2. BEARISH_FVG_OB           mirror for short
    │
    ├─ 3. BULLISH_STRUCTURAL_BREAK if last_bos.direction=bullish AND direction=long
    │                                              AND (NOT require_retest OR significance==major)
    │       confidence = max(mtf, smc, 0.5)
    │
    ├─ 4. BEARISH_STRUCTURAL_BREAK mirror for short
    │
    ├─ 5. BULLISH_LIQUIDITY_SWEEP  if active_sweep.depth>=sweep_min_pct AND
    │                                              sweep.type=bullish_sweep AND direction=long
    │
    ├─ 6. BEARISH_LIQUIDITY_SWEEP  mirror for short
    │
    ├─ 7. BULLISH_RANGE_BREAKOUT   if pos_in_range>=0.95 AND direction=long
    │                                              AND total_confluence>=breakout_min/2
    │
    ├─ 8. BEARISH_RANGE_BREAKDOWN  mirror at pos_in_range<=0.05
    │
    └─ 9. NONE                     fall-through, confidence=0.0
```

**Insertion point for counter branches (Phase 4):** between branch 2 (BEARISH_FVG_OB) and branch 3 (BULLISH_STRUCTURAL_BREAK). Two new branches:
- `2.5 — BULLISH_FVG_OB_COUNTER` (when direction=short, counter zones are bullish)
- `2.6 — BEARISH_FVG_OB_COUNTER` (when direction=long, counter zones are bearish)

These activate only when in-direction branches 1–2 fell through (in-direction zones missing). Confidence ×0.7 per `counter_confidence_multiplier`.

### X5 — `XRAY_NONE_REASON` is one-liner blame, not structured evidence

**Status:** ACTIVE. Confirmed.

Emitted at `src/workers/structure_worker.py:164–172`:

```
XRAY_NONE_REASON | sym=<sym> closest_type=<X> missed_by='<reasons>' weakest_input=<X>
                   mtf=<x.xx> smc=<x.xx> direction=<X> structure=<X>
```

**Missing evidence (Phase 6 will add):**
- `in_direction_fvg=<missing|filled|too_far>`
- `in_direction_ob=<missing|stale|too_far>`
- `counter_direction_fvg=<missing|filled|too_far>`
- `counter_direction_ob=<missing|stale|too_far>`
- `last_bos_significance=<major|minor|none>`
- `last_bos_age_bars=<n>`
- `recent_sweep=<true|false>`
- `range_compression=<true|false>`
- `atr_pct=<x>`
- `window_pct_fvg=<x>`
- `window_pct_ob=<x>`
- `first_failure_branch=<branch_name>`

After Phase 4 ships, NONE only fires when neither direction has structure AND no BoS / sweep / range. Rich evidence at that point makes calibration data-driven.

### X6 — `structural_break_require_retest = true` rejects minor BoS

**Status:** ACTIVE. Confirmed.

- `config.toml:1037` (and any prior layer1 config block): `structural_break_require_retest = true`.
- Effect: BoS branches at `structure_engine.py:794, 805` require `last_bos.significance == "major"` — minor BoS coins fall through to NONE.
- Last 100 NONE lines: 9 `no_bullish_bos` + 1 `no_bearish_bos` = **10% of NONE failures**. Phase 6 (Lever B) flips to `false` and applies a `structural_break_minor_confidence_multiplier = 0.8`.

---

## ATR threading decision (Phase 2)

H1 NATR computed inline inside `engine.analyze()` from the candles already in hand:

```python
def _compute_h1_natr_pct(self, highs, lows, closes, lookback: int = 14) -> float:
    """Normalized ATR as percentage of current price.

    Computed from the H1 candles passed to analyze() — no dependency on
    the volatility_profile worker cache (which is a separate worker
    with its own cold-start path). Returns 0.0 if insufficient candles.
    """
    if len(closes) < lookback + 1:
        return 0.0
    highs_w  = highs[-lookback:]
    lows_w   = lows[-lookback:]
    closes_w = closes[-lookback - 1: -1]
    tr = np.maximum.reduce([
        highs_w - lows_w,
        np.abs(highs_w - closes_w),
        np.abs(lows_w - closes_w),
    ])
    atr = float(tr.mean())
    last_close = float(closes[-1])
    return (atr / last_close) * 100.0 if last_close > 0 else 0.0
```

Stored on StructuralAnalysis as new field `atr_pct_h1: float = 0.0`. Threaded into `_find_nearest_*` via signature change.

---

## Verification gate — answers (Phase 0 prerequisite)

1. **Is `_find_nearest_fvg` still direction-locked?** → Yes, `structure_engine.py:554–569` (verbatim verified).
2. **Is `_find_nearest_ob` still direction-locked?** → Yes, `structure_engine.py:572–587` (verbatim verified).
3. **What is `SetupType`'s current variant set?** → 9 variants listed above. `(str, Enum)` mixin. No counter variants.
4. **What is the exact `classify_setup()` branch order?** → 8 in-direction branches + NONE fall-through, mapped above.
5. **What does `XRAY_NONE_REASON` currently emit?** → 8 fields: `closest_type`, `missed_by`, `weakest_input`, `mtf`, `smc`, `direction`, `structure`, plus `sym` (no in/counter zone evidence, no ATR, no BoS detail).
6. **What is the current baseline `setup_type=none` count per cycle?** → avg 30.6/50 across 5 cycles. `pass_xray` avg 19.6. `qualified` avg 1.4.

All 6 answered. Phase 0 gate cleared.

---

## Files touched in Phase 0 (commits)

| Commit | Files |
|---|---|
| `d8fa264` (operator batch) | config.toml, src/brain/strategist.py, src/core/layer_manager.py, src/strategies/scanner.py, src/workers/scanner_worker.py, 6 test files, tests/test_force_include_filter.py |
| `7c42a0c` (phase0 prep) | scripts/xray_none_root_cause_probe.py, dev_notes/scanner_fail_bucket_deep_analysis_2026-04-29.md, dev_notes/xray_setup_none_root_cause_2026-04-30.md |
| Tag `pre-xray-counter-setup-fix` at `7c42a0c` | — |

Phase 0 complete. Proceeding to Phase 1.
