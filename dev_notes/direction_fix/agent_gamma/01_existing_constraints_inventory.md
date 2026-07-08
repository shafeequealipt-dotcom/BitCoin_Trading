# Agent GAMMA — Portfolio Constraints Inventory (R4)

This document inventories every portfolio-level or trade-admission constraint that currently lives in the codebase. For each constraint the file path, line range, function or attribute, current value, and direction-awareness are listed. The conclusion at the bottom states whether the system has ANY portfolio direction concentration enforcement today.

Verified read-only on branch `investigate/direction-fix-phase0`, HEAD `73202662235c77c25fd89b9d0092fa57550227c6`.

## How to read this inventory

For every constraint a single row is given:

- Location: absolute file path and line range
- Function or attribute: the exact symbol
- Value: current numeric threshold, default, or behavior
- Direction-aware: whether the constraint considers Buy vs Sell
- Why-if-not: short reason direction is not in the contract today

## Layer 4 Gate — `src/apex/gate.py`

The gate runs 15 numbered checks (CHECK 0 through CHECK 14). It is the LAST checkpoint before a trade reaches Shadow. The gate "NEVER blocks a trade" per the module docstring at `src/apex/gate.py:1-6`, with two exceptions added later that DO block via `_gate_rejected`:

1. CHECK 0 — Claude directive size cap (Phase 5)
   - Location: `src/apex/gate.py:65-97`
   - Function: `TradeGate.validate()` block at CHECK 0
   - Value: `gate_apex_size_cap_mult` default `1.5`, computed as `claude_orig * cap_mult`
   - Direction-aware: NO
   - Why-if-not: caps APEX inflation only

2. CHECK 1 — Maximum position size
   - Location: `src/apex/gate.py:99-104`
   - Function: `TradeGate.validate()` CHECK 1
   - Value: `max_position_size_usd` (settings) default `1200.0`
   - Direction-aware: NO
   - Why-if-not: per-position, not portfolio

3. CHECK 2 — Maximum leverage
   - Location: `src/apex/gate.py:106-111`
   - Function: `TradeGate.validate()` CHECK 2
   - Value: `max_leverage` (settings)
   - Direction-aware: NO

4. CHECK 3 — Maximum concurrent positions
   - Location: `src/apex/gate.py:113-129`
   - Function: `TradeGate.validate()` CHECK 3
   - Value: hard-coded `max_concurrent = 5`. When `open_count >= 5`, size is reduced to `size * 0.3` (NOT blocked)
   - Direction-aware: NO
   - Why-if-not: counts total positions, ignores direction. The 14:45 cascade had 10 simultaneous opens; CHECK 3 only down-sized them but did not block

5. CHECK 4 — Capital availability + conviction weighting + zero-conviction reject
   - Location: `src/apex/gate.py:131-261`
   - Function: `TradeGate.validate()` CHECK 4
   - Value: T2-2 / F14 zero-conviction REJECT when `_xray<=min_xray AND _setup<=min_setup AND _rr<=min_rr` (all three at-or-below). Otherwise conviction weight `[0.5, 2.5]` applied to base `40%` of available, clamped `[5%, 50%]`
   - Direction-aware: NO
   - Why-if-not: scales by conviction, not direction balance

6. CHECK 5 — Duplicate position on same symbol
   - Location: `src/apex/gate.py:263-274`
   - Function: `TradeGate.validate()` CHECK 5
   - Value: if a position already exists on the symbol, new size is halved
   - Direction-aware: NO (same-symbol regardless of side)

7. CHECK 6 — Recent cooldown — revenge-trade defense
   - Location: `src/apex/gate.py:276-320`
   - Function: `TradeGate.validate()` CHECK 6 (T2-1 / F20)
   - Value: if symbol is in cooldown AND new direction == prior loss direction, HARD REJECT via `_gate_rejected`. Otherwise size halved
   - Direction-aware: YES, but PER-SYMBOL only — checks that this symbol's new entry direction matches this symbol's prior loss direction. Does NOT check portfolio direction concentration

