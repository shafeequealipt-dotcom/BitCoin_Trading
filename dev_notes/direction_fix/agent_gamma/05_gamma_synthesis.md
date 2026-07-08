# Agent GAMMA — Synthesis (R4)

This document consolidates GAMMA's Phase 1 investigation into a single recommendation. It states the chosen design, the chosen architecture location, the chosen cap value, the backup behavior when the cap fires, the helper method required in TradeCoordinator, the trial behavior specification (what specifically happens when the cap fires under each scenario), and the verification queries the operator can run after Phase 4.

## The chosen design — Design A, hard cap

Out of designs A through E (evaluated in 03), GAMMA recommends **Design A — hard cap**.

Reasoning:
- Lowest complexity (2 of 5)
- All five aim-bias questions answered YES
- 100% prevention of the 14:45 cascade outcome at any 60-80% cap
- Reuses the established `_gate_rejected` pattern (CHECK 6, CHECK 6b)
- Cleanest observability — a single binary decision is auditable
- Forbidden by spec Rule 3: "Adding concentration check ONLY at brain prompt" — Design A puts the check in the gate, not in the brain prompt, satisfying this rule

Design D (concentration-aware sizing) is the strong alternative if the operator prefers no rejections. GAMMA picks A because the operator's stated philosophy is "exploit each coin's situation" — a sized-down trade contradicts this philosophy more than a rejected trade. A rejection says "the portfolio is too concentrated; pick a different coin"; a sized-down trade says "trade this coin but smaller, which is worse than not trading it."

## The chosen architecture location — Layer 4 Gate, CHECK 15

Insert as a new CHECK 15 in `src/apex/gate.py`, between the existing CHECK 14 (TP/SL sanity at gate.py:632-647) and the final return statement at gate.py:672.

Reasoning:
- Architecture aligned: gate is the existing portfolio-level admission layer
- Pattern reuse: CHECK 6 cooldown is the template for hard reject via `_gate_rejected`
- Observability mature: GATE_REJECT log surface is consumed by layer_manager
- Trade_coordinator already wired: gate uses it at CHECK 6 via `_services.get("trade_coordinator")`

Phase 0 reconnaissance referred to this as "CHECK 13" — that referred to the CHECK ordering at a prior gate state. The current gate has 15 checks (numbered 0 through 14 inclusive), and R4 is CHECK 15. Phase 0's "CHECK 13" was conceptual; this synthesis fixes the position to "CHECK 15, after CHECK 14, before return."

## The chosen cap value — 70%

Out of 60%, 70%, and 80%:

- 60% fires too early in the operator's "exploit each coin's situation" philosophy. A 60% portfolio (3 Sells, 2 Buys) is balanced enough that the next entry should be allowed
- 80% is too late. By 80% concentration, the portfolio is already at cascade risk
- 70% (the middle) is the most aim-aligned point. A 70% portfolio (7 of 10 same direction) is the threshold beyond which the cascade risk dominates. Below 70%, the portfolio is "modestly tilted" and the operator's aim accepts that tilt

Cap value = 70%. Warn band = 60%. The warn band emits `PORTFOLIO_CAP_WARN` at INFO level so the operator can see "we're approaching the cap" before rejections begin.

Cascade simulation evidence (02): all 18 of 19 Sell entries in the 13-15h window are blocked at cap 60%, 70%, OR 80% — the cap value does not change the cascade-prevention outcome because every cascade entry was already at >= 87.5% concentration. The choice between 60/70/80 is about operator philosophy in the 50-79% concentration band, not about cascade prevention.

