# DELTA 04 — Master Recommendation

Agent DELTA Phase 2.5. This is the operator's Phase 2.7 approval artifact.
The four operator-approved options across three agents have been
analyzed, sequenced, integration-checked, and trial-predicted. This
document gathers everything the operator needs to approve, modify, or
request further investigation before Phase 3 begins.

## Executive summary

The system exhibits a structural Sell bias (89% Sell directives on
2026-05-16; -$31.82 cascade at 14:45) caused by four compounding
mechanisms: ALPHA found a cross-layer information loss (APEX cannot see
the inverted `trade_direction` from XRAY counter setups); BETA found a
regime-only APEX_DIR_LOCK that ignores structural-RR evidence and a
10x XRAY override threshold that creates a 3-9x dead zone where 8 trades
were suppressed; GAMMA found the system has no portfolio direction
concentration cap (every cascade-member entry was at >= 87.5% same-direction
concentration). The four operator-approved fixes plumb a missing field
(ALPHA), make the lock structurally-aware (BETA R2), auto-tune the
override threshold from per-direction WR (BETA R3), and add an
aim-conditional portfolio cap (GAMMA). Combined, they prevent the
BSBUSDT-class loss, prevent the 14:45-class cascade, and shift the
Sell/Buy directive ratio from 89/11 toward 60-75/25-40 — within the
aim-aligned balance band the operator targets. Implementation is
sequential (R1 -> R2 plus R3 -> R4) across three branches with no
cross-agent code conflicts beyond a deterministic append-only merge in
`settings.py`.

## The four operator-approved options in one place

### R1 — ALPHA Option E plus Option D (XRAY counter-trade inversion)

- Decision record: `dev_notes/direction_fix/agent_alpha/07_alpha_phase2_decision.md`
- Branch: `fix/r1-xray-counter-inversion`
- Mechanism: plumb `trade_direction` from `StructuralAnalysis` through
  `src/apex/assembler.py:737` into a new `StructuralData.trade_direction`
  field on `src/apex/models.py:258`. Add observability via new fields on
  `XRAY_CLASSIFY_SUMMARY` and a new `XRAY_DIRECTION_SPLIT` per-tick line
  in `src/workers/structure_worker.py:273-277`.
- Out-of-scope for ALPHA: BETA consumes the field; GAMMA also consumes it.

### R2 — BETA Option B with static asymmetric thresholds (APEX_DIR_LOCK)

- Decision record: `dev_notes/direction_fix/agent_beta/09_beta_phase2_decisions.md`
- Branch: `fix/r2-r3-apex-direction-lock` (combined with R3)
- Mechanism: modify `_check_direction_lock` at `src/apex/optimizer.py:1265-1311`
  to read `package.structural_data.rr_long`, `rr_short`, and
  `trade_direction`. Bail out of the lock when:
  - For Sell-to-Buy override direction: `rr_long/rr_short >= 3.0` OR
    `trade_direction == "long"`
  - For Buy-to-Sell override direction: `rr_short/rr_long >= 10.0` AND
    `trade_direction == "short"` (require both — protects the
    worse-WR direction harder)
- New settings on `APEXSettings`:
  `apex_lock_structural_override_ratio_buy_to_sell: float = 10.0`,
  `apex_lock_structural_override_ratio_sell_to_buy: float = 3.0`.
- New event: `APEX_LOCK_DECISION_EXPLAINED` with fields `regime`,
  `ratio_long_to_short`, `trade_direction`, `verdict={fired, bailed_structural}`.

### R3 — BETA Option E (per-direction WR auto-tuning override threshold)

- Decision record: same file as R2 (lines 36-58)
- Branch: same as R2
- Mechanism: modify `src/workers/strategy_worker.py:1671-1717` to derive
  `xray_lock_override_ratio_threshold` dynamically from per-direction WR
  in the trade_log:
  - For Sell-to-Buy override: `threshold = base * (1 - buy_wr / 100)`
  - For Buy-to-Sell override: `threshold = base * buy_wr / 100`
  - Caps: floor 2.0x, ceiling 15.0x.
