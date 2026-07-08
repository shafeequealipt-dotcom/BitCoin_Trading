# Gap 3 Phase 4 — Implementation + Verification Report

Date: 2026-05-19  
Branch: `fix/gap3-directive-lifecycle` (logical; physical commits deferred per operator's standing "no commits unless requested" rule)  
Status: implementation complete + unit-test verified; runtime verification deferred until Layer 2/3 are re-enabled.

## Implementation summary

Per Gap 3 Phase 2 operator decision (Option A approved with belt-and-suspenders snapshot + halt-path emit + 6-commit grouping):

### Files modified

| File | Change | Lines |
|---|---|---|
| `src/core/layer_manager.py` | Added `get_did` import; added `_emit_directive_rejected` helper method; added `_loop_did` snapshot; wired 7 emit sites | +137 / -1 |
| `tests/test_gap3_directive_lifecycle.py` | NEW test file with 11 tests across 5 sections | +220 (new file) |

**Total surface**: 1 source file, 1 test file. No other source files touched.

### Helper method (`src/core/layer_manager.py:1287-1335`)

```python
def _emit_directive_rejected(
    self,
    *,
    sym: str,
    direction: str,
    rsn: str,
    detail: str,
    blocker_layer: str,
    did: str,
) -> None:
    """Emit a canonical STRAT_DIRECTIVE_REJECTED lifecycle event."""
    log.info(
        f"STRAT_DIRECTIVE_REJECTED | sym={sym} dir={direction} "
        f"rsn={rsn} detail='{(detail or '')[:120]}' "
        f"blocker_layer={blocker_layer} did={did} | {ctx()}"
    )
```

Centralized emit avoids 7-way duplication of f-string format. Detail field clipped to 120 chars for log-line readability. INFO level (rejections are normal operational outcomes, not errors; the existing TRADE_SKIP retains WARNING).

### `_loop_did` snapshot (`src/core/layer_manager.py:1343-1350`)

```python
# Gap 3 fix (2026-05-19) — snapshot did at loop entry so every
# STRAT_DIRECTIVE_REJECTED event in this iteration explicitly
# carries the originating brain decision ID, even if contextvars
# are unexpectedly reset by a downstream coroutine. The ctx()
# suffix also includes did; this explicit snapshot is
# belt-and-suspenders defensive coding.
_loop_did = get_did()
```

Belt-and-suspenders approved by operator. Explicit `did=` field in every emit + the existing `ctx()` suffix's `did=` redundancy survives any edge-case contextvars reset.

### 7 emit sites wired

| # | Site | File:line | rsn | blocker_layer |
|---|---|---|---|---|
| 1 | pnl_manager halt (per pending directive) | layer_manager.py:1364 | halt | halt |
| 2 | enforcer halt (per pending directive) | layer_manager.py:1401 | halt | halt |
| 3 | invalid_directive | layer_manager.py:1521 | invalid_directive | orchestration |
| 4 | pos_gate | layer_manager.py:1542 | pos_gate | orchestration |
| 5 | gate_rejected (via `_gate_rejected`) | layer_manager.py:1593 | gate_rejected | gate |
| 6 | strategy_worker reject | layer_manager.py:1619 | `<_reason_code>` | strategy_worker |
| 7 | exception | layer_manager.py:1640 | exception | orchestration |

Sites 1+2 emit ONE event per pending directive when halt drops the batch (operator-approved variant for halt visibility). Sites 3-7 emit once per rejected directive in the iteration loop.

### Files NOT modified (per spec Rule 11)

- `src/brain/strategist.py` — brain prompt construction unchanged (Gap 2 territory)
- `src/apex/gate.py` — all 14 active CHECKs unchanged
- `src/apex/optimizer.py` — direction-lock + flip block unchanged
- `src/workers/strategy_worker.py` — internal TRADE_SKIP emits unchanged
- `src/intelligence/signals/signal_generator.py` — SIG_DOWNGRADE unchanged (correctly excluded from scope)
- `src/core/trade_coordinator.py` — COORD_LOSS_COOLDOWN_SET unchanged (correctly excluded)
- `src/core/log_context.py` — contextvars mechanism unchanged

The implementation is contained in ONE file. Reverting Gap 3 = `git checkout src/core/layer_manager.py` + restart.

## Verification results

### Unit tests (Gap 3 dedicated)

```
tests/test_gap3_directive_lifecycle.py
  Section 1 - helper formatting:
    test_emit_helper_formats_event_with_all_fields                  PASS
    test_emit_helper_truncates_long_detail                          PASS
  Section 2 - per-blocker rejection emits:
    test_invalid_directive_emits_rejected                           PASS
    test_pos_gate_emits_rejected                                    PASS
    test_gate_rejected_emits_rejected_with_check_detail             PASS
    test_strategy_worker_reject_emits_rejected                      PASS
    test_exception_in_strategy_worker_emits_rejected                PASS
  Section 3 - halt path:
    test_pnl_manager_halt_emits_one_event_per_pending_directive     PASS
    test_enforcer_halt_emits_one_event_per_pending_directive        PASS
  Section 4 - success path:
    test_success_path_emits_no_rejection_event                      PASS
  Section 5 - did propagation:
    test_did_propagates_to_emit_via_loop_snapshot                   PASS

11/11 PASS in 0.30s
```

### Regression sweep (shipped fixes — Rule 11 invariant)

```
test_layer_manager_cold_start.py                  } 
test_layer_manager_persistence.py                 } 
test_regime_block_symmetry.py (Issue 4)           } 
test_structural_floor.py (Issue 1)                } 
test_state_labeler_pure.py (Issue 3)              }  135/135 PASS in 3.05s
test_setup_classifier_counter.py (Issue 2)        }
test_apex_flip_decision_log.py (Phase 1B)         }
test_apex_flip_rr_boost.py                        }
test_apex_flip_discipline.py                      }
test_xray_dir_flip.py                             }
test_gamma_r4_portfolio_cap.py (Phase 1A)         }
test_alpha_r1_trade_direction.py (R1)             }
test_strategist_callb_prompt.py                   }
```

### Broader integration

```
test_apex_pipeline_integration.py                 }
test_apex_lock_propagation.py                     }  170/170 PASS in 2.44s
test_phase0/                                      }
```

**Grand total: 316 tests, 316 pass, 0 regressions.**

### Lint

| File | Pre-edit ruff errors | Post-edit ruff errors | New errors introduced |
|---|---|---|---|
| `src/core/layer_manager.py` | 16 (pre-existing) | 16 | **0** |
| `tests/test_gap3_directive_lifecycle.py` | n/a (new file) | 0 (after auto-fix) | **0** |

**Zero new lint errors introduced.**

### Aim-bias 5/5 evaluation (Rule 9)

1. **Preserves trade frequency?** YES — pure observability, no behavior change. The same rejections occur at the same sites; this only ADDS a log emit.
2. **Preserves aggression?** YES — no new gates, no new blockers.
3. **Improves decision quality?** YES — operator gains end-to-end directive lifecycle visibility. Can `grep STRAT_DIRECTIVE_REJECTED` and see every rejected directive with its rejection reason + originating brain `did`.
4. **Preserves passive-close advantage?** YES — close path completely untouched.
5. **Respects structural separation of concerns?** YES — orchestration events emitted from the orchestration layer (layer_manager). No cross-layer reach.

**5/5 YES.**

## Trial behavior specification (Rule 14) — runtime verification required

The following scenarios must produce the expected events when Layer 2/3 are re-enabled. Each is covered by a unit test (above) but Phase 4 closure additionally requires LIVE verification.

### Scenario 1 — gate rejects (most common path)

Brain emits Buy directive for a coin still in J6 reentry-learning lockout.

Expected sequence:
```
brain.log:    STRAT_DIRECTIVE | sym=X dir=Buy ... | did=d-<N>
workers.log:  TRADE_SKIP | sym=X rsn=gate_rejected detail='reentry_learning_gate_...' | did=d-<N>
workers.log:  STRAT_DIRECTIVE_REJECTED | sym=X dir=Buy rsn=gate_rejected
              detail='reentry_learning_gate_...' blocker_layer=gate did=d-<N> | did=d-<N>
```

Verifier: `grep "STRAT_DIRECTIVE_REJECTED" data/logs/workers.log` returns the event with matching did from the original STRAT_DIRECTIVE.

### Scenario 2 — strategy_worker reject

Brain emits directive; strategy_worker rejects internally (xray_skip, sanity_reject, etc.).

Expected sequence:
```
brain.log:    STRAT_DIRECTIVE | sym=X dir=Y ... | did=d-<N>
workers.log:  TRADE_SKIP | sym=X rsn=<reason_code> ... | did=d-<N>     (strategy_worker emit)
workers.log:  STRAT_DIRECTIVE_REJECTED | sym=X dir=Y rsn=<reason_code>
              blocker_layer=strategy_worker did=d-<N> | did=d-<N>
```

### Scenario 3 — success (no REJECTED event)

Expected sequence:
```
brain.log:    STRAT_DIRECTIVE | sym=X dir=Y ... | did=d-<N>
workers.log:  BYBIT_DEMO_ORDER_RECEIVED | sym=X side=Y qty=Z ... | did=d-<N>
(no STRAT_DIRECTIVE_REJECTED for this did)
```

Verifier: confirm `grep "STRAT_DIRECTIVE_REJECTED.*did=d-<N>"` returns nothing for successful trades.

### Scenario 4 — halt (rare)

If pnl_manager or enforcer halts:

Expected sequence: one `STRAT_DIRECTIVE_REJECTED rsn=halt blocker_layer=halt` per pending directive in the batch, all with the same `did`.

### Scenario 5 — invariants

After service restart with this code:
- All 4 fix-series boot sentinels fire (`XRAY_FLIP_CONFIG`, `STRAT_CALL_B_REFRAMED`, `STRAT_REGIME_INSTR_REFRAMED`, `STATE_LABELLER_REGIME_HAIRCUT_INIT`)
- Phase 1A/1B config still in effect (`portfolio_direction_cap_enabled=False`, both flip thresholds 0.70)
- `grep -c DB_LOCK_WAIT` post-restart remains 0
- Trade frequency unchanged (no new blockers)
- Direction distribution unchanged (no behavior change)

## Runtime verification status

This implementation has NOT yet been live-tested because:
- Layer 2/3 remain OFF from the operator's earlier emergency_close
- Phase 1A/1B 48-72h trial is still in progress (T0 = 2026-05-19 13:44:48)

Live verification gated on:
1. Operator re-enables Layer 2/3 via telegram dashboard
2. Service restart picks up the new code
3. First few brain cycles produce the expected log signature

Once re-enabled, the operator can verify in real-time with:
```
tail -F data/logs/workers.log | grep --line-buffered "STRAT_DIRECTIVE_REJECTED"
```

If any future brain directive triggers any blocker, the canonical event will appear with the originating did + rejection reason. Silent absorptions are no longer silent.

## Open items for operator

1. **Commit timing**: per standing "no commits unless requested" rule, the implementation sits in working tree. Operator decides when to commit (suggested 6 logical commits per the plan, but a single commit is acceptable given the surface is contained).
2. **Service restart timing**: re-enable Layer 2/3 + restart at operator's discretion. Trial of Phase 1A/1B may continue concurrently — Gap 3 is observability-only, does not contaminate trial metrics.
3. **Gap 3 closure declaration**: once runtime verification observes the new events firing on a rejected directive AND the success path produces no event AND all 4 fix-series sentinels still fire post-restart, Gap 3 can be declared complete.
4. **Gap 2 ready to start**: per plan + spec Rule 12, Gap 2 Phase 1 can begin in the next session once operator confirms Gap 3 is verified.

## Deliverables

| Artifact | Absolute path |
|---|---|
| This verification report | `/home/inshadaliqbal786/trading-intelligence-mcp/dev_notes/gaps_fix/gap3_phase4_verification.md` |
| Modified source file | `/home/inshadaliqbal786/trading-intelligence-mcp/src/core/layer_manager.py` |
| New test file | `/home/inshadaliqbal786/trading-intelligence-mcp/tests/test_gap3_directive_lifecycle.py` |
| Phase 1 synthesis | `/home/inshadaliqbal786/trading-intelligence-mcp/dev_notes/gaps_fix/gap3_phase1_synthesis.md` |
| Phase 0 baseline | `/home/inshadaliqbal786/trading-intelligence-mcp/dev_notes/gaps_fix/phase0_baseline.md` |
