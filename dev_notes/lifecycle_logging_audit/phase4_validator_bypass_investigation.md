# Phase 4.X — Validator-Bypass Investigation Report

**Date:** 2026-05-09
**Audit gap:** Phase 4.X (HIGH) — 3 validation surfaces appear bypassed: RISK_BLOCK = 0, SLTP_SKIP = 0, RULE_EVAL_START/END = 0 firings across all rotated logs.
**Investigation type:** READ-ONLY call-graph trace. No code changes.

---

## Findings

### 1. SLTPValidator (`src/core/sl_tp_validator.py`) — **ACTIVE, not bypassed**

**Construction:** `workers/manager.py:594` — `sl_validator = SLTPValidator(headspace_pct=2.5, max_distance_pct=15.0)`

**Active call sites:**
- `workers/strategy_worker.py:~1893` — pre-validation note in trade execution path.
- `workers/strategy_worker.py:~1935` — TP cap with SLTPValidator-aware logic.
- `workers/strategy_worker.py:~1975` — `validate_sl`, `validate_tp`, `validate_pair` invoked at the trade-execution gate.
- `workers/strategy_worker.py:~2002,2020` — TRADE_SKIP rsn=sltp_skip emitted when validator returns SKIP.

**Why 0 SLTP_SKIP firings in current rotation:**
- SLTPValidator's internal `SLTP_SKIP` tag fires only when SL distance is invalid (e.g., zero or beyond max_distance_pct = 15.0%).
- If all SL/TP values pass the distance check, no SLTP_SKIP fires.
- The strategy_worker-level `TRADE_SKIP rsn=sltp_skip` fires when validator returns SKIP action — but that's a different tag.
- 0 firings = system healthy (all SL/TP within bounds), NOT bypass.

**Conclusion:** SLTPValidator is in the active hot path. Not a gap. Document in Phase 11.

### 2. RiskManager.validate_trade (`src/risk/risk_manager.py`) — **BYPASSED in production**

**Construction:** `container.py:112` and `workers/manager.py:510` — `RiskManager(settings, db, services)`

**Call sites of `validate_trade` / `evaluate_trade`:**
- `brain/brain_v2.py:418` — `valid, issues = await self.risk_manager.validate_trade(...)`. **brain_v2.py is the LEGACY brain class, NOT the production active path.** Current production uses `brain/strategist.py` (CALL_A/CALL_B) which does NOT invoke risk_manager.validate_trade.

**Active uses of risk_manager:**
- `workers/position_watchdog.py:2388, 2438` — `await self.risk_manager.on_trade_closed(estimated_pnl)`. PnL accounting only, NOT trade validation.

**Why 0 RISK_BLOCK firings:**
- `validate_trade` is never called in the production CALL_A → APEX → TradeGate → execution flow.
- `RISK_BLOCK` log at `risk_manager.py:106` cannot fire if `validate_trade` isn't invoked.

**Conclusion:** **CONFIRMED BYPASS.** `RiskManager.validate_trade` is dead code in production. The 13 silent checks in `risk/validators.py:42-127` are not executed.

**Operator decision required:** delete the dead code OR re-wire if validation is desired. The current `apex/gate.py::TradeGate` performs analogous safety checks (max position, max leverage, max concurrent positions, conviction-weighted capital, RR sanity, TP/SL sanity) so the safety contract is not broken — but the `risk/validators.py` layer is genuinely orphaned.

### 3. RuleEngine (`src/core/rule_engine.py`) — **BYPASSED in production**

**Construction:** `workers/manager.py:691-692` — `rule_engine = RuleEngine(self._services, settings)`

**Active call sites:** None found in current production code path. The original `STRAT_L4 prose "Layer 4: {n} strategy hints for Claude (no rule engine execution)"` (deleted in Phase 12.1 commit `cc9f600`) explicitly stated this design choice: hints are passed to Claude as prompt context, not executed through rule_engine.

**Why 0 RULE_EVAL_START / RULE_EVAL_END firings:**
- `RuleEngine.evaluate(...)` is never called in the production flow.
- Its 4 INFO log calls (`rule_engine.py:50, 138, 144, 318, 322, 338`) cannot fire.

**Conclusion:** **CONFIRMED BYPASS.** RuleEngine is instantiated but never executed. Strategy hints are fed to Claude as context (`STRAT_L4 | hints=N filtered_from=N`).

**Operator decision required:** delete RuleEngine OR re-wire if rule-based execution is desired. Current production model is "Claude-as-rule-engine" — Claude reasons over the hints and emits directives.

---

## Recommended Phase 11 Followup

Add to `phase11_comprehensive_gap_report.md` Section 7:

> **4.X-G1 Investigation outcome (this session):**
> - SLTPValidator: ACTIVE — 0 SLTP_SKIP firings is healthy state, not a gap.
> - RiskManager.validate_trade + RuleEngine: BYPASSED in production. Both are dead code in the current strategist/APEX/gate flow.
>
> **Operator decision required:** delete dead code OR re-wire. Safety contract is preserved by `apex/gate.py::TradeGate` (analogous checks) — no production behavior change needed.

---

## Logging Implications

No new log statements required — these are call-graph findings. The 0 firings of RISK_BLOCK / RULE_EVAL_START accurately reflect that those code paths are not exercised. Operators can interpret the absence as "validators inactive", not "validators silently failing".

If the operator decides to keep the dead code (e.g., for future re-enablement), recommend adding a one-time `RISK_MANAGER_INACTIVE` / `RULE_ENGINE_INACTIVE` startup log so the inactive state is explicit.

---

## Verification

CI test_logging_routing.py: 3 passed (no code changes in this investigation).
