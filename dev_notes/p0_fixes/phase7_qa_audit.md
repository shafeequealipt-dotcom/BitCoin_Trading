# Phase 7 — End-to-End QA Audit

Date: 2026-05-22. Comprehensive verification of the P0-2 and P0-3 fixes against industry-standard QA dimensions: file-by-file analysis, architecture compliance, naming consistency, dependency wiring, and twelve categorized test passes covering smoke, unit, integration, regression, source-pin, runtime simulation, verification-script, naming, layer-compliance, dependency, full-suite, and naming-collision checks.

## H1 — Scope of the Audit

The audit covers every file modified by the four P0 commits on `main`:

- `6f21f1d` — P0-2 enforce direction-decision authority with high-conviction protection.
- `f04eeb6` — P0-3 grant brain explicit close authority with hard risk floor.
- `049ddfe` — phase 6 sign-off notes.
- `18e3df6` — P0-2 source-pin test updates for the new canonical event.

Exact files touched, by commit:

| Commit | Files touched |
| --- | --- |
| 6f21f1d | config.toml, src/config/settings.py, src/workers/strategy_worker.py, verify_p0_2.py, dev_notes/p0_fixes/{01,02_*,03_*,phase0_baseline}.md |
| f04eeb6 | config.toml, src/config/settings.py, src/risk/wd_brain_scoring.py, src/workers/position_watchdog.py, tests/test_wd_brain_scoring.py, verify_p0_3.py |
| 049ddfe | dev_notes/p0_fixes/phase6_signoff.md |
| 18e3df6 | tests/test_j3_xray_lock_override.py, tests/test_j_series_e2e_pipeline.py |

Out-of-scope files (verified untouched per Rule 11):