- New settings: `xray_lock_override_wr_base: float = 10.0`,
  `xray_lock_override_wr_floor: float = 2.0`,
  `xray_lock_override_wr_ceiling: float = 15.0`,
  `xray_lock_override_wr_window_trades: int = 200`.
- New event: `XRAY_OVERRIDE_RATIO_DETAIL` with fields `direction`,
  `buy_wr`, `sell_wr`, `derived_threshold`, `xray_ratio`, `verdict`.
- Backward-compat fallback to legacy static threshold when WR data
  unavailable (< 30 trades in window).

### R4 — GAMMA Design C (aim-conditional portfolio direction cap)

- Decision record: `dev_notes/direction_fix/agent_gamma/07_gamma_phase2_decisions.md`
- Branch: `fix/r4-portfolio-direction-cap`
- Mechanism: insert new CHECK 15 in `src/apex/gate.py` between CHECK 14
  (line 647) and the final `return trade` near line 672. When portfolio
  concentration reaches 70% (cap_pct), the cap fires ONLY when an
  alternative direction is viable:
  - `trade_direction` from `StructuralData` is opposite to proposed
    direction, OR
  - `rr_opposite/rr_chosen >= 2.0` from `StructuralData.rr_long`,
    `rr_short`.
- When cap fires: sets `_gate_rejected =
  "portfolio_direction_cap_{dir}_{pct}_aim_conditional"`; emits
  `PORTFOLIO_CAP_HIT` with `verdict=blocked_aim_conditional` at WARNING.
- When mono-trending and no alternative: emits `PORTFOLIO_CAP_HIT` with
  `verdict=permitted_mono_trending` at WARNING; trade executes.
- Helper: `TradeCoordinator.get_direction_counts() -> {Buy, Sell, total}`
  added near `src/core/trade_coordinator.py:1869`.
- New settings: `portfolio_direction_cap_enabled: bool = True`,
  `portfolio_direction_cap_pct: float = 0.70`,
  `portfolio_direction_cap_warn_pct: float = 0.60`,
  `portfolio_direction_cap_min_positions: int = 3`,
  `portfolio_direction_cap_opposite_ratio_threshold: float = 2.0`.
- New events: `PORTFOLIO_CONCENTRATION_CHECK`, `PORTFOLIO_CAP_HIT`,
  `PORTFOLIO_CAP_WARN`, `PORTFOLIO_DIRECTION_PERMITTED`.

### Sequencing

R1 (ALPHA) ships first. After Phase 4 verification passes, R2 plus R3
(BETA combined) ships. After their Phase 4 verification passes, R4
(GAMMA) ships last. Rationale in DELTA 02.

## Combined risk register

1. **R-1 — ALPHA propagation bug**: assembler may populate
   `trade_direction` from the wrong analysis object or under a different
   conditional than `setup_type`. Mitigation: ALPHA's 3 new tests +
   verification query V5 validate 100% match between `XRAY_CLASSIFY` and
   `StructuralData.trade_direction`.
2. **R-2 — BETA bail logic inverted**: a sign error in the structural-ratio
   comparison could relax the lock in the wrong direction. Mitigation:
   `APEX_LOCK_DECISION_EXPLAINED` event makes the verdict + ratio + trade_direction
   auditable; ~5 unit tests cover both directions explicitly.
3. **R-3 — BETA WR window pollution**: per-direction WR computed from the
   trade_log can be skewed by pre-fix biased history. Mitigation: BETA
   decision includes 200-trade window (matches COMPLETE_FINDINGS baseline);
   floor 2.0 / ceiling 15.0 caps prevent extreme tuning; cold-start fallback
   to legacy 10x when window has < 30 trades.
