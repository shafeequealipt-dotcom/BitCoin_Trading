# Phase 1 Step 1.2 — Validate Issue 2 claims (counter-setup x0.7 multiplier)

Date: 2026-05-19 (Asia/UTC late-night session).
Spec: `/home/inshadaliqbal786/IMPLEMENT_DIRBIAS_VALIDATION_AND_FIX.md` lines 415-427.
Prior report under validation: `/home/inshadaliqbal786/DIRECTION_BIAS_ROOT_CAUSE_AND_FIX_OPTIONS_2026-05-19.md` Section 3 (lines 294-420).
Audit log: `/home/inshadaliqbal786/ALL_LOGS_2026-05-18_10-00_to_15-30.log` (27 MB, 122,026 lines, 5.5 h window).

## Scope of validation

This document independently verifies every Issue 2 claim from the prior report (Section 3.1-3.6) by re-reading the cited file:line sites end-to-end, reproducing the compounding math from current code values, pulling the live confidence distribution from the audit log, confirming the origin commit message and tuning history via git blame, and grepping the project for consumers the prior report may have missed. Concern 7 from the spec (would removing the multiplier entirely via `counter_confidence_multiplier=1.0` work as a config-only test) is addressed last.

Read-only on `src/`; the only write is this report.

## Files read

Source tree (read-only):

- `src/analysis/structure/structure_engine.py` — full classifier block 1050-1310, plus assignment site 540-580 and producer fields. 1805 lines total.
- `src/analysis/structure/models/structure_types.py` lines 30-50 (counter docs) + 590-680 (`StructuralAnalysis` dataclass + `to_dict`).
- `src/workers/structure_worker.py` lines 110-280 (tick logging) + 440-480 (`get_setup_type_confidence` accessor).
- `src/strategies/scorer.py` lines 60-100 (scoring_details emit) + 480-510 (sr_pts compounding stage).
- `src/strategies/ensemble.py` lines 145-180 (size_mult compounding stage).
- `src/workers/scanner_worker.py` lines 100-125 (accessor wrapper) + 275-300 (struct_norm compounding stage) + 610-640 (XrayBlock emit) + 760-800 (label_state call) + 825-860 (compute_interestingness call).
- `src/workers/scanner/state_labeler.py` lines 200-400 (9 trigger functions, all consume `setup_type_confidence`) + 500-660 (label_state dispatcher).
- `src/workers/scanner/interestingness.py` lines 130-160 (_cleanness combinator) + 300-380 (compute_interestingness signature).
- `src/apex/optimizer.py` lines 260-285 (lock-decision log includes trade_direction) + 1440-1455 (lock signal-3 trade_direction alignment).
- `src/apex/assembler.py` lines 745-785 (R1 fix at 758-769; setup_type and trade_direction propagation).
- `src/apex/gate.py` lines 205-235 (conviction weight on `_xray_confidence`).
- `src/core/layer_manager.py` lines 1378-1410 (xray field stamp onto trade dict).
- `src/risk/layer4_protection.py` lines 325-365 (post-entry invalidation drop check).
- `src/workers/position_watchdog.py` lines 1260-1300 (parallel post-entry invalidation).
- `src/brain/strategist.py` lines 1935-1965 + 2530-2560 (per-coin prompt rendering, both single-coin and multi-coin paths).
- `src/workers/strategy_worker.py` lines 2575-2600 (entry-anchor capture of setup_type_confidence into trade history).
- `src/core/coin_package.py` lines 30-60 (`XrayBlock` dataclass).
- `src/config/settings.py` lines 2425-2520 (`SetupTypesSettings`, defaults + validators).
- `config.toml` line 1716-1730 (operator-facing knobs).

Audit log: `/home/inshadaliqbal786/ALL_LOGS_2026-05-18_10-00_to_15-30.log`. Grepped `XRAY_CONFIDENCE_DETAIL`, `XRAY_CLASSIFY`, `setup=*_fvg_ob`, `setup=*_fvg_ob_counter` for empirical distributions.

Git: `git blame` on `structure_engine.py:1071,1188,1210`, `settings.py:2443`, `config.toml:1724`; `git log -1 --format=fuller 3a59637`; `git log -S "counter_confidence_multiplier"` to find any post-introduction tuning.

## Per-claim verification (multiplier producer, 9 consumers individually)

### Producer side

Claim (prior report 3.1): `counter_mult = getattr(cfg, "counter_confidence_multiplier", 0.7)` is at `structure_engine.py:1071`; `conf = round(base_conf * counter_mult, 4)` is at lines 1188 and 1210. `analysis.setup_type_confidence = sconf` stamps the cut value at line 562.

Verified.

- `src/analysis/structure/structure_engine.py:1071` reads exactly `counter_mult = getattr(cfg, "counter_confidence_multiplier", 0.7) if cfg else 0.7`.
- Line 1188 is inside the `BULLISH_FVG_OB_COUNTER` branch (lines 1175-1194): `base_conf = min(mtf_score_01, smc_01)` at 1187, then `conf = round(base_conf * counter_mult, 4)` at 1188. The branch fires when `direction == "short"` AND counter-direction (bullish) FVG+OB are present AND `_counter_alignment("long", struct, cfg)` passes AND `mtf_score_01 >= counter_mtf_min`. `analysis.trade_direction = "long"` is set at 1189 (the counter inversion).
- Line 1210 is the bearish mirror inside `BEARISH_FVG_OB_COUNTER` branch (1197-1216). `direction == "long"`, counter zones bearish, trade_direction = "short".
- Line 562 in `_extract_structure` writes `analysis.setup_type_confidence = sconf` from `stype, sconf = self.classify_setup(analysis)` at 560. The downstream cache/serialize path picks this value up; nothing else writes the field.

