# DELTA 01 — Integration Analysis

Agent DELTA Phase 2.5. Identifies and resolves all cross-agent conflicts
between ALPHA Option E plus D (R1), BETA Option B plus E (R2 plus R3), and
GAMMA Design C (R4). Each conflict is classified, traced to the originating
artifact, and assigned a specific resolution.

## Code-level conflicts

### One file is touched by two agents

`src/apex/assembler.py` is touched only by ALPHA Option E (line 737, adding the
`sd.trade_direction = analysis.trade_direction` assignment). BETA reads
`package.structural_data` inside `optimizer.py`; BETA does not edit
`assembler.py`. GAMMA reads `package.structural_data` inside `gate.py`;
GAMMA also does not edit `assembler.py`. So although three agents depend on
the field, only ALPHA writes the producer. There is no code-edit conflict
in `assembler.py`.

`src/apex/models.py` is touched only by ALPHA (adding the
`trade_direction: str = ""` field on `StructuralData`). BETA and GAMMA
both read this field but neither edits the model definition. No conflict.

`src/apex/optimizer.py` is touched only by BETA (R2 Option B modifies
`_check_direction_lock` at lines 1265-1311 and the post-parse override at
lines 359-371). ALPHA does not edit `optimizer.py`. GAMMA does not edit
`optimizer.py`. No conflict.

`src/workers/strategy_worker.py` is touched only by BETA (R3 Option E
modifies the override block at lines 1671-1717). No conflict.

`src/apex/gate.py` is touched only by GAMMA (adding CHECK 15 between the
existing CHECK 14 at line 647 and the final `return trade` near line 672).
No conflict.

`src/core/trade_coordinator.py` is touched only by GAMMA (adding the
`get_direction_counts()` helper near line 1869 before `cleanup_stale`). No
conflict.

`src/config/settings.py` is touched by BETA (new `xray_lock_override_wr_*`
fields and the asymmetric `apex_lock_structural_override_ratio_*` fields)
and by GAMMA (new `portfolio_direction_cap_*` fields in `APEXSettings`).
Both agents only append new fields; neither modifies an existing field.
Append-only edits on the same file from two branches will produce a
predictable merge conflict in `settings.py` near the bottom of the
`APEXSettings` dataclass when both branches merge into HEAD. This is a
standard textual merge that the orchestrator resolves by keeping both
field groups in the order ALPHA -> BETA -> GAMMA (no agent owns
`settings.py` exclusively).

`src/workers/structure_worker.py` is touched only by ALPHA Option D
(extending XRAY_CLASSIFY_SUMMARY at lines 273-277 and adding
XRAY_DIRECTION_SPLIT). No conflict.

`src/brain/strategist.py` is NOT touched by ALPHA Option E (the brain
prompt already reads `trade_direction`). Option C was deferred. No
conflict.

### Resolution

- ALPHA, BETA, GAMMA all live on distinct branches off HEAD 7320266.
- Only `src/config/settings.py` produces a deterministic merge conflict
  (append-only). Orchestrator resolves by accepting both field groups in
  sequence order R1 -> R2/R3 -> R4 when merging each branch into the
  integration line.
- No source-file edit conflict requires per-agent coordination beyond the
  sequencing already decided.

## Data-flow conflicts

### The new `StructuralData.trade_direction` field

ALPHA Option E adds `trade_direction: str = ""` to the `StructuralData`
dataclass at `src/apex/models.py:258`. Three consumers read it:

1. BETA R2 Option B in `_check_direction_lock` at `optimizer.py:1265-1311`.
   BETA's specification (decision record line 11-19) reads
   `structural_data.rr_long`, `rr_short`, and `trade_direction`. The
   trade_direction is treated as one of two signals (alongside the ratio)
   that bail the lock out. BETA's expected field value: `"long"`, `"short"`,
   or `""` (empty fallback to ratio-only logic).

2. GAMMA R4 Design C in CHECK 15 at `gate.py` (between line 647 and 672).
   GAMMA's specification (decision record line 39-58) reads
   `package.structural_data.trade_direction`, `rr_long`, `rr_short`. GAMMA
   uses `trade_direction` to determine `opposite_viable` for the
   aim-conditional cap. Expected field value: same `"long"` / `"short"` /
   `""`.

3. ALPHA Option D observability writes XRAY_CLASSIFY_SUMMARY but does NOT
   read `StructuralData.trade_direction` (it reads the upstream
   `analysis.trade_direction` directly in `structure_worker.py`). No
   contention here.

### Shape contract

The shape that all three consumers expect is identical: a string with one of
three values: `"long"`, `"short"`, or `""`. ALPHA's assembler will populate
it from `analysis.trade_direction` which `structure_engine.py:1189` and
`:1211` already write as `"long"` or `"short"`; defaults to `""` when no
counter setup classified.

