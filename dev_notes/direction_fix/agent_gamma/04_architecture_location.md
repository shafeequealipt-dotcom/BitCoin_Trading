# Agent GAMMA — Architecture Location (R4)

This document evaluates the three candidate insertion locations for the new portfolio direction concentration cap: Layer 4 Gate, Layer 3 APEX, and Layer 2 Brain prompt. Each is scored on cleanness, visibility, override capacity, aim alignment, soft-cap compatibility, and test reach. A recommendation is given with reasoning.

## Candidate 1 — Layer 4 Gate (`src/apex/gate.py`)

Add as a new CHECK 15 (or CHECK 13 per Phase 0's note — but CHECK 13 is currently R:R, so CHECK 15 is the correct slot, after CHECK 14).

Operating in CHECK 15 form, the cap runs AFTER APEX has completed optimization and after every other gate check has been applied. The trade dict at this point contains the final `direction`, `size_usd`, `leverage`, `take_profit_price`, `stop_loss_price`. The cap reads `TradeCoordinator._trades` via the existing `_services.get("trade_coordinator")` channel and computes pre-entry direction counts.

Cleanness versus other layers: HIGH. The gate is purpose-built for portfolio-level constraints. CHECK 3 (max concurrent positions, hard-coded 5) and CHECK 5 (duplicate symbol) already operate on portfolio state. CHECK 6 (cooldown) already uses `_services.get("trade_coordinator")` to consult coordinator memory. Adding CHECK 15 follows established pattern; no new architectural surface introduced.

Visibility (brain visibility, ops visibility):
- Brain visibility: NO directly. The brain produces a CALL_A directive, which goes APEX -> gate -> Shadow. The brain learns of CHECK 15 rejection ONLY by seeing fewer of its directives executed (or by reading the next CALL_A prompt's "blocked symbols" context, if that exists)
- Ops visibility: YES. CHECK 15 emits `PORTFOLIO_CAP_HIT` at WARNING level; visible in `data/logs/workers.log`. `GATE_REJECT` line includes the reason string `portfolio_direction_cap_{dir}_{pct}`. Ops can grep for it.

Override capacity: MEDIUM. Two override pathways:
- Operator can set `portfolio_direction_cap_enabled=False` in settings to disable entirely
- Operator can set cap value to 100% to effectively disable
- No per-trade override (a strong contrarian signal cannot bypass the cap by itself; this is intentional — bypass logic would be a band-aid)

Aim alignment: HIGH. Matches the aim-bias evaluation in 03 — all five YES for Design A.

Soft-cap (Design B/D) compatibility: HIGH. CHECK 15 can size-down rather than block, following the same modification pattern as CHECK 5 (`size_halved`) and CHECK 6 (`size_halved_cooldown`).

Test reach: HIGH. Existing test files `tests/test_apex_pipeline_integration.py`, `tests/test_apex_sell_bias_gates.py`, `tests/test_t3_1_safety_gates.py`, `tests/test_p6_layer3_gate_bybit_demo.py` all exercise TradeGate end-to-end and can add CHECK 15 coverage with minimal scaffolding. The `mock_services` fixture pattern is reusable.

Trade-offs:
- Pros: respects architecture, leverages existing patterns, easy to test, easy to disable, clear observability
- Cons: brain has no visibility into the cap until next cycle (rejection feedback is one-cycle delayed; the operator's note in FINDINGS.md:383 about adding gate-rejection feedback to the brain prompt is a separate operator request that R4 should not block on)

## Candidate 2 — Layer 3 APEX (`src/apex/optimizer.py`)

Add after APEX optimization completes but before the gate runs. Two sub-options:

- 2a: Inside `optimizer.optimize()`, post-DeepSeek decision, pre-validation. Inject "concentration check" as a new decision input
- 2b: As a new layer between APEX and gate, in `layer_manager` or `strategy_worker` between the `optimizer.optimize()` call and the `gate.validate()` call

Cleanness: LOW for 2a (APEX is the optimization layer; portfolio direction is admission, not optimization), MEDIUM for 2b (adds a new layer between L3 and L4 that doesn't exist today).

Visibility:
- Brain visibility: NO same as gate
- Ops visibility: depends on whether 2a or 2b. 2a hides the cap inside the APEX layer, harder to grep. 2b is greppable but unclear from name

Override capacity: low. APEX is the OPTIMIZATION layer; injecting admission logic into it would either complicate optimizer or require a parallel layer.

Aim alignment: MEDIUM. APEX's job is to optimize a trade's parameters; adding admission gating to it muddies the layer contract.

Soft-cap compatibility: HIGH if implemented as 2a (sizing already happens in optimizer). 2b would need to call gate-internal logic.

Test reach: MEDIUM. Existing APEX tests are extensive but tightly coupled to optimization decisions; testing admission gating in APEX would require new fixtures.

Trade-offs:
- Pros: closer to the source (APEX_DIR_LOCK is the proximate cause R4 is back-stopping)
- Cons: muddies the layer contract; not aligned with the project's structural separation

## Candidate 3 — Layer 2 Brain prompt (advisory text in CALL_A)

Inject portfolio direction concentration as a CONTEXT field in the CALL_A brain prompt. Example: "Current portfolio is 87% Sell (10 Sells, 1 Buy). Please consider direction balance when proposing."

This is advisory; the brain may ignore the context.

Cleanness: HIGH (uses existing prompt-context channel; no new code paths). Lowest impact on architecture.

Visibility:
- Brain visibility: YES (full visibility — the cap state appears in every prompt)
- Ops visibility: LOW. The brain's decision-making is opaque; the cap may or may not influence the output

Override capacity: 100% (the brain is allowed to ignore the context). This is also the design's biggest weakness.

Aim alignment: PARTIAL. Matches the operator's aim of "brain decides per-coin," but does not actually enforce the constraint.

Soft-cap compatibility: HIGH (just text). The brain reads the message and makes a softer judgment.

Test reach: LOW. Testing a prompt-context field is hard; the test would need to assert that the brain's decision changes when the context is included, which is brittle.

Trade-offs:
- Pros: zero risk of false rejection; brain has full discretion
- Cons: the brain has been observed producing 89% Sell directives in this session despite the data already supporting balance (XRAY suggested_direction is the upstream lever, not the brain's "ignorance of balance"). Adding a prompt context line is unlikely to change brain behavior unless other R1/R2/R3 fixes upstream of the brain are in place. The forbidden band-aid in spec Rule 3 explicitly lists "Adding concentration check ONLY at brain prompt (brain might ignore)" as forbidden

## Multi-location consideration: brain context PLUS gate

The brain-prompt context (option 3) is NOT exclusive with the gate check (option 1). Operator can implement both:
- Gate CHECK 15 enforces the hard limit
- Brain prompt context informs the brain of current concentration, so its CALL_A reasoning incorporates the cap state

This is a useful hybrid because the brain's awareness of the cap state may reduce same-direction proposals organically, reducing how often CHECK 15 has to fire.

But the operator's spec Rule 3 forbids "Adding concentration check ONLY at brain prompt" — meaning brain-prompt-only is forbidden, but brain-prompt-plus-gate is fine. The brain-prompt context is a NICE-TO-HAVE; the gate check is the actual fix.

## Summary table

| Location | Clean | Brain visibility | Ops visibility | Override | Aim align | Soft-cap | Test reach | Verdict |
|----------|-------|------------------|----------------|----------|-----------|----------|------------|---------|
| Gate (CHECK 15) | HIGH | NO | HIGH | MEDIUM | HIGH | HIGH | HIGH | RECOMMEND |
| APEX 2a | LOW | NO | MEDIUM | LOW | MEDIUM | HIGH | MEDIUM | AVOID |
| APEX 2b (new layer) | MEDIUM | NO | MEDIUM | LOW | MEDIUM | MEDIUM | MEDIUM | AVOID |
| Brain prompt | HIGH | YES | LOW | 100% | PARTIAL | HIGH | LOW | NOT-SUFFICIENT-ALONE |

## Recommendation

Place R4 as a new CHECK 15 in `src/apex/gate.py:670` (immediately before the final `return trade`). Pattern follows CHECK 6 (cooldown) for hard-reject mechanics and CHECK 3 (max concurrent positions) for the "consult position service" pattern. The trade_coordinator service is already wired and available via `_services.get("trade_coordinator")` (gate uses it at CHECK 6).

Optionally add prompt context to CALL_A as a Phase 2 enhancement (after R4 ships and is verified). The brain-prompt is "nice to have" — informs the brain but does not enforce. The gate check is "must have" — enforces.

Phase 0 reconnaissance flagged "CHECK 13" as the leading candidate position. This is incorrect — CHECK 13 is currently R:R validation (gate.py:613-630), CHECK 14 is TP/SL sanity (gate.py:632-647). The correct insertion point is CHECK 15, after CHECK 14, before the final `return trade` at line 672. This file consolidates the position to CHECK 15. Phase 0 line-numbered the gate without re-reading the current 14-check ordering; the gate has grown.

## Reasoning

Three criteria drive the choice:

1. Architecture alignment. Gate is the layer for portfolio-level admission constraints (max positions, capital, cooldown). R4 is a portfolio-level admission constraint. They belong together
2. Existing pattern reuse. CHECK 6 and CHECK 6b already use `_gate_rejected` for hard rejections; gate.py's pattern is well-tested and the test infrastructure exists
3. Aim-bias preservation. Layer 4 is downstream of brain decisions (R4 does not silence the brain) and upstream of execution (R4 does not change the close path). It surgically fits the operator's stated philosophy: "brain proposes; APEX optimizes; gate is the last safety check"

## Implementation skeleton (informational — operator approves design before code)

The skeleton would be a new block in `TradeGate.validate()` between line 647 and line 649:

- `# ═══ CHECK 15: Portfolio direction concentration cap (R4) ═══`
- Read direction counts from `trade_coordinator.get_direction_counts()` (new helper to add to coordinator)
- Compute `pre_pct` for the new direction
- If `pre_pct >= cap_pct` and same direction: set `_gate_rejected = "portfolio_direction_cap_{dir}_{pct}"`, log `PORTFOLIO_CAP_HIT`, append to modifications, return trade
- If `pre_pct >= warn_pct`: log `PORTFOLIO_CAP_WARN`, continue
- Else: log `PORTFOLIO_CONCENTRATION_CHECK` with verdict=pass, continue

Helper to add at `src/core/trade_coordinator.py:1869` (before `cleanup_stale`):

- `def get_direction_counts(self) -> dict[str, int]:`
- Returns `{"Buy": N_buy, "Sell": N_sell, "total": N_total}` from `self._trades`

The full implementation is GAMMA's Phase 3 work; this is documentation only.