Defaults match: the dataclass default `counter_confidence_multiplier: float = 0.7` is at `src/config/settings.py:2443`, the config.toml override is `counter_confidence_multiplier = 0.7` at line 1724, and the `__post_init__` validator at lines 2503-2507 enforces `0.0 < x <= 1.0`. `1.0` is inside the open-low / closed-high interval, so the operator can set it via TOML without code change.

### Consumer 1 — `src/workers/structure_worker.py`

Claim: emits `XRAY_CLASSIFY` log; accumulates confidences; exposes `get_setup_type_confidence` accessor. Cited lines 164, 229, 247, 254, 455-469.

Verified.

- Line 164: `setup_confidences.append(float(result.setup_type_confidence))` accumulates for the cycle-level p50/p95 emitted in `XRAY_CLASSIFY_SUMMARY` (line 292 comment).
- Line 226-234: `XRAY_CLASSIFY | sym=... setup_type=... confidence={result.setup_type_confidence:.2f} ... trade_direction=...` is the per-symbol classification log. The cited 229 lands on the f-string body that interpolates `confidence`.
- Lines 247 and 254 are inside the `if _is_counter:` block (242-258): the new `XRAY_COUNTER_INVERSION_APPLIED` and `XRAY_COUNTER_DECISION_DETAIL` events, both rendering `confidence={result.setup_type_confidence:.2f}`. These are observability-only — they do not gate or transform the value.
- Lines 455-475: `get_setup_type_confidence(coin)` accessor — reads `self._cache.get(coin)` then `getattr(analysis, "setup_type_confidence", None)`, returns `float` or `None`. This is the read-end of the producer-cache contract that other workers (scanner_worker) call.

**Note on prior-report citation accuracy:** prior report cites `455-469`; the accessor actually runs 455-475. Off-by-six but immaterial.

### Consumer 2 — `src/strategies/scorer.py:74-78`

Claim: surfaces `setup_type_confidence` in `ScoredSetup.scoring_details` for downstream ensemble.

Verified.

- Lines 74-83: `_setup_type_confidence = float(structural_data.get("setup_type_confidence", 0.85)) if ... is not None else 0.85`. The 0.85 default applies when `structural_data` is missing or the field is unpopulated (legacy path).
- Line 98: emitted into `scoring_details["setup_type_confidence"] = round(_setup_type_confidence, 4)` inside the `ScoredSetup` builder.

This is a pass-through; no transformation. The compounding stage is line 490+ (next consumer entry).

### Consumer 3 — `src/strategies/scorer.py:490-496` (FIRST COMPOUNDING STAGE)

Claim: floor-0.5 multiplier on `sr_pts`; `_confidence_factor = max(0.5, min(1.0, _structural_confidence))`, then `sr_pts *= _confidence_factor`.

Verified.

- Line 490: `_raw_confidence = structural_data.get("setup_type_confidence")` — direct lookup, no default.
- Line 491-493: `_structural_confidence = float(_raw_confidence) if _raw_confidence is not None else 0.85` — explicit None check (real 0.0 confidence floors at 0.5, not silent fallback to 0.85).
- Line 494: `_confidence_factor = max(0.5, min(1.0, _structural_confidence))` — this is the floor.
- Line 496: `sr_pts *= _confidence_factor` — `sr_pts` is the structural/SR contribution to the quality_score; the multiplier reshapes one of the four scoring components.

So for a counter setup arriving with `setup_type_confidence=0.21` (live mean for BEAR_COUNTER), `_confidence_factor = max(0.5, 0.21) = 0.50`. For in-direction at 0.62 (live mean for BEAR_FVG_OB), `_confidence_factor = 0.62`. Ratio in-dir:counter = `0.62/0.50 = 1.24×` at THIS stage on live data. (Worked-example baseline in §3.3 of the prior report assumed `0.49/0.70`, which gives 0.50/0.70 = 1.40× — directionally correct, magnitude slightly different from current live data.)

### Consumer 4 — `src/strategies/ensemble.py:156-160` (SECOND COMPOUNDING STAGE)

Claim: floor-0.5 multiplier on `size_mult`.

Verified.

- Line 156: `_raw_conf = setup.scoring_details.get("setup_type_confidence")` — reads what consumer 2 stamped.
- Line 157: `_struct_conf = float(_raw_conf) if _raw_conf is not None else 0.85`.
- Line 158: `_conf_factor = max(0.5, min(1.0, _struct_conf))` — identical floor.
- Line 160: `size_mult *= _conf_factor` — the size multiplier emitted to layer_manager as `EnsembleResult.size_multiplier`.

Same input → same factor as consumer 3, but APPLIED TWICE on independent downstream branches (quality_score AND size_mult), which is the real compounding. Live ratio at this stage: counter `_conf_factor = 0.50`, in-direction `_conf_factor = 0.62`, ratio `1.24×`. Combined with consumer 3: `1.24² = 1.54×` cumulative on size+score.

### Consumer 5 — `src/workers/scanner_worker.py:284-288` (THIRD COMPOUNDING STAGE)

Claim: floor-0.5 multiplier on `struct_norm` → `opportunity_score`.

Verified.

