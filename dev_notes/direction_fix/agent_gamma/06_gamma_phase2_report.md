# Agent GAMMA — Phase 2 Operator Report (R4)

This is the Phase 2 report for Agent GAMMA's investigation of Root Cause R4 (no portfolio direction concentration cap). It is operator-facing. Plain prose, h2/h3 structure, no emoji. The report leads with the confirmation that the cap is genuinely absent from the code (no partial implementation hiding somewhere), then walks through the cascade evidence, ranks the top three designs, and ends with the GAMMA recommendation. The decision request at the bottom asks the operator to confirm design choice, cap value, and insertion layer before Phase 3 implementation begins.

## Confirmation — Portfolio direction cap is genuinely absent

Phase 1 inventory (deliverable 01) verified the claim from Phase 0 by reading every relevant file end-to-end:

- The gate (`src/apex/gate.py`) has 15 checks. None aggregate cross-symbol direction counts at admission time
- The risk manager (`src/risk/risk_manager.py`) orchestrates drawdown, validator, portfolio analyzer, position sizer. None gate admission by portfolio direction
- The portfolio analyzer (`src/risk/portfolio.py`) has a `check_correlation()` function at lines 92-102 that COUNTS positions per direction and emits text warnings, but the text warnings are not consumed by any admission code — they are diagnostic only
- The position sizer (`src/risk/position_sizer.py`) uses direction only for SL placement geometry; no portfolio constraint
- The validator (`src/risk/validators.py`) has a per-symbol per-side duplicate check at lines 103, 123 ("Already have a Buy on BTCUSDT"); not a portfolio constraint
- The trade coordinator (`src/core/trade_coordinator.py`) holds `_trades` dict with `TradeState.side` per position, but offers no method to aggregate direction counts. The `get_status()` method at lines 1836-1868 returns per-symbol stats but omits the `side` field
- The performance enforcer (`src/strategies/performance_enforcer.py`) tracks `_per_direction` Buy/Sell win-loss counters for coaching text, but does not gate admission on direction balance
- The fund manager has two observational direction metrics — `EcosystemHealthMonitor._correlation_score()` and `RiskWeatherAssessor._assess_correlation()` — both feed health/risk-weather dashboards, neither gates admission

The three per-symbol direction-aware mechanisms (gate CHECK 6 cooldown, gate CHECK 6b re-entry learning, validator duplicate check) all operate on `(symbol, direction)` pairs — they prevent re-entering the same symbol in the same direction. None of them notice that the portfolio is 87% one direction across different symbols.

R4 is a NEW code path. There is no partial implementation to extend. The cap is genuinely absent.

## Cascade evidence — 14:45 of 2026-05-16

GAMMA reconstructed the 14:45 5-position SL cascade from authoritative `data/trading.db` trade_log entries (deliverable 02). Twenty-one trades were opened between 13:48 and 14:53 UTC. Twenty of them were Sell. The one Buy (OPUSDT) won +$2.30; all the Sells either lost or barely broke even.

### The cascade itself

Five Sell positions hit stop-loss within 6 minutes:

- 14:45:08 AVAXUSDT SL -$5.61 (2.7 min hold)
- 14:45:11 APTUSDT SL -$4.98 (20 min hold)
- 14:45:46 SANDUSDT SL -$4.42 (12 min hold)
- 14:46:17 LINKUSDT SL -$2.70 (12 min hold)
- 14:51:24 ORCAUSDT SL -$14.11 (9 min hold)

Total: -$31.82. The four initial SL hits at 14:45:08-14:46:17 all closed at near-identical -0.29% to -0.33% PnL — diagnostic of synchronized SL trips across positions with similar SL-distance configurations. A single market bounce swept all four.

If the broader 8-minute window 14:43:12 to 14:51:24 is counted (adding MNTUSDT -$5.33 and AXSUSDT -$0.92), the cumulative loss is -$38.07. This is the figure FINDINGS.md reports.