4. **R-4 — BETA double-feedback loop**: R2's structural consultation and
   R3's WR-aware threshold are not coupled, but both react to the same
   underlying pipeline. A sequence where R2 fires for narrow technical
   reason while R3's derived threshold is at floor 2.0 could over-relax.
   Mitigation: R2 thresholds are static (asymmetric 10x / 3x); they do not
   self-tune. The two layers compose without redundancy.
5. **R-5 — GAMMA mono-trending over-permit**: in a genuinely mono-bearish
   market with no counter setups, GAMMA's aim-conditional cap permits
   every Sell entry. The cascade-class outcome may still occur. Mitigation:
   GAMMA still emits `PORTFOLIO_CAP_HIT` with verdict=permitted_mono_trending
   so the operator sees the events. If observed, operator can lower the
   ratio threshold (2.0) or remove the aim-conditional branch entirely
   (revert to Design A behavior).
6. **R-6 — GAMMA min-positions floor masks small portfolio cascades**:
   `min_positions=3` means N=2 portfolios at 100% concentration are not
   blocked. Real-world: a $200 portfolio with 2 Sells at 100% could still
   cascade. Mitigation: the cascade-class risk requires N >= 3 to be
   meaningful (one position cannot self-cascade); the operator can tune
   the floor down to 2 if N=2 cascades materialize.
7. **R-7 — Settings.py merge conflict**: append-only conflict between
   BETA's fields and GAMMA's fields. Mitigation: orchestrator resolves
   in sequence order (already specified in DELTA 02).
8. **R-8 — Test fixture break on existing tests**: existing
   `StructuralData(...)` fixtures may fail if they use positional args
   and rely on a specific field count. Mitigation: ALPHA Phase 3 audits
   each existing test for positional usage; conversion to keyword
   arguments is mechanical.
9. **R-9 — Integration test cross-merge fragility**: the three
   cross-merge integration tests (test_alpha_beta_gamma_e2e_*) need
   fixtures that exercise the full pipeline. Mitigation: authored at
   GAMMA Phase 3 when all three fixes are present. Run on the integration
   tip before final merge.
10. **R-10 — Live trial under-load**: 24-hour trial may not surface
    cascade-class events if regime is mono-trending. Mitigation: if the
    24-hour log shows no `PORTFOLIO_CAP_HIT` with
    `verdict=blocked_aim_conditional`, operator extends trial to 72
    hours OR runs replay against the 2026-05-16 log slice with R4 active
    to confirm cascade-prevention behavior.
11. **R-11 — Cross-agent timing drift**: if ALPHA Phase 4 verification
    takes 24+h and operator pre-merges BETA before verification completes,
    BETA's consumer reads a field that was never confirmed. Mitigation:
    sequencing is sequential by operator decision; BETA's Phase 3 cannot
    start until ALPHA Phase 4 GO is recorded.
12. **R-12 — Brain over-rotation when cap blocks**: after CHECK 15
    rejects a Sell, the brain's next CALL_A cycle may pick the SAME coin
    in the SAME direction (cap state is opaque to the brain). Mitigation:
    the brain prompt could include cap state in a future Phase 5
    enhancement; for now, the cap simply rejects and the brain self-rotates
    naturally as the universe coin order shifts.

## Combined verification criteria (drawn from spec Part J)

### Per-agent verification criteria

ALPHA (after R1 ships):

- V1: `grep "XRAY_CLASSIFY_SUMMARY |" workers.log | tail -50` shows new
  `trade_dir_long=N trade_dir_short=N counter_count=N` fields
- V2: `grep "APEX_DIR_LOCK |" workers.log` lines include
  `trade_direction=` field
- V3: counter setups no longer blocked at APEX (`grep "APEX_DIR_LOCK |"
  | grep "counter" | grep "locked=False"` returns > 0 events)
- V4: brain Buy directive count rises (target ~30%+ Buy directive share)
- V5: 100% of `bullish_fvg_ob_counter` events show
  `trade_direction=long suggested_direction=short`

BETA (after R2+R3 ships):