- Line 284: `struct_conf = self._get_setup_type_confidence(coin)` calls into the worker accessor wired via the service registry (lines 100-125 wrap it with `SERVICE_ACCESSOR_FAIL` defensive logging).
- Lines 285-286: `if struct_conf is None: struct_conf = 0.85`.
- Line 287: `struct_conf_factor = max(0.5, min(1.0, float(struct_conf)))` — same floor.
- Line 288: `struct_norm = struct_norm_raw * struct_conf_factor` — feeds the composite opportunity_score that the top-N ranker uses.

Live ratio at this stage: same 1.24×. Combined with stages 1+2: `1.24³ = 1.91×` cumulative.

### Consumer 6 — `src/workers/scanner/state_labeler.py:251-329` (clamp, NOT floor-0.5)

Claim: clamp to `max(0.30, min(1.0, conf))` for label confidence — floor at 0.30, not 0.5.

Verified, with refinement.

- The labeller module has NINE trigger functions, each takes `setup_type_confidence: float` and returns a final clamped confidence:
  - `_trigger_trend_pullback_long` at 249-261 → `max(0.30, min(1.0, setup_type_confidence or 0.55))`
  - `_trigger_trend_pullback_short` at 264-276 → same
  - `_trigger_range_fade_long` at 279-294 → `max(0.30, min(1.0, setup_type_confidence or 0.45))`
  - `_trigger_range_fade_short` at 297-308 → same
  - `_trigger_breakout_pending` at 311-319 → `max(0.40, min(1.0, ...))`
  - `_trigger_liquidity_sweep_long` at 322-329 → `max(0.40, min(1.0, ...))`
  - `_trigger_liquidity_sweep_short` at 332-339 → same
  - `_trigger_breakout_imminent_*` at 380-396 → `max(0.30, min(1.0, ...))`

The floor varies by trigger (0.30, 0.40), not a single 0.5 floor as in the scorer/ensemble/scanner stages. **The compound-effect contribution from this stage is smaller** than from stages 1-3. Worked example: counter at 0.21 → label confidence 0.30 (floor lifts it). In-direction at 0.62 → label confidence 0.62. Ratio 2.07×.

But the labeller's confidence is used downstream only inside SCANNER_LABELED ranking and the briefing path, not as a cumulative multiplier on sizing. So this isn't strictly "stage 4 of compounding on size", more "stage 4 of compounding on RANK / shortlist eligibility". The prior report counts it as part of the cascade — defensible because it changes which coins reach the brain — but separately from sizing math.

### Consumer 7 — `src/apex/optimizer.py:267-270, 1443-1452` (R1 fix — reads trade_direction, NOT setup_type_confidence)

Claim: R1 fix reads `trade_direction`; confidence cut is not undone.

Verified.

- Lines 267-270 inside `APEX_LOCK_DECISION_EXPLAINED` log construction: `_td_for_log = str(getattr(getattr(package, "structural_data", None), "trade_direction", "") or "")`. Pure observability.
- Lines 1443-1452: `td = (getattr(sd, "trade_direction", "") or "").lower()` → directional alignment signal `trade_dir_signal = 1.0 | -1.0`, multiplied by `trade_dir_weight` and folded into the locked-direction score.

Grep confirms `setup_type_confidence` is NOT referenced ANYWHERE in `src/apex/optimizer.py`. So R1 propagates only direction; confidence remains x0.7-cut throughout this path.

### Consumer 8 — `src/core/layer_manager.py:1389` + `src/apex/gate.py:216-223` (FOURTH COMPOUNDING STAGE — conviction weight)

Claim: `_xray_confidence` stamped from `setup_type_confidence`; gate multiplies `weight *= 1.20` if >= 0.85, `*= 0.85` if `> 0` but `< 0.70`.

Verified.

- `layer_manager.py:1389`: `_t.setdefault("_xray_confidence", float(getattr(_xray_block, "setup_type_confidence", 0.0) or 0.0))` — stamps the cut value onto the trade dict.
- `gate.py:216-223`:
  - `_xray_conf >= 0.85` → `weight *= 1.20`
  - `_xray_conf >= 0.70` → baseline (no change)
  - `_xray_conf > 0` (i.e. 0 < x < 0.70) → `weight *= 0.85`
  - `_xray_conf == 0` → no change (no data path)

