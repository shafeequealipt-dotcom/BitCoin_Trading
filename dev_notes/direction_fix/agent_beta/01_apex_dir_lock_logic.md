# BETA 01 — APEX_DIR_LOCK Decision Tree and Caller Chain

This document decodes `_check_direction_lock()` and the full caller chain that produces the APEX_DIR_LOCK and APEX_DIR_LOCK_OVERRIDE events. Every claim in this file is grounded in the file:line excerpts cited. The lock logic and override logic are tightly coupled; both surfaces are documented here.

## The lock decision function

The single source of truth for the lock is `_check_direction_lock()` in `src/apex/optimizer.py:1265-1311`. The function takes the IntelligencePackage, the brain's chosen direction, and the current regime string and returns a `(locked: bool, reason: str)` tuple.

The decision tree, verbatim from optimizer.py:1285-1311:

```
natural_dir = {
    "trending_down": "Sell",
    "trending_up": "Buy",
}.get(regime)

# Trending: always lock — Claude already considered per-coin regime
if natural_dir:
    if claude_direction == natural_dir:
        return True, f"{regime} aligns with {claude_direction}"
    else:
        return (
            True,
            f"Claude chose {claude_direction} against {regime} "
            f"(per-coin override)",
        )

# Volatile: lock unless overwhelming evidence for opposite
if regime == "volatile":
    if self._check_flip_evidence(
        package.symbol_history.trades, claude_direction,
    ):
        return False, ""
    return True, "volatile regime, insufficient flip evidence"

# Ranging / dead / unknown: no pre-call lock — confidence-gated
# flip discipline is enforced post-parse (Phase 9).
return False, ""
```

The flip-evidence helper (optimizer.py:1250-1263) returns True only when the opposite-direction history has at least 8 trades AND at least 70 % win rate. Below that bar the volatile lock fires.

## Per-regime outcome table

The table below is the exact, exhaustive outcome of `_check_direction_lock()` for every regime value the package's situation_data can hold.

| Regime | Claude direction | Locked | Reason emitted |
|---|---|---|---|
| `trending_up` | Buy | True | `trending_up aligns with Buy` |
| `trending_up` | Sell | True | `Claude chose Sell against trending_up (per-coin override)` |
| `trending_down` | Sell | True | `trending_down aligns with Sell` |
| `trending_down` | Buy | True | `Claude chose Buy against trending_down (per-coin override)` |
| `volatile` | Buy or Sell | True unless opp-direction history has ≥ 8 trades and ≥ 70 % WR | `volatile regime, insufficient flip evidence` |
| `volatile` | with strong opp evidence | False | empty |
| `ranging` | Buy or Sell | False at pre-parse; post-parse confidence gate fires (`_enforce_flip_confidence`) | post-parse only |
| `dead` | Buy or Sell | False at pre-parse; post-parse confidence gate fires | post-parse only |
| `unknown` / anything else | Buy or Sell | False at pre-parse; post-parse confidence gate fires | post-parse only |

The trending lock is symmetric in code — same return shape for `trending_up`/`trending_down` and same alignment check. But the production effect is asymmetric on May 16 because the population of trending_up emissions (174) is 22× smaller than trending_down (3,922). The lock fires symmetrically; the input distribution is what produces the asymmetric bias.

## The full cascade — brain → APEX → lock → override → strategy_worker

This is the call chain from a brain directive to a placed order, with every direction-mutating point named:

1. **Brain CALL_A** produces `directive["direction"]` (Buy or Sell). This is `claude_direction` for the rest of the chain.
2. **WorkerManager** invokes `TradeOptimizer.optimize(directive, plan)` (optimizer.py:118).
3. **Assembler** produces the IntelligencePackage with `situation_data.regime` (the regime APEX consumes is the VOL_PROFILE per-coin regime, not the global BTC regime — verified in assembler.py:680-695).
4. **Pre-call lock gate** at optimizer.py:242-267:
   - Reads `regime = package.situation_data.regime`.
   - Calls `_check_direction_lock(package, claude_direction, regime)`.
   - If locked, emits `APEX_DIR_LOCK | sym=... dir={claude_direction} regime=... reason='...'` (line 251).
   - Injects the lock reason into `package.directive.reasoning` as a `[DIRECTION LOCKED: ...]` prefix (lines 256-260). DeepSeek sees this in its user prompt.
   - Stashes `_apex_lock_state` so the lock can be plumbed downstream regardless of any subsequent exception.