### Pre-cascade portfolio direction concentration

At the moment of each cascade-member entry:

- AVAXUSDT entered 14:42:25 with 7 Sells + 1 Buy already open (87.5% Sell concentration)
- ORCAUSDT entered 14:42:27 with 8 Sells + 1 Buy already open (88.9% Sell concentration)
- APTUSDT (second entry, 14:25) entered with 9 Sells + 1 Buy already open (90.0% Sell concentration)
- SANDUSDT entered 14:34:03 with 9 Sells + 1 Buy already open (90.0% Sell concentration)
- LINKUSDT entered 14:34:05 with 11 Sells + 1 Buy already open (91.7% Sell concentration)

Every single cascade-member entry was at >= 87.5% Sell concentration. No portfolio direction cap was in place; the brain (responding to the upstream R1+R2+R3 bias) produced these directives and they all reached execution.

### Cap simulation against the cascade

If a hard cap had been in place during this window, every cascade-member entry would have been blocked at any reasonable cap value (60%, 70%, OR 80%) because the pre-entry concentration was always >= 87.5%.

- 60% cap: 18 of 19 Sell entries blocked, full -$31.82 cascade prevented
- 70% cap: 18 of 19 Sell entries blocked, full -$31.82 cascade prevented
- 80% cap: 18 of 19 Sell entries blocked, full -$31.82 cascade prevented
- 90% cap: 14 of 19 Sell entries blocked, 4 of 6 cascade-window losses prevented (-$12.02)

The cap value of 60/70/80% does NOT change the cascade-prevention outcome in this evidence. It changes only what happens in the 50-79% concentration band that this session did not visit much.

## Top three designs ranked

GAMMA evaluated five candidate designs (A through E, full evaluation in deliverable 03). Ranked top three:

### 1. Design A — Hard cap (RECOMMENDED)

When portfolio concentration in one direction reaches N%, new entries in that same direction are blocked outright via `_gate_rejected`. Opposite-direction entries unaffected.

- Implementation complexity: 2 of 5
- Aim-bias: ALL FIVE QUESTIONS YES
- Cascade prevention: 100% at any 60-80% cap
- Cleanness: HIGH (reuses existing `_gate_rejected` pattern from CHECK 6)
- Visibility: HIGH (clean PORTFOLIO_CAP_HIT WARNING log)
- Override: medium (config-tunable; per-trade override forbidden by spec Rule 3)

### 2. Design D — Concentration-aware sizing (alternative)

Each new entry's size scales inversely with current direction concentration via a smooth multiplier. No hard rejection — just smaller sizes when concentrated.

- Implementation complexity: 2 of 5
- Aim-bias: 4 YES, 1 PARTIAL (mixes sizing with admission)
- Cascade prevention: ~75% magnitude reduction (5 positions still enter but at 25% size)
- Visibility: MEDIUM (decision hidden in size column)
- The cascade SHAPE persists (5 positions still hit SL together, just smaller magnitudes)

### 3. Design B — Soft cap with size reduction (close alternative)

50% no reduction, 60% reduce by 50%, 70% reduce by 75%, 80% block. A graduated response.

- Implementation complexity: 3 of 5
- Aim-bias: 4 YES, 1 PARTIAL
- Cascade prevention: similar to D
- The 4 thresholds add configuration surface area without clear benefit

Designs C (aim-conditional) and E (time-rotation) are NOT recommended — both introduce cross-layer dependencies that violate the architecture constraints in spec A.5.

## GAMMA recommendation

Design A (hard cap) at cap value 70%, inserted as new CHECK 15 in `src/apex/gate.py` after CHECK 14 (line 647) and before the final return (line 672).

### Why Design A over D

The operator's stated trading philosophy (spec A.1) is "characterize each coin's current situation and exploit it." A rejected trade says "the portfolio is too concentrated; pick a different coin." A sized-down trade says "trade this coin but smaller, which is worse than not trading it." Design A asks the brain to find a better opportunity; Design D asks the brain to compromise on the chosen opportunity. The first is more aligned with "exploit each situation"; the second hides the cap decision in the size column.

