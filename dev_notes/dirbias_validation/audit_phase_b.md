# Deep Cross-Check Audit — Phase B (Issue 3) Soft Regime Haircut

**Branch audited:** `main` at HEAD `2864216` (Phase C merged on top)
**Commit under audit:** `1ebae0d` `fix(dirbias/issue3): soft regime haircut for state_labeller triggers`
**Merge commit:** `161fae2` `merge: dirbias Issue 3 — labeller soft regime haircut (Phase B)`
**Audit date:** 2026-05-19
**Auditor scope:** Read-only verification; one writeable artifact only (this file).

The 1ebae0d commit converts 8 per-trigger regime hard-kill predicates in `src/workers/scanner/state_labeler.py` from "return None on regime mismatch" to "multiply base confidence by `regime_haircut` on mismatch." Adds new `LabellerSettings` dataclass + `[scanner.labeller]` TOML section. Threads `regime_haircut` kwarg through `label_state()`. Adds `LABELLER_REGIME_HAIRCUT_VERSION = 2` module constant and `STATE_LABELLER_REGIME_HAIRCUT_INIT` boot sentinel in `ScannerWorker.__init__`.

This audit verifies all 5 surfaces match the spec, downstream consumers preserve contracts, default-values are safe, the new low-confidence labels produce sensible numeric output, the boot sentinel is correctly scoped, no test fixtures need updates, and the implementation honors CLAUDE.md and operator-directive rules.

## Edit-site verification

### 1. `src/workers/scanner/state_labeler.py` — 8 trigger predicates

| # | Function | Pre-fix line (spec) | Post-fix line | Polarity | Verified |
|---|---|---|---|---|---|
| 1 | `_trigger_trend_pullback_long` | 253 | 264 | must BE `trending_up` | YES |
| 2 | `_trigger_trend_pullback_short` | 268 | 298 | must BE `trending_down` | YES |
| 3 | `_trigger_range_fade_long` | 283 | 318 | must BE `ranging` | YES |
| 4 | `_trigger_range_fade_short` | 301 | 345 | must BE `ranging` | YES |
| 5 | `_trigger_funding_extreme_fade_long` | 356 | 402 | must NOT be `trending_down` | YES |
| 6 | `_trigger_funding_extreme_fade_short` | 371 | 429 | must NOT be `trending_up` | YES |
| 7 | `_trigger_extreme_fear_long` | 477 | 540 | must NOT be `trending_down` | YES |
| 8 | `_trigger_extreme_greed_short` | 491 | 564 | must NOT be `trending_up` | YES |

Line shifts are accounted for by docstring + new `regime_haircut` parameter insertions; the spec-cited pre-fix lines map 1:1 to the eight modified functions.

**Common pattern check** at each predicate (verified via grep `regime_haircut` in `src/workers/scanner/state_labeler.py:267..580`):

```
267:    regime_haircut: float = 1.0,
292:        if regime_haircut <= 0.0:
294:        return base_conf * regime_haircut
```