5. **DeepSeek call** at optimizer.py:300-345. DeepSeek may return a different direction despite the prompt instruction.
6. **Post-parse lock-override gate** at optimizer.py:352-371:
   - If `direction_locked and optimized.direction != claude_direction`, emit `APEX_DIR_LOCK_OVERRIDE | sym=... qwen_tried={optimized.direction} locked_to={claude_direction} regime=...` (line 360).
   - Hard-reset `optimized.direction = claude_direction` (line 365).
   - Reset `optimized.was_flipped = False`.
   - Increment `_lock_override_count`.
   - Set `_dir_lock_override_fired = True` for the decision-log emit below.
7. **Counter-protection gate** at optimizer.py:432-463 (skipped here — only fires when scanner labels a counter setup).
8. **Insufficient-data gate** at optimizer.py:465-498 (skipped here — fires only when target-direction has fewer than 5 trades).
9. **Flip-confidence gate** at optimizer.py:500-519 (`_enforce_flip_confidence`). Only governs ranging/dead/unknown post-parse; the trending and volatile regimes are already governed by step 6.
10. **Flip-resize policy** at optimizer.py:521-529 (`_apply_flip_resize_policy`). Only fires when a flip survived all preceding gates.
11. **Unified decision log** at optimizer.py:611-629 emits `APEX_FLIP_DECISION` with `brain_dir`, `apex_dir`, `flip_attempted`, `flip_accepted`, `decision_reason` (one of `lock_override`, `counter_protected`, `insufficient_data`, `conf_below_threshold`, `flip_accepted`, `no_flip_attempt`).
12. **Lock plumb-through** at optimizer.py:658-663: `optimized.is_locked, optimized.lock_reason = _apex_lock_state`. This is what propagates the lock past APEX into the trade dict the strategy_worker eventually reads.
13. **Layer_manager** copies `is_locked` / `lock_reason` onto the trade dict as `_apex_locked` / `_apex_lock_reason` (verified by the strategy_worker reading at line 1648).
14. **strategy_worker._execute_claude_trade** at strategy_worker.py:1417-2300+ runs the XRAY direction gate at lines 1589-1838. This is where the threshold-override decision is made — covered in deliverable 03.

## Where the Qwen client fits

`src/apex/qwen_client.py` is a pure HTTP client. It POSTs to OpenRouter and returns parsed JSON. It has zero direction logic, zero override logic, zero retry of "try a different direction." It can return `direction = "Buy"` or `direction = "Sell"`, but whether that direction stands is decided entirely by the gates in `optimizer.py` (steps 6-9 above). Qwen "trying to override" the lock means: DeepSeek returned a JSON payload whose `direction` field differs from `claude_direction`, and the optimizer's lock-override gate reverted it. Qwen has no role in the decision after that point.

The system prompt at prompts.py:21-75 nominally instructs DeepSeek to consider flipping when TIAS history overwhelmingly supports the opposite direction. But the trending/volatile lock at step 6 is unconditional — it reverts DeepSeek's flip regardless of the TIAS evidence DeepSeek may have considered.

## Symmetry analysis — is the lock symmetric?

**At code level: yes, structurally symmetric.** The natural_dir map (line 1285-1288) treats trending_up and trending_down identically. The reason strings are mirror images. The override fallback path is the same for both.

**At behavior level: yes, symmetric.** When regime = trending_up and Claude chose Sell, the lock fires with the same shape as trending_down + Claude chose Buy. The May 16 session shows 2 events of `dir=Buy regime=trending_up reason='trending_up aligns with Buy'` and 3 events of `dir=Buy regime=trending_down reason='Claude chose Buy against trending_down (per-coin override)'` — both directions get locked.