A second reason is observability. The operator (a blind user with a screen reader) benefits from binary signals: TRADE-EXECUTED vs TRADE-REJECTED is unambiguous. SIZED-DOWN is fuzzy: the same coin executes but smaller; without reading the precise size and the precise multiplier, the operator cannot tell whether the cap fired or whether some other check fired.

### Why 70% over 60% or 80%

The cascade evidence does not discriminate between 60-80% because every cascade entry was at >= 87.5%. The choice is about the 50-79% band:

- 60% fires when portfolio is "modestly tilted" (e.g., 3 Sells, 2 Buys). The operator's philosophy of "exploit each coin" suggests this tilt is acceptable
- 80% is so close to 100% that the cap is almost a no-op (it fires only at extreme concentration; the 14:45 cascade had ALREADY reached 87.5% before the cascade-members entered, so 80% would still have caught them — but 80% feels like a back-stop only, not a balancer)
- 70% is the most aim-aligned point: portfolios may be moderately tilted (50-69%) without firing the cap, but at 70% concentration the cascade risk dominates and the cap engages

70% also aligns with existing observational direction code: `EcosystemHealthMonitor._correlation_score()` (60-67% concentration is "low correlation" by its scoring) and `RiskWeatherAssessor._assess_correlation()` (80% is "high risk"). 70% sits between observational warning and observational alarm.

### Why gate CHECK 15 over APEX or brain prompt

The gate is the layer for portfolio-level admission constraints. CHECK 3 (max concurrent positions), CHECK 5 (duplicate symbol), CHECK 6 (cooldown) all consult the position service or coordinator to enforce portfolio-shape rules. CHECK 15 follows this pattern.

APEX is the OPTIMIZATION layer; injecting admission gating into APEX violates the layer contract.

Brain prompt is advisory; the brain has been observed producing 89% Sell directives in the 2026-05-16 session despite XRAY data suggesting the upstream bias. A brain-prompt-only fix is forbidden by spec Rule 3 because "brain might ignore."

A hybrid (gate CHECK 15 enforcement plus brain-prompt context informing the brain of cap state) is acceptable. GAMMA defers the brain-prompt context to a Phase 2 enhancement after R4 ships and is verified.

## Trial behavior specification

Synthesized from deliverable 05; reproduced here for the operator's review:

### When system is 70% Sell and tries to enter another Sell

- Gate CHECK 0-14 runs as normal
- CHECK 15 calls `coordinator.get_direction_counts()` and reads `{Buy: 3, Sell: 7, total: 10}`
- `pre_pct = 70.0%`
- Cap is 70.0%. Condition `pre_pct >= cap_pct` is TRUE
- Sets `_gate_rejected = "portfolio_direction_cap_Sell_70pct"`
- Emits `PORTFOLIO_CAP_HIT | sym={symbol} new_dir=Sell pre_pct=70.0% cap_pct=70.0% buys=3 sells=7 total=10` at WARNING
- layer_manager observes `_gate_rejected`, logs `GATE_REJECT`, SKIPS execution
- Brain's next CALL_A cycle starts fresh; if R1+R2+R3 fixes are in place, directives are more balanced

### When system is 70% Sell and tries to enter a Buy

- CHECK 15 reads `{Buy: 3, Sell: 7, total: 10}`
- New direction is Buy; `pre_pct_buy = 30.0%`
- Cap-violated check is direction-specific: 30% < 70% → not violated for Buy
- Gate emits `PORTFOLIO_CONCENTRATION_CHECK` INFO with verdict=pass
- Trade executes through to Shadow normally

### When system is 65% Sell (warn band) and tries to enter a Sell

