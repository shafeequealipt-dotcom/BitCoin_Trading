# Agent GAMMA — Cap Design Options (R4)

This document evaluates the five candidate designs (A through E) for the portfolio direction concentration cap. Each design is rated on implementation complexity, insertion point, aim-bias compliance against the five questions, expected cascade impact, false-rejection risk, backup behavior, and telemetry footprint.

The aim-bias questions (from spec A.4 and Phase 0 reconnaissance) are answered explicitly per design.

## Aim-bias questions reminder

1. Does this preserve trade frequency?
2. Does this preserve aggression?
3. Does this improve decision quality?
4. Does this preserve passive-close advantage (data-lake watchdog)?
5. Does this respect structural separation of concerns?

A design must answer YES (without operator override) to all five to ship as recommended.

## Cascade reference

Cascade reconstruction (02): the 14:45 5-position SL cascade. Each new Sell entry from 13:48 onward was at >= 87.5% Sell concentration. Total loss: -$31.82 in 6 minutes, with -$38.07 in the broader 8-minute window. Cap simulations at 60/70/80 all block 18 of 19 Sell entries; only the first Sell (HYPEUSDT, zero concentration) clears.

## Design A — Hard cap

When portfolio concentration in one direction reaches N%, new entries in that same direction are blocked outright. New entries in the opposite direction are unaffected.

- Cap value: 60%, 70%, or 80%
- Mechanism: read `TradeCoordinator._trades` for live Buy/Sell counts, compute pct, return `_gate_rejected = "portfolio_direction_cap"` if new direction's pre-entry pct would be at-or-above cap. Note: gate operates on "pre-entry pct" so a single-direction portfolio at 100% (N=1, 1 Sell open) blocks the second Sell at any cap value
- Where: new CHECK 15 in `src/apex/gate.py` (after CHECK 14, before return)
- Reject string: `portfolio_direction_cap_{dir}_{pct}` so layer_manager's `GATE_REJECT` log message is informative

Implementation complexity: 2 of 5 (low). Reads existing `_trades` dict; emits standard `_gate_rejected`. New helper `TradeCoordinator.get_direction_counts() -> dict` is needed (~10 LOC). Test additions: 4-5 cap-firing unit tests, 1 integration test that exercises gate CHECK 15 end-to-end. The unique work versus existing gate checks is the cap-comparison arithmetic and the new helper.

Insertion point integration: same pattern as CHECK 6 (cooldown) — set `trade["_gate_rejected"] = reason`; `modifications.append("REJECTED:portfolio_direction_cap_..."); log.warning(GATE_REJECT ...)`. The layer_manager observes `_gate_rejected` and skips execution. This pattern is established and well-tested.

Aim-bias evaluation:

1. Preserves trade frequency? Partial yes. When portfolio is balanced, no blocking. When portfolio is 70%+ one direction, opposing-direction entries still fire freely (the brain's CALL_A produces ~1-3 candidates per cycle; if even one is opposite the dominant direction, it executes). In the 13-15h cascade window R4 alone would have reduced Sells from 19 to 1 — but R4 is designed to ship after R1+R2+R3 reduce the upstream bias. With R1+R2+R3 in place, the brain produces ~50/50 calls and R4 only fires occasionally
2. Preserves aggression? Yes. Brain still proposes decisively; APEX still optimizes; the cap is the LAST checkpoint. Aggression is about the brain's voice, not about every trade getting through
3. Improves decision quality? Yes. Concentrated portfolios have correlated SL trips (the 14:45 cascade is the textbook case). Blocking same-direction entries when already 70%+ one-direction directly improves decision quality by preventing cascade-class outcomes
4. Preserves passive-close advantage? Yes. R4 only gates ENTRY. Close paths (data-lake watchdog, profit sniper, time-decay) are untouched
5. Respects structural separation? Yes. Gate is the existing layer for portfolio-level admission constraints (CHECK 3 max concurrent positions, CHECK 5 dup, CHECK 6 cooldown). R4 is a portfolio constraint; gate is its layer

Expected cascade impact: 100% prevention at 60/70/80%. Cascade members (AVAX, APT@14:25, SAND, AXS, LINK, ORCA@14:42) all entered at >= 87.5% concentration; all five are blocked at any reasonable cap.

False rejection risk: HIGH when R4 ships alone, LOW when R4 ships with R1+R2+R3 in place. The "false rejection" here means "the brain genuinely picked a good Sell setup but the cap blocked it because the portfolio was already 70% Sell." This rejection is BY DESIGN — the operator's stated philosophy is balanced both-direction trading. A genuinely good Sell setup at 70%+ concentration means the operator picked TOO MANY Sells already; the cap protects against further pile-on. False rejection only becomes problematic if the brain's bias is correct AND the market continues one-directional for >30 minutes — that scenario is the Design E rotation concern. The Cap value table in section "Cap value recommendation" addresses this trade-off.

Backup behavior when cap fires: HARD REJECT via `_gate_rejected`. Same pattern as gate CHECK 6 same-direction cooldown. No size-down, no queue, no defer. The cap fires LOUDLY (WARNING log) so the operator sees every rejection.

Telemetry footprint: three new events (per spec Rule 6 requirements):

- `PORTFOLIO_CONCENTRATION_CHECK`: emitted every gate CHECK 15 evaluation, fields = `sym`, `new_dir`, `buys`, `sells`, `pct_same_dir`, `cap_pct`, `verdict={pass|block|warn}`
- `PORTFOLIO_CAP_HIT`: emitted when verdict=block. Fields = `sym`, `new_dir`, `pre_pct`, `cap_pct`, `would_be_post_pct`, `total_open_positions`. Logged at WARNING
- `PORTFOLIO_CAP_WARN`: emitted at warning band (e.g., 65-70% if cap is 70%). Logged at INFO. Informs the brain on the next CALL_A prompt-build that it's near the cap (Rule 6's third event family)
- `PORTFOLIO_DIRECTION_PERMITTED`: emitted on pass with `pct_same_dir` below warn band. Fields = same as CHECK. INFO level

## Design B — Soft cap with size reduction

A graduated response: 50% no reduction, 60% reduce new entry size by 50%, 70% reduce by 75%, 80% block. This is a smooth-instead-of-cliff response.

