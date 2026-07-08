# Phase 0 — Three-Gaps Fix Baseline

Captured: 2026-05-19 14:51 UTC, prior to Gap 3 Phase 1 investigation start.

## Service state

- `trading-workers`: active (since 13:44:55 restart per layer_state.json)
- `trading-mcp-sse`: active
- Time since Phase 1A/1B restart: ~1 h 6 min (T0 = 13:44:48 UTC)

## Layer state (operator-controlled)

From `data/layer_state.json` written at 2026-05-19T13:44:55+00:00:

```
{
  "layer_active": {"1": true, "2": false, "3": false},
  "user_stopped": false,
  "timestamp": "2026-05-19T13:44:55.355131+00:00"
}
```

**Layer 2 (Brain CALL_A/CALL_B) and Layer 3 (APEX execute) are OFF** from the operator's earlier emergency_close at 09:35:52 UTC. Layer 1 (data ingestion) is ON and heartbeating (133 WORKER_LIVENESS_HEARTBEAT events post-restart).

**Implication for Gap 3 Phase 1 investigation**: this is investigation-only (no behavior change). The current quiet runtime is acceptable. Phase 4 verification of Gap 3 will require the operator to re-enable Layer 2/3 to exercise live STRAT_DIRECTIVE_REJECTED events.

## Boot sentinels post-Phase-1 restart (13:44:48 UTC)

| Sentinel | Time | Origin |
|---|---|---|
| `XRAY_FLIP_CONFIG tp_min_distance_pct=0.50 min_touches_resistance=2 min_touches_symmetric=True` | 13:44:51.649 | structure_engine __init__:89 |
| `STRAT_CALL_B_REFRAMED system_prompt_version=2 contract=aggressive_management` | 13:44:53.832 | strategist __init__:617 |
| `STRAT_REGIME_INSTR_REFRAMED block_version=2 mode=symmetric_scenario` | 13:44:53.832 | strategist __init__:627 |
| `STATE_LABELLER_REGIME_HAIRCUT_INIT version=2 haircut=0.50 mode=soft_haircut` | 13:44:54.209 | scanner_worker __init__:104 |

All 4 fix-series sentinels confirmed firing. The 4-fix series is loaded and active.

## Phase 1A/1B config verification (live Settings.load round-trip)

```
portfolio_direction_cap_enabled       = False   (Phase 1A — cap disabled)
apex_min_flip_confidence_buy_to_sell  = 0.7     (Phase 1B — symmetric, was 0.95)
apex_min_flip_confidence_sell_to_buy  = 0.7     (Phase 1B — unchanged)
```

All Phase 1 changes in effect. config.toml has uncommitted modifications in working tree (the Phase 1A/1B edits), per operator's standing instruction not to commit unless requested.

## Baseline metrics (post-13:44:48 restart, current workers.log)

| Event | Count post-restart |
|---|---|
| STRAT_DIRECTIVE | 0 (Layer 2 OFF) |
| BYBIT_DEMO_ORDER_RECEIVED | 0 (Layer 3 OFF) |
| XRAY_DIR_FLIP | 0 |
| SIG_DOWNGRADE | 0 |
| COORD_LOSS_COOLDOWN_SET | 0 |
| invalid=True clamp activations | 0 |
| TRADE_SKIP | 0 |
| WORKER_LIVENESS_HEARTBEAT | 133 (Layer 1 healthy) |

These zero counts are expected with Layer 2/3 OFF. They establish the post-Phase-1 idle baseline.

## Trial-window evidence (in rotated log)

Phase 1A/1B trial T0 was 13:44:48. The 2-hour-9-min monitoring window (10:55-13:04 UTC) data lives in `data/logs/workers.2026-05-19_11-26-15_574407.log` (8.9 MB, sealed at 13:44 when rotation occurred). This is the audit-target for:

- Gap 3 directive lifecycle trace (10 trial batches, 16 BYBIT executions, 4 XRAY_DIR_FLIP events)
- Gap 1 clamp activation audit (~2 MNTUSDT activations at 11:34:30 and 12:02:13 per prior cross-check)
- Gap 2 MNTUSDT pattern verification (3 brain directives, all flipped)

## DB cascade check (Rule 13)

- Lifetime `DB_LOCK_WAIT` count in general.log: 1366
- Earliest: 2026-05-11 08:27:12
- Latest: 2026-05-16 06:56:32
- Post-Phase-1 restart (13:44:48 onwards): **0**
- `DB_MIGRATION_CASCADE` lifetime: 0

The historical DB_LOCK_WAIT events pre-date the J11 refactor. Zero events post-restart confirms J11 is working. Rule 13 invariant holds.

## Git state

```
HEAD: 2b0fa06 polish(dirbias/issue3): split STATE_LABELLER sentinel f-string for ruff E501
Working-tree modifications: config.toml only (Phase 1A/1B edits, not committed per operator instruction)
Untracked: tests/test_combined_real_pipeline_e2e.py (pre-existing scratch test, not part of three-gaps work)
```

No source code or test modifications pending. Phase 0 begins on a clean source/test tree.

## Plan-mode timeline corrections (carry into Gap 3 Phase 1)

The plan-mode Explore agents identified two discrepancies between the spec's claims (Part A.3 lines 91-94) and current code/logs. These will be re-verified in Gap 3 Phase 1 Step 3.1 but are noted here:

1. **Batch 12 HYPEUSDT (12:48:25)**: spec says "silently absorbed by cooldown"; actual cause was `reentry_learning_gate_same_conditions` (J6 gate fired at 12:48:41.055, did=d-1779194759952). The cooldown was active but the J6 gate intercepted first.

2. **Batch 14 HYPEUSDT (13:04:33)**: spec implies SIG_DOWNGRADE absorption; actual cause was `portfolio_direction_cap_Buy_80pct_aim_conditional` at 13:04:52.543. The portfolio cap WAS firing pre-Phase-1A; Phase 1A disabled it. This data point is from the trial window when cap was still active.

3. **Batch 13 HYPEUSDT (12:56:40)**: SIG_DOWNGRADE attribution claim is **unconfirmed** — no STRAT_DIRECTIVE for HYPEUSDT found at that exact time in brain.log. The 12:56:00 SIG_DOWNGRADE event has `no_ctx` and no traceable directive linkage. This validates Gap 3's underlying premise: SIG_DOWNGRADE events are orphaned from the directive lifecycle.

These corrections strengthen Gap 3's case, not weaken it. The exact rejection mechanism in each batch was a different blocker; the COMMON gap is the absence of a canonical `STRAT_DIRECTIVE_REJECTED` event tying any of them back to the originating directive.

## Phase 0 outcome

- All Phase 0 actions complete
- 0 anomalies detected
- 0 regressions vs Phase 1A/1B trial baseline
- Phase 1 invariants intact (cap disabled, flip thresholds symmetric, all 4 sentinels firing)
- Working tree clean on source/tests
- Trial window data preserved in rotated log file

**Gate satisfied. Gap 3 Phase 1 may begin.**
