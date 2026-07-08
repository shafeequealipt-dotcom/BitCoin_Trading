# Phase 0 — Reconnaissance Report

This document captures the read-only reconnaissance executed in plan mode for the three-agent direction-bias fix. It establishes the file:line ground truth that the spec was built on, flags drift between the spec's mental model and current code, and records the decisions made with the operator before any code change.

## Context

Spec source: `/home/inshadaliqbal786/IMPLEMENT_THREE_AGENT_DIRECTION_BIAS_FIX.md`
Evidence source: `dev_notes/direction_bias_investigation/COMPLETE_FINDINGS.md`
Live session source: `dev_notes/live_monitoring_20260516/FINDINGS.md`
Base SHA (HEAD at Phase 0 entry): `73202662235c77c25fd89b9d0092fa57550227c6`
Base branch: `fix/j1-orphan-positions`
Operator: Inshad (blind, uses screen reader; all reports use plain prose + h2/h3, no emoji)
Date: 2026-05-16

## Operator Decisions (Recorded)

- Pace: single-session compressed. Phase 0+1 in parallel via subagents; Phase 2/3/4 sequential per spec mandate. All operator gates honored.
- Branch base: HEAD `fix/j1-orphan-positions` (includes H1/H3/H4 already shipped — none touch direction surfaces).
- Investigation discretion: each agent is free to overturn the spec's diagnosis if Phase 1 evidence contradicts it (spec Rule 5).
- Out of scope: brain CLI internals, Bybit demo HTTP layer, Shadow adapter internals, regime.py (commit 6938c69 is in HEAD — confirmed), DB concurrency (J11), previously shipped J-series / Tier 1 / Tier 2 / observability / brain-enrichment, H1/H2/H6/H3/H4.

## Branch Plan

- Shared Phase 0 docs branch: `investigate/direction-fix-phase0` (current; docs only)
- ALPHA implementation branch: `fix/r1-xray-counter-inversion` (created off HEAD before Phase 3-1)
- BETA implementation branch: `fix/r2-r3-apex-direction-lock` (created off HEAD before Phase 3-2)
- GAMMA implementation branch: `fix/r4-portfolio-direction-cap` (created off HEAD before Phase 3-3)

The three fix branches are created up-front at Phase 0 close so each agent's scope is identifiable. Only the branch matching the agent currently in Phase 3 is checked out at any time. DELTA's sequence may reshuffle which goes first.

## ALPHA Scope Verification (R1 — XRAY Counter-Trade Inversion)

### File:Line Verification

- `src/analysis/structure/structure_engine.py:269-275` — MATCH. Inside `analyze()`: comment "Determine suggested direction from market structure" at 269; then `suggested_direction = ""`; then conditional "long" if uptrend else "short" if downtrend.
- `src/analysis/structure/structure_engine.py:1008-1309` — MATCH. `classify_setup()` decision tree. Docstring at 1008-1042 explicitly states: "For counter setups (BULLISH_FVG_OB_COUNTER / BEARISH_FVG_OB_COUNTER) it's the OPPOSITE of suggested (the counter-trade payoff)."
- Counter branches verified at:
  - `1166-1194` — `BULLISH_FVG_OB_COUNTER` branch; line 1189 sets `analysis.trade_direction = "long"` (inverts from suggested "short")
  - `1196-1216` — `BEARISH_FVG_OB_COUNTER` branch; line 1211 sets `analysis.trade_direction = "short"` (inverts from suggested "long")

### Drift Flag — Material (must be resolved by ALPHA Phase 1)

The spec describes R1 as: "These are bullish FVG/OB structures detected AGAINST a trending_down regime. The counter-trade logic collapses them to `suggested_direction=short`."

The actual code behavior (per docstring + classifier body): bullish_fvg_ob_counter setups invert `analysis.trade_direction` to `"long"` — the counter-trade payoff. `suggested_direction` is NOT mutated by `classify_setup`.

The spec's R1 diagnosis depends on which field actually reaches the brain prompt. Phase 0 consumer trace shows `suggested_direction` propagates to `apex/assembler.py:737` → `apex/models.py:258` (StrategyCall). `trade_direction` is consumed elsewhere. ALPHA Phase 1 must determine:
- Which field reaches the brain CALL_A prompt for new-trade decisions?
- Which field reaches the XRAY suggested_direction telemetry that COMPLETE_FINDINGS counted as 87% short?
- If `suggested_direction` does NOT include the counter inversion, what is the actual mechanism producing the 691 `bullish_fvg_ob_counter` → suggestion=short outcomes COMPLETE_FINDINGS claims?

If the spec's mechanism is wrong, ALPHA's Option recommendations change. ALPHA must reconcile this before proposing any fix.

### Sizing

- `structure_engine.py` — 1805 LOC
- `market_structure.py` — 306 LOC
- `structure_worker.py` — 453 LOC

### Existing Tests

