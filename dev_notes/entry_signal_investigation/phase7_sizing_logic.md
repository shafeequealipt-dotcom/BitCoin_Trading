# Phase 7 — Sizing Logic

The complete path from signal/consensus to final position size.

## TL;DR: Sizing Is NOT A Single Function

The system has **four separately-controlled sizing influences** that are NOT composed into one multiplier. Instead, each layer either OVERRIDES or DOWNGRADES the size proposed by the previous layer. The Phase 1 strategist agent confirmed: there is no `final_size_mult` field on `CoinPackage`, and the strategist never reads the ensemble's computed size multiplier.

The four influences:

1. **Ensemble `final_size_mult`** — computed (ensemble.py:275-293), logged, **DISCARDED**.
2. **Strategist via Claude** — picks `size_usd` from `[0, per-trade ceiling]` based on prompt text.
3. **APEX optimizer** — clamps `size_usd` between floor 100 and cap (max_position_size_usd=1200).
4. **APEX gate** — multiplies `size_usd` through 7 layered checks.

The "consensus-rises-with-size" pattern observed in Phase 3 Claim 3 happens via Claude's interpretation of the consensus label in the prompt, NOT via code-side multiplication.

## Layer 1: Ensemble `final_size_mult` (Dead End)

Per Phase 4: `final_size_mult = CONSENSUS_SIZE[consensus] × clamp(setup_type_confidence, 0.5, 1.0)`. Domain `[0.075, 1.0]`.

```
STRONG, setup_conf=0.85 → 1.00 × 0.85 = 0.85
GOOD,   setup_conf=0.85 → 0.75 × 0.85 = 0.6375
WEAK,   setup_conf=0.85 → 0.30 × 0.85 = 0.255
STRONG, counter setup    → 1.00 × 0.50 = 0.50  (counter_mult=0.7 → 0.385 clamped to 0.5 floor)
```

This value lives on `EnsembleResult.size_multiplier` (ensemble.py:304). It is emitted to the `ENSEMBLE_VOTE_WEIGHTED` log line. It is NOT placed onto any `CoinPackage` field that the strategist consumes (Phase 1 strategist map confirms — `pkg.strategies` has only `fired_count, fired_strategies, ensemble_consensus, consensus_score, total_score`).

**Conclusion**: the ensemble's sizing recommendation is computed and discarded. Sizing is fully delegated to Claude based on the consensus LABEL alone.

## Layer 2: Claude Selects `size_usd`

The strategist's TRADE_SYSTEM_PROMPT instructs Claude (strategist.py:106 and mirror :367):
> "size_usd: within the per-trade size limit shown above — strong conviction = larger, borderline = smaller. Stay within the numeric ceiling."

Claude sees:
- `Per-trade size limit: $X` (strategist.py:3718-3720) from `tiered_capital.get_limits(equity, deployed).max_single_trade`.
- For each coin: the ensemble consensus label ("STRONG" / "GOOD" / "WEAK"), the SIG_CLASSIFY direction & confidence, the setup type, the per-coin regime, the votes/opposition breakdowns.

Claude returns a JSON object with `size_usd` for each proposed trade.

The strategist parser (`_parse_trade_plan` at strategist.py:4946-4986) reads `size_usd` from Claude's response. No code-side multiplier is applied at this stage.

### What ACTUALLY happens when consensus is STRONG

Claude's reasoning: "STRONG consensus → high conviction → larger size." The data in Phase 3 Claim 3 shows:
- ≤4 supporting strategies (often WEAK consensus): 107 trades, +$300 net.
- 5+ supporting (STRONG-leaning): combined negative.

Since the per-strategy weights are uniform 1.0, 5+ supporters generates `agreeing >= 5.0` which qualifies for both STRONG (with low opposing) and GOOD (with mid opposing). The label appears in the prompt as STRONG → Claude sizes up → larger size on worse outcomes.

This sizing-on-consensus is happening in CLAUDE'S REASONING, not in code. Removing the consensus label from the prompt would break this loop.

## Layer 3: APEX Optimizer Clamps

Code: `src/apex/optimizer.py:_apply_constraints` (`:908-1047`).