Implementation complexity: 3 of 5 (medium). Mathematically straightforward but bands and thresholds add config surface area (4 numbers vs Design A's 1 number). Same insertion point as Design A.

Insertion point: same as Design A — CHECK 15 in gate.py.

Aim-bias evaluation:

1. Preserves trade frequency? Yes (better than A). Trades still fire at 60-79% concentration, just smaller. Frequency is preserved more aggressively than Design A
2. Preserves aggression? Mostly yes. Brain proposes; APEX optimizes; gate sizes-down rather than blocks. But this sends a mixed signal — gate is changing APEX's chosen size based on portfolio direction, which couples R4 to APEX sizing in a way that hides the cap behavior in the size column rather than expressing it as a rejection
3. Improves decision quality? Partial yes. Smaller size on concentrated direction = smaller loss when cascade fires. But still permits the cascade-shape outcome (multiple positions in same direction hit SL together — the loss magnitude is reduced but the correlation risk remains)
4. Preserves passive-close advantage? Yes (close paths untouched)
5. Respects structural separation? PARTIAL. Soft-cap mixes admission-gating with sizing — two concerns in one check. The gate has CHECK 1 (max size) for sizing; bringing portfolio-direction logic into sizing blurs the separation

Expected cascade impact: 14:45 cascade losses reduced by ~75% (from -$31.82 to ~-$8). The 5 positions still enter, but at 25% size. Final $ loss is proportional. Cascade SHAPE persists.

False rejection risk: LOWER than Design A in pure trade-frequency terms (more trades execute), HIGHER in portfolio-shape terms (cascade still happens at smaller magnitude).

Backup behavior when at hard limit (80%): rejects same as Design A. Below 80% but above 60%: size-down with `modifications.append("portfolio_concentration_size_reduce_{pct}%")`.

Telemetry footprint: same four events, with `PORTFOLIO_CAP_HIT` additionally fired at the band boundary transitions (50→60 → 70 → 80).

## Design C — Aim-bias-conditional cap

The cap fires ONLY when XRAY analysis suggests the OPPOSITE direction is viable at the moment (i.e., the new same-direction entry is being rejected because a Buy is genuinely available right now and the system should pick that instead).

Implementation complexity: 5 of 5 (high). Requires:

- New CHECK 15 reading concentration AND XRAY structural state for ALL universe coins simultaneously
- New helper that scans the structure_cache for opposite-direction setups with viable XRAY confidence (>= 0.6) above some count threshold (e.g., 3 opposite-direction setups available)
- Coupling between gate (admission) and structure_cache (Layer 1B output)
- The decision becomes "is there a viable Buy alternative" rather than "is the portfolio too one-directional"
- This is also pre-R1 contingent: if R1 is broken (XRAY suggested_direction is always 87% short), the helper is using corrupted data

Insertion point: same as A.

Aim-bias evaluation:

1. Preserves trade frequency? Yes. Cap only fires when there's a viable alternative; otherwise pass-through
2. Preserves aggression? Yes
3. Improves decision quality? Yes when working correctly, but the decision is now contingent on XRAY signal integrity (R1 scope) and on a "viable alternative exists" check that has high false-positive/false-negative risk
4. Preserves passive-close advantage? Yes
5. Respects structural separation? NO. Gate now reads structure_cache from a different layer, couples to L1B, and depends on R1 being fixed. Cross-layer dependency violates the Phase 0 architecture constraint

Expected cascade impact: 14:45 cascade fully blocked IF XRAY had viable opposite-direction signals at those moments. The cascade reconstruction shows the brain produced only ONE Buy (OPUSDT at 14:25) in the entire 21-trade window — implying XRAY did NOT have abundant opposite-direction setups. Design C would have BLOCKED LITTLE because the alternative-availability check would have failed most of the time.

False rejection risk: high in both directions (over-permissive when XRAY signal is weak; under-permissive when XRAY is over-confident).

Backup behavior: when no alternative exists, allow pass-through. When alternative exists, hard reject same as A.

Telemetry: needs additional event `PORTFOLIO_CAP_ALTERNATIVE_AVAILABLE` and `PORTFOLIO_CAP_ALTERNATIVE_ABSENT` to make the decision tree auditable.

## Design D — Concentration-aware sizing (smooth)

Each new entry's size scales inversely with current direction concentration as a single multiplier:
- 50% direction = 1.0x size
- 60% direction = 0.8x size
- 70% direction = 0.5x size
- 80% direction = 0.25x size
- 90% direction = 0.1x size
- 100% direction = block (or fallback to 0)

No hard rejection band — just a smooth multiplier on size.

Implementation complexity: 2 of 5 (low). One-line lookup table; one multiplication; one log emission.

Insertion point: could go either in CHECK 4 (capital availability — sizing logic) or as a new CHECK 15 (portfolio direction sizing). Spec Part F explicitly calls Design D "concentration-aware sizing".

Aim-bias evaluation:

1. Preserves trade frequency? YES (best of all designs). Trades still execute at 70-89% concentration, just smaller
2. Preserves aggression? Mixed. Brain proposes a size; APEX optimizes; gate now scales DOWN based on portfolio direction. The brain's directive size becomes contingent on portfolio state. This is closer to the existing pattern of CHECK 3 (max concurrent positions also reduces size by 0.3x at the 5-position cap)
3. Improves decision quality? Partial yes. Smaller losses on cascade events. But the cascade SHAPE persists (5 positions trip SL together, just smaller losses)
4. Preserves passive-close advantage? Yes
5. Respects structural separation? PARTIAL. Mixes admission with sizing (same critique as B)

Expected cascade impact: 14:45 cascade losses reduced by ~75-90% at 70-90% concentration. The 5 entries enter at 0.1x to 0.25x size. Final $ loss ~ $4 instead of $31.82.

False rejection risk: very low (no rejections). False sizing risk: moderate (sizing logic now has another input variable that operator must understand).

Backup behavior: no rejection; size scaling at every band.

Telemetry: `PORTFOLIO_CONCENTRATION_CHECK` with `size_multiplier_applied`. Cleaner than B because there's only one decision branch (size = old_size * multiplier).

## Design E — Time-based cap rotation

If portfolio has been >= 70% one direction for >= 30 minutes, force the NEXT entry to be opposite direction (only if structure permits) or block.

Implementation complexity: 5 of 5 (high). Requires:

- Time-series state tracking (when did portfolio first cross 70%? Has it stayed there 30 minutes?)
- Logic to interact with brain — "force opposite direction" means either rejecting all same-direction brain proposals OR sending a directive into the brain prompt that says "the next trade must be Buy"
- Either interpretation is a heavy architectural change

Insertion point: same as A but with time-state.

Aim-bias evaluation:

1. Preserves trade frequency? Mixed. Will block all same-direction trades after the rotation trigger fires. Frequency drops to opposite-direction-only until rotation completes
2. Preserves aggression? NO. Force-opposite contradicts the operator's "exploit each coin's situation" philosophy
3. Improves decision quality? Mixed. Time-rotation captures the intuition that markets mean-revert, but does so with a heuristic rather than data
4. Preserves passive-close advantage? Yes
5. Respects structural separation? NO. Couples gate to brain CALL_A reasoning (forcing direction). Crosses architecture boundaries

Expected cascade impact: hard to predict — depends on whether the 14:00-14:42 build-up triggered the 30-minute rotation timer. If it did, the cascade members would be blocked. If not, they pass.

False rejection risk: high. The time threshold (30 min) is arbitrary. A coin that genuinely deserves a Sell after 30 minutes of one-direction is incorrectly forced to Buy or blocked.

Backup behavior: block or force-opposite. Both options are problematic.

Telemetry: requires new state-event family for rotation timer.

## Summary table

| Design | Complexity | Aim-bias | Cascade prevention | False reject | Best for |
|--------|-----------|----------|-------------------|--------------|----------|
| A (hard cap) | 2 | All YES | 100% | Acceptable, hardest stop | Operator wants clear, predictable, auditable behavior |
| B (soft cap w/ size) | 3 | 4 YES, 1 PARTIAL | Reduced magnitude | Lower | Operator wants smoother throttling |
| C (aim-conditional) | 5 | 4 YES, 1 NO | Conditional, R1-dependent | High both ways | Avoid — too coupled |
| D (concentration sizing) | 2 | 4 YES, 1 PARTIAL | Reduced magnitude | Very low | Operator wants no rejections |
| E (time rotation) | 5 | 3 YES, 2 NO | Conditional | High | Avoid — over-coupled |

## Cap value recommendation (preliminary)

Synthesis in 05 will state the final recommendation. Preliminary read:

- 60% — fires early; reduces concentrated-portfolio risk most aggressively; rejects more legitimate trades
- 70% — middle ground; aligns with `RiskWeatherAssessor` 80% bucket inferentially; respects "the portfolio can still be modestly tilted (60-69%) without firing the cap"
- 80% — fires late; near-100% one-direction portfolios already; effectively a true back-stop rather than a balancer

The cascade reconstruction (02) is silent between 60-80% because every cascade entry was at >= 87.5%. The cap value choice depends on operator philosophy: 60% favors balance, 80% favors back-stop.

Sentinel recommendation: 70%. Rationale developed in 05.

## Recommendation pre-synthesis

Design A at cap 70% is the leading candidate because:

- Complexity 2 of 5 — low, no cross-layer dependencies
- Five YES on aim-bias
- 100% cascade prevention at any 60-80% cap value
- Clear, auditable, telemetric
- Reject pattern matches existing CHECK 6 and CHECK 6b — established hard-reject pattern

Design D (concentration-aware sizing) is the strong alternative because:

- Complexity 2 of 5
- Four YES + one PARTIAL on aim-bias (sizing mixed with admission, but the same critique applies to existing CHECK 3)
- Reduced cascade magnitude (~75% reduction) without rejecting trades
- Less operator-friction (no GATE_REJECT noise)

Designs B, C, E are not recommended. B is a softer A; C and E introduce cross-layer coupling that violates the architecture constraints (spec A.5).

If the operator prefers no rejections, choose D. If the operator prefers an explicit, predictable, auditable cap, choose A. The synthesis in 05 picks one with full justification.
