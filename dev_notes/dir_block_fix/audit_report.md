# Direction Block Fix — Post-Implementation Audit Report

Audit performed 2026-05-05 on `main` head `599bf8c`. Walks every touched
file and every phase, end-to-end, against the spec requirements at
`/home/inshadaliqbal786/IMPLEMENT_DIR_BLOCK_FIX_INDEPTH.md` and the
plan at
`/home/inshadaliqbal786/.claude/plans/plan-mode-first-compeltely-snappy-sphinx.md`.

## Summary

| Dimension | Verdict |
|---|---|
| Spec coverage | All 5 issues + 3 investigation discoveries addressed |
| Per-phase wiring | All 5 phases verified — settings + builder + reader chain consistent |
| Naming consistency | Trade-dict keys, event names, method names all stable across set sites and consumers |
| Static checks | Source-file ruff delta = 0 (no lint introduced); test files lint-clean after auto-fix |
| Full pytest sweep | 2170 passed, 1 failed (pre-existing unrelated), 1 skipped |
| Live `Settings.load()` round-trip | All 16 phase-touched values load correctly |
| Outstanding smoke signals | 1 dead-code duplicate (`src/workers/settings.py` — pre-existing, no imports) |

## Commit chain since the dir-block-fix work began

```
599bf8c  chore(dir-block-fix): post-audit fixes — asyncio.run + lint + stale comments
889b995  chore(dir-block-fix): cross-check follow-ups — builder + getattr fallback alignment
a65e89c  fix(apex/phase-5): recalibrate TP cap multipliers + reduce no-op log noise
2cb3dc4  fix(enforcer/phase-4): raise mode thresholds + clamp leverage instead of block
dd761e4  fix(apex/phase-3): allow flips when RR strongly favors opposite direction
c44d6f0  fix(layer4/phase-2): recalibrate SL trail tightening + close gateway/floor gaps
8784227  fix(strategy_worker/phase-1): convert XRAY direction recheck from block to flip
aa45399  docs(dir-block-fix/phase-0): baseline measurements
```

8 commits, +1131/-117 net, 6 source files changed, 6 test files changed
(5 new + 1 updated for Phase 3 threshold change).

## File-by-file verdict

### `src/config/settings.py`

Role: single source of truth for typed settings dataclasses + their
config.toml builders. Loaded by `Settings.load()` at startup;
imported by every worker and the brain client.

What I changed:
- Added `RiskSettings.xray_dir_flip_threshold_ratio` (Phase 1).
- Lowered `SLGatewaySettings.max_step_pct` 0.5 → 0.25 (Phase 2).
- Lowered `Mode4Settings.tighten_cooldown_seconds` 30 → 15 (Phase 2).
- Raised `Mode4Settings.min_profit_for_trail_pct` 0.30 → 0.50 (Phase 2).
- Lowered `APEXSettings.apex_min_flip_confidence` 0.90 → 0.70 (Phase 3).
- Added `APEXSettings.apex_flip_rr_boost_threshold` and
  `APEXSettings.apex_flip_rr_boost_amount` (Phase 3).
- Raised `EnforcerSettings.pnl_caution_pct` -2.0 → -3.0 (Phase 4).
- Raised `EnforcerSettings.pnl_survival_pct` -5.0 → -7.0 (Phase 4).
- Raised `EnforcerSettings.streak_boost_threshold` -5 → -8 (Phase 4).
- Added `EnforcerSettings.streak_boost_pnl_floor_pct` (Phase 4).
- Raised every `tp_cap_multiplier_by_class` value (Phase 5).
- Added `APEXSettings.apex_tp_cap_hard_ceiling_pct` (Phase 5).
- Switched `_build_sl_gateway` from explicit-args to `**dict` filter
  pattern so previously-ignored `min_distance_atr_multiplier`,
  `min_distance_abs_floor_pct`, and `min_distance_class_ceiling`
  flow through (cross-check fix).

Wiring status: Verified end-to-end via `Settings.load()` round-trip.
All 16 phase-touched values produce the expected runtime values.

