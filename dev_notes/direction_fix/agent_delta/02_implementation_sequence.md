# DELTA 02 — Implementation Sequence

Agent DELTA Phase 2.5. Records the operator's pre-decided sequencing
(R1 -> R2 plus R3 -> R4), explains the cross-agent rationale for each
position in that order, defines per-fix go and no-go criteria, lists
rollback steps, and confirms the branch strategy.

## Why R1 (ALPHA) goes first

ALPHA Option E plumbs the `trade_direction` field from
`StructuralAnalysis` through `src/apex/assembler.py:737` into a new
`StructuralData.trade_direction` field on `src/apex/models.py:258`. This
field is consumed by:

- BETA's R2 Option B in `_check_direction_lock` at
  `src/apex/optimizer.py:1265-1311`. Without ALPHA's plumbing, BETA's
  consumer reads a field that does not exist on `StructuralData`.
- GAMMA's R4 Design C aim-conditional cap at `src/apex/gate.py` CHECK 15.
  Without ALPHA's plumbing, GAMMA's `if trade_direction == "long":` check
  always fails (the field never exists) and the aim-conditional path
  always falls back to the structural-ratio check.

ALPHA must ship FIRST and pass Phase 4 verification before BETA's Phase 3
begins. The verification confirms:

- `dataclasses.asdict(structural_data)` contains the new
  `trade_direction` key
- Live `XRAY_CLASSIFY_SUMMARY` lines include the new `trade_dir_long`,
  `trade_dir_short`, `counter_count` fields (Option D observability)
- The new `XRAY_DIRECTION_SPLIT` per-tick line emits

ALPHA Option D (observability) is bundled with Option E in the same
branch (`fix/r1-xray-counter-inversion`) — they ship together because
they touch adjacent code paths in `structure_worker.py` and assembler.

## Why R2 plus R3 (BETA) go second together

BETA's two changes (Option B for R2, Option E for R3) operate at different
layers (optimizer vs strategy_worker) but share the same data source
(`package.structural_data` and the trade_log per-direction WR
respectively). Operator approved the combined branch
`fix/r2-r3-apex-direction-lock`.

R2 fires FIRST in the per-trade chain (lock decision, pre-parse). R3 fires
SECOND (override of an already-locked direction, post-parse). The two
compose: when R2 relaxes the lock, R3 has nothing to override; when R2
fires the lock, R3 governs whether the structural-RR override clears the
threshold.

R3 Option E reads per-direction WR from the trade_log. The trade_log is
populated independently of R1's `trade_direction` field — Option E does
NOT depend on R1's plumbing. So BETA's R3 portion could theoretically
ship without R1, but R2 cannot. The operator chose to ship both R2 and
R3 on the same branch for efficiency.

BETA Phase 3 begins ONLY after ALPHA Phase 4 verification passes:

- ALPHA Phase 4 confirms `StructuralData.trade_direction` populates
  correctly in production
- ALPHA Phase 4 confirms the observability lines emit
- BETA's branch is created off the merged R1 commit (not the HEAD
  7320266 the branch was pre-registered against; orchestrator rebases
  BETA's branch onto the integration tip before BETA Phase 3 starts)

## Why R4 (GAMMA) goes last

GAMMA Design C is an aim-conditional cap. The cap fires only when the
opposite direction is viable AS DETECTED BY XRAY (`trade_direction` field
on `StructuralData`, which is ALPHA-plumbed) OR by the structural ratio
(`rr_long`, `rr_short` on `StructuralData`, which already exist).

If GAMMA ships before R1+R2+R3 verify in production, the cap fires
constantly because the brain produces 89% Sell directives (the 2026-05-16
baseline) and the portfolio rapidly concentrates. With R4 alone, 18 of 19
Sell entries are blocked at the 70% cap (per GAMMA deliverable 02
cascade simulation). Trade frequency drops dramatically.

If GAMMA ships AFTER R1+R2+R3 verify, the brain's directive distribution
is more balanced (target: 50/50 in mixed markets), the portfolio
concentrates less often, and GAMMA's cap is a true back-stop that fires
rarely.

GAMMA Phase 3 begins ONLY after BETA Phase 4 verification passes:

- BETA Phase 4 confirms the lock fires less often (target ~50-65 events
  per 5-hour session, down from 80)
- BETA Phase 4 confirms the override threshold auto-tunes from WR data
- BETA Phase 4 confirms the Sell/Buy directive ratio improves toward
  balance (target 30-40% Buy, up from 14%)

GAMMA's branch is rebased onto the integration tip (R1+R2+R3 merged)
before GAMMA Phase 3 starts.

## Per-fix go and no-go criteria

### ALPHA Phase 4 go and no-go

GO criteria (all must hold):