1. **Static cap**: `max_position_size_usd = 1200.0` (settings.py:2131).
2. **Dynamic cap (off by default)**: `apex_size_cap_pct_of_equity = 0.0` (settings.py:2197) — when nonzero, effective cap = `max(static_cap, capital * pct / 100)`. Comment notes this is the J5 dynamic cap that was added but defaults to disabled.
3. **Conviction floor**: `apex_size_conviction_floor = 0.5` (settings.py:2198).
4. **Final**:
   ```
   _conviction_scale = max(_conviction_floor, trade.confidence)
   final_size = max(100.0, _post_cap * _conviction_scale)
   ```
5. **Hard floor**: 100 USD (optimizer.py:975).

So at APEX layer:
- If Claude proposed `size_usd = 800` with `confidence = 0.85`:
  - `_post_cap = min(800, 1200) = 800`
  - `_conviction_scale = max(0.5, 0.85) = 0.85`
  - `final_size = max(100, 800 × 0.85) = 680`
- If `confidence = 0.30`:
  - `_conviction_scale = max(0.5, 0.30) = 0.5`
  - `final_size = max(100, 800 × 0.5) = 400`

The conviction floor at 0.5 means a low-confidence proposal still keeps 50% of its proposed size.

Logged events: `APEX_SIZING_DECISION` (:977-986), `APEX_SIZING_CAP_HIT`, `APEX_SIZING_FLOOR_HIT` (:988-996).

## Layer 4: APEX Gate (7 Size-Modifying Checks)

Code: `src/apex/gate.py`. The gate cannot reject for size reasons; it can only downgrade. 7 sizing checks fire in order (each can mutate `trade["size_usd"]`):

| Check | Condition | Effect |
|---|---|---|
| 0 | `size_usd > _claude_original × gate_apex_size_cap_mult (1.5)` | cap to 1.5× of Claude's original |
| 1 | `size_usd > max_position_size_usd (1200)` | cap to 1200 |
| 3 | `open_count >= max_concurrent (5)` | size × 0.3 |
| 4 | conviction-weighted capital ceiling | size = min(size, `available × weighted_pct`) |
| 5 | duplicate position | size × 0.5 |
| 7 | `size_usd < min_size (50)` | floor to 50 |
| 12 | `apex_confidence < gate_confidence_floor (0.50)` | `size *= max(0.3, conf / 0.50)` |
| 13 | RR validation: rr==0 → ×0.25; 0<rr<0.5 → ×0.5 | size shrink |

Check 4 — the conviction-weighted capital ceiling — is the most complex and has the largest impact:

```python
# gate.py:131-258
weighted_pct = base_pct (0.4)
               × score_mult (0.80 to 1.20 by setup_score band)
               × xray_conf_mult (0.85 to 1.20 by xray confidence band)
               × rr_mult (0.90 to 1.15 by RR band)
               × profit_factor_weight (0.5 to 2.0 by PF band)
weighted_pct = clamp(weighted_pct, 0.05, 0.50)
size_cap = available × weighted_pct
```

Where:
- score_mult: `>=80 → 1.20, >=68 → 1.0, >=56 → 0.90, >0 → 0.80` (gate.py:200-207)
- xray_conf_mult: `>=0.85 → 1.20, >=0.70 → 1.0, >0 → 0.85` (`:217-222`)
- rr_mult: `>=3.0 → 1.15, >=1.5 → 1.0, >0 → 0.90` (`:226-231`)
- profit_factor_weight: `>3.0 → 2.0, >2.0 → 1.5, >1.0 → 1.0, >0.5 → 0.7, else 0.5` (`:595-604`)

This is the ONE place in the entry path where structural quality (setup_score), xray confidence, RR, and historical profit factor are MULTIPLIED into a size cap. But:
- `available` defaults to 1000 USD if account_state is missing (`:134`).
- The weighted_pct is clamped to [0.05, 0.50] — between 5% and 50% of available capital.
- A trade with all top-band multipliers: `0.4 × 1.20 × 1.20 × 1.15 × 2.0 = 1.32` → clamped to 0.50.
- A trade with all bottom-band: `0.4 × 0.80 × 0.85 × 0.90 × 0.5 = 0.122` → 12.2% of available.

So in practice, on $1000 available: top-band trades cap at $500, bottom-band at $122. The gap is large but the cap floor (50 USD) ensures even bottom-band trades have minimum size.

## Hardcoded Sizing Values