8. CHECK 6b — J6 re-entry learning gate
   - Location: `src/apex/gate.py:322-496`
   - Function: `TradeGate.validate()` CHECK 6b via `coordinator.check_reentry_learning_gate()`
   - Value: blocks when (current_regime, current_setup_type, current_direction) all match a prior losing thesis on the same symbol. H4 (2026-05-16) added recency, magnitude, and price-drift escapes
   - Direction-aware: YES, but PER-SYMBOL only — direction is one of three match keys, not a portfolio constraint

9. CHECK 7 — Minimum position size floor
   - Location: `src/apex/gate.py:498-503`
   - Function: `TradeGate.validate()` CHECK 7
   - Value: `min_size = 50.0` USD floor
   - Direction-aware: NO

10. CHECKS 8-12 — APEX guardrails (TP floor, trail activation, trail distance, mode override, confidence-based size scaling)
    - Location: `src/apex/gate.py:531-608`
    - Direction-aware: NO (TP/SL/trail bounds; symmetric on direction sign)

11. CHECK 13 — R:R ratio validation
    - Location: `src/apex/gate.py:613-630`
    - Direction-aware: NO

12. CHECK 14 — TP/SL sanity
    - Location: `src/apex/gate.py:632-647`
    - Direction-aware: NO (symmetric)

Gate conclusion: NO check considers portfolio direction concentration. CHECK 3 caps total positions at 5 (with size reduction), CHECK 6 / 6b apply per-symbol direction memory, but the cross-symbol portfolio direction picture is completely absent. There is no CHECK 15 today.

## Layer 3 APEX — `src/apex/optimizer.py`

- `_check_direction_lock()` at `src/apex/optimizer.py:1265-1311` returns `(True, "trending_down aligns with Sell")` for trending_down regime regardless of brain or XRAY preference. This is BETA's R2 territory, NOT GAMMA's R4 scope.
- APEX flip-confidence asymmetry exists but is per-trade, not portfolio
- No portfolio direction concentration check anywhere in APEX

## Risk Manager — `src/risk/risk_manager.py`

End-to-end review of all 230 lines:

1. `validate_trade()` at lines 57-115
   - Calls `drawdown.check_circuit_breakers()` (daily loss limit, max drawdown, consecutive losses cooldown). None of these are direction-aware
   - Calls `validator.validate_order()` which checks per-trade SL/TP/leverage sanity and the existing-position-on-this-symbol-side check (validators.py:103, 123)
   - No portfolio direction concentration

2. `calculate_position_size()` at lines 117-146
   - Delegates to `PositionSizer.recommend()` which evaluates fixed-pct, ATR-based, Kelly. Picks the most conservative. Direction-aware only at SL placement geometry
   - No portfolio direction concentration

3. `get_portfolio_risk()` at lines 152-174
   - Calls `portfolio.get_exposure()` (observational), `drawdown.get_daily_pnl()`, `drawdown.get_current_drawdown()`. None gate admission
   - No portfolio direction concentration

Risk Manager conclusion: orchestrates four sub-services. NONE enforce direction balance.

## Portfolio Analyzer — `src/risk/portfolio.py`

End-to-end review of all 120 lines:

1. `get_exposure()` at lines 26-70
   - Returns total exposure, effective leveraged exposure, per-position breakdown, largest position
   - Direction-aware: NO at the gating level. Returns side per position in the dict but does not gate

2. `check_concentration()` at lines 72-89
   - Checks per-symbol position size vs equity (`max_position_size_pct` default `10.0`)
   - Checks single-position-over-50%-of-total
   - Direction-aware: NO

3. `check_correlation()` at lines 92-102
   - Observational warning when all positions are same direction or 3+ in ALT_CORRELATED set
   - Direction-aware: YES, but OBSERVATIONAL only — emits a warning string in the `warnings` list; no admission gate fires. Returns text "All N positions are long/short — directional exposure risk" with NO downstream consumer that blocks based on it

Portfolio Analyzer conclusion: `check_correlation()` line 100-102 is the closest existing direction-aware code. It is text-only diagnostic, no enforcement.

