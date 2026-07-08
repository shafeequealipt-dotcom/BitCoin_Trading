# Phase C (Issue 1) Deep Cross-Check Audit — `99b3420` + merge `2864216`

Branch audited: `main` (HEAD at audit time).
Subject commit: `99b3420 fix(dirbias/issue1): xray min-edge floor + symmetric min_touches resistance`.
Merge commit: `2864216 merge: dirbias Issue 1 — xray min-edge floor + symmetric min_touches (Phase C)`.
Tracker spec: `dev_notes/dirbias_validation/01_validate_issue1.md` (Phase 1.1 root causes), `10_evaluate_concern1.md` (rejected Phase A2 RR-floor band-aid), `13_evaluate_concern4.md` (active defaults verdict), `20_recommendation.md` (Phase C scope).

Audit posture: read-only across the codebase; this single markdown is the only write surface.

---

## Edit-site verification

Each surface promised by the original spec is verified against actual HEAD source by file:line, with the relevant code quoted.

### 1. `src/config/settings.py` `StructureSettings` — new fields

`src/config/settings.py:2393-2425`:

```python
@dataclass
class StructureSettings:
    """X-RAY Structural Intelligence configuration."""
    enabled: bool = True
    worker_interval_seconds: int = 60
    cache_ttl_seconds: int = 300
    min_candles: int = 50
    swing_lookbacks: list[int] = field(default_factory=lambda: [3, 5, 10])
    cluster_pct: float = 0.3
    min_touches: int = 2
    # Issue 1 of 2026-05-19 direction-bias fix Phase C — symmetric
    # min_touches filter for resistance levels. Legacy behavior at
    # src/analysis/structure/support_resistance.py:126 hardcoded a
    # `>= 1` filter for resistance while support used config-driven
    # `min_touches >= 2`. In sustained downtrending markets, this
    # asymmetry filtered out single-touch swing lows but kept single-
    # touch swing highs, producing `sup=0 res=5` in 80.7% of audited
    # XRAY_ANALYZE rows — which collapsed `rr_long` toward 0 and
    # triggered cascading Buy → Sell flips in strategy_worker. Default
    # 2 symmetrizes with support. Operator may lower to 1 in markets
    # where resistance levels are clearly genuine on single touch.
    min_touches_resistance: int = 2
    # Issue 1 of 2026-05-19 direction-bias fix Phase C — minimum-edge
    # floor on structural_tp (consumed by _calc_long at
    # structural_levels.py:101 and _calc_short at :176). When the
    # nearest resistance/support is closer to current_price than this
    # percent, the structural_tp is clamped to be at least this far
    # away. Prevents `rr_long` (or `rr_short`) from collapsing toward
    # zero when price is at or near a level. Default 0.5 (i.e. 0.5%
    # minimum reward distance — well below the typical SL distance of
    # 1-2%, so legitimate tight setups still register but pathological
    # collapses are clamped).
    tp_min_distance_pct: float = 0.5
```

Both fields present.
Both defaults match the Concern 4 verdict (ACTIVE defaults: `min_touches_resistance=2` symmetric with `min_touches=2`; `tp_min_distance_pct=0.5`).
Comments reference the 80.7% sup=0/res=5 audit finding from `dev_notes/dirbias_validation/01_validate_issue1.md`.

Verdict: PASS.

### 2. `src/analysis/structure/models/structure_types.py` `StructuralPlacement` — new flag

`src/analysis/structure/models/structure_types.py:139-152`:

```python
    is_fallback_rr: bool = False  # True when SL or TP used percentage fallback (not structural)
    # Issue 1 of 2026-05-19 direction-bias fix Phase C — flag set when
    # the raw structural_tp (computed from nearest_res/nearest_sup +
    # tp_buffer) would have landed on the WRONG SIDE of current_price
    # (i.e. structural_tp <= current_price for a long, or >= for a
    # short). This occurs when price is at or above the resistance
    # zone (long case) or at/below support (short case), and was
    # historically masked by the abs() call in the reward formula.
    # The clamped `structural_tp` is still emitted (forced to be at
    # least tp_min_distance_pct away from current_price) so that
    # rr_long/rr_short never collapse to ~0, but downstream consumers
    # may use this flag to qualify their handling (e.g. APEX optimizer
    # may reduce sizing, the watchdog may skip force-close decisions
    # premised on the structural placement).
    is_structurally_invalid: bool = False
```

`to_dict()` exposes the flag at `src/analysis/structure/models/structure_types.py:173`:

```python
            "is_structurally_invalid": self.is_structurally_invalid,
```

Default is `False`. Position is appended after `is_fallback_rr` (last in dataclass).

Verdict: PASS.

### 3. `src/analysis/structure/support_resistance.py:122-138` — symmetric resistance filter

Pre-fix the file hardcoded `r.touches >= 1` for resistance. Post-fix:

```python
        # Filter by minimum touches
        min_t = self._settings.min_touches
        support_levels = [s for s in support_levels if s.touches >= min_t]
        # Issue 1 of 2026-05-19 direction-bias fix Phase C: previously
        # this filter hardcoded `>= 1`, which kept single-touch swing
        # highs while the support filter dropped single-touch swing
        # lows (min_touches=2). In sustained downtrending markets the
        # asymmetry produced sup=0 res=5 in 80.7% of audited cycles,
        # collapsing rr_long → 0 and cascading Buy → Sell flips. Now
        # config-driven via min_touches_resistance (default 2,
        # symmetric with support). Operator may set
        # ``[analysis.structure] min_touches_resistance = 1`` in
        # config.toml to restore legacy single-touch resistance
        # detection if needed for less-trending markets.
        min_t_resistance = self._settings.min_touches_resistance
        resistance_levels = [
            r for r in resistance_levels if r.touches >= min_t_resistance
        ]
```

Both support and resistance now read from config; resistance uses the new `min_touches_resistance` field. No hardcoded threshold remains anywhere in this filter pair. Symmetric.

Verdict: PASS.

### 4. `src/analysis/structure/structural_levels.py` — min-edge TP clamp + flag

`_calc_long` clamp block at `src/analysis/structure/structural_levels.py:97-127`:

```python
        # TP placement
        structural_tp = 0.0
        tp_ref = ""
        # Issue 1 of 2026-05-19 direction-bias fix Phase C: track the
        # "structurally invalid" flag for callers that want to qualify
        # their handling. Flag is set when the raw resistance-based TP
        # would have landed on the WRONG SIDE of current_price (i.e. at
        # or below current_price for a long), indicating price is at or
        # above the nearest resistance zone. Without the clamp introduced
        # below, that condition collapsed reward → 0 and rr_long → 0.
        is_structurally_invalid = False
        if resistances:
            nearest_res = resistances[0]
            raw_tp = nearest_res.zone_low - (nearest_res.price * tp_buffer)
            # Minimum-edge floor: TP must be at least tp_min_distance_pct
            # above current_price for a long. When the raw value violates
            # that, clamp UP to the floor and flag the placement as
            # structurally invalid for downstream consumers.
            min_tp_distance = current_price * (
                self._settings.tp_min_distance_pct / 100.0
            )
            min_tp = current_price + min_tp_distance
            if raw_tp < min_tp:
                is_structurally_invalid = True
                structural_tp = min_tp
                tp_ref = (
                    f"clamped_min_edge_${nearest_res.price:.2f}_"
                    f"floor={self._settings.tp_min_distance_pct:.2f}pct"
                )
            else:
                structural_tp = raw_tp
                tp_ref = f"at_resistance_${nearest_res.price:.2f}"
```

Then `is_structurally_invalid` is added to the `StructuralPlacement` return at `src/analysis/structure/structural_levels.py:171`:

```python
            is_fallback_rr=("fallback" in sl_ref or "fallback" in tp_ref),
            is_structurally_invalid=is_structurally_invalid,
        )
```

`_calc_short` mirror at `src/analysis/structure/structural_levels.py:198-258`:

```python
        # TP: at nearest support (just above)
        # Issue 1 of 2026-05-19 direction-bias fix Phase C — mirror of
        # the long-side clamp. Flag set when raw support-based TP would
        # have landed on or above current_price (the wrong side for a
        # short), and clamp DOWN to the min_tp_distance floor.
        structural_tp = 0.0
        tp_ref = ""
        is_structurally_invalid = False
        if supports:
            nearest_sup = supports[0]
            raw_tp = nearest_sup.zone_high + (nearest_sup.price * tp_buffer)
            min_tp_distance = current_price * (
                self._settings.tp_min_distance_pct / 100.0
            )
            max_tp = current_price - min_tp_distance
            if raw_tp > max_tp:
                is_structurally_invalid = True
                structural_tp = max_tp
                tp_ref = (
                    f"clamped_min_edge_${nearest_sup.price:.2f}_"
                    f"floor={self._settings.tp_min_distance_pct:.2f}pct"
                )
            else:
                structural_tp = raw_tp
                tp_ref = f"at_support_${nearest_sup.price:.2f}"
```

with the return:

```python
            direction="short",
            is_fallback_rr=("fallback" in sl_ref or "fallback" in tp_ref),
            is_structurally_invalid=is_structurally_invalid,
        )
```

Reward formula preserved (still `abs()`) at `src/analysis/structure/structural_levels.py:135-137`:

```python
        risk = abs(current_price - structural_sl)
        reward = abs(structural_tp - current_price)
        rr_ratio = reward / risk if risk > 0 else 0.0
```

and mirror at `:228-230`:

```python
        risk = abs(structural_sl - current_price)
        reward = abs(current_price - structural_tp)
        rr_ratio = reward / risk if risk > 0 else 0.0
```

The clamp guarantees `structural_tp - current_price` is positive for longs and negative for shorts, so `abs()` no longer masks a sign flip — yet existing tests that depend on the `abs()` shape still see the same code. Backward compatible.

`XRAY_LEVELS` DEBUG log lines extended to include `invalid={is_structurally_invalid}` at `src/analysis/structure/structural_levels.py:153-157` (long) and `:240-244` (short).

Verdict: PASS.

### 5. `src/analysis/structure/structure_engine.py` — boot sentinel `XRAY_FLIP_CONFIG`

`src/analysis/structure/structure_engine.py:81-98`:

```python
        log.info("XRAY_INIT | engine=structure_engine")
        # Issue 1 of 2026-05-19 direction-bias fix Phase C — boot
        # sentinel for the new min-edge floor + symmetric min_touches
        # resistance filter. Mirrors STRAT_REGIME_INSTR_REFRAMED
        # (Phase A) / STATE_LABELLER_REGIME_HAIRCUT_INIT (Phase B). Lets
        # log-tail monitoring verify the active config without reading
        # config.toml.
        try:
            log.info(
                f"XRAY_FLIP_CONFIG | "
                f"tp_min_distance_pct={self._settings.tp_min_distance_pct:.2f} "
                f"min_touches_support={self._settings.min_touches} "
                f"min_touches_resistance={self._settings.min_touches_resistance} "
                f"min_touches_symmetric="
                f"{self._settings.min_touches == self._settings.min_touches_resistance}"
            )
        except Exception as _e:
            log.debug(f"XRAY_FLIP_CONFIG_FAIL | err='{str(_e)[:80]}'")
```

Sentinel emits AFTER `XRAY_INIT`, wrapped in try/except so a config-attribute regression cannot crash engine init. Field names exactly match spec: `tp_min_distance_pct`, `min_touches_support`, `min_touches_resistance`, `min_touches_symmetric`.

Verdict: PASS.

### 6. `config.toml` `[analysis.structure]` — surfaced keys

`config.toml:1612-1632`:

```toml
min_touches = 2
# Issue 1 of 2026-05-19 direction-bias fix Phase C — symmetric
# resistance touch filter. Legacy behavior at
# src/analysis/structure/support_resistance.py:126 hardcoded `>= 1`
# for resistance while support uses min_touches=2. In sustained
# downtrending markets, the asymmetry filtered out single-touch swing
# lows but kept single-touch swing highs, producing sup=0 res=5 in
# 80.7% of audited XRAY_ANALYZE rows. The result was rr_long
# collapse → cascading Buy → Sell flips at execution. Default 2
# symmetrizes with support. Operator may lower to 1 for markets
# where single-touch resistance detection is desired.
min_touches_resistance = 2
# Issue 1 of 2026-05-19 direction-bias fix Phase C — minimum-edge
# floor on structural_tp distance from current_price. Consumed by
# _calc_long at structural_levels.py:97-145 and _calc_short at
# :155-215. When the nearest resistance/support is closer to
# current_price than tp_min_distance_pct, the structural_tp is
# clamped to be at least this far away and StructuralPlacement's
# is_structurally_invalid flag is set so downstream consumers can
# qualify their handling. Prevents rr_long (or rr_short) from
# collapsing toward zero when price is at or near a level. Default
# 0.5% — well below the typical SL distance of 1-2% so legitimate
# tight setups still register but pathological collapses are clamped.
tp_min_distance_pct = 0.5
```

Both surfaced with rationale comments. Defaults match `StructureSettings` defaults — operator-tunable.

Verdict: PASS.

### 7. `tests/test_structural_floor.py` — new test file

9 tests confirmed present at `tests/test_structural_floor.py`:

| Section | Test name | Line | Subject |
|---|---|---|---|
| 1 | `test_structural_placement_default_is_structurally_invalid_false` | 62-67 | Default flag value |
| 1 | `test_structural_placement_to_dict_includes_invalid_flag` | 70-76 | to_dict() round-trip |
| 2 | `test_structure_settings_defaults_active` | 82-89 | ACTIVE defaults (Concern 4) |
| 2 | `test_structure_settings_legacy_resistance_filter_via_config` | 92-95 | Operator override |
| 3 | `test_calc_long_clamps_tp_when_resistance_below_floor` | 101-131 | Long-side clamp behavior |
| 3 | `test_calc_long_does_not_clamp_when_resistance_above_floor` | 134-155 | Long-side no-clamp behavior |
| 4 | `test_calc_short_clamps_tp_when_support_above_floor` | 161-187 | Short-side clamp behavior |
| 5 | `test_resistance_filter_symmetric_with_support_by_default` | 217-228 | Symmetric filter default |
| 5 | `test_resistance_filter_legacy_single_touch_via_config` | 231-241 | Legacy single-touch override |

All 9 tests pass (see regression results section below). All tests use keyword-only construction (`StructuralPlacement(is_structurally_invalid=True)`, `StructureSettings(**base)`) so no positional-arg drift risk.

Verdict: PASS.

---

## Test coverage analysis

Coverage maps to the spec's coverage list:

| Required | File:line | Status |
|---|---|---|
| StructuralPlacement default flag | `tests/test_structural_floor.py:62-67` | Present |
| to_dict() includes flag | `tests/test_structural_floor.py:70-76` | Present |
| Settings defaults ACTIVE | `tests/test_structural_floor.py:82-89` | Present |
| Operator override (legacy mode) | `tests/test_structural_floor.py:92-95` | Present |
| `_calc_long` clamp engages | `tests/test_structural_floor.py:101-131` | Present |
| `_calc_long` no-clamp path | `tests/test_structural_floor.py:134-155` | Present |
| `_calc_short` clamp engages | `tests/test_structural_floor.py:161-187` | Present |
| Symmetric filter default | `tests/test_structural_floor.py:217-228` | Present |
| Legacy single-touch via config | `tests/test_structural_floor.py:231-241` | Present |

Gap (worth noting but not in original spec): no test for `_calc_short` no-clamp path (Section 4 only has the clamp case; the long-side has both). This is an asymmetric coverage gap but the implementation `_calc_short` is mechanically the mirror of `_calc_long` — the no-clamp branch shares a common return path. Severity: low.

Also no integration test for the `XRAY_FLIP_CONFIG` boot sentinel (verified via Phase 4 live-log spec instead — `dev_notes/dirbias_validation/phase6_phase_bc_trial.md:16`).

---

## Downstream consumer survey

Comprehensive grep across `src/` and `tests/` for every consumer that touches `rr_long`, `rr_short`, `rr_best`, `rr_ratio`, `structural_tp`, `structural_sl`, `is_structurally_invalid`, `tp_min_distance_pct`, `min_touches_resistance`, and `StructuralPlacement`.

### Producer + assembly path

| File:line | Behavior with clamp active |
|---|---|
| `src/analysis/structure/structural_levels.py:107-127` (`_calc_long`) | Sets `is_structurally_invalid` and clamps `structural_tp` UP when raw TP `< current_price + min_tp_distance`. |
| `src/analysis/structure/structural_levels.py:206-222` (`_calc_short`) | Mirror — clamps DOWN when raw TP `> current_price - min_tp_distance`. |
| `src/analysis/structure/structure_engine.py:298-356` (assemble dual-direction rr) | Calls `_sl_engine.calculate(direction="long")` and `(direction="short")`, then assembles `rr_long`/`rr_short`/`rr_best`. With the clamp active, both rr values are always `> 0` (never collapse). The "chosen" placement object retains the `is_structurally_invalid` of its own direction; the OPPOSITE direction's flag is implicitly discarded along with the discarded `long_pl`/`short_pl` object (see Discrepancies section). |

### Downstream consumers of `rr_long` / `rr_short`

**1. `src/apex/optimizer.py:1420-1441` — direction-lock structural-signal**

```python
        # Signal 2: structural R:R (log-scale, signed)
        structural_signal = 0.0
        if sd is not None:
            rr_long = getattr(sd, "rr_long", None)
            rr_short = getattr(sd, "rr_short", None)
            if (
                rr_long is not None
                and rr_short is not None
                and rr_long > 0
                and rr_short > 0
            ):
                if claude_direction == "Buy":
                    ratio = rr_long / rr_short
                else:
                    ratio = rr_short / rr_long
                # Clamp ratio to [0.01, 100] before log so a 0 or extreme
                # value cannot blow up the score.
                ratio = max(0.01, min(100.0, ratio))
                structural_signal = math.log(ratio)
```

Pre-Phase-C: with `rr_long=0`, both the `> 0` guard fails AND the `max(0.01, ...)` clamp blunts extreme values, so `math.log` never blows up. Phase C makes `rr_long > 0` always true (clamp guarantees positive reward), so the guard passes more often — `structural_signal` will compute on more inputs. The pre-clamp pathological case (rr_long=0 → ratio=0 → log = -inf) was already defensively clamped; Phase C just adds correctness so the clamp is rarely needed. Behavior change: more structural_signal computations, no blow-up risk.

Verdict: SAFE.

**2. `src/apex/optimizer.py:484-503` — flip RR-boost**

```python
                _sd = getattr(package, "structural_data", None)
                if _sd is not None:
                    if claude_direction == "Buy":
                        _rr_chosen = float(getattr(_sd, "rr_long", 0.0) or 0.0)
                        _rr_flipped = float(getattr(_sd, "rr_short", 0.0) or 0.0)
                    elif claude_direction == "Sell":
                        _rr_chosen = float(getattr(_sd, "rr_short", 0.0) or 0.0)
                        _rr_flipped = float(getattr(_sd, "rr_long", 0.0) or 0.0)
                    if _rr_chosen > 0 and _rr_flipped > 0:
                        _ratio = _rr_flipped / _rr_chosen
```

Same shape — guarded by `_rr_chosen > 0 and _rr_flipped > 0`. With clamp active, both are guaranteed > 0 — but the ratio that triggers the boost (`_ratio >= 3.0` typical threshold) is naturally bounded by clamp-floor math. With `tp_min_distance_pct=0.5` and `sl_buffer_pct=0.15`, the minimum clamped RR is roughly 0.5%/(some risk%) — for a typical 2% SL distance, min rr ≈ 0.25. So `_rr_chosen=0.25` is now the floor instead of zero; ratios of 8× ÷ 0.25 = 32× are mathematically possible but should be rare in practice (the clamp engages only when price is at/above resistance for a long, an edge condition).

Verdict: SAFE — boost engages more reliably (no false-zero path), magnitude bounded by `[0.01, ...]` clamps elsewhere.

**3. `src/apex/gate.py:752-781` — portfolio direction cap aim-conditional**