- `XRAY_CLASSIFY_SUMMARY` lines in live `data/logs/workers.log` contain
  `trade_dir_long=N trade_dir_short=N counter_count=N` (ALPHA verification
  query V1)
- `XRAY_DIRECTION_SPLIT` events emit per tick at INFO
- A representative APEX_DIR_LOCK event includes the new
  `trade_direction=` field sourced from `StructuralData.trade_direction`
  (verification query V2)
- ALPHA test additions all pass: ~5 new tests covering propagation and
  observability
- No regression in existing tests under `tests/test_setup_classifier_*`
  and `tests/test_structure_engine_*`

NO-GO triggers:

- `StructuralData.trade_direction` is empty when an associated
  `XRAY_CLASSIFY` line shows non-empty `trade_direction` — propagation
  broken
- Production log shows the new fields but values are systematically
  inverted or stale — assembler bug
- Existing structure-engine or counter-setup tests fail — fixture
  break-through

### BETA Phase 4 go and no-go

GO criteria:

- `APEX_DIR_LOCK` event count drops from baseline 80 to 50-65 in a
  5-hour session
- `APEX_LOCK_DECISION_EXPLAINED` events emit with `verdict={fired,
  bailed_structural}` distribution sensible (estimate: 50-60% fired,
  40-50% bailed)
- `XRAY_FLIP_SUPPRESSED_BY_LOCK` count drops toward 0 (was 8 on May 16)
- `XRAY_OVERRIDE_LOCK` count rises to ~10-14 (was 6)
- `XRAY_OVERRIDE_RATIO_DETAIL` events show derived threshold ranging
  between 2.0 (floor) and 15.0 (ceiling)
- Sell-share of STRAT_DIRECTIVE entries falls from 86% to 60-75% (target)
- BETA test additions all pass: ~9-14 new tests
- No regression in BETA-touched existing tests
  (`test_apex_direction_lock.py`, `test_apex_lock_propagation.py`,
  `test_j3_xray_lock_override.py`)

NO-GO triggers:

- Lock fires more often, not less — Option B bail logic inverted
- Override threshold floors at 2.0 for every trade — WR computation broken
- Override threshold ceilings at 15.0 for every trade — WR computation
  not consuming data
- Sell-share drops below 30% (over-flipping; lock too weak)
- Existing lock-propagation tests fail — semantic change leaked

### GAMMA Phase 4 go and no-go

GO criteria:

- `PORTFOLIO_CONCENTRATION_CHECK` emits on every gate run at INFO
- `PORTFOLIO_CAP_HIT` count is low (0-5 per 24h) — back-stop behavior
- `PORTFOLIO_CAP_WARN` count is moderate (0-20 per 24h) — warn-band visible
- `PORTFOLIO_DIRECTION_PERMITTED` emits when appropriate
- For at least one CAP_HIT instance, fields show `verdict=blocked_aim_conditional`
  with non-empty `trade_direction` or non-trivial `rr_long`/`rr_short`
  (proving the aim-conditional path engaged)
- SQL cascade-window check (post-fix) shows no hour with 5+ same-direction
  opens
- Day-level direction distribution closer to 50/50 in mixed markets
- GAMMA test additions all pass: ~12 new tests + 1 integration
- Cascade integration test passes (simulates 14:45 window, asserts blocks)
- No regression in `test_apex_pipeline_integration.py`,
  `test_apex_sell_bias_gates.py`

NO-GO triggers:

- `PORTFOLIO_CAP_HIT` count exceeds 10 per 24h — cap too aggressive (R1+R2+R3
  not balancing brain output as expected)
- All CAP_HIT events show `verdict=blocked_aim_conditional` without
  `trade_direction` being set — Option E plumbing leaked
- `PORTFOLIO_CAP_HIT` shows `verdict=permitted_mono_trending` exclusively —
  aim-conditional path not engaging
- Trade frequency drops more than 30% from pre-fix daily baseline —
  cap engaging too aggressively

## Rollback plan per branch

### ALPHA rollback

If ALPHA Phase 4 fails:

1. Revert the merge of `fix/r1-xray-counter-inversion` to integration
   tip via `git revert -m 1 <merge_sha>` (creates a forward revert
   commit; preserves history)
2. Verify the production log returns to pre-fix `XRAY_CLASSIFY_SUMMARY`
   format
3. BETA and GAMMA branches do NOT need rollback because they had not
   shipped yet (sequencing prevents this)
4. Root-cause the failure on the original ALPHA branch; re-attempt
   Phase 3 with a corrected approach

### BETA rollback

If BETA Phase 4 fails after ALPHA shipped:

1. Revert the merge of `fix/r2-r3-apex-direction-lock` via `git revert
   -m 1`
2. ALPHA's plumbing stays in place — `StructuralData.trade_direction`
   continues to populate but is unconsumed by APEX (returns to pre-fix
   behavior where `_check_direction_lock` is regime-only)