**The bias is in the regime distribution feeding the lock, not the lock itself.** May 16 had 3,922 trending_down vs 174 trending_up emissions in the input population. Symmetric locks applied to asymmetric inputs produce asymmetric outputs.

## All overrides currently in place

The codebase has exactly three mechanisms that can override or relax the lock decision:

1. **`_check_flip_evidence()` in volatile regime** (optimizer.py:1250-1263 + 1302-1307). When the opposite direction has ≥ 8 trades with ≥ 70 % WR, the volatile lock returns `(False, "")` and Qwen's flip stands. This is a TIAS-history-aware relaxation; only active for `regime == "volatile"`. It does NOT apply to trending_up / trending_down — those lock unconditionally.
2. **Post-parse XRAY structural-RR override** at strategy_worker.py:1671-1717. Fires AFTER the brain → APEX → execute chain. When `_apex_locked` is True AND `ratio = rr_opposite / rr_chosen > xray_lock_override_ratio_threshold` (default 10.0), the locked direction is reverted and the trade flips to the structural winner. This is R3.
3. **Counter-trade protection in APEX** (optimizer.py:432-463). This is NOT a lock override — it is the opposite: it reverts Qwen's flip when the brain's chosen direction is a deliberate counter-trade. It strengthens the lock for counter setups.

There are NO other override paths. The post-parse `_enforce_flip_confidence` only fires for ranging/dead/unknown — those regimes are already unlocked at the pre-call stage; the confidence gate does not relax a lock.

## What inputs the lock has access to

The lock function receives `(package, claude_direction, regime)`. It actively reads:

- `regime` (the regime string from `package.situation_data.regime`) — directly used in the natural_dir mapping.
- `claude_direction` — used for the alignment / against-regime branching.
- `package.symbol_history.trades` (only in the volatile branch via `_check_flip_evidence`) — used to compute opp-direction trade count and WR.

What the lock does NOT consult — but could in principle:

- `package.situation_data.regime_confidence` or any confidence score. There is no confidence threshold; the lock fires the moment regime is trending or volatile, regardless of whether confidence is 0.30 or 0.95.
- `package.structural_data.rr_long`, `rr_short`, `rr_ratio`. The structural R:R is not part of the lock decision — XRAY structural evidence only enters the cascade post-execute via the override gate in strategy_worker.
- TIAS conviction profile (profit factor, per-direction WR for THIS coin in THIS regime) outside the volatile branch. Conviction history could (and arguably should) influence the trending lock — currently it does not.
- The 50-coin universe history (aim-bias data — Buys 55.6 % WR vs Sells 41.8 % WR over the last 200 trades). This data is computed but never consumed by `_check_direction_lock()`.

This is the surface area that the R2 fix options in deliverable 05 will modify.

## Plumbing artifacts

The lock state is plumbed out of APEX three ways:

1. `optimized.is_locked` and `optimized.lock_reason` fields on the OptimizedTrade dataclass (optimizer.py:658-663).
2. Trade-dict fields `_apex_locked` and `_apex_lock_reason` placed by layer_manager (read at strategy_worker.py:1648, 1695, 1714).
3. The `APEX_DIR_LOCK` log line itself, which carries the regime and reason as searchable telemetry.

The plumbing is robust — the lock state survives APEX exceptions (via the captured `_apex_lock_state` tuple at line 143 / 267 / 663) and survives fallback paths (via the `lock_state` kwarg threaded into `_fallback()` at line 690/733).

## Summary for synthesis

- The lock fires unconditionally in trending_up, trending_down, and (usually) volatile.
- The trending branches are symmetric in code; production asymmetry comes from input regime distribution.
- The lock consumes regime alone — it ignores regime confidence, structural R:R, conviction, and aim-bias evidence.
- Override paths exist but are narrow: (a) volatile + strong opp TIAS, (b) strategy_worker structural-RR > 10×, (c) APEX counter-protection (reinforces the lock, does not relax it).
- The May 16 dominant effect is 71 APEX_DIR_LOCK fires forcing Sell direction in trending_down regime, plus 5 forcing Sell in volatile regime. Together they account for 76 of 80 lock events (95 %).