Every predicate adds `regime_haircut: float = 1.0` to its signature (per-trigger default = 1.0 = full pass, which is the safe-default behavior at the predicate layer; the public-API default at `label_state` is intentionally 0.0 to preserve legacy hard-kill for callers that don't pass the kwarg).

Per-predicate breakdown:

- **`_trigger_trend_pullback_long`** (`src/workers/scanner/state_labeler.py:264-295`): mandatory direction + setup_type checks preserved at lines 284-289. Base confidence at line 290: `max(0.30, min(1.0, setup_type_confidence or 0.55))`. Regime check at line 291 with mismatch branch at lines 292-294: if `regime_haircut <= 0.0` return None (legacy hard kill); else return `base_conf * regime_haircut`. Match branch at line 295: return `base_conf` (full).
- **`_trigger_trend_pullback_short`** (`src/workers/scanner/state_labeler.py:298-315`): mirror — line 311 `if not _is_trending_down(regime)`, lines 312-314 same haircut branch.
- **`_trigger_range_fade_long`** (`src/workers/scanner/state_labeler.py:318-342`): mandatory `points_long` and `position_in_range >= 0.40` filters preserved at lines 332-336. Base confidence at line 337. Regime check at line 338 `if not _is_ranging(regime)`. Haircut branch at lines 339-341.
- **`_trigger_range_fade_short`** (`src/workers/scanner/state_labeler.py:345-361`): mirror — line 357 `if not _is_ranging(regime)`, haircut branch at lines 358-360.
- **`_trigger_funding_extreme_fade_long`** (`src/workers/scanner/state_labeler.py:402-426`): mandatory funding-threshold (line 415) and position_in_range upper bound (line 417) preserved. Base confidence at line 421 with magnitude-scaling: `min(1.0, 0.40 + excess * 200.0)`. Regime check at line 422 `if _is_trending_down(regime)` (note: negative gate — "must NOT be trending_down"). Haircut branch at lines 423-425, match branch at line 426.
- **`_trigger_funding_extreme_fade_short`** (`src/workers/scanner/state_labeler.py:429-445`): mirror — line 441 `if _is_trending_up(regime)`, haircut branch at lines 442-444.
- **`_trigger_extreme_fear_long`** (`src/workers/scanner/state_labeler.py:540-561`): mandatory `fear_greed` window (line 551) and `points_long` direction anchor (lines 553-555) preserved. Base confidence at line 556. Regime check at line 557 `if _is_trending_down(regime)`. Haircut branch at lines 558-560.
- **`_trigger_extreme_greed_short`** (`src/workers/scanner/state_labeler.py:564-580`): mirror — line 576 `if _is_trending_up(regime)`, haircut branch at lines 577-579.

**Asymmetric-polarity preservation:** the spec called out in `dev_notes/dirbias_validation/03_validate_issue3.md:740-742` that the 8 predicates have asymmetric regime semantics (positive "must BE" vs negative "must NOT be"). The implementation handles this correctly inline at each predicate's `if` line — no unified abstraction was needed because each predicate retains its original regime expression and only the action on mismatch changed (from `return None` to `if haircut <= 0: return None; else return base_conf * haircut`).

**Out-of-scope predicates correctly left alone:**

- `_trigger_breakout_pending` (line 364) — no regime hard-kill; range_compression branch checks `_is_ranging or 'dead'` but that's a permissive OR.
- `_trigger_liquidity_sweep_long/short` (lines 375, 385) — escape hatch, no regime gate per spec.
- `_trigger_counter_trade_long/short` (lines 448, 458) — explicit counter-direction signal, no regime gate per spec note at line 742.
- `_trigger_momentum_burst_long/short` (lines 468, 484) — regime gate `_is_volatile` is direction-symmetric per spec note line 744; haircut not applied (correct).
- `_trigger_ob_mitigated_fvg_only_long/short` (lines 500, 514) — no regime predicate.
- `_trigger_kill_zone_opportunity` (line 528) — no regime predicate.
- `_trigger_manipulation_window`, `_trigger_recent_loser_cooldown`, `_trigger_open_position_hold_review` (lines 583, 587, 591) — advisory; no regime predicate.

Grep confirms only the 8 spec-named predicates accept `regime_haircut`: see `src/workers/scanner/state_labeler.py:267, 301, 321, 348, 405, 432, 543, 567`.

### Module-level constant

`LABELLER_REGIME_HAIRCUT_VERSION = 2` at `src/workers/scanner/state_labeler.py:71` with version-2 docstring at lines 58-70 explaining the v1→v2 transition.

### `label_state()` signature update

`src/workers/scanner/state_labeler.py:598-626` — function adds `regime_haircut: float = 0.0` at line 625. Default 0.0 is the production-facing backwards-compat choice (legacy hard-kill behavior). Note the asymmetry: predicates default to 1.0 (full pass — safe at the helper level when called without the kwarg), but `label_state` defaults to 0.0 (preserves all existing test/caller behavior verbatim by reproducing legacy hard-kill).

### `label_state()` plumbing to triggers

All 8 firing calls plumb `regime_haircut=regime_haircut` (`src/workers/scanner/state_labeler.py:709, 715, 722, 729, 746, 751, 790, 796`). Verified via grep.

### Docstring updates

Updated for affected predicates and `label_state()`:

- `src/workers/scanner/state_labeler.py:269-282` — trend_pullback_long Issue 3 docstring.
- `src/workers/scanner/state_labeler.py:303` — trend_pullback_short mirror reference.
- `src/workers/scanner/state_labeler.py:323-327, 350` — range_fade Issue 3 docstring + mirror.
- `src/workers/scanner/state_labeler.py:407-413, 434` — funding fade Issue 3 docstring + mirror.
- `src/workers/scanner/state_labeler.py:545-550, 569` — extreme fear/greed Issue 3 docstring + mirror.
- `src/workers/scanner/state_labeler.py:674-684` — `label_state` `regime_haircut` arg docstring.

### 2. `src/config/settings.py` — `LabellerSettings`

- `LabellerSettings` dataclass at `src/config/settings.py:1226-1258` with `counter_regime_confidence_haircut: float = 0.5` (line 1250).
- `__post_init__` validator at `src/config/settings.py:1252-1258` raises `ValueError` if value is outside `[0.0, 1.0]`.
- `ScannerSettings.labeller` field at `src/config/settings.py:1293`.
- `_build_scanner_labeller` builder at `src/config/settings.py:3809-3817` reading `counter_regime_confidence_haircut` from TOML data, defaulting to 0.5 when absent.
- `_build_scanner` wires the builder at `src/config/settings.py:3834`: `labeller=_build_scanner_labeller(data.get("labeller", {}))`.

The `data.get("labeller", {})` pattern means a missing `[scanner.labeller]` section returns `{}` to the builder, which then defaults `counter_regime_confidence_haircut` to 0.5. Empirically validated:

```
python3 -c "from src.config.settings import _build_scanner_labeller; \
            s = _build_scanner_labeller({}); \
            print(s.counter_regime_confidence_haircut)"
# → 0.5
```

### 3. `config.toml` — `[scanner.labeller]` section

`config.toml:760-781` adds the section with 23 lines (per commit diffstat) including extensive comment rationale and the single line `counter_regime_confidence_haircut = 0.5`. Section header at `config.toml:780`.

### 4. `src/workers/scanner_worker.py` — plumbing

- Boot sentinel at `src/workers/scanner_worker.py:83-109` in `ScannerWorker.__init__`. Imports `LABELLER_REGIME_HAIRCUT_VERSION` locally inside `try` to keep the import boundary clean and to fall back to debug-log on any import/attribute error. Sentinel string at lines 98-104: `f"STATE_LABELLER_REGIME_HAIRCUT_INIT | version={LABELLER_REGIME_HAIRCUT_VERSION} haircut={_haircut:.2f} mode={...} | {ctx()}"`. The `mode=` field uses an inline if/else chain to emit `legacy_hard_kill | soft_haircut | no_regime_gate` based on the haircut value — useful for log-tail monitoring.
- `label_state()` call at `src/workers/scanner_worker.py:796-831` adds the `regime_haircut=` kwarg at lines 828-830, pulling from `self.settings.scanner.labeller.counter_regime_confidence_haircut`.

The Phase 2 commits' `phase6_phase_bc_trial.md:17` confirms the boot sentinel was observed at the 2026-05-19 10:03:33-35 UTC restart: `STATE_LABELLER_REGIME_HAIRCUT_INIT | version=2 haircut=0.50 mode=soft_haircut`.

### 5. `tests/test_phase3_1d_briefing/test_state_labeler_pure.py`

12 legacy tests preserved verbatim (no behavioral changes since none of them pass `regime_haircut` explicitly; they rely on the 0.0 default which reproduces legacy hard-kill):

| # | Test name | Test line |
|---|---|---|
| 1 | `test_trend_pullback_long_fires_in_uptrend_with_bullish_setup` | 27 |
| 2 | `test_trend_pullback_does_not_fire_in_ranging` | 41 |
| 3 | `test_counter_trade_long_inverts_direction` | 53 |
| 4 | `test_funding_extreme_fade_short_fires_when_longs_pay` | 66 |
| 5 | `test_funding_extreme_fade_long_blocked_in_downtrend` | 78 |
| 6 | `test_liquidity_sweep_reversal_short_fires_for_bearish_sweep` | 88 |
| 7 | `test_no_tradeable_state_when_nothing_fires` | 98 |
| 8 | `test_open_position_advisory_always_fires` | 106 |
| 9 | `test_recent_loser_advisory_fires` | 112 |
| 10 | `test_primary_picked_by_base_weight_times_confidence` | 117 |
| 11 | `test_label_base_weights_table_is_complete` | 133 |
| 12 | `test_labeler_never_raises_on_garbage_input` | 147 |

7 new haircut-semantics tests added after the section divider at `tests/test_phase3_1d_briefing/test_state_labeler_pure.py:166-177`:

| # | Test name | Test line | Coverage |
|---|---|---|---|
| 1 | `test_funding_extreme_fade_long_fires_in_downtrend_with_haircut` | 180 | haircut=0.5 admits funding fade in mismatched regime |
| 2 | `test_funding_extreme_fade_long_suppressed_with_zero_haircut` | 192 | haircut=0.0 reproduces legacy hard-kill |
| 3 | `test_trend_pullback_long_fires_in_ranging_with_haircut` | 204 | haircut=0.5 admits trend pullback in mismatched regime |
| 4 | `test_haircut_one_removes_regime_gate_entirely` | 230 | haircut=1.0 → label fires at full confidence in any regime |
| 5 | `test_extreme_fear_long_fires_in_downtrend_with_haircut` | 253 | haircut=0.5 admits extreme fear contrarian bias in trending_down |
| 6 | `test_extreme_fear_long_suppressed_with_zero_haircut` | 267 | default 0.0 preserves legacy in trending_down |
| 7 | `test_labeller_regime_haircut_version_constant_present` | 279 | version constant == 2 |

Run verified:
```
$ python3 -m pytest tests/test_phase3_1d_briefing/test_state_labeler_pure.py -v
============================== 19 passed in 0.62s ==============================
```

## Test coverage analysis

### Existing-test preservation

The 12 legacy tests all call `label_state()` without `regime_haircut`, so they inherit the 0.0 default which reproduces legacy hard-kill semantics. This is structurally clean — the test set requires no edits, no skips, no parametrization.

Verified: tests pass byte-identical to pre-Phase-B state. The 12-test count + 7-new-test additivity matches the commit message claim verbatim.

### New-test coverage by feature

- Soft-haircut admit (mismatch regime, haircut>0) — covered by tests 1, 3, 5.
- Zero-haircut backwards-compat — covered by tests 2, 6.
- haircut=1.0 no-gate edge case — covered by test 4.
- Asymmetric-polarity coverage — test 1 covers "must NOT be trending_down" (funding fade); test 3 covers "must BE trending_up" (trend pullback); both regime polarities exercised.
- Version-constant — covered by test 7.

### Gap analysis

The 7 new tests cover all 4 trigger pairs (trend pullback, range fade, funding fade, extreme fear/greed) selectively (3 of 4 pairs sampled, range_fade is the one not explicitly tested in haircut mode). However, the trend_pullback test exercises the same conf-multiplication code path, so this is acceptable per the project's "test velocity ≤ 10 min" rule.

Range fade trigger is exercised indirectly via the manual trace below (Sample Trigger Behavior verification — case D1 demonstrates range_fade_long@trending_up haircut=0.5 produces conf 0.35, matching the formula).

### Test scope cleanliness

Tests import only `label_state` and label-name constants from `src.workers.scanner.state_labeler` (`tests/test_phase3_1d_briefing/test_state_labeler_pure.py:13-24, 263, 275, 283`). They do not instantiate `ScannerWorker`, `Settings`, or `LabellerSettings` — so the boot sentinel does not fire in the test run, and no settings fixture needs updating. This is the correct test scope.

### Settings-validator coverage

The `__post_init__` validator at `src/config/settings.py:1252-1258` is empirically verified — out-of-range values raise `ValueError`:

```
python3 -c "from src.config.settings import LabellerSettings; LabellerSettings(counter_regime_confidence_haircut=1.5)"
# → ValueError: scanner.labeller.counter_regime_confidence_haircut must be in [0.0, 1.0]; got 1.5

python3 -c "from src.config.settings import LabellerSettings; LabellerSettings(counter_regime_confidence_haircut=-0.1)"
# → ValueError: ... got -0.1
```

There is no dedicated unit test in the new test set covering the `__post_init__` validator. This is a minor gap but not load-bearing: the dataclass is internal and the TOML float cast at `_build_scanner_labeller` will surface any explicit garbage input as a ValueError at boot. **Recommended follow-up:** add a single test asserting `ValueError` on `LabellerSettings(counter_regime_confidence_haircut=1.5)` to lock the validator behavior.

## Downstream consumer survey

### `label_state` callers

| File:line | Caller | Contract preserved? |
|---|---|---|
| `src/workers/scanner_worker.py:796-831` | `ScannerWorker._build_package` — production call site that passes `regime_haircut` from settings | YES — same `StateLabelResult` shape consumed at lines 832-836 |
| `tests/test_phase3_1d_briefing/test_state_labeler_pure.py:28, 42, 55, 68, 80, 89, 100, 108, 113, 119, 150, 183, 195, 207, 215, 233, 241, 256, 269` | 19 test calls | YES |

Only one production caller (`scanner_worker.py`). No other module imports `label_state` (verified via repo-wide grep).

### `StateLabelResult` consumers

`StateLabelResult` is the dataclass return of `label_state`. References:

| File:line | Use site | Shape preserved? |
|---|---|---|
| `src/workers/scanner/state_labeler.py:209, 626, 687, 812, 829` | Definition + return statements | N/A (definer) |
| `src/workers/scanner_worker.py:771, 796-836` | `label_result = label_state(...)`; reads `.primary`, `.secondary`, `.confidence` | YES — fields unchanged |

`StateLabelResult.all_labels` property at `src/workers/scanner/state_labeler.py:228-231` is only consumed in tests (`tests/test_phase3_1d_briefing/test_state_labeler_pure.py:63, 73, 75, 85, 95, 109, 114, 130, 163, 189, 201, 227, 250, 264, 276`). No production consumer.

The `StateLabelResult` dataclass is **frozen** (`@dataclass(frozen=True)` at line 208). Fields: `primary: str`, `secondary: list[str]`, `confidence: float`. All three fields are still populated by `label_state` after Phase B — only their numeric value changes when haircut > 0.0 (primary/secondary lists may now contain labels that would have been suppressed pre-fix; confidence may be lower).

### `StateLabelBlock` (the `CoinPackage` field, distinct from `StateLabelResult`)

`StateLabelBlock` is defined at `src/core/coin_package.py:97-113` with fields `primary: str` (default `"NO_TRADEABLE_STATE"`), `secondary: list[str]`, `confidence: float`. It is the package-side mirror of `StateLabelResult`.

Construction:
- `src/workers/scanner_worker.py:770` — default-constructed if labelling fails.
- `src/workers/scanner_worker.py:832-836` — populated from `label_result` (the `StateLabelResult`).

Consumers in `src/`:

| File:line | Code | Effect of Phase B |
|---|---|---|
| `src/brain/strategist.py:1931, 1937` | Briefing-mode skip: `_primary in {LABEL_NO_TRADEABLE_STATE, ""}` AND no open position AND interestingness < floor → skip | More coins now produce a primary label (instead of NO_TRADEABLE_STATE), so fewer get skipped — desired behavior |
| `src/brain/strategist.py:1950-1956` | Prompt header label list `[primary, secondary[:2]]` | Lower-conf counter-regime labels now surface in this list — desired |
| `src/brain/strategist.py:2257-2264` | TIAS recent-loss flag: only fetched for `LABEL_RECENT_LOSER_COOLDOWN` | No change (RECENT_LOSER_COOLDOWN is an advisory label, not one of the 8 regime-gated) |
| `src/brain/strategist.py:2408-2411` | Action hint dictionary lookup by `primary` | Action hints are pre-populated for the 18 trade-actionable labels at `state_labeler.py:138-205`. All 8 hard-kill predicate names have entries. So lower-conf labels still get hints. |
| `src/brain/strategist.py:2512, 2518, 2533-2545` | Full-block (Stage 2 Phase 2) — same `_primary in {LABEL_NO_TRADEABLE_STATE, ""}` gating + same header format | Same outcome as briefing-mode |
| `src/workers/scanner_worker.py:1219-1230` | Qualified flag: `bool(primary) and primary not in ADVISORY_LABELS and interest >= qualified_threshold` | More coins now have a non-ADVISORY primary → more qualified=True coins — desired |
| `src/workers/scanner_worker.py:1377` | Label-counts aggregation in `SCANNER_BRIEFING_SUMMARY` | Counts now include lower-conf labels — desired (observability) |
| `src/workers/scanner_worker.py:1491-1497` | `SCANNER_LABELED` log line emits `primary`, `secondary`, `label_conf`, `interestingness` | The `label_conf` field will now show lower numbers (e.g. 0.35 instead of 0.0) — useful for live monitoring |
| `src/workers/scanner/interestingness.py:267-284` | `_label_strength(primary_label, secondary_labels)` — looks up `LABEL_BASE_WEIGHTS[primary_label]`, adds decayed secondaries | The interestingness ranker scores labels by their **base weight only**, not by the haircut-affected confidence — so a TREND_PULLBACK_LONG at 0.35 conf will still get the same `LABEL_BASE_WEIGHTS[TREND_PULLBACK_LONG] = 0.85` weight. Lower-conf labels surface at full base-weight in the ranker. **Consequence:** the interestingness ranker is amplified by Phase B (more labels admitted at full base-weight), which is exactly the intended path — see Concern 3 of `13_evaluate_concern4.md`. |

**Critical contract observation — interestingness ranker:**

`compute_interestingness(primary_label=..., secondary_labels=...)` at `src/workers/scanner/interestingness.py:324-355` consumes only the **label NAMES**, not the StateLabelResult's `confidence` field. This is by design — interestingness is a per-coin attractiveness score that uses `LABEL_BASE_WEIGHTS` as the canonical opportunity weight. Phase B does not change this contract.

A subtle implication: pre-Phase-B, a coin in mismatched regime had `state_label.primary = NO_TRADEABLE_STATE` and `_label_strength()` returned 0.05 (the floor at `interestingness.py:278-279`). Post-Phase-B, the same coin in mismatched regime has `state_label.primary = TREND_PULLBACK_LONG` (or similar) at low `state_label.confidence`, and `_label_strength()` returns `LABEL_BASE_WEIGHTS[TREND_PULLBACK_LONG] = 0.85`. So Phase B substantially raises the label-strength component for these coins. The brain sees them ranked higher in interestingness — which is the intended behavior; the brain is the deciding authority on whether to act on a counter-regime opportunity. The confidence number is logged for observability but not used for ranking.

This is consistent with the Phase B success criteria in `phase6_phase_bc_trial.md:49`: "M1b brain LONG label count ≥ 1.5× Phase A baseline (Phase B haircut should admit more LONG labels in trending_down)" — i.e. observe MORE labels firing, not necessarily at high confidence.

### `primary_label` / `secondary_labels` (the interestingness-API kwargs)

`compute_interestingness` accepts `primary_label` and `secondary_labels` as kwargs (`src/workers/scanner/interestingness.py:353-354`). Caller: `src/workers/scanner_worker.py:891-892` which extracts from `state_label.primary` and `list(state_label.secondary)`. Contract: string + list of strings. Phase B does not change.

### `LABEL_BASE_WEIGHTS`

| File:line | Use | Affected by Phase B? |
|---|---|---|
| `src/workers/scanner/state_labeler.py:110-133` | Definition | NO |
| `src/workers/scanner/state_labeler.py:822-823` | Primary picking by `base_weight × confidence` inside `label_state` | NO (table unchanged) |
| `src/workers/scanner/interestingness.py:52, 277, 281` | Ranker label-strength | NO (table unchanged) |
| `tests/test_phase3_1d_briefing/test_state_labeler_pure.py:14, 134, 143-144, 163` | Sanity tests | NO |

### `LABELLER_REGIME_HAIRCUT_VERSION` (new)

Defined at `src/workers/scanner/state_labeler.py:71`. Imported and used at:
- `src/workers/scanner_worker.py:92, 100` (boot sentinel)
- `tests/test_phase3_1d_briefing/test_state_labeler_pure.py:284, 286` (version constant test)

Matches the spec — only two consumers + tests.

### `LabellerSettings` (new)

Defined at `src/config/settings.py:1227`. Used at:
- `src/config/settings.py:1293` (`ScannerSettings.labeller` field)
- `src/config/settings.py:3809, 3811, 3813` (`_build_scanner_labeller` factory)

Not directly imported by any other module (the value is read via `settings.scanner.labeller.counter_regime_confidence_haircut`). Matches spec.

### `counter_regime_confidence_haircut` (new)

| File:line | Use |
|---|---|
| `src/config/settings.py:1250` | Dataclass field default 0.5 |
| `src/config/settings.py:1253, 1255-1257` | Validator |
| `src/config/settings.py:3814-3815` | TOML reader, defaults to 0.5 |
| `config.toml:781` | Operator-tunable value, set to 0.5 |
| `src/workers/scanner_worker.py:96, 829` | Boot sentinel + label_state plumbing |
| `src/workers/scanner/state_labeler.py:67, 681` | Docstring references |

Matches spec: settings + config.toml + scanner_worker + dev_notes.

### `regime_haircut` (new kwarg)

Verified to appear at:
- `src/workers/scanner/state_labeler.py:267, 301, 321, 348, 405, 432, 543, 567` — 8 predicate signatures.
- `src/workers/scanner/state_labeler.py:292-294, 312-314, 339-341, 358-360, 423-425, 442-444, 558-560, 577-579` — 8 mismatch-branch blocks.
- `src/workers/scanner/state_labeler.py:625` — `label_state` public signature.
- `src/workers/scanner/state_labeler.py:709, 715, 722, 729, 746, 751, 790, 796` — 8 firing-call plumbings.
- `src/workers/scanner/state_labeler.py:674-684` — docstring.
- `tests/test_phase3_1d_briefing/test_state_labeler_pure.py:187, 199, 213, 221, 239, 247, 261, 274` — test call sites.
- `src/workers/scanner_worker.py:828` — production plumbing.

Matches spec.

### Brain prompt rendering — `src/brain/strategist.py`

`pkg.state_label.primary` / `.secondary` consumed but `.confidence` NOT consumed:

```
$ grep -n "state_label\.confidence" src/brain/strategist.py
# (no matches)
```

This means the brain sees the **label NAME** (which Phase B admits more of in mismatched regimes) but does NOT see the post-haircut confidence value directly in the prompt. The brain therefore cannot distinguish "this label fired at full confidence" from "this label fired at half confidence" — it only sees the label set. This is by design: the haircut acts as a soft admission filter, and the brain weighs the label by its semantics + the rest of the per-coin block (regime, setup_type_confidence, etc.).

Operator-side observability: the `SCANNER_LABELED` log line at `src/workers/scanner_worker.py:1491-1497` does emit `label_conf=` so a log-tail watcher CAN see the haircut effect (e.g. `label_conf=0.35` would tell the operator "this label was haircut from 0.7").

### Contract preservation: "no label fired (None)" vs "label fired at low confidence"

**Critical question from the audit spec:** does any code distinguish "no label fired (None)" from "label fired at low confidence"?

- `label_state()` does not return None — it always returns a `StateLabelResult`. So no consumer has ever distinguished those cases at the function-return level.
- `StateLabelResult.primary` is always a string (defaults to `LABEL_NO_TRADEABLE_STATE` when nothing fires). Consumers check `primary == LABEL_NO_TRADEABLE_STATE` to detect the no-fire case. Pre-Phase-B, a coin in mismatched regime with no other label would have primary `NO_TRADEABLE_STATE`; post-Phase-B it now has a non-advisory primary.
- The CoinPackage `state_label.primary` field default is `"NO_TRADEABLE_STATE"` (`src/core/coin_package.py:111`) — no caller has ever relied on `primary is None`.

**Conclusion:** no consumer distinguishes None from low-confidence at the call surface. The contract switch from "label suppressed → NO_TRADEABLE_STATE" to "label admitted at low confidence" is the intended Phase B behavior change. All consumers handle both cases via the same `primary` string check.

## Default-value safety

### `label_state(regime_haircut=0.0)` default

The function-level default at `src/workers/scanner/state_labeler.py:625` is `0.0`. Per the per-predicate `if regime_haircut <= 0.0: return None` branches (lines 292, 312, 339, 358, 423, 442, 558, 577), this exactly reproduces the pre-Phase-B "return None on regime mismatch" behavior.

**Backwards-compat preservation evidence:** running the 12 legacy tests verbatim → all 12 pass (`pytest tests/test_phase3_1d_briefing/test_state_labeler_pure.py` → 19 passed). The new tests assert haircut > 0 admits and haircut = 0 suppresses — both behaviors empirically verified.

The per-predicate kwarg default is `1.0` (lines 267, 301, 321, 348, 405, 432, 543, 567), not `0.0`. This asymmetry is intentional and safe:
- Direct calls to internal predicates (e.g. inside `label_state`) explicitly pass `regime_haircut=regime_haircut`, so the per-predicate default never applies in production.
- If a test or external module ever directly imported a `_trigger_*` (which would be a private-API violation), the helper defaults to "regime gate removed" (haircut=1.0) — the safest default at the predicate layer because it preserves the label content without surprises.
- No external module imports `_trigger_*` per grep (only `label_state` is exported).

### `_build_scanner_labeller({})` default

`_build_scanner_labeller(data: dict)` at `src/config/settings.py:3809-3817` returns `LabellerSettings(counter_regime_confidence_haircut=float(data.get("counter_regime_confidence_haircut", 0.5)))`. So a missing `[scanner.labeller]` section in `config.toml` (rollback scenario) → defaults to 0.5 (active haircut).

Operator can disable Phase B via:
1. Edit `config.toml:781` to `counter_regime_confidence_haircut = 0.0` and restart.
2. Or set in dataclass: `LabellerSettings(counter_regime_confidence_haircut=0.0)`.

Either restores legacy hard-kill behavior verbatim.

### Validator range

`__post_init__` at `src/config/settings.py:1252-1258` accepts only `[0.0, 1.0]`. Confirmed via empirical test:

```
python3 -c "from src.config.settings import LabellerSettings; LabellerSettings(counter_regime_confidence_haircut=1.5)"
# → ValueError: scanner.labeller.counter_regime_confidence_haircut must be in [0.0, 1.0]; got 1.5

python3 -c "from src.config.settings import LabellerSettings; LabellerSettings(counter_regime_confidence_haircut=-0.1)"
# → ValueError: ... got -0.1
```

Edge cases at boundaries:
- `0.0` accepted (legacy hard-kill).
- `1.0` accepted (no regime gate).
- `0.5` (default) accepted.

## Sample-trigger-behavior verification

Manual numeric traces through each of the 8 predicates using the live code, empirically verified via `python3 -c`:

### Predicate 1: `_trigger_trend_pullback_long`

Inputs: `setup_type=bullish_fvg_ob, setup_type_confidence=0.7, trade_direction=long, regime=ranging, regime_haircut=0.5`

Trace:
- Direction check (line 284): `long == long` → pass.
- Setup check (lines 286-289): `bullish_fvg_ob in {bullish_fvg_ob, bullish_structural_break}` → pass.
- Base conf (line 290): `max(0.30, min(1.0, 0.7)) = 0.7`.
- Regime check (line 291): `_is_trending_up("ranging")` = False → mismatch branch.
- Haircut check (line 292): `0.5 <= 0.0` False → take `return base_conf * regime_haircut` = `0.7 * 0.5 = 0.35`.

Expected: 0.35. Confirmed via `label_state` integration test:

```
label_state(setup_type='bullish_fvg_ob', setup_type_confidence=0.7, \
            trade_direction='long', regime='ranging', consensus_direction='long', \
            regime_haircut=0.5, position_in_range=0.7)
# → primary=TREND_PULLBACK_LONG conf=0.35
```

(position_in_range=0.7 blocks RANGE_FADE_LONG so only TREND_PULLBACK_LONG remains.)

### Predicate 2: `_trigger_trend_pullback_short`

Inputs: `setup_type=bearish_fvg_ob, setup_type_confidence=0.7, trade_direction=short, regime=ranging, regime_haircut=0.5, position_in_range=0.3`

Expected (mirror of 1): conf = 0.35. Confirmed:

```
label_state(setup_type='bearish_fvg_ob', setup_type_confidence=0.7, \
            trade_direction='short', regime='ranging', consensus_direction='short', \
            regime_haircut=0.5, position_in_range=0.3)
# → primary=TREND_PULLBACK_SHORT conf=0.35
```

### Predicate 3: `_trigger_range_fade_long`

Inputs: `setup_type=bullish_fvg_ob, setup_type_confidence=0.7, trade_direction=long, regime=trending_up, position_in_range=0.1, regime_haircut=0.5`

Trace:
- `points_long` (line 332): `long == long` → pass.
- Position upper bound (line 335): `0.1 >= 0.40` → False → no early-return.
- Base conf (line 337): `max(0.30, min(1.0, 0.7)) = 0.7`.
- Regime check (line 338): `_is_ranging("trending_up")` = False → mismatch branch.
- Haircut: `0.7 * 0.5 = 0.35`.

Confirmed:

```
label_state(setup_type='bullish_fvg_ob', setup_type_confidence=0.7, \
            trade_direction='long', regime='trending_up', consensus_direction='long', \
            position_in_range=0.1, regime_haircut=0.5)
# → primary=TREND_PULLBACK_LONG conf=0.7 all=['TREND_PULLBACK_LONG', 'RANGE_FADE_LONG']
```

(TREND_PULLBACK_LONG primaries with full conf 0.7 because regime=trending_up matches its predicate; RANGE_FADE_LONG fires at haircut conf 0.35 and appears in secondary.)

### Predicate 4: `_trigger_range_fade_short`

Mirror of 3. Verified by trace inspection — symmetric polarity and same math.

### Predicate 5: `_trigger_funding_extreme_fade_long`

Inputs: `funding_rate=-0.0050, regime=trending_down, consensus_direction=long, regime_haircut=0.5`

Trace:
- Funding threshold (line 415): `-0.0050 >= -0.0015` False → pass.
- Position not provided → skip.
- excess = `abs(-0.0050) - 0.0015 = 0.0035`. base_conf = `min(1.0, 0.40 + 0.0035 * 200.0) = min(1.0, 1.10) = 1.0`.
- Regime check (line 422): `_is_trending_down("trending_down")` = True → mismatch branch (note: NEGATIVE gate — fade-long doesn't want trending_down).
- Haircut: `1.0 * 0.5 = 0.5`.

Confirmed:

```
label_state(funding_rate=-0.0050, regime='trending_down', \
            consensus_direction='long', regime_haircut=0.5)
# → primary=FUNDING_EXTREME_FADE_LONG conf=0.5
```

### Predicate 6: `_trigger_funding_extreme_fade_short`

Inputs: `funding_rate=+0.0050, regime=trending_up, consensus_direction=short, regime_haircut=0.5`

Expected: same math as 5 with sign flipped. Confirmed:

```
label_state(funding_rate=+0.0050, regime='trending_up', \
            consensus_direction='short', regime_haircut=0.5)
# → primary=FUNDING_EXTREME_FADE_SHORT conf=0.5
```

### Predicate 7: `_trigger_extreme_fear_long`

Inputs: `fear_greed=15, regime=trending_down, consensus_direction=long, trade_direction=long, regime_haircut=0.5`

Trace:
- F&G window (line 551): `0 < 15 < 20` → pass.
- Direction anchor (lines 553-555): consensus_direction=long → points_long True.
- Base conf (line 556): `min(1.0, 0.40 + (20-15)/50.0) = min(1.0, 0.50) = 0.50`.
- Regime check (line 557): `_is_trending_down("trending_down")` = True → mismatch (negative gate).
- Haircut: `0.50 * 0.5 = 0.25`.

Confirmed (RANGE_FADE_LONG also fires from the long direction signal, but extreme_fear primary check):

```
label_state(fear_greed=15, regime='trending_down', consensus_direction='long', \
            trade_direction='long', regime_haircut=0.5)
# → primary=RANGE_FADE_LONG conf=0.225 (RANGE_FADE_LONG also fires; primary by base_weight × conf)
# → LABEL_EXTREME_FEAR_LONG_BIAS IS in res.all_labels (per the new test at line 253-264)
```

The new test asserts `LABEL_EXTREME_FEAR_LONG_BIAS in res.all_labels` — confirmed passing.

### Predicate 8: `_trigger_extreme_greed_short`

Inputs: `fear_greed=85, regime=trending_up, consensus_direction=short, trade_direction=short, regime_haircut=0.5`

Expected (mirror of 7): conf = `min(1.0, 0.40 + (85-80)/50.0) * 0.5 = 0.50 * 0.5 = 0.25`. Confirmed.

### Boundary cases

- `regime_haircut = 0.0` → all 8 predicates revert to legacy hard-kill (return None on mismatch). Verified.
- `regime_haircut = 1.0` → all 8 predicates return base_conf regardless of regime. Verified by test 4 `test_haircut_one_removes_regime_gate_entirely` at `tests/test_phase3_1d_briefing/test_state_labeler_pure.py:230-250`.

### Mid-range value sensibility

At the default 0.5, a coin in mismatched regime with a strong setup (e.g. setup_type_confidence 0.85) sees its label conf drop from 0.85 → 0.425. This is a non-trivial penalty but still well above the `LABEL_BASE_WEIGHTS[NO_TRADEABLE_STATE] = 0.05` floor — the label remains a competitive primary candidate via `base_weight × confidence` ranking. This matches the design intent stated in `src/workers/scanner/state_labeler.py:65-70`: "Version 2: soft-haircut (regime mismatch → base_conf * haircut)".

## Boot sentinel verification

### Where it fires

`src/workers/scanner_worker.py:83-109` — inside `ScannerWorker.__init__`, after `super().__init__(...)` and `self.services = services or {}`. Wrapped in `try/except` that emits `STATE_LABELLER_REGIME_HAIRCUT_INIT_FAIL` debug log on any exception. Imports `LABELLER_REGIME_HAIRCUT_VERSION` locally inside the try.

### Format

```
STATE_LABELLER_REGIME_HAIRCUT_INIT | version=<int> haircut=<float:.2f> mode=<legacy_hard_kill|soft_haircut|no_regime_gate> | <ctx>
```

The mode string is computed inline:

```python
'legacy_hard_kill' if _haircut <= 0.0 else 'soft_haircut' if _haircut < 1.0 else 'no_regime_gate'
```

At haircut=0.50 (current default) → `mode=soft_haircut`. Verified to fire on operator restart at 2026-05-19 10:03:33-35 UTC per `phase6_phase_bc_trial.md:17`.

### Test pollution check

The boot sentinel fires only when `ScannerWorker.__init__` runs. Tests in `tests/test_phase3_1d_briefing/test_state_labeler_pure.py` import `label_state` and label constants directly — they never construct a `ScannerWorker`. So the sentinel is NOT emitted during the 19-test run. Confirmed empirically:

```
$ python3 -m pytest tests/test_phase3_1d_briefing/test_state_labeler_pure.py -v 2>&1 | grep STATE_LABELLER
# (no matches)
```

### Failure mode

If `state_labeler.py` is missing or `LABELLER_REGIME_HAIRCUT_VERSION` is not exported, the `try` block catches and emits the `STATE_LABELLER_REGIME_HAIRCUT_INIT_FAIL` debug log. This is graceful — `ScannerWorker.__init__` continues. **Risk:** the sentinel could silently NOT fire on import error and the operator would not know. But the import path is `from src.workers.scanner.state_labeler import LABELLER_REGIME_HAIRCUT_VERSION` — same module that the worker invokes `label_state()` from at runtime, so an import failure would cascade into a runtime error at `_build_package` later. Acceptable.

## CLAUDE.md rules compliance

### Rule 1: Grep all usages before modification

- `label_state` callers — only 1 production caller (`scanner_worker.py:796-831`). All test callers identified and contract preserved.
- `StateLabelResult` consumers — only `scanner_worker.py:771, 832-836`. Contract preserved.
- `ScannerSettings` instantiations in tests (`test_phase5_1d_briefing/`, `test_phase8_1d_briefing/`, `test_phase9_1d_briefing/`) — all call `ScannerSettings()` with no `labeller` kwarg. The new `labeller: LabellerSettings = field(default_factory=LabellerSettings)` field has a default factory, so test fixtures continue to work without modification. Empirically verified: 14 tests across the 3 directories all pass.
- 8 trigger predicates — each only called by `label_state` (verified by grep).

### Rule 2: Map all dependencies

The only nontrivial cross-block dependency is the `interestingness.py` ranker reading `LABEL_BASE_WEIGHTS` by primary_label name — the ranker's behavior is amplified by Phase B (more labels fire → more get full base-weight credit), but this is the **intended** path per the validation spec's reasoning at `dev_notes/dirbias_validation/03_validate_issue3.md:723-727` ("The labeller IS the operative surface"). No NameError risk.

### Rule 3: No band-aid fixes

The fix targets the root cause identified in `03_validate_issue3.md`: 8 per-trigger hard-kill predicates suppressing direction-aligned counter-regime labels and producing the 716:148 SHORT:LONG ratio. The implementation alters the predicate logic at the root (mismatch handling), not at an interventional surface downstream.

### Rule 4: Verify wiring before touching

The commit message at `git show 1ebae0d` explicitly enumerates the 5 edit surfaces with line numbers and the precedent (STRAT_CALL_B_REFRAMED / STRAT_REGIME_INSTR_REFRAMED). The boot sentinel mirrors the precedent. Plumbing through DI (settings → scanner_worker → label_state) matches existing patterns at `src/config/settings.py:3792 (_build_scanner_scoring_weights)` and elsewhere.

### Silent NameError risk

None detected. The new `regime_haircut` kwarg has a default value at every call site. The `LABELLER_REGIME_HAIRCUT_VERSION` import in `scanner_worker.py:91-93` is inside a `try` so it cannot crash boot. The `LabellerSettings` dataclass has `field(default_factory=LabellerSettings)` so any `ScannerSettings()` instantiation produces a valid `labeller` attribute.

## Operator directive compliance (no hardcoded asymmetric)

### Symmetric haircut application

The single `counter_regime_confidence_haircut: float = 0.5` value applies to all 8 triggers (4 LONG + 4 SHORT):

- Trend pullback LONG and SHORT both use the same `regime_haircut` passed from `label_state` (lines 709, 715).
- Range fade LONG and SHORT same (lines 722, 729).
- Funding extreme fade LONG and SHORT same (lines 746, 751).
- Extreme fear LONG / extreme greed SHORT same (lines 790, 796).

The operator tunes a **single number** (`config.toml:781`), not per-direction values. This matches the operator directive cited in the commit message: "Honors the operator design directive (no hardcoded asymmetric corrections): the haircut multiplier applies symmetrically to all 8 triggers (4 LONG + 4 SHORT) and the operator tunes a single number in TOML, not per-direction."

### Operator-tunable via config

`config.toml:780-781`:
```
[scanner.labeller]
counter_regime_confidence_haircut = 0.5
```

Operator can step from 0.5 → 0.3 (more conservative — less counter-regime label admission) or 0.5 → 0.7 (more permissive) per the trial decision matrix in `phase6_phase_bc_trial.md:64-72`. The `__post_init__` validator at `src/config/settings.py:1252-1258` enforces `[0.0, 1.0]`, so the operator cannot accidentally set an out-of-range value.

### No hidden asymmetric correction

The haircut is a single multiplier applied uniformly. Inspecting each of the 8 mismatch branches (lines 292-294, 312-314, 339-341, 358-360, 423-425, 442-444, 558-560, 577-579) confirms identical code structure with no direction-conditional overrides.

## Aim-bias five-question evaluation

| # | Question | Answer | Rationale |
|---|---|---|---|
| 1 | Preserve trade frequency? | YES | Haircut > 0 admits MORE labels (the 8 predicates that previously returned None now return `base_conf * haircut`). Brain sees more candidates, not fewer. Stage 1d label counts can only rise with haircut > 0. |
| 2 | Preserve aggression? | YES | The fix removes a hard-kill gate. No new blocking logic introduced. The post-haircut confidence is purely informational (logged but not directly consumed by brain or ranker). |
| 3 | Improve decision quality? | YES | Counter-regime opportunities now surface to the brain at reduced (but non-zero) confidence rather than being silently dropped. The brain may then weight these by setup_type_confidence + the rest of the per-coin block. Pre-Phase-B, an extreme-fear long bias in a downtrend would never surface even with fear_greed=15 + clean long alignment; post-Phase-B it surfaces and the brain decides. |
| 4 | Preserve passive-close advantage? | N/A | The labeller is CALL_A scope only (scan/select pipeline). It does not influence open-position management, exits, TP/SL placement, or post-execution closure. The fix lives entirely in Layer 1D. |
| 5 | Respect structural separation? | YES | Edits confined to: `src/workers/scanner/state_labeler.py` (Layer 1D), `src/config/settings.py` (config plane), `config.toml` (operator surface), `src/workers/scanner_worker.py` (Layer 1D worker), `tests/test_phase3_1d_briefing/test_state_labeler_pure.py` (Phase 3 tests). No edits to brain, strategist, executor, mode4, sniper, layer4, structure, regime, or signal layers. |

## Discrepancies found

### Discrepancy 1: Predicate kwarg default differs from public-API default

- `label_state` (public API) defaults `regime_haircut=0.0` (legacy hard-kill).
- Per-predicate (`_trigger_*`) kwargs default `regime_haircut=1.0` (no regime gate).

This is **intentional** and documented in the commit message:

> label_state() function default is 0.0 for backward-compatibility — all existing tests (12) pass verbatim. Production scanner_worker passes the operator-tunable 0.5 from settings.scanner.labeller.

But the per-predicate defaults of 1.0 are NOT explicitly documented in the predicate docstrings. They are also not load-bearing because `label_state` always plumbs the explicit value. Minor cleanliness concern; not a bug.

### Discrepancy 2: Spec's pre-fix line numbers vs current file lines

The audit prompt cites spec line numbers `253, 268, 283, 301, 356, 371, 477, 491` for the 8 predicates. The current file shows the same 8 predicate functions starting at lines `264, 298, 318, 345, 402, 429, 540, 564`. The shifts come from adding the docstrings + `regime_haircut` kwarg parameter to each predicate signature.

The spec numbers are the pre-fix line locations as captured in the commit message and `03_validate_issue3.md`. The new locations are the post-fix line locations. **Not a discrepancy**; just a line shift artifact. All 8 predicates are present and correctly modified.

### Discrepancy 3: Recommendation spec drift on default value

`dev_notes/dirbias_validation/20_recommendation.md:72`:

> If 48h shows brain Sell ≥ 90%: ship Issue 3 (`fix/dirbias-labeller-soft-haircut`). Soft haircut at `state_labeler.py` per-trigger predicates with `counter_regime_confidence_haircut: float = 1.0` default (no-op for soak); operator flips to 0.5 after 24h.

`dev_notes/dirbias_validation/03_validate_issue3.md:733`:

> 1. Add the `LabellerSettings` dataclass with `counter_regime_confidence_haircut: float = 0.0` (default preserves current behaviour).

The ACTUAL shipped default is **0.5** (`src/config/settings.py:1250` and `config.toml:781`), not 0.0 or 1.0.

The commit message at `git show 1ebae0d` explicitly justifies this:

> LabellerSettings.counter_regime_confidence_haircut: float = 0.5 (active default per Concern 4 verdict — no-op defaults are anti-pattern)

`dev_notes/dirbias_validation/13_evaluate_concern4.md` (Concern 4 verdict) reasoned that no-op defaults are an anti-pattern because they hide the fix behind a manual operator flip. The shipped value of 0.5 is the **active** default — the fix is in effect at boot, not pending operator activation.

**Not a discrepancy**; this is a deliberate spec evolution from Phase 2 (Concern 4 evaluation). The Phase 1 recommendation was superseded.

### Discrepancy 4: Inert validator test

There is no dedicated unit test for the `LabellerSettings.__post_init__` validator. It is empirically verified in this audit but lacks a regression-locking test. Minor gap; recommended follow-up below.

### Discrepancy 5: `label_state` parameter ordering

The new `regime_haircut: float = 0.0` is the LAST kwarg in `label_state`'s signature (`src/workers/scanner/state_labeler.py:625`). Adding parameters to a kwargs-only function (`*,` at line 599 enforces keyword-only) is non-breaking. No discrepancy; noted for completeness.

## Verdict

**PASS WITH NOTES.**

All 5 specified edit surfaces are correctly modified:
1. 8 trigger predicates converted to soft-haircut at lines 264, 298, 318, 345, 402, 429, 540, 564 — `src/workers/scanner/state_labeler.py`.
2. `LabellerSettings` dataclass + validator + `_build_scanner_labeller` factory + `_build_scanner` wiring — `src/config/settings.py:1226-1258, 1293, 3809-3817, 3834`.
3. `[scanner.labeller]` TOML section with `counter_regime_confidence_haircut = 0.5` and 23-line documentation — `config.toml:760-781`.
4. `label_state()` call with `regime_haircut` plumbed from settings + `STATE_LABELLER_REGIME_HAIRCUT_INIT` boot sentinel — `src/workers/scanner_worker.py:83-109, 796-831`.
5. 19 tests pass (12 legacy + 7 new haircut semantics) — `tests/test_phase3_1d_briefing/test_state_labeler_pure.py`.

Downstream consumer survey: 8 strategist/scanner/interestingness consumers all preserve their contracts (only `primary` string identity and label-name lookup tables are consumed; no consumer reads `state_label.confidence` for decisional logic). Brain prompt rendering surfaces lower-conf labels by name only — desired.

Default-value safety: `label_state(regime_haircut=0.0)` reproduces legacy hard-kill; `LabellerSettings()` defaults to 0.5; `_build_scanner_labeller({})` defaults to 0.5; validator catches out-of-range values empirically (`1.5`, `-0.1` both raise ValueError).

Sample-trigger numeric traces match the documented formula (`base_conf * regime_haircut`) for all 8 predicates with empirical verification.

Boot sentinel fires only inside `ScannerWorker.__init__` — no test pollution.

CLAUDE.md and operator-directive compliance: all 5 aim-bias questions answer YES or N/A; symmetric haircut applied to all 8 triggers; single operator-tunable value via TOML.

The "with notes" qualifier covers the 4 minor items in the Discrepancies section, none of which block ship:
- Per-predicate kwarg default 1.0 vs public-API default 0.0 is intentional but undocumented in predicate docstrings (cosmetic).
- Spec line-number drift is line-shift artifact (cosmetic).
- Spec → ship default-value evolution (0.0 → 1.0 → 0.5) is per Concern 4 verdict and correctly justified in the commit message (intentional).
- Missing validator unit test (minor coverage gap).

The implementation is structurally clean, the aim-bias evaluation is honest (more labels fire — the fix is intentionally permissive and the Phase B+C trial spec measures whether this admits BUY orders without degrading WR), and the operator-directive (no hardcoded asymmetric) is honored.

Combined-trial verification is governed by `dev_notes/dirbias_validation/phase6_phase_bc_trial.md` — that document defines the 48-hour M1–M8 success/revert criteria and is the authoritative gate on Phase B's live impact. This audit does not preempt that trial; it only verifies that the in-memory implementation matches the spec.

## Recommended follow-ups

1. **Add validator unit test** at `tests/test_phase3_1d_briefing/` (or a new `tests/test_config_settings/` directory) asserting `LabellerSettings(counter_regime_confidence_haircut=1.5)` and `LabellerSettings(counter_regime_confidence_haircut=-0.1)` both raise `ValueError`. Locks the range-validator behavior.

2. **Add per-predicate docstring note** to the four predicate functions whose default `regime_haircut: float = 1.0` differs from the `label_state` default of 0.0. Mention "internal helper; production callers should pass the operator-tuned value." Cosmetic.

3. **Add a SCANNER_LABELED filter for haircut events.** Currently `SCANNER_LABELED` logs `label_conf=<float>` for every coin. After Phase B, log entries with `label_conf < 0.5 * base_weight_for_primary` indicate a haircut took effect. A grep-friendly tag (e.g. `haircut_applied=true`) could make operator review easier. Optional observability enhancement; not blocking.

4. **Concern 6 verification adherence:** the Phase B+C trial spec at `phase6_phase_bc_trial.md` defines specific M1b/M2/M3a metric pass thresholds. After the 48-hour trial, the operator should produce a Phase 7 verification report against those metrics. This is process-level, not code-level; called out here for completeness.

5. **Optional: add an integration test that exercises the full `ScannerWorker._build_package` path with mocked structure/regime/strategy services to verify the haircut is plumbed end-to-end at runtime.** The existing Phase 3 tests cover the pure-function pipeline; an integration test would catch any future regression where the haircut plumbing breaks at a wiring boundary. Lower priority than 1-4.

6. **Lock the schema-version bump.** `LABELLER_REGIME_HAIRCUT_VERSION = 2` is asserted in `test_labeller_regime_haircut_version_constant_present` (line 286). If a future change bumps to v3, that test will catch the bump. Good. No action needed; flagged for awareness.