```python
                                        if not opp_viable and _sa.structural_placement:
                                            _rr_long = (
                                                _sa.structural_placement.rr_long or 0.0
                                            )
                                            _rr_short = (
                                                _sa.structural_placement.rr_short or 0.0
                                            )
                                            if new_dir_norm == "Buy" and _rr_long > 0:
                                                if (
                                                    _rr_short / max(_rr_long, 0.01)
                                                    >= opp_ratio_threshold
                                                ):
```

Defensively clamps `_rr_long` to `max(0.01)` in the denominator regardless. Phase C means `_rr_long > 0` is always satisfied for placements with resistance data, so the cap engages more often. Threshold default is 2.0 (`portfolio_direction_cap_opposite_ratio_threshold`), so a 4×/2.0 = 2× ratio with the clamp floor is the marginal case. Operator-controlled threshold protects against false positives.

Verdict: SAFE.

**4. `src/apex/assembler.py:782-788` — package the rr_long/rr_short into IntelligencePackage**

```python
        if analysis.structural_placement:
            sp = analysis.structural_placement
            sd.rr_ratio = sp.rr_ratio        # = rr_best (backward compat)
            sd.rr_quality = sp.rr_quality
            sd.rr_long = sp.rr_long
            sd.rr_short = sp.rr_short
            sd.rr_best_direction = sp.rr_best_direction
```

Pure pass-through.

Verdict: SAFE.

**5. `src/apex/models.py:294-350` — StructuralData container**

```python
    rr_long: Optional[float] = None          # R:R for LONG direction
    rr_short: Optional[float] = None         # R:R for SHORT direction
    ...
            if self.rr_long is not None and self.rr_short is not None:
```

Optional fields, defensive `is not None` checks. Phase C does not change shape.

Verdict: SAFE.

**6. `src/workers/strategy_worker.py:1699-1977` — XRAY flip block**

Critical consumer. The key compute lines:

```python
                if _sp and _sp.rr_long > 0 and _sp.rr_short > 0:
                    if direction == "Buy" and _sp.rr_long < 1.0 and _sp.rr_short >= 2.0:
                        log.warning(
                            f"XRAY_DIR_MISMATCH | sym={symbol} dir=Buy "
                            f"rr_long={_sp.rr_long:.1f} rr_short={_sp.rr_short:.1f} "
                            ...
```

At `strategy_worker.py:1727-1731`:

```python
                    _ratio = 0.0
                    if direction == "Buy" and _sp.rr_long > 0:
                        _ratio = _sp.rr_short / _sp.rr_long
                    elif direction == "Sell" and _sp.rr_short > 0:
                        _ratio = _sp.rr_long / _sp.rr_short
```

Pre-Phase-C: `_sp.rr_long` collapsed to 0 → outer guard `_sp.rr_long > 0` failed → ratio stayed at 0 → no flip; OR `rr_long` was small-positive (collapse near-zero) → `_ratio` exploded (e.g., 338x in audit logs cited by `J3` block at `:1762-1764`).

Post-Phase-C: `rr_long` is always positive (clamp guarantees ≥ small floor). With `tp_min_distance_pct=0.5` and typical 2% SL: `rr_long ≥ 0.5/2.0 = 0.25`. So if `rr_short = 5`, `_ratio = 5/0.25 = 20×`. The 20× would still trigger the flip (`_flip_threshold = 3.0`), and the operator-tunable `xray_lock_override_ratio_threshold` (default 10×) would also be exceeded.

The behaviorally meaningful change: the cascade is now bounded by clamp arithmetic instead of the abs() collapse artifact. The 338x extreme case becomes a more honest 20x. The semantic intent (flip on superior opposite-direction RR) is preserved with cleaner inputs.

Verdict: SAFE — flip ratios become physically bounded, no test/assertion broken.

**7. `src/workers/strategy_worker.py:1937-1964` — flip mutation + DB persistence**

```python
                        if _flipped_dir == "Sell":
                            _new_sl = _sp.short_sl_price
                            _new_tp = _sp.short_tp_price
                            _new_rr = _sp.rr_short
                            _orig_rr = _sp.rr_long
                        else:
                            _new_sl = _sp.long_sl_price
                            _new_tp = _sp.long_tp_price
                            _new_rr = _sp.rr_long
                            _orig_rr = _sp.rr_short
                        ...
                        trade["_xray_flip_rr_long"] = round(float(_sp.rr_long), 2)
                        trade["_xray_flip_rr_short"] = round(float(_sp.rr_short), 2)
```