| Path | Value | Where | What it gates |
|---|---|---|---|
| ensemble.py | CONSENSUS_SIZE dict | :261 | base_size_mult |
| ensemble.py | conf_factor clamp [0.5, 1.0] | :291 | final_size_mult floor |
| strategist.py | "leverage 1-5x", "3-5x on testnet" | :105, :151 | Claude prompt |
| settings.py | max_position_size_usd 1200 | :2131 | APEX hard cap |
| settings.py | max_leverage 5 | :2132 | APEX leverage cap |
| settings.py | apex_size_conviction_floor 0.5 | :2198 | APEX conviction scale floor |
| settings.py | apex_size_cap_pct_of_equity 0.0 | :2197 | APEX dynamic cap (disabled) |
| settings.py | gate_apex_size_cap_mult 1.5 | :2161 | Gate Check 0 |
| settings.py | gate_confidence_floor 0.50 | :2156 | Gate Check 12 |
| gate.py | max_concurrent 5 | :114 | Gate Check 3 |
| gate.py | min_size 50.0 | :322 | Gate Check 7 floor |
| gate.py | available default 1000.0 | :134 | Gate Check 4 capital fallback |
| gate.py | base_pct 0.4 | :239 | Gate Check 4 base weighted_pct |
| gate.py | weighted_pct clamp [0.05, 0.50] | :244 | Gate Check 4 envelope |
| gate.py | score_mult bands | :200-207 | Gate Check 4 |
| gate.py | xray_conf_mult bands | :217-222 | Gate Check 4 |
| gate.py | rr_mult bands | :226-231 | Gate Check 4 |
| gate.py | profit_factor_weight bands | :595-604 | Gate Check 4 conviction weight |
| optimizer.py | size floor 100 | :975 | APEX hard size floor |

## Inputs To Sizing

What feeds final_size_mult / final size:
1. **consensus** (label) — flows ensemble → strategist prompt → Claude. Not multiplied in code.
2. **setup_type_confidence** — ensemble multiplies into final_size_mult (discarded). Gate Check 4 uses xray_conf_mult.
3. **conviction** — Claude proposes; APEX optimizer applies conviction_floor=0.5; Gate Check 12 scales below 0.50.
4. **regime** — DOES NOT directly feed sizing. Only indirectly via APEX Tier selection and APEX lock signal.
5. **capital / equity** — Gate Check 4 reads from `_account_state_getter` (fallback 1000); per-trade ceiling fed to strategist from `tiered_capital.get_limits`.
6. **profit_factor** (historical) — Gate Check 4 conviction_weight multiplier (0.5 to 2.0).
7. **RR (reward:risk)** — Gate Check 4 rr_mult + Gate Check 13 (rr==0 → ×0.25; <0.5 → ×0.5).
8. **score** (setup_score) — Gate Check 4 score_mult.
9. **open positions count** — Gate Check 3 (≥5 → ×0.3); Gate Check 5 (duplicate → ×0.5).

## Does Size Rise With Consensus Strength?

**YES via Claude's prompt reasoning, NO in code.** The Phase 3 verification confirmed:
- ≤4 supporting strategies: 107 trades, net +$300 (mean +$2.80).
- 5+ supporters (likely STRONG/GOOD consensus): negative or barely positive.

Claude sees the consensus label in the prompt and Sizes up. The code does not enforce this.

## Is There A Cap Or Inversion Above A Consensus Threshold?

**NO**. The ensemble's CONSENSUS_SIZE map is monotonic (1.0 / 0.75 / 0.50 / 0.30 / 0.15). There is no inversion or cap above WEAK / GOOD / STRONG.

The closest thing is:
- `single_strategy_max_share` cap (default 1.0 = disabled) on per-strategy contribution.
- The gate's Check 0 cap at 1.5× Claude's original size — but this caps APEX's growth of size, not the strategist's choice of size.

Neither is a "size-down on high-consensus" inversion.

## Summary

The system has 4 layers of sizing modulation:
1. Ensemble computes a size_multiplier — DISCARDED.
2. Claude picks raw `size_usd` from the per-trade ceiling — the actual driving choice.
3. APEX optimizer clamps via `_apply_constraints` (size_floor=100, max=1200, conviction_floor=0.5).
4. APEX gate's 7 size-modifying checks — most importantly Check 4's conviction-weighted capital ceiling.

The herding-vs-outcome inverse correlation (Phase 3 Claim 3) is enforced by Claude's prompt-reading, not by code. The system has no built-in mechanism to cap or invert size at high consensus.