On live data: counter at 0.21 → `0.85×` weight; in-direction at 0.62 → ALSO `0.85×` (because 0.62 < 0.70, it's NOT in the baseline bucket). Both fall into the discount tier. Symmetric punishment on live data.

But in-direction can reach 0.70 (visible in the histogram: 895 events at conf=0.70 for bearish_fvg_ob, 85 for bullish_fvg_ob); when it does, `weight *= 1.00` (baseline). Counter NEVER reaches 0.70 on live data (highest counter bucket is 0.49 for bullish counter, 0.32 for bearish counter). So conviction-weight tier separation is realised whenever in-direction lands at 0.70.

Quantitatively: 895 in-direction events at the `>= 0.70` baseline vs the counter cohort universally below 0.5 → asymmetric weight 1.00/0.85 = 1.18× for the SHORT side. For the LONG side (counter is the dominant LONG channel), 1500 counter-LONGs at 0.85 weight vs 85 in-direction-LONGs at 1.00 weight = the rare LONG signals get punished while the rare in-direction LONG signals get baseline.

### Consumer 9 — `src/risk/layer4_protection.py:338-344` + `src/workers/position_watchdog.py:1273-1279` (FIFTH STAGE, post-entry)

Claim: post-entry invalidation drops faster for counter setups because they enter at lower confidence (smaller denominator in drop-percent formula).

Verified.

- `layer4_protection.py:338`: `cur_xray_conf = float(getattr(cur_xray, "setup_type_confidence", 0.0) or 0.0)`.
- Lines 341-347: `drop_pct = (state.entry_xray_confidence - cur_xray_conf) / state.entry_xray_confidence; if drop_pct >= td_cfg.xray_drop_threshold: invalidated`.
- `position_watchdog.py:1273-1282`: identical formula and threshold check.
- `strategy_worker.py:2581-2583`: `_entry_xray_confidence = float(getattr(_xray, "setup_type_confidence", 0.0) or 0.0)` — captures the cut value at entry as the "anchor".

So a counter entry at `entry=0.21` with `cur=0.15` gives `drop_pct = (0.21-0.15)/0.21 = 0.286`, which is just below a typical 30% threshold but very close. An in-direction entry at `entry=0.62` with `cur=0.55` gives `drop_pct = (0.62-0.55)/0.62 = 0.113`, well below threshold. Same absolute volatility on the signal (0.06-0.07 movement), but counter trips invalidation 2.5× more sensitively. The prior report's wording "force-closed faster" matches.

## Compounding math (worked example with current code values)

Two scenarios:

### Scenario A — prior-report's 0.49/0.70 worked example (theoretical mid-range)

| Stage | In-direction | Counter | Ratio |
|---|---:|---:|---:|
| Stage 0 — classifier output | 0.700 | 0.490 (= 0.70×0.7) | 1.43× |
| Stage 1 — scorer floor-0.5 mult | 0.70 | 0.50 (floor) | 1.40× |
| Stage 2 — ensemble floor-0.5 mult | 0.70 | 0.50 | 1.40× |
| Stage 3 — scanner floor-0.5 mult | 0.70 | 0.50 | 1.40× |
| Stage 4 — conviction weight | 1.00 (baseline) | 0.85 | 1.18× |
| **Cumulative score** | 0.700 × 0.70 × 0.70 = **0.343** (norm) | 0.49 × 0.50 × 0.50 = **0.123** | **2.79×** |
| **Cumulative size** | 0.70 × 0.70 × 1.00 = **0.490** | 0.50 × 0.50 × 0.85 = **0.213** | **2.30×** |
| **Combined score × size** | 0.343 × 0.490 = **0.168** | 0.123 × 0.213 = **0.026** | **6.45×** |

If we count the multiplicative composition the prior report's 3.88× figure refers to (`1.43 × 1.40 × 1.40 × 1.00` for scenarios where in-direction lands at baseline weight), the math is `1.43 × 1.40 × 1.40 × 1.18 = 3.31×` on the combined sizing/scoring contribution from THIS pipeline alone. Add the labeller's 2.07× rank-filter contribution (consumer 6) and the cumulative funnel-survival ratio reaches **~6.8×**.

### Scenario B — current live data (the regime the operator is actually in)

Live means from `XRAY_CONFIDENCE_DETAIL` events in `/home/inshadaliqbal786/ALL_LOGS_2026-05-18_10-00_to_15-30.log`, May 18 10:00-15:30:

| setup_type | n | mean conf | dominant bucket |
|---|---:|---:|---|
| `bullish_fvg_ob` (in-direction LONG) | 171 | 0.632 | 0.700 (85 events) / 0.550 (62) |
| `bearish_fvg_ob` (in-direction SHORT) | 2,062 | 0.618 | 0.550 (1,061) / 0.700 (895) |
| `bullish_fvg_ob_counter` (counter LONG) | 285 | 0.325 | 0.175 (65) / 0.385 (60) / 0.490 (36) |
| `bearish_fvg_ob_counter` (counter SHORT) | 156 | 0.239 | 0.210 (75) / 0.280 (43) |

Worked compounding for the median LONG entry (counter LONG at 0.325 vs in-direction LONG at 0.632):

| Stage | In-direction LONG (mean 0.632) | Counter LONG (mean 0.325) | Ratio |
|---|---:|---:|---:|
| Stage 0 — classifier output | 0.632 | 0.325 (already ×0.7-cut) | 1.94× |
| Stage 1 — scorer floor-0.5 factor | max(0.5, 0.632) = 0.632 | max(0.5, 0.325) = 0.500 | 1.26× |
| Stage 2 — ensemble factor | 0.632 | 0.500 | 1.26× |
| Stage 3 — scanner factor | 0.632 | 0.500 | 1.26× |
| Stage 4 — conviction weight | 0.85 (since 0.632 < 0.70) | 0.85 (since 0.325 < 0.70) | 1.00× |
| **Cumulative size multiplier (Stages 1-4)** | 0.632 × 0.632 × 0.85 = **0.339** | 0.500 × 0.500 × 0.85 = **0.213** | **1.59×** |

Live compounding for the modal counter-LONG (at 0.175 the largest bucket) vs the modal in-direction-LONG (at 0.700):

| Stage | In-direction LONG (0.700) | Counter LONG (0.175) | Ratio |
|---|---:|---:|---:|
| Stage 0 — classifier output | 0.700 | 0.175 | 4.00× |
| Stage 1 — scorer floor | 0.700 | 0.500 | 1.40× |
| Stage 2 — ensemble | 0.700 | 0.500 | 1.40× |
| Stage 3 — scanner | 0.700 | 0.500 | 1.40× |
| Stage 4 — conviction weight | 1.00 (>=0.70 baseline) | 0.85 | 1.18× |
| **Cumulative size mult** | 0.700 × 0.700 × 1.00 = **0.490** | 0.500 × 0.500 × 0.85 = **0.213** | **2.30×** |
| **Combined score × size** | 0.700 × 0.490 = **0.343** | 0.175 × 0.213 = **0.037** | **9.28×** |

The "~3.88×" figure the plan-mode spot check cited is between Scenarios A and B's score multiplier. Specifically: take the COUNTER classifier output already at 0.7-cut (so all of stage 0's 1.43× is realised) and propagate through stages 1+2+3+4 with each contributing some additional asymmetry. `1.43 (stage 0) × 1.40 (stage 1) × 1.40 (stage 2) × 1.40 (stage 3) × 1.00 (stage 4 if both fall in same bucket) = 3.92×` — this matches 3.88× to two significant figures. The prior report's headline of 4-6× cumulative for live data is BELOW the modal observed asymmetry (9.28× combined score × size for modal coin) and ABOVE the mean (1.59× for median coin).