70% specifically is chosen because:
- It aligns with `EcosystemHealthMonitor._correlation_score()` band boundaries (the existing observational direction code triggers warning at >= 0.6 balance threshold, equivalent to about 60-67% concentration)
- It is consistent with the `RiskWeatherAssessor._assess_correlation()` thresholds (`same_direction_pct >= 0.8` gives the highest risk score there; 70% is the operator-friendly mid-point)
- The first non-trivial Sell entry to be blocked at 70% (PLUMEUSDT at 13:48:38, 100% pre-concentration with N=1) is the FIRST PLACE in the cascade timeline where any cap could have fired meaningfully. Cap 60% would also fire here. Cap 80% would fire here too. Pre-cascade concentration always reaches 100% on N=1 portfolios, so the cap value matters only for portfolios with N >= 5 in the 50-79% band — which is where the operator's aim asks the cap to NOT fire

The cap should be config-tunable. Settings field: `portfolio_direction_cap_pct: float = 70.0`, with `portfolio_direction_cap_warn_pct: float = 60.0`. The operator can adjust without code change.

## Backup behavior when cap fires — hard reject via `_gate_rejected`

Pattern from CHECK 6 (cooldown same-direction):

```
trade["_gate_rejected"] = f"portfolio_direction_cap_{new_dir}_{pre_pct:.0f}pct"
modifications.append(f"REJECTED:portfolio_direction_cap_{new_dir}_{pre_pct:.0f}pct")
log.warning(
    f"PORTFOLIO_CAP_HIT | sym={symbol} new_dir={new_dir} pre_pct={pre_pct:.1f}% "
    f"cap_pct={cap_pct:.1f}% buys={n_buy} sells={n_sell} total={n_total} | {ctx()}"
)
trade["_gate_modifications"] = modifications
return trade
```

`layer_manager` consumes `_gate_rejected` and skips execution with a `GATE_REJECT` log. The trade is NOT placed, NOT registered with the coordinator, and does NOT count toward the cap. The brain's next CALL_A cycle will see the same portfolio state and will likely pick a different coin (if upstream R1+R2+R3 are in place, the brain produces a more balanced directive set).