## Position Sizer — `src/risk/position_sizer.py`

End-to-end review of all 194 lines:

1. `fixed_percentage()`, `atr_based()`, `kelly_criterion()`, `fixed_usd()`, `recommend()`
   - All four methods are direction-aware only at SL placement (Buy SL below entry, Sell SL above entry)
   - None consider portfolio direction concentration

Position Sizer conclusion: direction is used for SL geometry; no portfolio constraint.

## Stop Loss Calculator — `src/risk/stop_loss.py`

End-to-end review (158 lines): direction is used for SL placement geometry (Buy SL below entry, Sell above entry, etc.). No portfolio constraint.

## Trade Validator — `src/risk/validators.py`

End-to-end review (161 lines):

- `validate_order()` checks: SL on the correct side of entry (Buy SL < entry, Sell SL > entry), TP on the correct side, leverage cap, and ONE per-symbol-per-side duplicate check at lines 103, 123 ("Already have a {side} position in {symbol}")
- Direction-aware: PER-SYMBOL only ("already have a Buy on BTCUSDT, you cannot open another Buy on BTCUSDT in the same direction"). NOT a portfolio constraint
- No portfolio direction concentration

## Drawdown Tracker — `src/risk/drawdown.py`

End-to-end review (217 lines): tracks daily PnL, drawdown depth, consecutive losses, post-loss cooldowns. Operates on PnL magnitude and trade timestamps. Direction-aware: NO.

## Layer 4 Protection Service — `src/risk/layer4_protection.py`

End-to-end review of all 508 lines:

1. `is_protected()` at lines 182-269
   - Three guards: min_hold (300s), profit_guard, structural_invalidation
   - All operate on close paths, NOT entry admission

2. `compute_structural_invalidation()` at lines 287-387
   - Disjunction: XRAY drop >= 40%, setup-type drift, regime inversion >= 60% confidence
   - Direction-aware: YES via the regime-inversion arm (lines 376-385). But this is per-position structural-validity for CLOSE decisions, NOT portfolio-level direction concentration for ENTRY decisions

Layer 4 Protection conclusion: protects open positions from premature close. Does not gate entry.

## Performance Enforcer — `src/strategies/performance_enforcer.py`

End-to-end review of 864 lines:

1. `_per_direction` dict at lines 65-68 and 643-651
   - Tracks Buy and Sell wins/losses per session
   - Direction-aware: YES, observational only. Used for `get_coaching_text()` brain-prompt text, NOT for admission gating

2. `get_max_positions_override()` at lines 168-181
   - HALTED returns 0, SURVIVAL returns `level_2_max_positions=2`, PRESERVATION returns `level_1_max_positions=3`
   - Direction-aware: NO (total positions cap)

3. `get_size_multiplier()` at lines 194-224 (referenced "sz_mult")
   - 1.0 / 0.75 / 0.50 / 0.25 / 0.40 / 0.50 / 0.0 based on PnL bands
   - Direction-aware: NO

4. `qualify_survival_trade()` at line 352
   - Quality gate when in SURVIVAL level
   - Direction-aware: NO

5. `clamp_leverage()` at lines 132-166
   - Clamps to L1/L2/HALTED caps
   - Direction-aware: NO

Performance Enforcer conclusion: PnL-band-based throttling. Tracks direction stats but does not gate admission on direction balance.

## Trade Coordinator — `src/core/trade_coordinator.py`

End-to-end review of 1876 lines:

1. `_trades: dict[str, TradeState]` at line 146 — in-memory authoritative open-position state
   - Keyed by symbol
   - `TradeState.side: str` at line 37 — populated at `register_trade()` line 513
   - DOES expose `side` per-position but offers NO convenience method to get direction counts

2. `register_trade()` at lines 403-560
   - Sets `_trades[symbol] = TradeState(...)` and logs `COORD_REG`. Records side at line 513
   - No direction concentration check
   - Direction-aware: per-trade only (records side; does not aggregate)

3. `get_status()` at lines 1836-1868
   - Returns `{"active_trades": N, "positions": {...}}` per symbol
   - Each position dict contains `source`, `category`, `strategy`, `peak_pnl` — but NOT `side`
   - No direction breakdown surface