ALPHA's deliverable 04 (Option E mechanism, lines 276-285) confirms
`trade_direction: str = ""` default. BETA's consumption is defensive
(falls back to ratio when `trade_direction == ""`). GAMMA's consumption
is also defensive (the `if trade_direction and trade_direction != "":`
guard at decision record line 47-50).

### Resolution

- Shape: string, one of `{"long", "short", ""}`. All three consumers
  agree.
- Producer (ALPHA) MUST populate from `analysis.trade_direction` exactly,
  with no transformation. The string-vs-enum issue noted in `setup_type`
  propagation (assembler.py:748-753 wraps the enum in `str(...).value`)
  does NOT apply here — `analysis.trade_direction` is already a string in
  `structure_engine.py:1189`.
- BETA and GAMMA both treat the empty string as "fall back to ratio."
  This is a coherent contract.

## Design-level conflicts

### BETA's lock relaxes; GAMMA's cap is aim-conditional

The operator picked GAMMA Design C (aim-conditional cap that fires only
when the opposite direction looks viable). This makes GAMMA's cap less
aggressive than the originally recommended Design A. GAMMA fires only
when `trade_direction` says opposite OR `rr_opposite/rr_chosen >= 2.0`.

BETA Option B relaxes the lock whenever `rr_long/rr_short >= 3.0` for the
Sell-to-Buy direction. This means BETA already lets the trade pass when
structure favors the opposite direction.

The interaction: when BETA's lock relaxes (because structure favors the
opposite direction), BETA permits the brain's Buy directive to stand and
APEX outputs Buy. The Buy directive then reaches GAMMA's CHECK 15. By
that point the portfolio may or may not be concentrated. If concentrated
in Sell at 70%+ and the new directive is Buy, GAMMA's cap does NOT fire
(Buy is the under-represented direction). The Buy trade executes.
Cap-aware behavior is consistent across both agents.

What if BETA's lock does NOT relax (regime aligns, structure aligns, lock
fires Sell), brain produces Sell, and GAMMA sees portfolio at 70% Sell?
GAMMA's aim-conditional logic asks: is opposite direction viable? Reads
`trade_direction` — if `"short"` and ratio supports short, opposite is
NOT viable. Cap does NOT fire. Sell goes through. Portfolio continues
piling Sell — a mono-bearish market gets exploited.

If instead the brain's Sell came with `trade_direction="long"` (counter
setup), BETA's lock would have already relaxed at the structural-ratio
check; the trade would be Buy by the time it reaches GAMMA, and CHECK 15
would not block. So GAMMA's aim-conditional logic engages only on the
narrow case where BETA's lock fired AND `trade_direction` differs OR
`rr_opposite/rr_chosen >= 2.0` but BETA's lock still fired (because the
ratio is between 2.0 and 3.0). That is the design-intentional band:
"BETA says structure is mildly opposite, lock fires; GAMMA says portfolio
is 70% concentrated AND structure is mildly opposite — block this entry."

### Resolution

The two layers do not contend. BETA's lock acts at the per-trade structural
decision. GAMMA's cap acts at the portfolio-shape decision. The narrow
overlap band (BETA's structural ratio in 2.0-3.0 range where its lock
still fires) is where GAMMA's aim-conditional cap adds genuine value:
the trade is structurally borderline, portfolio is concentrated, so the
cap rejects. This is the intended composition.

GAMMA's `portfolio_direction_cap_opposite_ratio_threshold` (default 2.0)
is intentionally LOWER than BETA's lock-relax threshold (3.0 for
Sell-to-Buy). This ensures GAMMA can fire in the band BETA does not
relax. The operator can tune both together if production shows the band
needs widening.

## Observability conflicts

### New log event names

ALPHA Option E + D adds these new events:
- `XRAY_DIRECTION_SPLIT` (new per-tick line, structure_worker)
- New fields on existing `XRAY_CLASSIFY_SUMMARY`: `trade_dir_long`,
  `trade_dir_short`, `counter_count`

BETA Option B + E adds these new events:
- `APEX_LOCK_DECISION_EXPLAINED` (new, optimizer)
- `XRAY_OVERRIDE_RATIO_DETAIL` (new, strategy_worker)

GAMMA Design C adds these new events:
- `PORTFOLIO_CONCENTRATION_CHECK` (new, gate)
- `PORTFOLIO_CAP_HIT` (new, gate)
- `PORTFOLIO_CAP_WARN` (new, gate)
- `PORTFOLIO_DIRECTION_PERMITTED` (new, gate)

### Namespace conflicts

The `XRAY_*` namespace is jointly owned by ALPHA (`XRAY_CLASSIFY_*`,
`XRAY_DIRECTION_SPLIT`) and BETA (`XRAY_OVERRIDE_*`). The two agents use
sub-prefixes that do not collide.