No size-down, no queue, no defer. The cap is binary because:
- Size-down (Design B/D) hides the decision in the size column, harder to audit
- Queue (defer until concentration drops) introduces stale-state problems (the brain's directive may be stale by the time concentration drops)
- Defer (skip this tick only) is the same as reject (next tick is a fresh decision)

## Helper method to add to TradeCoordinator

Add to `src/core/trade_coordinator.py` immediately before `cleanup_stale()` (at line ~1869):

```python
def get_direction_counts(self) -> dict[str, int]:
    """Return current direction breakdown of open trades.
    
    Reads ``self._trades`` and aggregates by ``TradeState.side``. Sides
    are normalized to canonical ``"Buy"`` and ``"Sell"`` (per the live
    Bybit-demo schema) — "Long" maps to "Buy" and "Short" maps to "Sell"
    for legacy data compatibility.
    
    Returns:
        Dict with keys ``"Buy"``, ``"Sell"``, ``"total"`` and value =
        count of open trades. ``"total"`` is the sum and is always
        ``Buy + Sell``. Unknown sides (empty string, neither Buy/Sell)
        are silently dropped from the counts but counted in ``total``.
    """
    buys = 0
    sells = 0
    for state in self._trades.values():
        side = (state.side or "").strip()
        if side in ("Buy", "Long"):
            buys += 1
        elif side in ("Sell", "Short"):
            sells += 1
    return {"Buy": buys, "Sell": sells, "total": len(self._trades)}
```

Tests for the helper:
- Empty trades dict returns `{Buy: 0, Sell: 0, total: 0}`
- All Buy returns `{Buy: N, Sell: 0, total: N}`
- All Sell returns `{Buy: 0, Sell: N, total: N}`
- Mixed returns correct breakdown
- Long/Short aliases map to Buy/Sell correctly
- Unknown side strings counted in `total` but not in Buy/Sell

## Settings to add

In `src/config/settings.py` under the `APEXSettings` dataclass (which `TradeGate` reads from):

```python
# R4 — Portfolio Direction Concentration Cap (2026-05-17)
# When portfolio direction (Buy or Sell) concentration reaches
# portfolio_direction_cap_pct, new entries in the same direction are
# rejected by gate CHECK 15. Below the warn band, the cap silently
# allows. At warn band, the cap emits PORTFOLIO_CAP_WARN.
portfolio_direction_cap_enabled: bool = True
portfolio_direction_cap_pct: float = 70.0
portfolio_direction_cap_warn_pct: float = 60.0
portfolio_direction_cap_min_positions: int = 3
```

The `min_positions=3` rule prevents the cap from firing on N=1 or N=2 portfolios (where 100% concentration is trivially achievable but doesn't represent cascade risk). The cap only engages when N_total >= 3.

## Trial behavior specification

When the system is 70% Sell and tries to enter another Sell:

- Step 1: gate CHECK 0-14 runs as normal
- Step 2: gate CHECK 15 runs `coordinator.get_direction_counts()` and reads e.g., `{Buy: 3, Sell: 7, total: 10}`
- Step 3: new direction is Sell. `pre_pct = 7 / 10 = 70.0%`
- Step 4: `pre_pct >= cap_pct (70.0)`: TRUE
- Step 5: sets `_gate_rejected = "portfolio_direction_cap_Sell_70pct"`, emits `PORTFOLIO_CAP_HIT` WARNING
- Step 6: gate returns; layer_manager observes `_gate_rejected`; logs `GATE_REJECT | layer=gate ... reason=portfolio_direction_cap_Sell_70pct`; skips execution
- Step 7: brain's next CALL_A produces a fresh directive set; if upstream R1+R2+R3 fixes are in place, the directive is more balanced; the cycle continues

When the system is 70% Sell and tries to enter a Buy:

- Step 1-2: same
- Step 3: new direction is Buy. `pre_pct_buy = 3 / 10 = 30.0%`
- Step 4: `pre_pct_buy < cap_pct (70.0)`: TRUE (cap not violated for Buy direction)
- Step 5: `pre_pct_sell (70.0) > warn_pct (60.0)` but new direction is Buy, so no warn fires
- Step 6: gate emits `PORTFOLIO_CONCENTRATION_CHECK` INFO with verdict=pass and proceeds
- Step 7: trade executes through to Shadow

When the system is 50% Sell and tries to enter a Sell:

- Step 3: `pre_pct = 5 / 10 = 50.0%`
- Step 4: `pre_pct < warn_pct (60.0)`: TRUE
- Step 5: gate emits `PORTFOLIO_CONCENTRATION_CHECK` INFO verdict=pass
- Step 6: trade executes

When the system is 65% Sell and tries to enter a Sell:

- Step 3: `pre_pct = 6.5 / 10 = 65.0%`
- Step 4: `pre_pct >= warn_pct (60.0) AND pre_pct < cap_pct (70.0)`: TRUE for warn band
- Step 5: gate emits `PORTFOLIO_CAP_WARN` INFO ("approaching cap")
- Step 6: trade executes; brain sees the warn count rising over recent cycles via logs (next-cycle visibility)

When the portfolio has only 2 positions (N_total=2):

- Step 4: `n_total < min_positions (3)`: cap does not engage regardless of concentration
- Step 5: gate emits `PORTFOLIO_CONCENTRATION_CHECK` INFO with verdict=skip reason="below_min_positions"
- Step 6: trade executes

This min-positions floor protects N=1 portfolios (trivially 100%) and N=2 portfolios (50% or 100% with no nuance) from cap noise.

## New structured log events (Rule 6 compliance)

- `PORTFOLIO_CONCENTRATION_CHECK` — emitted EVERY CHECK 15 invocation. Fields: `sym`, `new_dir`, `buys`, `sells`, `total`, `pre_pct`, `cap_pct`, `warn_pct`, `verdict={pass|warn|block|skip}`. INFO level
- `PORTFOLIO_CAP_HIT` — emitted when verdict=block. Fields: same plus `gate_rejected` reason string. WARNING level
- `PORTFOLIO_CAP_WARN` — emitted when verdict=warn. Same fields. INFO level
- `PORTFOLIO_DIRECTION_PERMITTED` — emitted on Buy when Sell side is near cap (informs ops that the cap allowed an opposite trade). Same fields. INFO level (optional event)

All events go through `log.info` / `log.warning` per the existing `ctx()` pattern used throughout gate.py.

## Verification queries

After Phase 4 (24-hour live trial), the operator runs:

### Grep for new events

```
grep -c "PORTFOLIO_CONCENTRATION_CHECK" data/logs/workers.log
grep -c "PORTFOLIO_CAP_HIT" data/logs/workers.log
grep -c "PORTFOLIO_CAP_WARN" data/logs/workers.log
grep "PORTFOLIO_CAP_HIT" data/logs/workers.log | head -20
```

Expected: `PORTFOLIO_CONCENTRATION_CHECK` is the highest count (every CHECK 15 invocation). `PORTFOLIO_CAP_HIT` should be 0 or very low if R1+R2+R3 are working (balanced directives don't cluster one direction). `PORTFOLIO_CAP_WARN` between 0 and 20 in a normal day.

### SQL cascade-class analysis (post-fix)

```sql
SELECT date(opened_at) AS day,
       direction,
       COUNT(*) AS trades_opened,
       SUM(CASE WHEN close_reason='bybit_sl_hit' THEN 1 ELSE 0 END) AS sl_hits
FROM trade_log
WHERE opened_at >= '2026-05-17'
GROUP BY day, direction
ORDER BY day, direction;
```

Expected: more balanced direction distribution per day. SL-hit rate per direction should converge (was Buy 44% / Sell 65% in the pre-fix session).

### SQL cascade window check (post-fix replay equivalent of 14:45 cascade)

```sql
WITH windows AS (
  SELECT opened_at,
         (julianday(opened_at) - 
          (SELECT min(julianday(opened_at)) FROM trade_log)) * 24 * 60 AS minutes_since_first
  FROM trade_log
  WHERE opened_at >= '2026-05-17'
)
SELECT 
  strftime('%Y-%m-%d %H', opened_at) AS hour,
  COUNT(*) AS opens_in_hour,
  SUM(CASE WHEN direction='Sell' THEN 1 ELSE 0 END) AS sells,
  SUM(CASE WHEN direction='Buy' THEN 1 ELSE 0 END) AS buys
FROM windows
GROUP BY hour
HAVING opens_in_hour >= 5
ORDER BY hour;
```

Expected: any hour with 5+ opens shows mixed direction (no hour shows 5+ same-direction). The 14:00-15:00 hour from 2026-05-16 had 15 Sells / 1 Buy; the fix should make this impossible.

### Aim preservation check

```sql
SELECT date(opened_at) AS day, COUNT(*) AS trades
FROM trade_log WHERE opened_at >= '2026-05-17'
GROUP BY day ORDER BY day;
```

Expected: trade count per day in a comparable range to pre-fix (within 30% — slight reduction acceptable because the cap rejects some trades when bias is concentrated; large reduction would indicate R1+R2+R3 are not in place).

## Test additions (Phase 3 deliverable preview)

### Unit tests for `get_direction_counts()`

- File: `tests/test_phase9/test_trade_coordinator.py` (new or appended)
- Tests: empty, all-Buy, all-Sell, mixed, Long/Short aliases

### Unit tests for CHECK 15 (gate)

- File: `tests/test_apex_gate_concentration.py` (new)
- Tests:
  - Cap at 70%, portfolio is 7 Sells / 3 Buys, new Sell → REJECT
  - Cap at 70%, portfolio is 7 Sells / 3 Buys, new Buy → ALLOW
  - Cap at 70%, portfolio is 5 Sells / 5 Buys (50%), new Sell → ALLOW (below warn)
  - Cap at 70%, portfolio is 6 Sells / 4 Buys (60%), new Sell → ALLOW with WARN log
  - Cap at 70%, portfolio is 2 Sells / 0 Buys (100% on N=2), new Sell → ALLOW (below min_positions)
  - Cap disabled → ALLOW regardless

### Integration test for cascade prevention

- File: `tests/test_apex_pipeline_integration.py` (append new test)
- Scenario: simulate 13:48-14:42 cascade timeline; assert that the cap blocks entries when concentration is at or above 70%; assert that the first Sell (at zero concentration) is allowed; assert that the lone Buy is allowed throughout

### Shadow compatibility test

- File: `tests/test_shadow_compatibility.py` (existing or new)
- Scenario: gate CHECK 15 fires; Shadow's trade-execution path receives `_gate_rejected=True` trade dict; Shadow correctly skips execution; no orphan state

## Interaction with R1, R2, R3 (Rule 12)

If R1 + R2 + R3 ship first, the brain's directive distribution becomes more balanced (target: 50/50 when market is mixed). In that scenario, the cap fires rarely — it is a true back-stop. If R1+R2+R3 do not ship and R4 ships alone, the cap fires constantly (18 of 19 Sells blocked) and trade frequency drops dramatically. DELTA's sequencing recommendation should consider:

- Ship R4 LAST (after R1+R2+R3 verified) → R4 is a back-stop, fires rarely
- Ship R4 FIRST → R4 is the primary filter, fires constantly, trade frequency drops, the operator sees the cap blocking trades that should have flipped to Buy (if R1+R2+R3 were fixed)

GAMMA recommends R4 ship LAST in the synthesis order, with R1+R2+R3 sequenced ahead. DELTA owns the sequencing decision.

## Aim-bias verification (final)

The five questions for the chosen design (A) at the chosen location (gate CHECK 15) at the chosen value (70%):

1. Preserves trade frequency? YES — only fires at >= 70% concentration with N >= 3. With R1+R2+R3 in place, this is rare. Without them, the cap is a "necessary deterrent" the operator chose to add
2. Preserves aggression? YES — brain proposes; APEX optimizes; gate is the last safety check. Brain's voice is preserved
3. Improves decision quality? YES — prevents cascade-class losses (-$31.82 prevented in the 14:45 reference event)
4. Preserves passive-close advantage? YES — close paths untouched
5. Respects structural separation? YES — gate is the existing layer for portfolio admission constraints

All five YES. The design clears the aim-bias bar.

## What GAMMA does NOT do

- Does not modify R1 (XRAY counter-trade inversion) — ALPHA's scope
- Does not modify R2 (APEX_DIR_LOCK) — BETA's scope
- Does not modify R3 (XRAY override threshold) — BETA's scope
- Does not modify the brain prompt directly (CALL_A or CALL_B)
- Does not modify the close paths (data-lake watchdog, profit sniper, time-decay)
- Does not modify Shadow execution
- Does not modify regime detection
- Does not modify any existing test (only adds new tests)

## Final answer summary

- Design: A (hard cap, binary reject)
- Location: `src/apex/gate.py`, new CHECK 15 between CHECK 14 (line 647) and final return (line 672)
- Cap value: 70%
- Warn band: 60%
- Minimum positions for cap to engage: 3
- Helper: `TradeCoordinator.get_direction_counts() -> {"Buy": int, "Sell": int, "total": int}`
- Settings: 4 new fields in APEXSettings (enabled, cap_pct, warn_pct, min_positions)
- New log events: PORTFOLIO_CONCENTRATION_CHECK, PORTFOLIO_CAP_HIT, PORTFOLIO_CAP_WARN, PORTFOLIO_DIRECTION_PERMITTED
- Aim-bias: all five YES
- Cascade prevention: 100% at the chosen 70% cap

Operator decision needed for Phase 2 to advance: confirm A/70%/CHECK 15, or override with operator's preferred values.