`_sp.long_sl_price` / `_sp.short_sl_price` / `_sp.long_tp_price` / `_sp.short_tp_price` are populated at `structure_engine.py:351-356` from `long_pl.structural_tp` / `short_pl.structural_tp` — which under Phase C carry the clamped TP. So a flip into `short` direction picks up the clamped short TP (if the short side's clamp engaged).

Verdict: SAFE — clamped TPs flow through to trade execution.

**8. `src/core/thesis_manager.py:150-260` — XRAY flip thesis persistence**

Stores `xray_flip_rr_long` / `xray_flip_rr_short` in DB; treated as opaque floats. Schema unchanged.

Verdict: SAFE.

### Downstream consumers of `rr_ratio` (= `rr_best`)

**1. `src/strategies/performance_enforcer.py:316, 387-388`**

```python
        old_rr = float(sp.rr_long if is_buy else sp.rr_short)
        ...
        if sp and sp.rr_ratio < self._l2_min_rr:
            return False, f"rr_{sp.rr_ratio:.1f}_below_{self._l2_min_rr}"
```

Reads `rr_long`/`rr_short` per direction and `rr_ratio` for the SURVIVAL-mode quality gate. With clamp active, `rr_ratio` (= `rr_best`) reflects honest math — a clamped TP yielding rr=0.25 will fail the `< l2_min_rr` (default 2.0) check, which is the correct behavior (it's a near-resistance situation, the trade SHOULD be filtered).

Verdict: SAFE — quality gate becomes more honest.

**2. `src/core/sl_tp_validator.py:196-199`**

Pure read of `placement["rr_ratio"]` for log decoration. No decision logic.

Verdict: SAFE.

**3. `src/core/layer_manager.py:1395-1398`**

```python
                _t.setdefault(
                    "_expected_rr",
                    float(getattr(_levels, "rr_ratio", 0.0) or 0.0),
                )
```

Stores rr in trade dict for downstream record-keeping. Pure pass-through.

Verdict: SAFE.

**4. `src/core/coin_package_validator.py:149-152`**

```python
        rr_ok = bool(pkg.xray.structural_levels and pkg.xray.structural_levels.rr_ratio > 0)
        _opt(rr_ok, "xray.structural_levels.rr_ratio")
```

Existential check: "rr_ratio is non-zero". With clamp active, `rr_ratio` is more reliably non-zero. SAFE.

**5. `src/core/coin_package.py:32`**

Dataclass field default — no logic. SAFE.

**6. `src/analysis/structure/mtf_confluence.py:196-201`**

```python
        if placement and placement.rr_ratio >= 2.0:
            contributions["rr_ratio"] = 1
```

A trade that previously had rr_ratio=0 (collapse) and contributed 0 to confluence now has rr_ratio=0.25-0.5 (clamped) — still below 2.0 threshold, still contributes 0. Higher cases unchanged. SAFE.

**7. `src/analysis/structure/setup_scanner.py:113, 160, 188, 202, 243-247`**

```python
        qual["rr_adequate"] = sp is not None and sp.rr_ratio >= 2.0
        ...
            active.append(f"rr_{sp.rr_ratio:.1f}" if sp else "rr_ok")
        ...
        desc_parts.append(f"RR=1:{sp.rr_ratio:.1f}" if sp else "")
        ...
            rr_ratio=sp.rr_ratio if sp else 0.0,
        ...
            if sp.rr_ratio >= 4.0:
```

`sp.rr_ratio` here is `rr_best` (max of long/short). Active threshold = 2.0. Phase C effect: previously `rr_long=0, rr_short=5` gave `rr_best=5` so `rr_adequate=True`. Now (clamp-aware) `rr_long=0.25, rr_short=5` still gives `rr_best=5` — same outcome. The "best" direction still dominates the rank, the clamped weak direction doesn't poison the max. SAFE.

**8. `src/strategies/scorer.py:473`**

```python
            _rr_ratio = placement.get("rr_ratio", 0)
```

Read from dict. SAFE.

### Downstream consumers of `structural_tp` / `structural_sl`

**1. `src/core/flip_tp_capper.py:54-155`** (TP-Volume-Closure XRAY-flip TP cap):

```python
def cap_xray_flip_tp(
    ...
    structural_tp: float,
    current_price: float,
    ...
```

Accepts `structural_tp` as input; clamps to a max distance from current price. With Phase C clamp active, structural_tp is already at minimum `0.5%` away — the volume-cap module operates on that input independently. The two clamps are complementary (Phase C floors the distance, flip_tp_capper caps it). They don't interact pathologically.

Verdict: SAFE.

**2. `src/analysis/structure/setup_scanner.py:204-205`**

```python
            structural_sl=sp.structural_sl if sp else 0.0,
            structural_tp=sp.structural_tp if sp else 0.0,
```

Pass-through to StructuralSetup output.

Verdict: SAFE.

**3. `src/workers/strategy_worker.py:2159` — `structural_tp=tp` (XRAY flip thesis blob)**

```python
                structural_tp=tp,
```

Stores TP into thesis blob; opaque.

Verdict: SAFE.

**4. `src/workers/scanner_worker.py:620-627` — `suggested_sl/tp`**

```python
                        levels.suggested_sl = float(getattr(sp, "long_sl_price", 0.0) or 0.0)
                        levels.suggested_tp = float(getattr(sp, "long_tp_price", 0.0) or 0.0)
                    elif direction == "short":
                        levels.suggested_sl = float(getattr(sp, "short_sl_price", 0.0) or 0.0)
                        levels.suggested_tp = float(getattr(sp, "short_tp_price", 0.0) or 0.0)
                    else:
                        levels.suggested_sl = float(getattr(sp, "structural_sl", 0.0) or 0.0)
                        levels.suggested_tp = float(getattr(sp, "structural_tp", 0.0) or 0.0)
```

For Buy/Sell directions reads dual-direction TP (already clamp-aware after Phase C). For undecided direction reads the `structural_tp` chosen at engine assembly time. SAFE.

### Downstream consumers of `StructuralPlacement` (object)

Two production callsites:
- `src/analysis/structure/structural_levels.py:159-172` (`_calc_long` return).
- `src/analysis/structure/structural_levels.py:246-259` (`_calc_short` return).

Both use kwargs only — no positional args. Adding `is_structurally_invalid` as the last field cannot break either.

Test callsites:
- `tests/test_structural_floor.py:65` — `StructuralPlacement()` — uses default (False).
- `tests/test_structural_floor.py:73` — `StructuralPlacement(is_structurally_invalid=True)` — keyword.
- `tests/tp_volume_fix_pipeline_test.py:483` — `StructuralPlacement()` — no args, picks up defaults including new flag = False.

No positional-arg drift risk.

Verdict: SAFE.

### Downstream consumers of `is_structurally_invalid`

Grep result: only the producer side (`src/analysis/structure/structural_levels.py`, `src/analysis/structure/models/structure_types.py`) plus the new tests (`tests/test_structural_floor.py`). Zero current downstream readers — flag is purely additive observability, exactly as the spec promised.

Future opportunities (not yet wired, not required by Phase C):
- APEX optimizer sizing reduction when `is_structurally_invalid=True`.
- Watchdog skipping force-close decisions premised on a structurally-invalid placement.
- `XRAY_LEVELS` DEBUG log already emits the flag — operator can grep prevalence (Phase 4 trial verification).

Verdict: SAFE — additive flag, no break risk.

### Downstream consumers of `tp_min_distance_pct` / `min_touches_resistance`

Grep result for each:

| Field | Reader (production code) | Status |
|---|---|---|
| `tp_min_distance_pct` | `src/analysis/structure/structural_levels.py:114, 209` (the producer) + `src/analysis/structure/structure_engine.py:91` (boot sentinel) | 2 readers, expected |
| `min_touches_resistance` | `src/analysis/structure/support_resistance.py:135` (the producer) + `src/analysis/structure/structure_engine.py:93, 95` (boot sentinel) | 3 readers, expected |

Both confined to `src/analysis/structure/` (Layer 1B). No cross-layer reach. Matches spec promise of "fix lives entirely in src/analysis/structure/".

Verdict: SAFE — clean confinement.

---

## Contract preservation analysis

The new field `is_structurally_invalid: bool = False` is appended to the `StructuralPlacement` dataclass. Default value preserves the pre-fix semantics for any code path that does NOT set it.

| Risk | Verified | Notes |
|---|---|---|
| Positional-arg drift | No risk | All `StructuralPlacement(...)` sites use keyword args. |
| `to_dict()` consumer mismatch | No risk | New key is additive in `to_dict()`. Existing consumers reading specific keys ignore the new entry. |
| Pickle/serialization back-compat | No risk | dataclass auto-handles defaults on de-serialization of older pickles. |
| Test fixtures that constructed `rr_long=0` to signal "invalid" | No risk found | Grep across `tests/` shows no fixture relied on `rr_long=0` as an implicit signal. The new authoritative signal is `is_structurally_invalid`, but it has no readers yet — both the old behavior (rr_long=0 indicating problem) and the new behavior (clamped rr_long ≥ floor) co-exist without contradiction during this transition. |
| Settings constructor without new fields | No risk | `StructureSettings()` default-constructs all fields; loaders (`_build_structure` at `src/config/settings.py:4138-4157` and `src/workers/settings.py:1161-1166`) filter to `hasattr(StructureSettings, k)` so unknown TOML keys are silently dropped, known ones (the new ones) pass through. |

Note on `src/workers/settings.py:594-627`: there is a second `StructureSettings` class defined there which does NOT have the new fields. Grep verified zero callers import from `src.workers.settings` for `StructureSettings` (all 12 consumers in `src/analysis/structure/` import from `src.config.settings`). The `src/workers/settings.py` `StructureSettings` is vestigial/dead-code. Verdict: not a contract risk for Phase C but a code-hygiene cleanup target for a future commit.

Verdict: PASS — no contract regressions.

---

## Pipeline trace

End-to-end Phase C flow:

1. **Config load** — `config.toml [analysis.structure]` → `_build_structure(data)` at `src/config/settings.py:4138-4157` → constructs `StructureSettings` with `tp_min_distance_pct=0.5`, `min_touches_resistance=2`.

2. **Engine init** — `StructureEngine.__init__` at `src/analysis/structure/structure_engine.py:60-98` emits `XRAY_INIT` then `XRAY_FLIP_CONFIG | tp_min_distance_pct=0.50 min_touches_support=2 min_touches_resistance=2 min_touches_symmetric=True` (or `False` if operator overrode).

3. **Support/Resistance detection** — `SupportResistanceEngine.calculate` at `src/analysis/structure/support_resistance.py:39-155`:
   - clusters swing points,
   - filters by `min_touches` (support) and `min_touches_resistance` (resistance) — symmetric since Phase C.

4. **Structural SL/TP placement** — `StructuralLevelCalculator.calculate(direction="long")` and `(direction="short")` at `src/analysis/structure/structural_levels.py:32-65`:
   - `_calc_long` (`:67-172`): computes raw TP, clamps UP if `raw_tp < min_tp`, sets `is_structurally_invalid=True` when clamped.
   - `_calc_short` (`:174-259`): mirror — clamps DOWN if `raw_tp > max_tp`.

5. **Dual-direction assembly** — `StructureEngine` at `src/analysis/structure/structure_engine.py:298-356`:
   - calls `_sl_engine.calculate` for both directions,
   - picks `long_pl` or `short_pl` based on `suggested_direction` (or by best rr if "ranging"),
   - populates `rr_long`, `rr_short`, `rr_best`, `rr_best_direction`, `long_sl_price`, `long_tp_price`, `short_sl_price`, `short_tp_price` from BOTH side calculators.
   - The chosen placement's `is_structurally_invalid` is whatever was set by its OWN direction's `_calc_*`.

6. **Cache** — placement is cached in `StructureWorker._cache` (read at `src/workers/scanner_worker.py:243-254`).

7. **Downstream readers**:
   - `StrategyWorker` (XRAY flip block at `:1699-1977`) — reads `rr_long`/`rr_short` for direction decision and `long_sl_price`/`short_sl_price`/`long_tp_price`/`short_tp_price` for flip-target SL/TP.
   - APEX optimizer (`:1420-1441`) — reads `rr_long`/`rr_short` for direction-lock structural signal.
   - APEX gate (`:752-781`) — reads for portfolio cap aim-conditional logic.
   - `PerformanceEnforcer` (`:316`) — reads `rr_long`/`rr_short` for SURVIVAL TP adjustment.
   - `MTFConfluence` (`:196`) — reads `rr_ratio` for confluence contribution.
   - `SetupScanner` (`:113, 160, 188, 202, 243-247`) — reads `rr_ratio` for qualification.

All readers either:
- Defensively guard against pre-clamp degenerate values (`rr_long > 0`, `max(rr_long, 0.01)`),
- Or compute on `rr_best` (which never collapsed even pre-fix because at least one side had valid resistance).

Phase C makes degenerate inputs structurally impossible; no reader was depending on the collapse behavior.

Verdict: PASS — pipeline trace is coherent end-to-end.

---

## Regression test results

Command executed (per spec):

```
pytest tests/test_xray_dir_flip.py tests/test_setup_classifier_counter.py \
       tests/test_apex_direction_lock.py tests/test_apex_flip_decision_log.py \
       tests/test_alpha_r1_trade_direction.py tests/test_strategist_callb_prompt.py \
       tests/test_apex_pipeline_integration.py tests/test_structural_floor.py -v --tb=short
```

Result:

```
collected 104 items

tests/test_xray_dir_flip.py ...                                          [  2%]
tests/test_setup_classifier_counter.py ..........................        [ 27%]
tests/test_apex_direction_lock.py ..................F..........          [ 55%]
tests/test_apex_flip_decision_log.py .......                             [ 62%]
tests/test_alpha_r1_trade_direction.py ......                            [ 68%]
tests/test_strategist_callb_prompt.py ...........                        [ 78%]
tests/test_apex_pipeline_integration.py .............                    [ 91%]
tests/test_structural_floor.py .........                                 [100%]

= 1 failed, 103 passed in 3.79s =
```

The single failure: `tests/test_apex_direction_lock.py::test_system_prompt_still_has_rsi_caution` at `tests/test_apex_direction_lock.py:297-301`. This asserts `"Oversold RSI in a downtrend" in STRATEGIST_SYSTEM_PROMPT`, which was removed from the strategist system prompt as part of a prior shipment.

Verified pre-existing failure: `git show 99b3420~1:tests/test_apex_direction_lock.py | grep -n test_system_prompt_still_has_rsi_caution` confirms the same `Oversold RSI in a downtrend` assertion existed at line 301 on the parent commit too. The failure is unrelated to Phase C.

Documentation reference for the pre-existing failure: `dev_notes/dirbias_validation/phase6_phase_a_trial.md:27`:

> All should pass; the 1 pre-existing failure `tests/test_apex_direction_lock.py::test_system_prompt_still_has_rsi_caution` is unrelated to these changes.

(The commit message references `09_phase1_synthesis.md` for this pre-existing failure — `phase6_phase_a_trial.md` carries the equivalent documentation; the trial validation file is the more relevant audit anchor.)

Verdict: PASS — 103 of 104 pass, with the 1 failure pre-existing and documented.

---

## Boot sentinel verification

Expected emission (from spec):

```
XRAY_FLIP_CONFIG | tp_min_distance_pct=0.50 min_touches_support=2 min_touches_resistance=2 min_touches_symmetric=True
```

Source emission at `src/analysis/structure/structure_engine.py:88-96`:

```python
            log.info(
                f"XRAY_FLIP_CONFIG | "
                f"tp_min_distance_pct={self._settings.tp_min_distance_pct:.2f} "
                f"min_touches_support={self._settings.min_touches} "
                f"min_touches_resistance={self._settings.min_touches_resistance} "
                f"min_touches_symmetric="
                f"{self._settings.min_touches == self._settings.min_touches_resistance}"
            )
```

With default settings:
- `tp_min_distance_pct=0.50`
- `min_touches_support=2`
- `min_touches_resistance=2`
- `min_touches_symmetric=True`

Matches spec exactly. Wrapped in try/except so a malformed settings injection cannot crash `StructureEngine.__init__` — falls back to `XRAY_FLIP_CONFIG_FAIL` DEBUG log.

Verdict: PASS — sentinel emits the contracted format and field names.

---

## CLAUDE.md rules compliance

CLAUDE.md mandates "grep all usages first, never assume self-containment".

| Rule | Applied to Phase C? | Evidence |
|---|---|---|
| Grep all usages of `min_touches` before adding `min_touches_resistance` | YES | The diff at `src/analysis/structure/support_resistance.py:122-138` shows BOTH support and resistance filters now read from `_settings`. The support filter behavior is unchanged (`min_t = self._settings.min_touches` then `>= min_t`); only resistance was added — confirming `min_touches` consumers were not broken. |
| Grep all consumers of `structural_tp` before adding the clamp | YES (verified by this audit) | Consumers: `flip_tp_capper.py`, `setup_scanner.py`, `scanner_worker.py`, `strategy_worker.py`, `structure_engine.py` (assembly). All either pass `structural_tp` through unchanged (clamped values flow seamlessly) or compute downstream metrics that strengthen, not regress, with the clamp. |
| Grep all consumers of `StructuralPlacement` for positional-arg breakage | YES (verified by this audit) | All instantiation sites use kwargs; the new `is_structurally_invalid` field is appended last in the dataclass; no positional-arg drift possible. |
| Verify the no-op-default anti-pattern (Concern 4) | YES | Both `tp_min_distance_pct=0.5` and `min_touches_resistance=2` are ACTIVE defaults. Confirmed at `src/config/settings.py:2414` and `:2425`. Matches `dev_notes/dirbias_validation/13_evaluate_concern4.md` verdict. |
| No band-aid patch (Concern 1) | YES | Phase A2 RR floor guard at `strategy_worker.py:1727-1739` was REJECTED per `10_evaluate_concern1.md`. Phase C does not add any threshold-based RR floor at the flip decision site. The clamp lives at the producer (`structural_levels.py`), addressing the math degeneracy at its origin. |

Verdict: PASS — CLAUDE.md rules respected.

---

## Operator directive compliance

Direction-bias correction must not introduce "hardcoded direction-specific asymmetric corrections." Phase C compliance:

**1. `min_touches_resistance=2`** — IS this a hardcoded asymmetric correction? NO.
- The legacy `>= 1` for resistance with `min_touches=2` for support was the asymmetric correction.
- The fix REMOVES that asymmetry by making both sides config-driven and defaulting to the same value (`2`).
- The symmetric default is enforced by the boot sentinel `min_touches_symmetric=True`.

**2. `tp_min_distance_pct=0.5`** — IS this a hardcoded direction-specific value? NO.
- The clamp applies equally to `_calc_long` (`structural_levels.py:114-117`) and `_calc_short` (`:209-212`) using the same `tp_min_distance_pct` field.
- The long clamp pushes UP, the short clamp pushes DOWN, but both use the same magnitude — symmetric in absolute terms.
- The value is operator-tunable via `config.toml [analysis.structure] tp_min_distance_pct`.

**3. Is the structural fix at the symptom or the source?** SOURCE.
- The collapse symptom was at `strategy_worker.py:1727-1739` (the flip ratio).
- The root cause was at `structural_levels.py:101` (no min-edge on TP) + `support_resistance.py:126` (asymmetric filter).
- Phase C edits the root causes; the symptom site is untouched.

Verdict: PASS — symmetric, operator-tunable, root-cause-targeted.

---

## Aim-bias five-question evaluation

| Question | Verdict | Reasoning |
|---|---|---|
| Preserve trade frequency? | YES | The clamp produces a valid TP (rr ≥ small floor) rather than collapsing to a zero-reward flip event. Trades that previously cascaded into XRAY_DIR_FLIP and then potentially blocked or mis-directed are now either correctly directed or correctly rejected — net result is slightly MORE trade attempts, not fewer. |
| Preserve aggression? | YES | No new BLOCK gates introduced. The `is_structurally_invalid` flag is additive and has zero current readers — purely observability. The only behavior change is replacing `abs()`-masked collapse outputs with honest clamped numbers; the downstream decision logic (XRAY flip threshold = 3.0, lock override = WR-aware) is unchanged. |
| Improve decision quality? | YES | `rr_long` now reflects structural truth: when the long side is fundamentally weak (price at/above resistance), the RR shows `~0.25-0.5` (clamped floor), not a collapse-math artifact of `0.0` or a small-positive-fluke. APEX optimizer's `log(rr_long/rr_short)` structural signal becomes monotonically meaningful. |
| Preserve passive-close advantage? | YES | Layer 4 (Watchdog, time-decay closure) uses the same `structural_placement` object via `structure_cache.get(symbol)` and reads `structural_sl`, `structural_tp`, `rr_long`, `rr_short`, etc. The clamp produces more conservative TPs (closer to current_price) in degenerate cases — which Layer 4 will treat as already-near-target, the right semantic. `is_structurally_invalid` is purely additive; no Layer 4 code reads it yet. |
| Respect structural separation? | YES | Every edit lives inside `src/analysis/structure/` (Layer 1B), `src/config/settings.py` (config layer), `config.toml` (operator surface), and `tests/test_structural_floor.py`. No cross-layer reach into Layer 2 (strategist), Layer 3 (executor), Layer 4 (watchdog), or downstream APEX. |

Verdict: PASS — aim-bias five-question evaluation clean.

---

## Discrepancies found

1. **`src/workers/settings.py:594` shadow `StructureSettings`** — a second `StructureSettings` dataclass exists at `src/workers/settings.py:594-627` which lacks the new `min_touches_resistance` and `tp_min_distance_pct` fields. Cross-checked with grep: no production import of `src.workers.settings.StructureSettings`. The class is vestigial/dead-code. NOT a Phase C defect — but a future cleanup target (`fix(cleanup)`: remove src/workers/settings.py StructureSettings or wire it to the canonical one). Severity: LOW (dead code).

2. **`is_structurally_invalid` is per-direction, not dual** — `_calc_long` sets the flag based on the LONG-side clamp; `_calc_short` based on SHORT-side. At `structure_engine.py:298-356` the assembly picks ONE placement (long_pl OR short_pl based on suggested_direction or rr_best) — the OTHER direction's flag is implicitly discarded along with the unused placement object. Downstream consumers reading `placement.is_structurally_invalid` see only the chosen direction's flag.

   This is correct semantics for the chosen-direction-trade decision: a trader entering "long" cares whether the LONG side's structural TP is invalid. But if a code path were to use `placement.short_tp_price` after a long placement was chosen, the consumer would not know that the short-side TP was also clamped. Per the audit, no current consumer does this — but it's a latent observability gap. Severity: LOW (currently no consumer).

3. **`_calc_short` has no test for the no-clamp branch** — `tests/test_structural_floor.py` Section 4 has `test_calc_short_clamps_tp_when_support_above_floor` (the clamp case) but no `test_calc_short_does_not_clamp_when_support_below_floor` (the no-clamp case) counterpart to `test_calc_long_does_not_clamp_when_resistance_above_floor`. The shorts mirror logic is mechanically identical to longs — the gap is asymmetric coverage, not a behavioral risk. Severity: LOW (low risk, but worth adding for symmetry).

4. **Commit message anchor inaccuracy** — the commit message at line 168 of `git show 99b3420` says:

   > "the 1 pre-existing failure test_system_prompt_still_has_rsi_caution was already failing on main HEAD before this change; documented in dev_notes/dirbias_validation/09_phase1_synthesis.md"

   Grep of `dev_notes/dirbias_validation/09_phase1_synthesis.md` returns no mention of `test_system_prompt_still_has_rsi_caution`. The actual documentation lives at `dev_notes/dirbias_validation/phase6_phase_a_trial.md:27`. The pre-existing-failure CLAIM is correct (verified independently in this audit), but the dev_notes anchor in the commit message points to the wrong file. Severity: LOW (docs only; the failure is genuinely pre-existing).

5. **`tp_min_distance_pct` clamp interaction with `tp_buffer_pct`** — when raw_tp is between `(current_price + min_tp_distance)` and `(nearest_res.zone_low - nearest_res.price * tp_buffer)`, the clamp does NOT engage and the original tp_buffer-based formula applies. With `tp_min_distance_pct=0.5` and `tp_buffer_pct=0.10`, there is an effective floor of ≈ 0.5% but the resistance-derived TP can still produce smaller rewards if the resistance zone is < 1% away. This is BY DESIGN per Concern 4 (the clamp activates only when raw_tp would land on the wrong side or extremely close). Not a defect — but worth noting that the clamp is NOT a "minimum reward enforcer" universally; it's a "minimum right-side distance enforcer." Severity: INFO.

6. **Boot sentinel `min_touches_symmetric` uses python `==` comparison** — the boolean is computed as `self._settings.min_touches == self._settings.min_touches_resistance`. If an operator sets unusual values (e.g., `min_touches=3` and `min_touches_resistance=3`), the sentinel reports `True` (symmetric). If they intentionally set asymmetric (`min_touches=2, min_touches_resistance=1`), reports `False`. Semantics correct — symmetry is the field-equality, not a fixed-value check. Severity: INFO.

Net: no Phase C functional defects found. Five LOW-or-INFO observations, all either dead-code or documentation-anchor minor issues.

---

## Verdict

**PASS WITH NOTES.**

All 7 spec-mandated edit surfaces are present, correct, and behaviorally consistent with the dev_notes/dirbias_validation/ Phase C plan:

1. `StructureSettings.min_touches_resistance: int = 2` + `tp_min_distance_pct: float = 0.5` shipped at `src/config/settings.py:2414, 2425`.
2. `StructuralPlacement.is_structurally_invalid: bool = False` shipped at `src/analysis/structure/models/structure_types.py:152` + serialized in `to_dict` at `:173`.
3. Symmetric `min_touches_resistance` filter shipped at `src/analysis/structure/support_resistance.py:135-138`.
4. `_calc_long` clamp + flag shipped at `src/analysis/structure/structural_levels.py:106-127`; `_calc_short` mirror at `:205-222`. Both return the flag via the StructuralPlacement.
5. `XRAY_FLIP_CONFIG` boot sentinel shipped at `src/analysis/structure/structure_engine.py:81-98` with the contracted format and field names.
6. `config.toml [analysis.structure]` surface shipped at `config.toml:1612-1632` with explanatory comments.
7. `tests/test_structural_floor.py` shipped at `tests/test_structural_floor.py` with all 9 promised tests, all passing.

Regression sweep: 103/104 pass. The 1 failure (`test_system_prompt_still_has_rsi_caution`) is pre-existing and documented.

Aim-bias five-question evaluation: clean PASS on all five.

CLAUDE.md rules + operator directives: respected throughout.

Five LOW/INFO observations are documented in Discrepancies. None of them are Phase C defects — they are either dead-code cleanup opportunities (`src/workers/settings.py` shadow class), latent observability gaps that no current consumer hits (`is_structurally_invalid` per-direction), or doc-anchor minor inaccuracies (commit-message ref to file 09 vs the actual phase6 trial doc).

---

## Recommended follow-ups

These are NOT required for Phase C ratification but are surfaced for the operator's roadmap:

1. **Downstream consumers that COULD use `is_structurally_invalid` for sizing** (Phase D candidates):
   - `src/apex/optimizer.py` sizing function — reduce size when `placement.is_structurally_invalid=True` (the spec at `structure_types.py:148-151` suggests this).
   - `src/watchdog/...` force-close logic — skip force-close decisions premised on a structurally-invalid placement.
   - `src/strategies/performance_enforcer.py:316-330` — when running SURVIVAL TP scaling, qualify the result if the underlying placement is structurally invalid.

2. **Resolve `src/workers/settings.py:594` shadow `StructureSettings`** — either remove (zero call sites) or wire to the canonical class to prevent future drift. The settings-class-divergence pattern caused a real bug earlier in the project history (citation: 2026-04-27 layer1 restructure debrief).

3. **Add `test_calc_short_does_not_clamp_when_support_below_floor`** — symmetric coverage of the no-clamp branch on short side. Minor.

4. **Phase 4 live trial verification** — per `dev_notes/dirbias_validation/phase6_phase_bc_trial.md:52`:
   - Monitor `XRAY_LEVELS` DEBUG logs for `invalid=True` occurrences over 4-6 hours.
   - Expected: ≥1 firing per ~10 minutes when prices are near resistance levels in trending markets.
   - Failure mode: 0 firings → clamp is inert; or 100% firings → clamp is over-firing.

5. **Future: dual-direction `is_structurally_invalid`** — currently the assembled placement carries the flag of the CHOSEN direction only. If a downstream consumer needs to know whether BOTH directions are structurally invalid (e.g., for "neither side has clean structure" reasoning), the model would need `is_long_structurally_invalid` + `is_short_structurally_invalid` separately. Not needed today; flagged for awareness.

6. **Correct commit-message anchor** (informational only) — the next direction-bias merge commit should reference `phase6_phase_a_trial.md:27` (or equivalent in `phase6_phase_bc_trial.md`) for the pre-existing failure note, rather than `09_phase1_synthesis.md`. Doc-only nit.
