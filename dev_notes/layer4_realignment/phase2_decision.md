# Phase 2 — Time-Decay Phase 3 verification (decision + outcome)

Spec: `IMPLEMENT_LAYER4_REALIGNMENT_INDEPTH.md` Phase 2 + Issue 3 + Issue 6
Plan: `/home/inshadaliqbal786/.claude/plans/plan-mode-first-compeltely-breezy-ember.md`
Date: 2026-05-06
Parent commit: `04a8170` (Phase 1D)

## Decision: Branch A (Phase 3 working as designed → ship observability only)

### Phase 0 baseline evidence

Time window split at the worker restart boundary `2026-05-06 09:00:21 UTC`:

| Event | PRE-restart 24h (pre-Phase-3 code) | POST-restart (Phase 3 active) |
|---|---|---|
| `TIME_DECAY_FORCE_CLOSE` | 29 (audit's "Anomaly #12" evidence) | **0** |
| `TIME_DECAY_STRUCT_GUARD` | 0 (gate did not exist) | 0 |
| `TIME_DECAY_STRUCT_INVALIDATED` | 0 (gate did not exist) | 0 |
| `TIME_DECAY_AGE_GUARD` | 0 (Phase 1 not yet shipped) | 72 |
| `TIME_DECAY_MAE_GUARD` | 0 (Phase 2 not yet shipped) | 40 |
| `TIME_DECAY_ANCHOR_LOAD` | 0 (Phase 3 anchor not shipped) | 24 |
| `time_decay_p_win_low` close-reason in DB | 40 | 0 |

The audit's "29 force-closes despite Phase 2/3 emitting zero warnings" was based on
PRE-restart code (Phase 3 commit `c744e26` shipped 2026-05-06 07:49 UTC; live workers
restarted 09:00:21 UTC). After the restart, Phase 1 (age guard, 300 s) catches most
fresh losers before Phase 2 (MAE/SL ratio) is evaluated; Phase 2 catches another batch;
by the time a position passes both, `p_win` apparently does not fall below
`p_win_force_close` (0.15). The Phase 3 structural-invalidation gate is dormant in a
"no need to fire" sense, not a "broken wiring" sense.

The decision-tree thresholds from the approved plan:
- **Branch A (working as designed):** ≤ 5 force-closes/24h with paired
  `TIME_DECAY_STRUCT_INVALIDATED` evidence → observability only.
- **Branch B (wrongly permissive):** ≥ 10 force-closes/24h with weak evidence → tighten thresholds.
- **Branch C (inactive wiring):** zero `STRUCT_GUARD` AND zero `STRUCT_INVALIDATED` despite
  force-closes occurring → fix wiring.

Observed: 0 force-closes/24h. Branch A unambiguously satisfied.

### Action shipped

Single commit: `feat(time-decay/phase-3-trace): add full evidence trace to force-close events`.

Adds `TIME_DECAY_FORCE_CLOSE_TRACE` (severity WARNING) immediately before the
force-close emission in `src/risk/time_decay_sl.py:calculate()`. Logs:
- Symbol, `p_win`, `pnl_pct`, `mae_pct`
- `entry_xray_confidence`, `entry_setup_type`, `entry_regime_at_open`,
  `entry_regime_confidence` — anchor values from TradeState
- `struct_required` (from cfg) — whether the Phase 3 gate is active
- `struct_invalidation` (from caller) — whether the gate said "real evidence"
- `reason` — structured tokens: `xray_drop=...`, `setup_drift:A->B`,
  `regime_inv:trending_*@0.NN`, or `no_data:xray_cache_miss` / `stable`

### Why no code-logic change

The audit's Anomaly #12 framed Phase 3 as possibly-broken. Verification against the
post-restart log shows it is correctly dormant (Phase 1+2 filter upstream). The function
itself (`position_watchdog.py:858–968`) is strict and fail-safe by design:
- 5 missing-data branches all return `(False, "no_data:...")` → calculator blocks force-close.
- 3 evidence checks (XRAY drop ≥ 40 %, setup drift, regime inversion ≥ 60 % conf) are
  conjunction-free disjunction; any one returns `(True, evidence)`.
- Default-True path: NONE. Cache miss is fail-safe block.

### Verification gate

| Item | Status |
|---|---|
| Phase 0 baseline shows ≤ 5 force-closes/24h | PASS (0/24h) |
| `TIME_DECAY_FORCE_CLOSE_TRACE` emits before every force-close | PASS (smoke test) |
| Existing `TIME_DECAY_STRUCT_INVALIDATED` and `TIME_DECAY_FORCE_CLOSE` events preserved | PASS (no change to those emission sites) |
| No threshold change | PASS (Branch A required no tightening) |

Phase 2 = ship the TRACE observability event and document that Phase 3 is working as
designed. If a future trial period reveals weak evidence behind force-closes, the
TRACE event makes Branch B (tighten thresholds) executable in a single follow-up
commit; the diagnostic surface is in place.