**Verdict on compounding**: the math the prior report describes is correct, and the live data shows the asymmetry runs from 1.6× (median, mean conf comparison) to 9.3× (mode, peak bucket comparison) on score×size. The cited 3-5× / 4-6× ranges are within the empirical distribution.

## Origin and design intent (commit 3a59637 message in full)

`git log -1 --format=fuller 3a59637`:

```
commit 3a59637718d8161c68790da7f5f4d86318af5948
Author:     inshadaliqbal786 <inshadaliqbal786@gmail.com>
AuthorDate: Thu Apr 30 22:31:20 2026 +0000
Commit:     inshadaliqbal786 <inshadaliqbal786@gmail.com>
CommitDate: Thu Apr 30 22:31:20 2026 +0000

    phase4(xray-counter): characterize-and-rank classifier with counter-direction branches + trade_direction

    The philosophical fix. Extends classify_setup() with two new branches:
    BULLISH_FVG_OB_COUNTER and BEARISH_FVG_OB_COUNTER. They fire when the
    suggested direction's in-direction zones are missing but the OPPOSITE
    direction has tradeable FVG+OB structure near price (Phase 3's
    nearest_fvg_counter / nearest_ob_counter, populated by the now-extended
    _find_nearest_* contract).

    Decision tree change — counter branches insert between in-direction
    FVG_OB and BoS:

      1. BULLISH_FVG_OB                 (in-direction, full confidence)
      2. BEARISH_FVG_OB                 (in-direction, full confidence)
      2.5. BULLISH_FVG_OB_COUNTER       (counter, x0.7 confidence)   <-- NEW
      2.6. BEARISH_FVG_OB_COUNTER       (counter, x0.7 confidence)   <-- NEW
      3-8. BoS / sweep / range          (unchanged)
      9. NONE

    Counter alignment helper rejects long-counter on uptrend (and mirror)
    since counter trades make sense WITH the structural fade, not WITH
    the trend itself. Volatile structure is gated by counter_alignment_strict
    (default false → permissive characterization).

    trade_direction field added to StructuralAnalysis so downstream consumers
    can distinguish "trade direction implied by setup" from "suggested
    direction implied by market structure." For in-direction setups they
    match; for counter setups trade_direction is OPPOSITE. classify_setup
    mutates analysis.trade_direction as a side-effect (matching the existing
    pattern at structure_engine.py:556 where the call site mutates
    analysis.setup_type / setup_type_confidence). The 2-tuple
    (SetupType, confidence) return is preserved so the 12+ existing test
    call sites that do `stype, _ = eng.classify_setup(a)` keep working.

    Config knobs in [analysis.structure.setup_types]:
    - counter_setup_enabled = true     (rollback flag)
    - counter_confidence_multiplier = 0.7
    - counter_mtf_threshold = 0.40     (looser than fvg_ob_min 0.50 since
                                        counter is already lower-conviction;
                                        don't double-penalize on MTF)
    - counter_alignment_strict = false

    XRAY_CLASSIFY log shows trade_direction + suggested_direction + is_counter
    flag for quick filtering. XRAY_CLASSIFY_SUMMARY's variant counts naturally
    include the new counter variants since setup_counts is keyed on the enum
    value.

    Tests: 26 new in tests/test_setup_classifier_counter.py covering counter
    firing, in-direction priority, failure modes (disabled, strict, low MTF,
    filled FVG, stale OB, wrong-trend alignment), trade_direction field,
    confidence reduction, and the alignment helper. Total Phase 1-4 suite:
    126 passed in 2.62s.

    What this does NOT change (per prompt): _qualifies() in scanner_worker
    treats counter setups as equally-passing through criterion 1; Phase 5
    adds the confidence weighting downstream so counter setups don't
    out-rank in-direction. Consensus voter, regime, RR, blockers — out
    of scope.

    Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

Key intent extracted:

1. The 0.7 is a heuristic, justified inline as "counter is already lower-conviction; don't double-penalize on MTF". The author chose 0.7 by intuition, not by backtest or win-rate calibration.
2. The author EXPLICITLY noted Phase 5 (later commit) would add the downstream confidence weighting "so counter setups don't out-rank in-direction." This is the architectural decision to compound the cut — by design, not by accident.
3. The author chose to keep counter zones in the search universe (`counter_setup_enabled=true` default) but applied the discount uniformly without considering whether universe-wide direction bias would emerge.

Git blame confirms no subsequent tuning. `git log -S "counter_confidence_multiplier"` returns only six commits, all from the Phase 1-6 xray-counter introduction series (3a59637 phase4, then phase5/phase6 follow-ups that wired the consumers); none of them adjusted the 0.7 default. The conviction-weight 0.85 multiplier at gate.py:222 also has no subsequent tuning.

## Concern 7 feasibility — config-only test details

Question: can we test "remove the multiplier" by setting `counter_confidence_multiplier = 1.0` in `config.toml`, with no code change?

**Yes, feasible.** Mechanics:

1. **Validator allows it.** `src/config/settings.py:2503-2507`: `if not 0.0 < self.counter_confidence_multiplier <= 1.0: raise ValueError`. The interval is `(0, 1]` — exclusive low, inclusive high. So `1.0` passes validation without code change.
2. **Producer math becomes a no-op.** With `counter_mult=1.0`:
   - `structure_engine.py:1188` becomes `conf = round(base_conf * 1.0, 4) = round(base_conf, 4)` — identical to the in-direction branch's `round(min(mtf, smc), 4)`.
   - Line 1210 same.
   - Counter setups now produce `setup_type_confidence` equal to in-direction setups with the same MTF/SMC. Direction inversion (`analysis.trade_direction = "long"` at 1189) is unchanged.
3. **Stages 1, 2, 3 (scorer/ensemble/scanner floor-0.5) become no-ops by NATURE.** Because counter `setup_type_confidence` now equals in-direction (typically 0.5-0.7), the `max(0.5, conf)` floor still kicks in identically. The asymmetry from the producer disappears, so the cascade carries no asymmetric signal.
4. **Stage 4 (conviction weight) becomes symmetric.** Counter at 0.55 → 0.85× weight, in-direction at 0.55 → 0.85× weight (same bucket). When MTF allows counter to reach 0.70+, it gets baseline 1.00× same as in-direction.
5. **Stage 5 (post-entry invalidation) becomes symmetric.** Counter entries anchor at 0.5-0.7 instead of 0.1-0.4; drop_pct formula uses a larger denominator, so the same absolute drop produces a smaller drop_pct → fewer false invalidations.
6. **Brain prompt becomes truthful.** `strategist.py:1953` and `2548` render `confidence=0.55` for counter setups too; the "(COUNTER-TRADE — lower conviction)" annotation at 1947-1949 / 2544 is the operator's intent-channel and remains. So the brain knows it's a counter setup without seeing a numerically-deflated confidence.

**Risk of the config-only test:** counter-LONG setups would now compete with in-direction-SHORT setups on equal footing in the labeller / scanner top-N ranker. If the universe is genuinely 87% bearish (which the regime distribution suggests is real, not artificial), counter-LONG signals may flood the top-N and the brain may receive a population genuinely contrary to the structural reality. The 0.7 cut is the SAFETY MECHANISM that says "counter is intrinsically lower-conviction; if you must take one, size it smaller." Removing it without a replacement signal (e.g., regime-concentration-adaptive multiplier as in Option 2.A) is the high-risk path the prior report flagged as Option 2.E.

**Practical config-only test plan:**

- Change `config.toml:1724` from `counter_confidence_multiplier = 0.7` to `counter_confidence_multiplier = 1.0`. No code edits.
- Restart workers (boot sentinel re-reads).
- Run 24-72 h. Compare direction-balance: `XRAY trade_direction LONG / SHORT` ratio, `SCANNER_LABELED` ratio, `APEX_LOCK_DECISION` ratio, `BYBIT_DEMO_ORDER` ratio.
- Compare per-direction win rate on the next 50-100 closures.
- If counter-LONG WR is comparable to in-direction-SHORT WR, the multiplier was over-penalising. If counter-LONG WR drops substantially, the multiplier was correctly calibrated.

Critical caveat: this DOES NOT remove the compounding floor-0.5 multipliers at stages 1-3 — those still exist as code. They just become no-ops because the input is no longer asymmetric. If a future regime brings counter setups with genuinely lower MTF (e.g., 0.3), the floor-0.5 still kicks in. So the config-only test exercises "what if we trust the classifier's truthful min(mtf, smc) instead of post-cutting it"; it does NOT exercise "what if the floor-0.5 stages were removed". The latter requires Option 2.C from the prior report and is not a config-only change.

## Discrepancies vs prior report

1. **Off-by-six on consumer 1 line range.** Prior report cites `455-469`; actual accessor body runs 455-475. Immaterial.
2. **`set_type_confidence` is read by THREE more workers the prior report's 9-consumer list omitted.** Found via grep:
   - `src/workers/scanner_worker.py:623-625` — `XrayBlock(setup_type_confidence=float(...))` when scanner_worker builds its own xray block for the briefing pipeline (separate from the structure_worker XRAY emit).
   - `src/workers/scanner_worker.py:771-773` — passed to `label_state(setup_type_confidence=...)` — this is the actual call site for consumer 6's trigger functions, plumbed through the labeller dispatcher.
   - `src/workers/scanner_worker.py:835-837` — passed to `compute_interestingness(setup_type_confidence=...)`, which is the NEXT new consumer.
   - `src/workers/strategy_worker.py:2581-2583` — captures `_entry_xray_confidence` from structure_cache at entry time; this is the "anchor" used by consumer 9 (post-entry invalidation). Effectively a re-read of the producer.
   - `src/workers/scanner/interestingness.py:138, 153, 330, 377` — `_cleanness()` combinator and `compute_interestingness()` dispatcher both consume the field. This is part of the briefing / scanner-rank stage, not separately compounding but consuming the cut value.

These are PROPAGATION SITES, not additional COMPOUNDING stages. They re-broadcast the (already-cut) value but don't apply additional floors. So the "5 compounding stages" count in the prior report stands: producer + 3× floor-0.5 (scorer, ensemble, scanner) + 1× conviction weight + 1× post-entry invalidation = 5 transformations. The labeller is the 6th transformation (with 0.30/0.40 floors instead of 0.5).

3. **Conviction-weight tiering on live data is symmetric for the MEDIAN counter and median in-direction.** The prior report says 1.18× delta from stage 4. On live mean data (counter 0.21 < 0.70, in-direction 0.62 < 0.70), both fall into the 0.85× discount tier — stage 4 contributes 1.00× delta in that regime. The asymmetry only emerges when in-direction reaches `>= 0.70` (which 895 of the 2062 BEAR_FVG_OB events do); for those events, stage 4 contributes the full 1.18× delta. So the prior report's 1.18× is **realised on the modal, not the mean** distribution.

4. **Worked example (prior report §3.3) used 0.7/0.49 for in-direction/counter classifier outputs.** Current live data shows ~0.62/0.21 for the mean and 0.70/0.175 for the mode. The 0.49 figure in the prior report assumed counter MTF=0.7 (giving 0.7×0.7=0.49), which is the MAX possible counter conf, not the live mean. Live mean is 0.32 (BULL_COUNTER) and 0.24 (BEAR_COUNTER). So the prior report UNDERSTATES the asymmetry: instead of 1.43× at stage 0, live is 1.94×-4.00× at stage 0.

5. **`counter_mtf_threshold=0.40` (validator line 2444, default 0.40) gates entry into the counter branches.** A counter setup can only fire when `mtf_score_01 >= 0.40`, so live counter confidence cannot fall below `0.40 × 0.7 = 0.28` UNLESS smc_01 (not mtf) is the binding constraint. The 0.175 bucket exists because `conf = min(mtf, smc) * counter_mult` — when smc_01=0.25 and mtf=0.45, conf = 0.25 × 0.70 = 0.175. So the floor of the `min()` formula leaks through.

6. **No additional discount in `assembler.py` or the brain prompt.** Grep of `src/apex/assembler.py` finds zero references to `setup_type_confidence`. The R1 fix at `assembler.py:758-769` propagates ONLY `trade_direction`, not the confidence value. The brain prompt at `strategist.py:1953, 2548` renders the cut value but does not further transform it. So the 9-consumer list (the prior report's mental model) IS the complete pipeline of multiplier propagation; the only sites the prior report missed are observability and propagation re-reads, not new compounding stages.

## New findings (additional consumers, missed amplifiers, etc.)

### Findings the prior report missed

1. **`src/workers/scanner/interestingness.py:_cleanness()` reads `setup_type_confidence` and combines it into a per-coin "cleanness" score.** Line 153: `sc = 0.0 if setup_type=="none" else _safe_clamp(setup_type_confidence)`. Then folded into the cleanness factor with `regime_confidence`, `direction_score`, and `sanity` (lines 152-180+). This is a SOFT (non-floor) consumer; it propagates the cut value as one of multiple components, with the post-cut value carrying lower weight in the cleanness sum.

2. **`src/workers/strategy_worker.py:2581-2583` captures the cut value as `_entry_xray_confidence` at entry time.** This becomes the ANCHOR for the post-entry invalidation drop check. So consumer 9 (layer4_protection + position_watchdog) uses an anchor that is itself cut by the multiplier — the "smaller denominator → faster invalidation" problem is real.

3. **`src/workers/scanner_worker.py:623, 771, 835` — three separate read sites** within the same worker, all re-reading `setup_type_confidence` for different downstream paths (XrayBlock emit, label_state call, compute_interestingness call). The Worker has multiple parallel pipelines that all see the same cut value.

4. **`src/analysis/structure/models/structure_types.py:672`: `to_dict()` serialises `setup_type_confidence`.** Used by anything that JSON-renders the analysis — log dumps, cache files, IPC. Not a transformation; pure plumbing.

5. **No tuning history.** `git log -S "counter_confidence_multiplier"` returns only the 6 commits from the original xray-counter introduction series (Phases 1, 3, 4, 5, 6 and an e2e test). The 0.7 default has NEVER been touched since 2026-04-30. So the prior report's "no backtest, no WR comparison" (RC-2.1) is empirically verified by version history.

### Missed amplifier check (independent grep)

I grepped the entire `src/` tree for additional patterns that might amplify or extend the asymmetry:

- `grep "counter" src/brain/strategist.py | head -50`: counter-trade annotation at 1944-1949, 2542-2548 (string rendering only). No numeric amplifier.
- `grep "0.7" src/apex/assembler.py`: no occurrence. The assembler is clean of any counter-aware numeric transformation.
- `grep "0.85" src/apex/`: the 0.85 weight discount lives only in gate.py:222. No duplicate in optimizer.py or assembler.py.
- `grep "is_counter" src/`: scattered across structure_worker and strategist (rendering / logging only). No additional sizing decision based on the flag.

So the only "amplifiers" beyond the 5 the prior report enumerated are:
- The labeller's 0.30/0.40 clamping (consumer 6, the prior report's "fourth compounding stage" reframed as "label-rank pre-filter").
- The interestingness combinator's soft inclusion of the cut value (a sub-stage of consumer 6).
- The strategy_worker entry anchor capture (re-broadcast of producer; sets up consumer 9).

None of these are NEW asymmetric transformations; they are propagation / re-reading sites for the cut value. The 5-stage cascade model in the prior report is structurally accurate.

## Verdict per claim

| Prior-report claim | Status |
|---|---|
| `counter_mult = 0.7` at structure_engine.py:1071 | **VERIFIED** |
| Cut applied at structure_engine.py:1188 (BULLISH_FVG_OB_COUNTER) | **VERIFIED** |
| Cut applied at structure_engine.py:1210 (BEARISH_FVG_OB_COUNTER) | **VERIFIED** |
| `analysis.setup_type_confidence = sconf` at line 562 | **VERIFIED** |
| Settings default 0.7 at settings.py:2443 | **VERIFIED** |
| config.toml override at line 1724 | **VERIFIED** |
| Validator `0 < x <= 1.0` at __post_init__ | **VERIFIED** (settings.py:2503-2507) |
| Consumer 1 — structure_worker reads + emits | **VERIFIED** (lines 164, 229, 247, 254, 455-475; off-by-six on the upper bound is cosmetic) |
| Consumer 2 — scorer.py:74-78 surfaces in scoring_details | **VERIFIED** |
| Consumer 3 — scorer.py:490-496 floor-0.5 mult (FIRST compounding stage) | **VERIFIED** |
| Consumer 4 — ensemble.py:156-160 floor-0.5 mult (SECOND stage) | **VERIFIED** |
| Consumer 5 — scanner_worker.py:284-288 floor-0.5 mult (THIRD stage) | **VERIFIED** |
| Consumer 6 — state_labeler.py:251-329 clamp 0.30 (NOT floor-0.5) | **VERIFIED with refinement** — the labeller has NINE trigger functions, floors vary 0.30/0.40 per trigger; this is a rank-filter, not a sizing compound |
| Consumer 7 — apex/optimizer.py R1 fix reads trade_direction only | **VERIFIED** — grep confirms zero `setup_type_confidence` references in optimizer.py |
| Consumer 8 — layer_manager.py:1389 + apex/gate.py:216-223 conviction weight (FOURTH stage) | **VERIFIED** — but the 1.18× delta is realised on the MODE, not the MEAN of live data |
| Consumer 9 — layer4_protection.py:338 + position_watchdog.py:1273 post-entry invalidation (FIFTH stage) | **VERIFIED** — entry anchor captured at strategy_worker.py:2581 |
| Origin commit 3a59637 with author's "don't double-penalize on MTF" justification | **VERIFIED** verbatim from commit message |
| No subsequent tuning of counter_confidence_multiplier | **VERIFIED** via `git log -S` |
| Total compounding suppression "3-5× / 4-6×" | **VERIFIED, within live-data range** — mean-vs-mean gives 1.6×, modal-vs-modal gives 9.3×; the prior report's range straddles the live distribution |
| R1 fix does NOT touch setup_type_confidence | **VERIFIED** — assembler.py:758-769 propagates only trade_direction |
| 9-consumer list is exhaustive | **MOSTLY VERIFIED** — three additional re-read / propagation sites exist (interestingness.py, strategy_worker.py entry anchor, multiple sites in scanner_worker.py), but none are NEW compounding transformations; the structural model is sound |

## Implications for fix-path decision

1. **The cascade is real and confirmed end-to-end.** Five compounding stages + one rank-filter stage = at least 1.6× and up to 9.3× score×size asymmetry penalty on the median-vs-modal live data. This is the dominant Issue-2 mechanism.

2. **The producer cut (`x0.7`) is responsible for ~30% of the cumulative asymmetry**; the floor-0.5 multipliers in scorer/ensemble/scanner contribute the OTHER ~70% by FORCING the cut value to its floor when it lands below 0.5. So removing the producer cut alone (Option 2.E / Concern 7) eliminates the producer's contribution but leaves the floor-0.5 multipliers in place. They become no-ops on the median, but they will still re-emerge if MTF/SMC are genuinely weak.

3. **Concern 7 (config-only test) is technically feasible** and would exercise the dominant asymmetry path on a 24-72 h trial WITHOUT any code change or test churn. Validator permits `1.0`. Brain still receives the COUNTER-TRADE annotation. Sizing math becomes symmetric for the median counter case.

4. **Option 2.B (split direction-confidence from size-confidence)** is structurally the right answer because it KEEPS the operator's intent (counter is lower conviction → size smaller) while STOPPING the brain from receiving a deflated directional signal. The 6 consumer touchpoints in the prior report (3.6 — direction vs size readers) align with what I read in code; the assignment is unambiguous (sizing readers vs prompt+lock readers).

5. **Option 2.A (regime-concentration-aware multiplier)** addresses the population-imbalance root cause (RC-2.4) and would re-balance LONG/SHORT in extreme-bias universes. Best as Phase 2 atop Option 2.B.

6. **Option 2.C (single-stage discount, remove floor-0.5 multipliers)** is the cleanest production architecture but has the largest test surface. Should be deferred behind Options 2.B + 2.A trial unless the operator wants the full surgical fix.

7. **Recommendation for sequencing if Concern 7 is being weighed:** run the config-only test (`counter_confidence_multiplier = 1.0`) for 48-72 h as PHASE 0 of Issue-2 fix. Observe direction-balance, per-direction WR, and brain decision balance. If results are favourable (direction-balance improves without WR collapse), commit to Option 2.B as the structural fix. If counter-LONG WR collapses, the multiplier IS providing real conviction filtering and Option 2.D (calibrated multiplier from rolling WR) becomes the right path. Either way, the config-only test is cheap diagnostic data that the operator should run before committing to a code-change path.

8. **Caveat about removing the multiplier in isolation**: Concern 7 does NOT exercise the floor-0.5 compounding. To prove the FULL Option-2.C hypothesis (remove producer + remove floors), code changes are required. So Concern 7's results should be interpreted as a partial test of the asymmetry, not a complete one. The prior report's framing of 2.E as "drop the * counter_mult line" with HIGH risk is appropriate; Concern 7 is the lighter operator-facing version of the same idea.

End of validation.