3. GAMMA does not roll back because GAMMA had not shipped yet
4. The integration tip after BETA revert is `ALPHA only` — a stable
   intermediate state

### GAMMA rollback

If GAMMA Phase 4 fails after BETA shipped:

1. Revert the merge of `fix/r4-portfolio-direction-cap` via `git revert
   -m 1`
2. CHECK 15 is removed; gate returns to 14 checks
3. The `TradeCoordinator.get_direction_counts()` helper revert is part
   of the same merge revert
4. ALPHA and BETA stay shipped — the integration tip is `ALPHA + BETA`
   — a stable intermediate state

### Combined rollback

If all three need rollback (e.g., a unifying production bug surfaces
late):

1. Revert the three branches in reverse merge order (GAMMA -> BETA ->
   ALPHA)
2. The integration tip returns to HEAD 7320266
3. Re-investigate at Phase 1 or Phase 2 level before retry

## Branch strategy

### Branch base

All three branches were created off `HEAD 7320266` at Phase 0:

- ALPHA: `fix/r1-xray-counter-inversion`
- BETA: `fix/r2-r3-apex-direction-lock`
- GAMMA: `fix/r4-portfolio-direction-cap`

### Merge order

Sequential as per operator's decision:

1. Merge `fix/r1-xray-counter-inversion` into integration line (after
   ALPHA Phase 4 GO).
2. Rebase `fix/r2-r3-apex-direction-lock` onto the new tip; merge after
   BETA Phase 4 GO.
3. Rebase `fix/r4-portfolio-direction-cap` onto the new tip; merge after
   GAMMA Phase 4 GO.

### Integration line

The integration line is the operator-chosen base. The default candidate
is `fix/j1-orphan-positions` (the same base Phase 0 branched from), but
the orchestrator may pick a fresh branch (e.g., `fix/direction-bias-integrated`)
to host the merges. Operator confirms at the moment of integration.

### Rebase vs merge

Each subsequent branch is REBASED (not merge-committed) onto the new
integration tip before its own merge, so the eventual history shows three
clean fast-forward-eligible merge points rather than a tangled merge graph.

### Conflict resolution at merge

Only `src/config/settings.py` has a deterministic textual append-only
conflict between BETA and GAMMA. Orchestrator resolves manually by
keeping both field groups (BETA's first, GAMMA's after, both inside the
same `APEXSettings` dataclass).

### Tests at each merge

After each merge, the orchestrator runs the affected agent's test slice
plus a smoke test on the broader test suite. Phase 4 verification (live
trial) follows on the integration tip with the merged branch active.

## Cross-merge integration tests

Three tests verify the full pipeline behavior across all three fixes
(authored at GAMMA Phase 3 or DELTA-driven Phase 5):

1. `test_alpha_beta_gamma_e2e_counter_setup_buy_passes` —
   bullish_fvg_ob_counter trigger; brain picks Buy; BETA's lock does
   not fire (trade_direction = long); APEX outputs Buy; gate CHECK 15
   does not block (Buy is opposite of dominant direction); order is
   placed.

2. `test_alpha_beta_gamma_e2e_mono_trending_cap_permits` — portfolio is
   85% Sell; XRAY confirms `trade_direction="short"` and `rr_short >>
   rr_long`; cap evaluates `opposite_viable=False`; emits
   `PORTFOLIO_CAP_HIT` with `verdict=permitted_mono_trending`; trade
   executes.

3. `test_alpha_beta_gamma_e2e_aim_conditional_cap_fires` — portfolio is
   75% Sell; XRAY shows `trade_direction="long"` (counter setup), but
   brain (against the structural counter) picked Sell; BETA's lock did
   not relax because the ratio is 2.5x (in the 2.0-3.0 band); cap
   evaluates `opposite_viable=True`; emits `PORTFOLIO_CAP_HIT` with
   `verdict=blocked_aim_conditional`; trade is rejected.

These integration tests are authored at GAMMA Phase 3 (the latest agent
holding shared scope) and exercise the merged ALPHA+BETA+GAMMA pipeline.

## Estimated implementation effort (clock time)

- ALPHA Phase 3 implementation: 2-4 hours (per ALPHA synthesis line 285)
- ALPHA Phase 4 verification: 24-hour live trial + 30 min review
- BETA Phase 3 implementation: 6-10 hours (R2 + R3 combined, larger
  feature; 4 atomic commits per BETA synthesis line 150)
- BETA Phase 4 verification: 24-hour live trial + 1 hour review
- GAMMA Phase 3 implementation: 4-6 hours (gate CHECK 15 + helper +
  ~12 tests)
- GAMMA Phase 4 verification: 24-hour live trial + 1 hour review

Total elapsed (sequential per operator decision): 3-4 days for
implementation + 3 days of live trials. Operator can compress trials if
production data converges faster than 24h.
