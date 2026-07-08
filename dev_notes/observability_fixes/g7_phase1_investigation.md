# G7 Phase 1 — Investigation: COORD_UNREGISTER (no code change required)

## Headline

Audit claim: `COORD_UNREGISTER` 0/20 events — defect.

Reality: the lifecycle uses a **two-event pair**:
- `COORD_CLOSE_START` at `src/core/trade_coordinator.py:962` — fires
  before dict pops and callbacks
- `COORD_CLOSE_END` at `src/core/trade_coordinator.py:1006` — fires
  after callbacks and cooldown setup

In the audited window: 8 START / 8 END (the 20 / 8 delta reflects
positions still open at window end, not a leak).

## Field completeness check

Audit-required fields:

| Audit field | Current emission | Site |
|-------------|------------------|------|
| sym | `sym=BTCUSDT` | START + END |
| close_reason | `by=watchdog` | START + END |
| pnl_pct | `pnl=+0.42%` | START |
| pnl_usd | `pnl$=+0.84` | START |
| hold_duration_ms | `held=12s` | START (seconds, not ms — but the same information) |

All audit-required fields are present in COORD_CLOSE_START. The
two-event pair (START + END) carries MORE observability than a single
`_UNREGISTER` would — START gives the close inputs and callback count,
END gives the latency from pre-callback to post-callback plus the
applied cooldown.

## Decision: no code change

Per Phase 0 naming-convention analysis (986 unique src/ tags), the
`_UNREGISTER` suffix does not exist anywhere in the codebase. Renaming
COORD_CLOSE_START/END to COORD_UNREGISTER would:

1. Reduce observability (two events to one)
2. Break log consumers indexed on COORD_CLOSE_*
3. Diverge from the THESIS_CLOSE / COORD_CLOSE_START established pattern

This gap closes with the Phase 0 documentation update mapping audit
names → actual tags. No branch commit required for trade_coordinator.py.

## Cluster sweep findings (deferred to G12+)

`COORD_RECONCILE_RUN` and `COORD_RECONCILE_DRIFT` from Cluster D
appear not to exist (`grep` returns no matches in src/). Whether the
coordinator runs any reconciliation between its `_trades` dict and the
exchange-side position list is an open question — the audit's F-26
ground-truth divergence (system thought 2 open, Bybit had 5) suggests
reconciliation either does not happen or happens silently. Operator
may choose to escalate this to G12 work.

The audit's `COORD_REGISTER_FAIL` / `COORD_UNREGISTER_FAIL` are also
not implemented but `register_trade` never raises a recoverable error
in the current code (it overwrites state unconditionally), and
`on_trade_closed` has the COORD_DOUBLE_CLOSE WARNING for the only
known fail-case (race-condition double-close). Field is closed for now.

## Verification (Phase 4)

Operator-side: confirm 1:1 START:END pairing across a 24-hour run
during the final integration verification (Tier 5). The pre-existing
emission already guarantees this barring BaseException propagation
between the two emit sites — a separate vulnerability shared with G1
but with much lower exposure (on_trade_closed is sync and short).

If BaseException pairing becomes an issue, that becomes a G12+
follow-up (apply try/finally similar to G1 fix).