- `APEX_DIR_LOCK` event count drops from 80 to 50-65 per 5-hour session
- `APEX_LOCK_DECISION_EXPLAINED` events emit with
  `verdict={fired, bailed_structural}` distribution sensible
- `XRAY_FLIP_SUPPRESSED_BY_LOCK` count drops to 0-2 (was 8)
- `XRAY_OVERRIDE_LOCK` count rises to 10-14 (was 6)
- `XRAY_OVERRIDE_RATIO_DETAIL` events emit with derived_threshold in [2.0,
  15.0] range
- Sell/Buy directive ratio improves to 60-75% Sell (was 89%)

GAMMA (after R4 ships):

- `PORTFOLIO_CONCENTRATION_CHECK` emits every gate run
- `PORTFOLIO_CAP_HIT` count 0-5 per 24h
- `PORTFOLIO_CAP_HIT` events include both
  `verdict=blocked_aim_conditional` AND
  `verdict=permitted_mono_trending` variants
- No hour shows 5+ same-direction opens (SQL cascade-window query)
- Day-level direction distribution closer to 50/50 in mixed markets

### Integrated verification (after all three ship)

- 72-hour trial showing combined log signatures from DELTA 03
- BSBUSDT-class trades enter Buy (no `XRAY_FLIP_SUPPRESSED_BY_LOCK` for
  those signatures)
- Cascade-class events (>= 5 same-direction in 8 minutes at >= 70%
  concentration) do not occur
- All five aim-bias questions answer YES per the spec's Part J checklist

## Estimated implementation effort per phase

- **ALPHA Phase 3**: 2-4 hours of focused work. One new dataclass field, one
  assembler assignment, one structure_worker log line, three tests.
- **ALPHA Phase 4**: 24-hour live trial. Operator-driven, ~30 min review.
- **BETA Phase 3**: 6-10 hours. R2 (2-3 hours: lock function modification, 2
  settings, 5 unit tests) plus R3 (3-5 hours: dynamic threshold derivation,
  4 settings, 6 unit tests, fallback path) plus integration verification
  (~1 hour). 4 atomic commits per BETA synthesis.
- **BETA Phase 4**: 24-hour live trial + 1 hour review.
- **GAMMA Phase 3**: 4-6 hours. CHECK 15 implementation (1 hour), helper
  (30 minutes), 5 settings (15 minutes), 12 tests (1.5-2 hours), 1
  integration test (1 hour), cross-merge integration tests (~1 hour).
- **GAMMA Phase 4**: 24-hour live trial + 1 hour review.
- **Final integrated 72-hour trial**: 3 days elapsed; minimal active operator
  involvement during the trial.