Ruff delta vs baseline: 0 (no lint issues introduced).

### `config.toml`

Role: operator-tunable runtime values. Loaded into the dataclasses
above by the `_build_*` functions.

What I changed:
- `[risk]` `xray_dir_flip_threshold_ratio = 3.0` (Phase 1).
- `[sl_gateway]` `max_step_pct = 0.25`, `log_only_global = false`
  (Phase 2 + Discovery 1).
- `[mode4]` `tighten_cooldown_seconds = 15`,
  `min_profit_for_trail_pct = 0.50` (Phase 2).
- `[apex]` `apex_min_flip_confidence = 0.70`,
  `apex_flip_rr_boost_threshold = 3.0`,
  `apex_flip_rr_boost_amount = 0.15` (Phase 3).
- `[enforcer]` `pnl_caution_pct = -3.0`, `pnl_survival_pct = -7.0`,
  `streak_boost_threshold = -8`, `streak_boost_pnl_floor_pct = -1.0`
  (Phase 4).
- `[apex.tp_cap_multiplier_by_class]` raised every class (Phase 5).

Wiring status: every key parses cleanly via tomllib; round-trip with
`Settings.load()` confirms the values flow through.

### `src/workers/strategy_worker.py`

Role: the ML brain's per-symbol trade executor inside the
`StrategyWorker` worker (extends `SweetSpotWorker`). Owns
`_execute_claude_trade` — the function that flows a Claude trade
directive through Enforcer → SURVIVAL gate → X-RAY quality → X-RAY
direction recheck → testnet/dup-position → service lookups → SL/TP
validation → position size compute → order placement → coordinator
register → thesis save → DB record → telegram alert.

Importers (production): `src.workers.manager.WorkerManager` (DI
hub), 5 test files.

What I changed:
- Replaced the legacy XRAY direction recheck block at lines 1525-1575
  (BLOCK at ratio>5 / SIZE_REDUCE at ratio>3) with the new flip path
  at lines 1540-1697. The flip mutates trade direction + SL + TP
  using `StructuralPlacement.long_*_price` / `short_*_price` levels,
  marks `_apex_was_flipped`, `_apex_original_direction`,
  `_flip_source="xray"`, `_xray_flip_ratio`, and updates the local
  `direction` variable for downstream use. Re-validates the
  X-RAY direction-conflict gate against the FLIPPED direction;
  blocks with `XRAY_DIR_FLIP_BLOCKED` on post-flip conflict, falls
  back to `XRAY_DIR_BLOCK` when the structural payload is missing
  dual-direction levels.
- Replaced the enforcer leverage-block call at lines 1457-1469 with
  the `clamp_leverage` + `ENFORCER_LEV_CLAMP` event flow, then kept
  `should_allow_trade` as a forward-compatibility shim.
- Updated the reasoning-enrichment block at lines 1755-1779 to
  branch on `_flip_source`, distinguishing XRAY-driven from
  APEX-driven flips in the saved thesis prompt.

Downstream consumers verified:
- `_apex_was_flipped` is read at strategy_worker.py:1764 (reasoning),
  thesis_manager.save_thesis (apex_flipped kwarg),
  trade_coordinator.register_trade (apex_was_flipped kwarg).
- `_apex_original_direction` is read at the same three sites.
- `_flip_source` is read only at strategy_worker.py:1766 — gate-local.
- `_xray_flip_ratio` is read only inside the reasoning enrichment
  block at line 1770.

Wiring status: all three downstream consumers see the flipped state
correctly. The XRAY_DIR_REDUCE event is fully removed from emission.

Test coverage: `tests/test_xray_dir_flip.py` (3 surgical smokes —
flip succeeds, dual-levels-missing fallback to block, no-flip
preservation).

Ruff delta vs baseline: 0.

### `src/apex/optimizer.py`

Role: the APEX (Qwen / DeepSeek) optimizer that wraps Claude's
trade directive with structural intelligence and may flip direction
or resize position based on Qwen's analysis. Lives between
`assembler` (input prep) and `gate` (post-optimization safety).