4. Per-symbol cooldown direction memory at `is_symbol_cooled_down` / `get_loss_cooldown_direction` (referenced from gate CHECK 6)
   - Direction-aware: PER-SYMBOL only

Trade Coordinator conclusion: `_trades` holds direction per-position via `TradeState.side`, but NO method aggregates across positions to give Buy/Sell counts. There is no helper like `get_direction_counts()` or `get_portfolio_direction_breakdown()`. This is the helper GAMMA needs to add (Synthesis section 05).

## Database Repository — `src/database/repositories/trading_repo.py`

- `get_all_positions()` at line 252 returns `list[Position]`, each Position has `.side` (Side enum). This is the persisted equivalent of `TradeState`
- Used by drawdown reconciliation; can be queried at gate time, but currently nothing in admission code aggregates `.side` across the list

## Fund Manager — Observational direction metrics (no enforcement)

These are the existing direction-aware code paths Phase 0 verified are diagnostic-only:

1. `EcosystemHealthMonitor._correlation_score()` at `src/fund_manager/ecosystem_health.py:101-128`
   - Inputs: `long_count`, `short_count`
   - Output: a score 4-25 with text "All positions same direction" warning
   - Direction-aware: YES, observational only. Fed into health dashboard, never gates admission

2. `RiskWeatherAssessor._assess_correlation()` at `src/fund_manager/risk_weather.py:272-315`
   - Counts `buy_count` and `sell_count` and computes `same_direction_pct`
   - Returns a risk score 3-15
   - Direction-aware: YES, observational only. Feeds the risk-weather meter, never gates admission

Both write metrics to monitoring surfaces. Neither calls into `trade_coordinator.register_trade()`, the gate, APEX, or any admission path.

## Summary table — Direction-aware enforcement points

| Constraint | Location | Direction-aware? | Portfolio-level? | Enforces? |
|------------|----------|------------------|------------------|-----------|
| Gate CHECK 5 (duplicate symbol) | gate.py:263-274 | NO | NO (per-symbol) | size halved |
| Gate CHECK 6 (cooldown) | gate.py:276-320 | YES per-symbol | NO | reject same-dir |
| Gate CHECK 6b (re-entry learning) | gate.py:322-496 | YES per-symbol | NO | reject same setup+regime+dir |
| Validator duplicate check | validators.py:103,123 | YES per-symbol | NO | block existing same-side |
| APEX_DIR_LOCK | optimizer.py:1265 | YES per-trade | NO | force direction (R2 scope) |
| Layer 4 regime inversion | layer4_protection.py:376-385 | YES per-position | NO | block close (not entry) |
| Portfolio.check_correlation | portfolio.py:92-102 | YES observational | YES observational | NO (text warning only) |
| Ecosystem._correlation_score | ecosystem_health.py:101-128 | YES observational | YES observational | NO (health dashboard) |
| Risk weather correlation | risk_weather.py:272-315 | YES observational | YES observational | NO (weather meter) |

## Concluding statement

There is no portfolio direction concentration cap in the trade admission path. Three observational direction metrics exist (`PortfolioAnalyzer.check_correlation`, `EcosystemHealthMonitor._correlation_score`, `RiskWeatherAssessor._assess_correlation`) and three per-symbol direction-aware gates exist (validator dup check, gate CHECK 6 cooldown, gate CHECK 6b re-entry learning). None of these aggregate cross-symbol direction counts at admission time and none refuse a trade based on portfolio direction concentration.

R4 is a NEW code path. The closest existing surfaces that GAMMA's design can build on are:

- `TradeCoordinator._trades` dict (authoritative live state) — needs a `get_direction_counts()` helper
- `TradeGate.validate()` CHECK 13 position (CHECK 13 is currently R:R; R4 inserts as CHECK 15 or repositions)
- `PortfolioAnalyzer.check_correlation()` text already counts directions but only emits warnings

Architecture recommendation in 04. Synthesis with cap value, design choice, and trial behavior in 05.