Total elapsed wall-clock to ship all four fixes: 4-5 days assuming
single-operator-attention; can compress to 3 days if trials run in
parallel with Phase 3 of the next agent (NOT recommended given
operator's sequencing decision).

## Critical files modified per agent

### ALPHA (R1)

- `src/apex/models.py:258` — add `trade_direction: str = ""` to `StructuralData`
- `src/apex/assembler.py:737` — populate `sd.trade_direction = analysis.trade_direction`
- `src/workers/structure_worker.py:273-277` — extend
  `XRAY_CLASSIFY_SUMMARY` with new fields; add `XRAY_DIRECTION_SPLIT` line
- `tests/test_apex_assembler.py` (new or existing) — 3 propagation tests
- `tests/test_structure_worker_observability.py` (new or existing) — 1 observability test

### BETA (R2 + R3)

- `src/apex/optimizer.py:1265-1311` — modify `_check_direction_lock` to
  consult `structural_data.rr_long`, `rr_short`, `trade_direction`
- `src/apex/optimizer.py:1486-1488` — review the volatile bail-out in
  `_enforce_flip_confidence` (operator note in BETA synthesis line 100;
  retained only when lock fired)
- `src/workers/strategy_worker.py:1671-1717` — modify override threshold
  resolution to derive from per-direction WR
- `src/config/settings.py:831` — keep legacy
  `xray_lock_override_ratio_threshold = 10.0` as fallback; append new
  WR-aware fields
- `src/config/settings.py:2203-2204` — leave the existing
  `apex_min_flip_confidence_*` fields untouched (they are for ranging/dead
  regimes; BETA's changes are for trending/volatile)
- `tests/test_apex_direction_lock_structural.py` (new) — 5-8 tests for
  Option B
- `tests/test_xray_override_wr_aware.py` (new) — 4-6 tests for Option E

### GAMMA (R4)

- `src/apex/gate.py` — insert CHECK 15 between line 647 (CHECK 14 end)
  and line 672 (final return); ~40 LOC
- `src/core/trade_coordinator.py:1869` — add `get_direction_counts()`
  method (~12 LOC) before `cleanup_stale`
- `src/config/settings.py` (APEXSettings) — append five new fields
- `tests/test_apex_gate_concentration.py` (new) — 6 tests for CHECK 15
- `tests/test_phase9/test_trade_coordinator.py` (new or appended) — 6
  tests for the helper
- `tests/test_apex_pipeline_integration.py` — append 1 cascade-prevention
  integration test
- `tests/test_alpha_beta_gamma_e2e.py` (new) — 3 cross-agent integration
  tests (authored when GAMMA's Phase 3 begins on the integration tip)

## Operator gate request

The operator's Phase 2.7 decision is one of:

1. **APPROVE** — proceed with Phase 3 sequenced as R1 -> R2+R3 -> R4.
   ALPHA starts Phase 3 implementation on `fix/r1-xray-counter-inversion`
   immediately. Orchestrator drives the sequence; DELTA's verification
   criteria gate each transition.

2. **MODIFY** — operator overrides one or more decisions before Phase 3
   starts. Common modification candidates per the agent decision records:
   - Lower GAMMA cap from 70% to 60% (more aggressive balancing)
   - Raise GAMMA cap to 80% (pure back-stop, less rejection)
   - Reduce BETA R3 to Option C (static asymmetric thresholds, no WR
     auto-tuning) if the WR-feedback loop risk is unacceptable
   - Skip BETA R3 entirely and keep R3 as future work
   - Defer GAMMA entirely (R1+R2 only) and revisit R4 after observing
     post-R1+R2 cascade frequency

3. **FURTHER INVESTIGATION** — operator requests deeper Phase 1.5 work
   on a specific concern. Candidates:
   - Per-direction WR distribution analysis before BETA Option E ships
     (verify the WR window is not pre-fix-biased)
   - Replay simulation of 2026-05-16 with all four fixes applied (high
     confidence prediction; low marginal value)
   - Brain-prompt cap-state context (deferred to Phase 5 per GAMMA
     synthesis)

DELTA recommends APPROVE. The investigation evidence is strong, the
designs are conservative (each preserves existing semantics; only adds
or relaxes), the sequencing is dependency-correct, the rollback plans
are clean, and the verification criteria are observable.

## Summary table — what ships, where, when

| Fix | Agent | Branch | File changes | New tests | Effort (h) | Phase 4 trial |
|---|---|---|---|---|---|---|
| R1 (E + D) | ALPHA | fix/r1-xray-counter-inversion | models.py, assembler.py, structure_worker.py | 4 | 2-4 | 24h |
| R2 (B static) | BETA | fix/r2-r3-apex-direction-lock | optimizer.py, settings.py | 5-8 | 2-3 | 24h |
| R3 (E WR-tune) | BETA | (same as R2) | strategy_worker.py, settings.py | 4-6 | 3-5 | 24h (combined with R2) |
| R4 (C aim-cond) | GAMMA | fix/r4-portfolio-direction-cap | gate.py, trade_coordinator.py, settings.py | 12-13 | 4-6 | 24h |
| Integrated trial | DELTA | (integration tip) | none | 3 cross-merge | 1 | 72h |

The integrated trial caps the Phase 4 cycle. Operator approves at Phase
4.7 gate (final go for full rollout / handover to operational tier).