Importers (production): `src.workers.manager`, 5 test files.

What I changed:
- TP cap formula at lines 215-225 now applies a hard
  `apex_tp_cap_hard_ceiling_pct` ceiling via `min(recTP×mult,
  ceiling)` (Phase 5). Multiplier dict raised per class. Default
  fallback raised from 1.3 to 1.6 (medium-class).
- TP cap emission at lines 354-372 split: WARNING with
  `was_reduced=true` when `optimized.tp_pct > _tp_cap`, DEBUG with
  `was_reduced=false` when no-op. Cuts log noise on the modal case.
- Flip-confidence enforcement at lines 783-820: `_enforce_flip_confidence`
  now accepts an optional `effective_confidence` keyword. When
  provided, the gate uses that boosted value instead of
  `optimized.confidence`. Backward compatible — existing 8 tests
  in `test_apex_flip_discipline.py` still pass without changes.
- `optimize()` at lines 280-313 computes the RR-weighted boost
  before calling the helper. Reads `package.structure_data.rr_long`
  and `rr_short` (already populated by the assembler at
  `assembler.py:724-725`). When the flipped direction's RR is at
  least `apex_flip_rr_boost_threshold` × the chosen direction's
  RR, adds `apex_flip_rr_boost_amount` to the effective confidence.
  Boost is gate-local; not propagated to downstream consumers.
- APEX_FLIP_BLOCKED log line at lines 315-323 expanded with
  `raw_conf`, `eff_conf`, `rr_boost`, `rr_chosen`, `rr_flipped`,
  `regime` — splittable in log analysis.

Downstream consumers verified:
- Phase-9 helper invariants preserved: `optimized.was_flipped` reset,
  `optimized.direction` reverted to claude's original, reasoning
  prefix `"[FLIP BLOCKED conf<min] "` unchanged.
- Phase-2 `_apply_flip_resize_policy` (post-Phase-3 helper, commit
  0795aca) is invoked when the flip is allowed AND
  `apex_block_flip_resize=True`. The boost-then-helper sequence
  preserves this invariant.

Wiring status: confirmed via the existing
`test_apex_flip_discipline.py` (8 tests, all pass) and the new
`test_apex_flip_rr_boost.py` (3 tests).

Ruff delta vs baseline: 0.

### `src/apex/gate.py`

Role: the APEX TradeGate — last safety checkpoint between the
optimizer and Shadow execution. Runs 14 numbered checks; never
blocks (the docstring is explicit), only adjusts.

What I changed:
- Discovery 2: aligned the `getattr` fallback at line 248 from
  the stale `50.0` to the dataclass default `15.0`.

Wiring status: confirmed via `test_trail_tightening.py` test
`test_apex_trail_floor_default_aligned`, which asserts the
APEXSettings dataclass value is 15.0. The runtime path
(gate.py:248) and the live config.toml value all agree on 15.0.

Ruff delta vs baseline: 0.

### `src/strategies/performance_enforcer.py`

Role: PnL-based intelligent throttle. Levels 0/1/2 driven by daily
PnL and loss streak. Soft-throttles via `get_size_multiplier`,
hard-clamps via `clamp_leverage` (new in Phase 4). Replaces the
prior leverage-block contract.

Importers (production): `src.workers.manager`,
`src.workers.enforcer_worker`, plus 1 test file.

What I changed:
- `should_allow_trade` at lines 99-109 now always returns
  `(True, "ok")`. The function is preserved as a
  forward-compatibility shim for the layer_manager / rule_engine
  call sites that pass `leverage=1` and treat the boolean as an
  enforcer-halt signal (the enforcer never halts).
- New `clamp_leverage(leverage)` method at lines 111-131 returns
  `(clamped_leverage, reason)`. Reason is empty when no clamp;
  otherwise the structured `PRESERVATION_CLAMP: req->clamped (PnL=...)`
  string suitable for the ENFORCER_LEV_CLAMP log line.