- `pre_pct = 65.0%`; warn = 60.0; cap = 70.0
- `pre_pct >= warn_pct AND pre_pct < cap_pct`: warn fires
- Emits `PORTFOLIO_CAP_WARN | sym={symbol} new_dir=Sell pre_pct=65.0% warn_pct=60.0% cap_pct=70.0%` INFO
- Trade executes (warn does not block)
- Operator sees the warn count rise in `data/logs/workers.log` and can intervene if desired

### When portfolio has only 2 positions

- `n_total = 2 < min_positions (3)`
- Cap does not engage; emits `PORTFOLIO_CONCENTRATION_CHECK` INFO verdict=skip reason=below_min_positions
- Trade executes

The `min_positions = 3` floor prevents cap noise on N=1 (trivially 100%) and N=2 (50% or 100% portfolios with no nuance).

## Verification queries (post-Phase-4)

The operator runs these after the 24-hour live trial:

### Grep for new log events

```
grep -c "PORTFOLIO_CONCENTRATION_CHECK" data/logs/workers.log
grep -c "PORTFOLIO_CAP_HIT" data/logs/workers.log
grep "PORTFOLIO_CAP_HIT" data/logs/workers.log | head -20
```

Expected: many `PORTFOLIO_CONCENTRATION_CHECK` (every gate run). Few `PORTFOLIO_CAP_HIT` if R1+R2+R3 are in place. Each HIT line has `sym`, `new_dir`, `pre_pct`, `cap_pct`, and the counts.

### SQL check — direction distribution per day

```sql
SELECT date(opened_at) AS day, direction, COUNT(*) AS trades_opened
FROM trade_log WHERE opened_at >= '2026-05-17'
GROUP BY day, direction ORDER BY day, direction;
```

Expected: balanced direction distribution per day. Pre-fix on 2026-05-16 was 11 Buy / 73 Sell (89% Sell). Post-fix target is closer to 50/50 when market is mixed.

### SQL check — no hour has 5+ same-direction opens

```sql
SELECT strftime('%Y-%m-%d %H', opened_at) AS hour,
       COUNT(*) AS opens,
       SUM(CASE WHEN direction='Sell' THEN 1 ELSE 0 END) AS sells,
       SUM(CASE WHEN direction='Buy' THEN 1 ELSE 0 END) AS buys
FROM trade_log WHERE opened_at >= '2026-05-17'
GROUP BY hour HAVING opens >= 5 ORDER BY hour;
```

Expected: no hour with 5+ opens shows all-Sell or all-Buy distribution. Pre-fix 2026-05-16 14:00 hour was 15 Sells / 1 Buy; the fix should make this impossible.

## Decision request

GAMMA's Phase 1 is complete. GAMMA recommends:

- Design A (hard cap with `_gate_rejected`)
- Cap value 70% with warn band 60% and minimum positions 3
- Location: new CHECK 15 in `src/apex/gate.py`, after CHECK 14, before final return
- New helper: `TradeCoordinator.get_direction_counts()`
- New settings: `portfolio_direction_cap_enabled` (True), `portfolio_direction_cap_pct` (70.0), `portfolio_direction_cap_warn_pct` (60.0), `portfolio_direction_cap_min_positions` (3) in `APEXSettings`
- New log events: PORTFOLIO_CONCENTRATION_CHECK (INFO), PORTFOLIO_CAP_HIT (WARNING), PORTFOLIO_CAP_WARN (INFO), PORTFOLIO_DIRECTION_PERMITTED (INFO)

Operator decisions required before Phase 3 begins:

1. Confirm Design A or substitute (D is the alternative for "no rejections")
2. Confirm cap value 70% or substitute (60% if you want earlier engagement; 80% if you want pure back-stop)
3. Confirm CHECK 15 in gate.py or substitute location
4. Confirm sequencing relative to R1/R2/R3 — GAMMA recommends R4 ship LAST (after R1+R2+R3) so it is a back-stop rather than the primary filter. DELTA's synthesis will own the final sequence

GAMMA awaits operator response. Phase 3 implementation does not begin until decisions 1, 2, 3, 4 are confirmed.