The `APEX_*` namespace is jointly owned by existing code (`APEX_DIR_LOCK`,
`APEX_FLIP_*`, etc.) and BETA's new `APEX_LOCK_DECISION_EXPLAINED`. The
new event uses a unique sub-prefix and does not collide.

The `PORTFOLIO_*` namespace is GAMMA-exclusive. No other agent emits in
this namespace.

### Resolution

No naming collision. All new events have unique prefixes or unique full
names. Phase 0 reconnaissance (lines 162-165) confirmed `PORTFOLIO_*` is
a clean namespace pre-fix.

## Test conflicts

### Existing test fixtures and the new StructuralData field

ALPHA's new `trade_direction` field defaults to `""`, so existing tests
that construct `StructuralData(...)` without naming the field will get
empty-string default. These tests pass unchanged. Tests that assert on
the full `__dict__` or `dataclasses.asdict(...)` of `StructuralData`
will see the new field; those need updating.

Phase 0 reconnaissance lists existing tests for each agent's surface:

- ALPHA scope: `tests/test_setup_classifier_counter.py`,
  `tests/test_structure_engine_alignment_broaden.py`,
  `tests/test_definitive_pipeline_e2e.py`. None construct
  `StructuralData` directly — they exercise `structure_engine.classify_setup`.
  No fixture break expected.

- BETA scope: `test_apex_direction_lock.py`, `test_apex_lock_propagation.py`,
  `test_j3_xray_lock_override.py`, `test_apex_flip_discipline.py`,
  `test_apex_pipeline_integration.py`. `test_apex_direction_lock.py`
  constructs `package` fixtures that may or may not include
  `structural_data`. If they do, fixtures need `trade_direction=""` (the
  default — no rewrite if using keyword args; required if using
  positional args).

- GAMMA scope: `tests/test_phase9/test_portfolio.py`,
  `tests/test_apex_pipeline_integration.py`,
  `tests/test_p6_layer3_gate_bybit_demo.py`. None construct
  `StructuralData` for the new field's sake; only the new CHECK 15 tests
  do.

### Test additions per agent

ALPHA Phase 3:
- 3 new tests in `tests/test_apex_assembler.py` (or new file) verifying
  trade_direction propagation
- Update existing counter-setup tests if fixtures use positional args

BETA Phase 3:
- 5-8 new tests in `tests/test_apex_direction_lock_structural.py` (new file)
  for Option B's structural-RR consultation
- 4-6 new tests in `tests/test_xray_override_wr_aware.py` (new file) for
  Option E's per-direction WR auto-tuning
- Updates to `test_apex_lock_propagation.py` if the `is_locked` plumbing
  changes; per BETA decision record line 31, the lock semantic is
  preserved, so this should not require updates

GAMMA Phase 3:
- 6 new tests in `tests/test_apex_gate_concentration.py` for CHECK 15
- 6 new tests in `tests/test_phase9/test_trade_coordinator.py` for the
  helper
- 1 integration test in `tests/test_apex_pipeline_integration.py` for
  cascade prevention

Total new tests: 25-30. No existing tests need behavior change; some
need fixture additions for the new field.

### Resolution

- Fixture-additions are append-only and do not produce conflicts.
- Each agent owns its own new test files.
- The `tests/test_apex_pipeline_integration.py` integration test sees
  changes from all three agents. The integration test file is shared but
  each agent adds independent test functions — no merge conflict in
  practice if function names are unique. Function names use a stable
  prefix per agent: `test_alpha_*`, `test_beta_r2_*`, `test_beta_r3_*`,
  `test_gamma_*`.

## Action items per conflict

1. `settings.py` append-only merge conflict. Resolution: orchestrator
   appends in sequence order (R1 -> R2 -> R3 -> R4) when integrating each
   branch.

2. `StructuralData.trade_direction` field. ALPHA produces from
   `analysis.trade_direction` with NO transformation. Field type is
   `str`, default `""`. BETA and GAMMA both read defensively (empty
   string -> fall back to ratio).

3. `tests/test_apex_pipeline_integration.py` integration test additions.
   Each agent prefixes its function names with agent identifier to avoid
   name collisions.

4. No code conflicts in source files beyond `settings.py`.

5. No event-name conflicts.

6. No design-conflict between BETA's lock-relax and GAMMA's
   aim-conditional cap. The 2.0 (GAMMA) vs 3.0 (BETA) threshold gap is
   intentional and useful.

7. No layer-doctrine conflict. GAMMA Design C was initially flagged as
   violating spec A.5 (gate reads L1B data) but ALPHA Option E places
   `trade_direction` on `StructuralData` (already a shared in-package
   field), so by the time the package reaches CHECK 15 the field is local
   to the package — no cross-layer call.