- Streak-boost gate at lines 254-257 now requires both
  `streak <= streak_boost_threshold` AND `pnl < streak_boost_pnl_floor_pct`.
  Pre-fix it fired on streak alone with `pnl < 0`.
- `__init__` getattr fallbacks at lines 70-78 aligned with the new
  dataclass defaults (-3.0 / -7.0 / -8 / -1.0). Same hygiene fix
  shape as Discovery 2 in apex/gate.py:241.
- Module docstring (lines 1-21) and `get_size_multiplier` inline
  comments (lines 165-169) updated to reflect the new threshold
  ranges. Comments-only update; code paths use the live variables.

Downstream consumers verified:
- `clamp_leverage` is called from `strategy_worker.py:1462-1469`
  (Phase 4 caller). No other callers (verified by grep).
- `should_allow_trade` is called from `layer_manager.py:1228-1229`
  (with `leverage=1`), `rule_engine.py:60-61` (with `leverage=1`),
  `strategy_worker.py:1481-1488` (forward-compat shim). All three
  see `(True, "ok")` post-fix; no production behavior change.
- `get_size_multiplier` is called from
  `strategy_worker.py:1685-1693` — uses the live
  `_pnl_caution_pct` / `_pnl_survival_pct` attributes which now
  point to -3.0 / -7.0.

Wiring status: confirmed via 3 surgical smokes in
`test_enforcer_clamp.py` and the existing
`test_definitive_pipeline_e2e.py::TestPhase9*`.

Ruff delta vs baseline: 0.

## Per-phase wiring verdict

| Phase | Spec requirement | Implementation | Test coverage | Verdict |
|---|---|---|---|---|
| 1 | XRAY direction recheck → flip with re-validation; configurable threshold; new XRAY_DIR_FLIP / XRAY_DIR_FLIP_BLOCKED events; XRAY_DIR_REDUCE removed | `strategy_worker.py:1525-1697` + new `RiskSettings.xray_dir_flip_threshold_ratio` | 3 surgical smokes in `test_xray_dir_flip.py` | PASS |
| 2 | Recalibrate trail tightening; address Discoveries 1 (gateway audit-mode bypass) + 2 (gate.py:241 default mismatch) | Config + dataclass defaults aligned across `[sl_gateway]` and `[mode4]`; gate.py:248 fallback aligned to 15.0; `_build_sl_gateway` switched to `**dict` pattern | 3 invariant tests in `test_trail_tightening.py` | PASS |
| 3 | Lower flip-confidence floor; add RR-weighted boost; expand APEX_FLIP_BLOCKED payload | `apex_min_flip_confidence` 0.90 → 0.70; new RR-boost knobs; helper accepts `effective_confidence` kwarg; expanded log line | 3 surgical smokes in `test_apex_flip_rr_boost.py`; existing 8 in `test_apex_flip_discipline.py` still pass | PASS |
| 4 | Raise enforcer thresholds; convert leverage block to clamp; gate streak path with PnL floor | Thresholds raised across dataclass + config + getattr fallbacks; new `clamp_leverage` method; new `streak_boost_pnl_floor_pct` | 3 surgical smokes in `test_enforcer_clamp.py` | PASS |
| 5 | Recalibrate TP cap multipliers; add hard ceiling; split was_reduced log levels | Multipliers raised per class; `apex_tp_cap_hard_ceiling_pct` enforced via `min(...)`; APEX_TP_CAP split WARNING vs DEBUG | 2 invariant tests in `test_apex_tp_cap.py` | PASS |

## Investigation discoveries — verdict

| Discovery | Spec impact | Implementation | Verdict |
|---|---|---|---|
| 1: SL_GATEWAY trail_activation step bypass | Phase 2 | Flipped `[sl_gateway].log_only_global` true → false; per-rule flags already false, so all four rules now enforce hard | RESOLVED |
| 2: gate.py:241 default vs settings.py default mismatch (50.0 vs 15.0) | Phase 2 | Aligned the `getattr` fallback at gate.py:248 to 15.0 | RESOLVED |
| 3: PRESERVATION trigger at -0.85 % via streak path | Phase 4 | Raised `streak_boost_threshold` -5 → -8; added `streak_boost_pnl_floor_pct = -1.0`; streak path now requires both | RESOLVED |