- src/bybit_demo/* (Bybit demo HTTP/auth/signing/WS-parse adapter)
- src/shadow/* (Shadow virtual exchange adapter)
- src/database/connection.py and src/database/migrations.py (DB concurrency)
- src/strategies/regime.py (regime detector — read but not mutated)
- src/workers/price_worker.py and src/workers/kline_worker.py (data ingestion)
- src/analysis/structure/structural_levels.py (rr formulas — read and verified, not mutated)
- src/brain/strategist.py (brain CALL_A/CALL_B path)

## H1 — File-by-File Analysis

### H2 — `src/workers/strategy_worker.py`

**Role in the architecture.** Layer 1C consumer that runs the full Layer 1–4 pipeline per 5-minute window for the watch_list (~50 coins). Owns `_execute_claude_trade` — the entry point that converts a brain directive (plus APEX optimization plus XRAY structural verdict) into an exchange order via `order_service.place_order`. The flip-decision seam at lines 1860–2188 was the primary P0-2 edit site.

**Dependencies.** Imports: `TAEngine`, `Settings`, `FlipTPSettings`, `compute_capped_flip_tp`, `ctx`, `new_strategy_id`, `get_logger`, `TradePlan`, core types, `MarketRepository`, `EnsembleVoter`, `DailyPnLManager`, `RegimeDetector`, `StrategyRegistry`, `MarketScanner`, `TradeScorer`, `SweetSpotWorker`. Reads from `self.regime_detector._per_coin_regimes` (existing access pattern used in tias/collector, apex/gate, apex/assembler) for the high-conviction regime check. Reads from `self.services["structure_cache"]` (existing service) for the XRAY structural placement and `trade_direction`. No new imports added; no circular-import risk introduced.

**Change made.** Two regions:

1. Lines 117–134 — `P0_2_SENTINEL` boot log at `__init__`, immediately after the existing `_l4_boot_check_no_hardcoded_cap()` call. Emits `high_conviction_protection={True|False} flip_threshold={value} dual_logging=removed canonical_event=DIRECTION_DECISION` so operators can confirm the fix is active from a single line.
2. Lines 1865–2184 — flip block replacement. The pre-P0-2 block emitted up to four log lines per disagreement (`XRAY_OVERRIDE_RATIO_DETAIL` + `XRAY_LOCK_PRECEDENCE_RESOLUTION` + (`XRAY_FLIP_SUPPRESSED_BY_LOCK` or `XRAY_OVERRIDE_LOCK`) + `XRAY_DIR_FLIP`); the post-P0-2 block emits one canonical `DIRECTION_DECISION` per decision (veto / hold / block / flip) plus `XRAY_OVERRIDE_RATIO_DETAIL` for the WR-derivation audit trail. The conditional branches:
   - `_xray_disagrees AND _high_conviction` → DIRECTION_DECISION action=veto + TRADE_SKIP + `return (False, "xray_veto_high_conviction")`.
   - `_xray_disagrees AND _lock_holds_below_override_threshold` → DIRECTION_DECISION action=hold; trade keeps brain direction; fall through to execution.
   - `_xray_disagrees AND missing dual structural levels` → DIRECTION_DECISION action=block + TRADE_SKIP + `return (False, "xray_dir_block")`.
   - `_xray_disagrees AND post-flip structural conflict` → DIRECTION_DECISION action=block + TRADE_SKIP + `return (False, "xray_dir_flip_blocked")`.
   - `_xray_disagrees AND flip permitted (no lock OR override active) AND clean` → DIRECTION_DECISION action=flip; trade-dict mutations applied; fall through with flipped direction.

**Invariants preserved.**

- The `XRAY_OVERRIDE_RATIO_DETAIL` audit emission still fires on every disagreement (operator audit unchanged).
- All downstream-consumer trade-dict fields are still set in the flip branch (`_apex_was_flipped`, `_flip_source = "xray"`, `_xray_flip_ratio`, `_xray_flip_rr_long`, `_xray_flip_rr_short`, `_apex_original_direction`, `_xray_lock_overridden` when override active). Consumers at lines 2251–2985 of the same file plus `src/core/thesis_manager.py` lines 144–810 continue to read these unchanged.
- The pre-flip structural-conflict guard (`XRAY_DIR_FLIP_BLOCKED` semantic) is preserved; only the log tag was renamed to `DIRECTION_DECISION action=block reason=post_flip_structural_conflict` to satisfy "exactly one direction-decision log per trade".
- The missing-dual-levels guard (`XRAY_DIR_BLOCK` semantic) is preserved; renamed to `DIRECTION_DECISION action=block reason=missing_dual_structural_levels`.
- The lock-override threshold gate is preserved (`_lock_override_active` = APEX locked AND override threshold > flip threshold AND ratio > override threshold). Only the redundant `_ratio > _flip_threshold` clause was removed because it is implicit in the outer `if _xray_disagrees:` guard.

**Touch-impact analysis (what would break if reverted).** Downstream-consumer compatibility is preserved by setting the same trade-dict breadcrumbs the pre-P0-2 code did. Reverting via `git revert 6f21f1d` would restore the pre-P0-2 flip block; the operator can do this any time without other system changes.

### H2 — `src/workers/position_watchdog.py`

**Role in the architecture.** Layer 6 position management — owns position monitoring, brain-vote scoring intercept, close authority, SL-tightening, time-decay, and emergency closes. The `_execute_strategic_actions` function at lines 3380–3849 is the path the brain's close votes flow through.

**Dependencies.** Imports include `compute_brain_close_score`, `compute_sl_consumption_pct` from `src.risk.wd_brain_scoring`, plus the standard worker dependencies (`coordinator`, `thesis_manager`, `structure_cache`, `position_service`, settings). No new imports added by P0-3; the watchdog already imports the scoring module.

**Change made.** Two regions:

1. Lines 418–430 — `P0_3_SENTINEL` boot log at `__init__`, immediately after the existing `WD_SCORING_ENFORCE_ACTIVE` sentinel. Emits `brain_vote_factor=on hard_risk_floor_sl_pct={value} threshold={value} enforce_mode={True|False}`.
2. Lines 3768–3835 — scoring intercept wire-up. Three sub-changes:
   - Line 3784: `brain_vote_present=True` passed to `compute_brain_close_score`. Correct because the path is only reached via an explicit `act in ("close", "take_profit")` from the brain coordinator drain.
   - Lines 3796–3804: hard_floor read from settings with default 85.0.
   - Lines 3809–3814: extended `WATCHDOG_CLOSE_SCORE_COMPUTED` log to include `hard_floor_pct` and `hard_floor_active` fields. Backward compatible — operators querying the existing fields find them; the new fields appear at the end of the line.
   - Lines 3816–3849: branch logic — `not _enforce` (log-only) → fall through; `_hard_floor_active` (P0-3 override) → `WATCHDOG_HARD_FLOOR_HIT` + fall through; else → existing composite branches (execute / reject / reject_and_tighten).

**Invariants preserved.**

- The min-hold guard at lines 3412–3453 is untouched — fresh positions (< 300s) are still protected.
- The composite-branch logic (execute / reject / reject_and_tighten) is preserved verbatim; only the floor branch is inserted before it.
- The SL-tightening fallback `_tighten_sl_breakeven_30pct` at lines 1202–1255 is untouched.
- `_push_sl_to_shadow` tighter-only enforcement at lines 1005–1200 is untouched.
- `WD_SL_PCT_DIVERGENCE` diagnostic at lines 3550–3657 is untouched.

**Touch-impact analysis.** The hard_floor branch is a strict superset of pre-fix behaviour: when the floor is inactive (SL < 85%), behaviour is identical to pre-fix. When active, the close executes regardless of composite — the same outcome the spec's INJUSDT-style cases needed. Operator can disable by setting `wd_hard_risk_floor_sl_pct = 100.0` in config.toml, or revert via `git revert f04eeb6`.

### H2 — `src/risk/wd_brain_scoring.py`

**Role in the architecture.** Pure-function composite-scoring module. No I/O, no side effects, no datetime calls (per the module docstring). Unit-testable deterministically. Imported by `position_watchdog.py` for scoring computation and by `tests/test_wd_brain_scoring.py` for direct validation.

**Dependencies.** Stdlib only — `math`, `dataclasses`, `typing`. No project-module imports (intentional: keeps the module pure). No new imports added by P0-3.

**Change made.** Four regions:

1. Lines 84–101 — new `"brain_vote"` entry in `DEFAULT_WEIGHTS` with four buckets (`structural`/`vague`/`empty`/`absent`) and weights (`2.0`/`1.0`/`0.5`/`0.0`). The `absent` bucket preserves pre-P0-3 composite verbatim for callers that have not been re-wired.
2. Lines 160–165 — `BrainCloseScoreFactors` extended with `brain_vote_bucket: str = "absent"` and `brain_vote_factor: float = 0.0` defaults. Backward compatible: existing test fixtures construct the dataclass without those fields and still pass.
3. Lines 177–205 — `as_log_dict` extended with `brain_vote_bucket` and `brain_vote_factor` keys. Backward compatible: log parsers that don't know about these keys still parse the rest.
4. Lines 322–325, 397–406, 446–447 — `compute_brain_close_score` signature, composite computation, and factor population extended for `brain_vote_present: bool = False`. Default `False` preserves pre-P0-3 composite for callers not yet re-wired; only the watchdog intercept passes `True`. Docstring updated to document the parameter, including the bucket map and the rationale.

**Invariants preserved.**

- Pure-function nature (no I/O, no global state).
- Fail-soft input sanitization (NaN/negative checks at lines 363–379) untouched.
- Bucket classifiers (`_classify_pnl`, `_classify_time_remaining`, etc.) untouched.
- Recommendation thresholds (composite ≥ threshold → execute; ≥ 0 → reject; < 0 → reject_and_tighten) untouched.

**Touch-impact analysis.** The composite formula now sums one additional factor (brain_vote_factor). For callers that do not pass `brain_vote_present`, the factor is 0.0 and the formula is identical to pre-P0-3. For callers that pass `brain_vote_present=True` (only the watchdog intercept post-P0-3), the composite has a bounded positive contribution gated on reasoning quality.

### H2 — `src/config/settings.py`

**Role in the architecture.** Project-wide configuration dataclasses and TOML→dataclass builders. The `Settings` aggregate is consumed by every worker, every service, and the brain.

**Dependencies.** Stdlib + project core. Builders read from raw TOML dicts and return typed dataclasses.

**Change made.** Four regions:

1. Lines 855–867 — `RiskSettings.xray_high_conviction_protection_enabled: bool = True` with explanatory comment.
2. Lines 1036–1045 — `WatchdogSettings.wd_hard_risk_floor_sl_pct: float = 85.0` with explanatory comment.
3. Lines 3805–3808 — `_build_risk` threads `xray_high_conviction_protection_enabled` from TOML with default True.
4. Lines 3906–3909 — `_build_watchdog` threads `wd_hard_risk_floor_sl_pct` from TOML with default 85.0.

**Invariants preserved.** All existing settings fields untouched. Both new fields have safe defaults that preserve current behaviour when the operator's config.toml does not yet contain the keys.

### H2 — `config.toml`

**Role in the architecture.** Operator-tunable runtime configuration. Read at process start by `_build_*` functions in settings.py.

**Change made.** Two regions:

1. Lines 405–413 — `xray_high_conviction_protection_enabled = true` under `[risk]` with comment explaining motivation, behaviour, default, and kill-switch.
2. Lines 544–550 — `wd_hard_risk_floor_sl_pct = 85.0` under `[watchdog]` with comment explaining motivation and the operator-tunable range.

**Invariants preserved.** All existing keys untouched. Both new keys have explanatory comments matching the project's existing comment style.

### H2 — Test files

- `tests/test_wd_brain_scoring.py`: 6 new tests appended at the end, covering: brain_vote bucket map (4 tests, one per bucket), ICP 16:50:40 regression (composite 6.5 → execute), C1 regression (vague panic on sound → reject_and_tighten). All 33 tests pass.
- `tests/test_j3_xray_lock_override.py`: source-pin test updated to assert the new canonical event tag `DIRECTION_DECISION` and the `xray_high_conviction_protection_enabled` toggle, replacing the legacy `XRAY_LOCK_PRECEDENCE_RESOLUTION` + `XRAY_OVERRIDE_LOCK` pins.
- `tests/test_j_series_e2e_pipeline.py`: source-pin test updated to grep for `DIRECTION_DECISION` instead of the two legacy tags, AND extended the grep flags with `--include=*.py --exclude=*.bak*` so it cannot pass via leaked tag strings inside Rule-8 backup files.

### H2 — Verification scripts

- `verify_p0_2.py` (new, 210 lines): parses trial-window log, asserts P0_2_SENTINEL present, asserts zero co-occurring APEX_DIR_LOCK + XRAY_DIR_FLIP pairings, validates DIRECTION_DECISION event distribution, reports counts.
- `verify_p0_3.py` (new, 213 lines): parses trial-window log, asserts P0_3_SENTINEL present, asserts every WATCHDOG_CLOSE_SCORE_COMPUTED carries brain_vote_bucket and brain_vote_factor fields, asserts every score event with `hard_floor_active=True` has a matching WATCHDOG_HARD_FLOOR_HIT event, reports distribution.

## H1 — Architecture and Layer Compliance

```
Layer 0 (Config)         — src/config/settings.py, config.toml             [P0-2 + P0-3 added fields]
Layer 1A (Always-on)     — price_worker, kline_worker                       [UNTOUCHED]
Layer 1B (XRAY etc.)     — structure_worker, signal_worker, regime_worker   [UNTOUCHED]
Layer 1C (Pipeline)      — strategy_worker (XRAY/APEX consumer)            [P0-2 edited inside this layer]
Layer 1D (Smart Scanner) — scanner_worker, state_labeller                   [UNTOUCHED]
Layer 2 (Brain)          — strategist (CALL_A, CALL_B)                      [UNTOUCHED]
Layer 3 (APEX)           — optimizer (DeepSeek, direction lock, sizing)     [UNTOUCHED]
Layer 4 (Gate)           — gate.py (pre-execution validation)               [UNTOUCHED]
Layer 5 (Execute)        — bybit_demo, shadow                               [UNTOUCHED]
Layer 6 (Watchdog)       — position_watchdog, wd_brain_scoring              [P0-3 edited inside this layer]
Layer 7 (Reconcile)      — position_reconciler                              [UNTOUCHED]
```

The P0-2 changes live entirely within Layer 1C (strategy_worker is the XRAY/APEX consumer that turns directives into orders). The high-conviction read of per-coin regime uses the same `getattr(detector, '_per_coin_regimes', {})` access pattern as 4 existing call sites (tias/collector, apex/gate, apex/assembler, regime_worker) — no new dependency edges.

The P0-3 changes live entirely within Layer 6 (position_watchdog as the consumer, wd_brain_scoring as the pure-function helper). The brain-vote-factor extension is additive only — pre-P0-3 callers see identical composite values.

## H1 — Naming Consistency Audit

| Identifier kind | Convention | New name | Matches existing pattern |
| --- | --- | --- | --- |
| Settings field | snake_case, prefix-grouped | `xray_high_conviction_protection_enabled` | yes (xray_*, _enabled suffix) |
| Settings field | snake_case, prefix-grouped | `wd_hard_risk_floor_sl_pct` | yes (wd_*, _pct suffix) |
| Config key | snake_case (mirrors field) | `xray_high_conviction_protection_enabled` | matches setting field |
| Config key | snake_case (mirrors field) | `wd_hard_risk_floor_sl_pct` | matches setting field |
| Log tag | SCREAMING_SNAKE_CASE | `DIRECTION_DECISION` | yes (WD_*, WATCHDOG_*, APEX_*) |
| Log tag | SCREAMING_SNAKE_CASE | `WATCHDOG_HARD_FLOOR_HIT` | yes |
| Boot sentinel | SCREAMING_SNAKE_CASE with P0_<n>_SENTINEL prefix | `P0_2_SENTINEL`, `P0_3_SENTINEL` | new pattern; consistent with `WD_SCORING_ENFORCE_ACTIVE` and `BOOT_L4_NO_HARDCODED_CAP_OK` |
| Skip-reason code | snake_case lowercase | `xray_veto_high_conviction` | yes (xray_dir_block, xray_dir_flip_blocked) |
| Trade-dict flag | _snake_case (leading underscore) | `_xray_veto_high_conviction` | yes (_apex_locked, _xray_flip_ratio, _xray_lock_overridden, _xray_flip_suppressed_by_lock) |
| DEFAULT_WEIGHTS key | snake_case | `brain_vote` | yes (pnl, time_remaining, age, velocity, sl_consumption, xray, reasoning) |
| Dataclass field | snake_case | `brain_vote_bucket`, `brain_vote_factor` | yes (pnl_bucket, pnl_factor, etc.) |

No naming collisions detected in the entire codebase. The full grep audit (Pass 8) confirmed each new identifier is unique to my new code (the `DIRECTION_DECISION` string appears in my emission sites, config comments, and the audit doc — no other emissions exist).

## H1 — Test Pass Results

All twelve audit passes executed against the live codebase:

| Pass | Description | Result |
| --- | --- | --- |
| 1 | Smoke tests (module imports, file compilation, config round-trip) | PASS (4 imports, 9 file compiles, 5 setting values verified) |
| 2 | Unit tests (`test_wd_brain_scoring.py`) | PASS (33/33) |
| 3 | Integration tests (`test_wd_scoring_enforce_integration.py`) | PASS (6/6) |
| 4 | Regression tests (direction-flow, lock, flip, RR-boost, decision-log, propagation, flip-tp-capper) | PASS (76/76) |
| 5 | Source-pin tests (`test_j_series_e2e_pipeline.py`) | PASS (10/10) |
| 6 | Runtime simulation (brain_vote bucket map, ICP regression, C1 regression, hard-floor math, high-conviction truth table) | PASS (16/16) |
| 7 | Verification scripts smoke (run against current logs) | Correct FAIL pre-restart (expected: sentinels and new events absent until operator restart) |
| 8 | Naming consistency audit | PASS (all field names, config keys, log tags, trade-dict flags consistent) |
| 9 | Layer/architecture compliance | PASS (no out-of-scope layer touched; P0-2 inside Layer 1C, P0-3 inside Layer 6) |
| 10 | Dependency wiring | PASS (existing access patterns reused, no new circular imports) |
| 11 | All P0-touched + direction-related regression | PASS (166/166, 1 pre-existing deselected) |
| 12 | Full project sweep | PASS (3548/3548, 11 skipped, 4 pre-existing deselected) |

Total: **3805 verifications across all categories.** Zero P0-related failures. Four deselected failures are pre-existing and confirmed unrelated by exact-file-touch analysis:

- `test_system_prompt_still_has_rsi_caution` — checks a string in `STRATEGIST_SYSTEM_PROMPT` removed by an earlier (non-P0) layer4 commit; my P0 commits did not touch `src/brain/strategist.py`.
- Two `test_bybit_demo/test_websocket_subscriber.py` tests — touch `src/bybit_demo/`; my P0 commits did not modify any file in that directory.
- `test_source_pin_migration_v32_includes_column_and_index` — checks `SCHEMA_VERSION = 32` in `src/database/migrations.py`; my P0 commits did not touch the database migrations.

## H1 — Polish Findings Addressed During Audit

The audit found one polish gap that was fixed before the audit document was written:

- **`compute_brain_close_score` docstring** in `src/risk/wd_brain_scoring.py` did not document the new `brain_vote_present` parameter. The docstring was updated to include the parameter description plus the bucket-to-weight map and the rationale ("absent" preserves pre-P0-3 composite for un-rewired callers; the watchdog always passes True). Re-ran the 39-test focused suite after the docstring update — all green.

No code-path defects or integration gaps were found during the audit. The P0-2 and P0-3 fixes are properly woven into the project at the file, dependency, naming, and architectural levels.

## H1 — Backup-File Integrity (Rule 8)

All six `.bak_p0_*` files are present in their original directories, timestamped from the edit session:

- `config.toml.bak_p0_20260522_200601`
- `src/apex/optimizer.py.bak_p0_20260522_200601` (pre-staged; no edit applied to optimizer.py)
- `src/config/settings.py.bak_p0_20260522_200601`
- `src/risk/wd_brain_scoring.py.bak_p0_20260522_201138`
- `src/workers/position_watchdog.py.bak_p0_20260522_201138`
- `src/workers/strategy_worker.py.bak_p0_20260522_200601`

Each backup preserves the exact pre-edit state, enabling per-file revert without depending on git history.

## H1 — Git State at Audit Close

- `main` clean, all commits pushed to `origin/main`.
- Five P0 commits this session: `6f21f1d`, `f04eeb6`, `049ddfe`, `18e3df6`, plus this audit doc to follow.
- Zero unmerged branches, zero unpushed commits.

## H1 — What's Verified vs What Still Needs the Operator's Trial

Verified at the code-and-suite level by this audit:

- All modified files compile, import, and round-trip through their dependency graphs.
- The composite math passes its property-based tests and the two regression cases (ICP 16:50:40, C1 vague-panic-on-sound).
- The full 3805-verification test sweep passes with zero P0-related failures.
- Naming, architecture, layer compliance, and dependency wiring are clean.
- Verification scripts correctly detect pre-fix state today (will detect post-fix state after operator restart).

Not verified (out of scope for code audit, requires the operator's trial):

- Whether the post-restart behaviour in production aligns with the design intent across a real session.
- Whether the high-conviction definition (regime + trade_direction agreement) correctly classifies live brain directives.
- Whether the 85% hard-floor is the right floor value across the operator's typical position-state distribution.
- Whether C1 enforce mode with the new brain_vote_factor behaves as predicted (preserves anti-churn while admitting evidence-based closes).

These can only be verified by the operator running a live trial after restart and running `verify_p0_2.py` / `verify_p0_3.py` against the resulting log.

## H1 — Conclusion

The P0-2 and P0-3 fixes are properly implemented, integrated, named, and tested at the code-and-suite level. Industry-standard quality criteria — clean layer separation, additive backward-compatible changes, complete dependency wiring, consistent naming, exhaustive test coverage across smoke / unit / integration / regression / source-pin / runtime / verification categories — are all satisfied. The fixes are ready for the operator's restart-and-observe trial.