- `tests/test_setup_classifier_counter.py` — counter setup branches, trade_direction inversion, confidence multiplier (0.7 default)
- `tests/test_structure_engine_alignment_broaden.py` — `_counter_alignment()` helper
- `tests/test_definitive_pipeline_e2e.py` — full structure pipeline

### Existing Observability

- `XRAY_CONFIDENCE_DETAIL` — fires per non-NONE classify_setup return
- Component events: `XRAY_INIT`, `XRAY_WIRE`, `XRAY_SESSION`, `XRAY_SCANNER`, `XRAY_SR`, `XRAY_LEVELS`, `XRAY_OB`, `XRAY_VPOC`, `XRAY_FIB`, `XRAY_BOS`, `XRAY_CHOCH`, `XRAY_FVG`, `XRAY_SHADOW_*`

### Architecture Note

`regime.py` is NOT imported by `structure_engine.py` — structure_engine is regime-independent (clean Layer 1A/1B separation). The "regime-aware" counter-setup logic gets regime context from `market_structure.structure` (computed earlier in the same tick).

## BETA Scope Verification (R2 + R3 — APEX Lock + XRAY Override Threshold)

### File:Line Verification

- `src/apex/optimizer.py:1265-1311` — `_check_direction_lock()`. Spec cited 1270-1311; off-by-5 at start, but the body (1285-1299, the regime decision tree) matches the spec exactly.
- `src/workers/strategy_worker.py:1671-1717` — XRAY override decision block. MATCH:
  - 1673 — reads `xray_lock_override_ratio_threshold`
  - 1676-1681 — computes `_lock_override_active` condition
  - 1689-1699 — emits `XRAY_FLIP_SUPPRESSED_BY_LOCK` when locked + no override
  - 1700-1717 — emits `XRAY_OVERRIDE_LOCK` when override active

### Config Flag Values (Current)

- `xray_lock_override_ratio_threshold` = 10.0 (settings.py:831; runtime fetch strategy_worker:1673) — the 10x threshold R3 calls out
- `apex_flip_rr_boost_threshold` = 3.0 (settings.py:2184; runtime fetch optimizer:422) — the 3x flip threshold; gap to 10x is the "dead zone"
- `apex_min_flip_confidence_buy_to_sell` = 0.95 (settings.py:2203; optimizer:1440-1446) — asymmetric high (harder Buy→Sell flips)
- `apex_min_flip_confidence_sell_to_buy` = 0.70 (settings.py:2204) — asymmetric low (easier Sell→Buy flips)
- `xray_dir_flip_threshold_ratio` = 3.0 (config.toml:403)

### Observation On Asymmetry

The system already has aim-aware asymmetric flip confidence (0.95 Buy→Sell vs 0.70 Sell→Buy — should make Sell→Buy easier than Buy→Sell). But the 10x XRAY override threshold cancels much of that out, because the override is the OTHER lever that lets XRAY flip APEX's lock, and that lever fires only at extreme ratios. The "dead zone" between 3.0x flip and 10.0x override traps strong-but-not-extreme structural Buy-flip evidence.

### Existing Observability (APEX flow)

- `APEX_DIR_LOCK`, `APEX_DIR_LOCK_OVERRIDE` (optimizer.py:252, 361)
- `APEX_FLIP`, `APEX_FLIP_BLOCKED`, `APEX_FLIP_DECISION`, `APEX_FLIP_INSUFFICIENT_DATA`, `APEX_FLIP_RESIZE_ACCEPTED`, `APEX_FLIP_RESIZE_CAPPED`, `APEX_FLIP_COUNTER_PROTECTED`
- `APEX_REGIME`, `APEX_OK`, `APEX_SKIP`, `APEX_TIER`, `APEX_SIZING`, `APEX_LEVERAGE`, `APEX_TP_CAP`
- `XRAY_LOCK_PRECEDENCE_RESOLUTION`, `XRAY_FLIP_SUPPRESSED_BY_LOCK`, `XRAY_OVERRIDE_LOCK`
- `XRAY_BLOCK`, `XRAY_CONFLICT`, `XRAY_DIR_BLOCK`, `XRAY_DIR_FLIP`, `XRAY_DIR_FLIP_BLOCKED`, `XRAY_DIR_MISMATCH`, `XRAY_FLIP_TP_DERIVATION`, `XRAY_FLIP_TP_DERIVATION_DEGRADED`

### Sizing

- `optimizer.py` — 1558 LOC
- `strategy_worker.py` — 2666 LOC
- `gate.py` — 795 LOC
- `assembler.py` — 818 LOC
- `qwen_client.py` — 364 LOC

### Existing Tests

- `test_apex_direction_lock.py`, `test_apex_lock_propagation.py`, `test_j3_xray_lock_override.py`
- `test_apex_flip_discipline.py`, `test_apex_flip_decision_log.py`, `test_apex_flip_rr_boost.py`
- `test_apex_sell_bias_gates.py`, `test_xray_dir_flip.py`, `test_xray_flip_tp_integration.py`
- `test_apex_pipeline_integration.py`, `test_j_series_e2e_pipeline.py`, `test_definitive_pipeline_e2e.py`