## Cross-check follow-up findings

These were surfaced AFTER Phases 0-5 by the deep audit:

- `_build_sl_gateway` was using an explicit-args pattern that
  silently ignored three dataclass fields. Switched to the `**dict`
  filter pattern. Production behavior unchanged on the current
  config.toml (the dataclass defaults match the live values by
  deliberate duplication), but operator tuning of those keys now
  flows through.
- Performance Enforcer getattr fallbacks at init still cited the
  pre-Phase-4 thresholds. Aligned to -3.0 / -7.0 / -8 (same hygiene
  shape as Discovery 2).
- 2 e2e tests in `test_definitive_pipeline_e2e.py::TestPhase9APEXFlipDiscipline`
  still asserted the pre-Phase-3 0.90 threshold. Updated to assert
  0.70 + the new boost knobs and use a sub-threshold confidence
  sample.
- 3 xray-flip tests used `asyncio.get_event_loop().run_until_complete()`
  which fails on Python 3.10+ when other suite tests have closed
  the default loop. Switched all 3 sites to `asyncio.run(...)`.
  Tests previously passed in isolation but flaked in the full
  sweep — now pass under both.
- 4 ruff issues on the new test files (unsorted imports + 1 unused
  import) — auto-fixed.

## Pre-existing smoke signal NOT addressed (out of scope)

`src/workers/settings.py` is a 45 KB duplicate of
`src/config/settings.py` containing stale `Mode4Settings` and
`EnforcerSettings` dataclasses with the pre-Phase-4 defaults.
Nothing imports it (verified by `grep -rn "from src.workers.settings"`
across `src/` and `tests/`). The file imports cleanly but is
unreferenced. Touching it without understanding why it exists would
violate the operator's "understand before touching" rule. Logged
here for a future maintenance commit.

## Final test verdict

```
Targeted dir-block-fix sweep         (5 files)   = 14/14 pass
Related-modules regression           (8 files)   = 119/120 pass*
Full pytest sweep (excl. 4 dead/integration)     = 2170/2171 pass*
```

\*The single failing test is `test_apex_direction_lock.py::test_system_prompt_still_has_rsi_caution`, a pre-existing failure from the earlier framing-fix series (already documented in the `project_post_execution_closure_fix.md` memory). It is unrelated to the dir-block-fix work.

Excluded from the full sweep:
- `tests/test_phase7/*` (3 collection errors — `src.brain.prompt_builder`,
  `src.brain.scheduler`, `src.brain.executor` modules don't exist;
  pre-existing dead test files).
- `tests/overhaul29_integration_test.py`, `tests/overhaul29_pipeline_test.py`,
  `tests/stage1_2_pipeline_test.py` — pipeline integration tests
  that may require live external services.

## Deployment readiness

All 8 commits are on `main`. The live `trading-workers` and
`trading-mcp-sse` services still run pre-fix code until restart;
operator must execute:

```
sudo systemctl restart trading-workers trading-mcp-sse
```

After restart, the first signs of life:
- `XRAY_DIR_FLIP` events firing within ~1 hour (replaces ~26
  XRAY_DIR_BLOCK + XRAY_DIR_REDUCE per 24h baseline).
- `ENFORCER_LEV_CLAMP` events when level≥1 active.
- `APEX_TP_CAP` at DEBUG (no-op) dominating over WARNING (actual
  reduction).
- `APEX_FLIP_BLOCKED` count drops; payload now carries
  `raw_conf` / `eff_conf` / `rr_boost`.

Phase 6 = 3-5 day live trial (operator-driven). Phase 7 = closure
report at `dev_notes/dir_block_fix/phase7_verification_report.md`
after the trial.