### Lineage Of The 10x Threshold

- Origin: commit `2120d22` — "j3/phase3: XRAY ratio override of APEX_DIR_LOCK precedence"
- The J3 fix introduced the override precisely to break APEX_DIR_LOCK in extreme structural-mismatch cases. The chosen ratio (10.0) was deliberately conservative. R3 argues it is too conservative.

### Regime Detector Status

Commit `6938c69` (regime detector calibration) is in HEAD (`git merge-base --is-ancestor 6938c69 HEAD` returns true). Per spec: regime.py is OUT OF SCOPE for modification. R2 must not change regime classification — only the LOCK that consumes it.

## GAMMA Scope Verification (R4 — Portfolio Direction Concentration Cap)

### Absent-Path Confirmation

The spec claims no portfolio direction concentration check exists in the admission/entry path. CONFIRMED by exhaustive grep across `src/`:

- `src/risk/portfolio.py:72-89` — `check_concentration()` only checks per-symbol concentration (size / equity); never direction
- `src/fund_manager/ecosystem_health.py:_correlation_score()` — has `long_count` / `short_count` but is observational/diagnostic only; no enforcement gate
- `src/fund_manager/risk_weather.py:296-298` — has `buy_count` / `sell_count` / `same_direction_pct` but is a risk-weather metric; no enforcement
- `src/apex/gate.py` — runs 12 checks (CHECK 1-12); none consider portfolio direction concentration
- `src/strategies/performance_enforcer.py` — sizing/throttling; direction-agnostic
- `src/core/trade_coordinator.py` — entry registration; no direction cap; `TradeState.side` exists (line 37) but no convenience getter for direction-breakdown

Direction-aware enforcement is genuinely absent. The R4 fix introduces a new code path; it is not "find the bug" but "design the missing constraint."

### Cleanest Insertion Point

`src/apex/gate.py` — Layer 4 gate, after CHECK 12 (CHECK 13 = portfolio direction concentration). This places the cap in the same enforcement layer as other portfolio-level constraints (max positions, capital cap, leverage clamp) and respects the project's layer boundaries.

GAMMA Phase 1 will validate this is the right insertion point versus alternatives (APEX optimization output, brain prompt-time advisory).

### Cascade Evidence

14:45 5-Sell cascade reconstructed in `dev_notes/live_monitoring_20260516/FINDINGS.md:410-417`:
- 4 simultaneous shorts hit SL within ~70 seconds (AVAX, APT, SAND, LINK), total -$17.71
- 5th short (ORCA) hit SL 6 min later, additional -$14.11
- Total cascade loss: -$31.82 in 6 minutes
- All 4 initial SLs at near-identical -0.29% to -0.33% PnL (same SL distance, synchronized entries, single market move hit all four)

A 60-70% concentration cap likely would have blocked 1-2 of the 4-5 entries.

### Existing Observability (Position/Portfolio Surface)

- `POSITION_CLOSE_REASON`, `POSITION_INVALIDATED`, `POSITION_RECONCILE`, `POSITION_RECONCILE_DRIFT`
- `SHADOW_POSITION_CLOSE`, `L4P_CHECK`, `COORDINATION_EVENTS`
- No existing `PORTFOLIO_CONCENTRATION_*` or `PORTFOLIO_DIRECTION_*` events — new namespace is clear

### Existing Tests

- `tests/test_phase9/test_portfolio.py` — Kelly, correlation, risk budget, stress (no direction concentration)
- `tests/test_phase9/test_risk_manager.py` — risk manager orchestration
- `tests/test_apex_sell_bias_gates.py`, `tests/test_t3_1_safety_gates.py`, `tests/test_p6_layer3_gate_bybit_demo.py`, `tests/test_apex_pipeline_integration.py` — all touch gate but none cover direction concentration

## Aim-Bias Reminder (Spec A.4 — must answer YES per fix)

1. Does this preserve trade frequency?
2. Does this preserve aggression?
3. Does this improve decision quality?
4. Does this preserve passive-close advantage (data-lake watchdog)?
5. Does this respect structural separation of concerns?

Each agent's `04_fix_options.md` and `05_synthesis.md` MUST answer these explicitly for every option.

## Trading Philosophy (Spec A.1 — non-negotiable)

"Characterize each coin's current situation and exploit it. Aggressively fetch maximum profitable trade across 10-12 best coins per cycle, with $100 capital allocation, 10-30 minute holds, and 1-2% targets."

Balanced both-direction trading. Buy on bullish setups, Sell on bearish setups, no direction bias. The fix REMOVES the bias; it does NOT add caution.

## Phase 0 Exit Criteria (all met)

- Tree created at `dev_notes/direction_fix/{agent_alpha,agent_beta,agent_gamma,agent_delta}/`
- File:line refs verified, with R1 drift flagged
- Branch base + branch names confirmed
- Operator decisions recorded
- Phase 0 docs committed on `investigate/direction-fix-phase0`

Phase 1 (parallel investigation) begins next.
