# Profit-Fetching Exit System — Implementation Report

This report records the build of the profit-fetching exit system specified in
PROFIT_FETCHING_SYSTEM_MASTER_BLUEPRINT.md and planned in
IMPLEMENT_PROFIT_FETCHING_SYSTEM.md. It is the downstream (exit and
position-management) half of the system. Built 2026-05-29, direct to the main
branch, in seven gated phases, each an atomic and independently reversible
commit. No new branch and no new directory were created. Written for a screen
reader: headings and prose, no tables, no emoji.

## What was built, in one paragraph

A time-driven, whole-position exit engine now lives inside the existing
ProfitSniper (the five-second decision loop) and writes every stop through the
existing SLGateway (the tighten-only executor). A continuous time-decay master
dial turns the trade's age into a smooth slide of every value from loose when
young to tight when old. The trailing stop is anchored to the high-water mark
(a true Chandelier), sized by a time-driven ATR multiple, and can never silently
disappear when ATR reads zero. A stepped break-even ladder locks a rising
guaranteed-profit floor as profit climbs. A single, auditable highest-stop-wins
spine reconciles the ladder, the Chandelier, and the safety stop and writes one
stop per tick. The brain's per-trade deadline no longer force-closes a still
winning trade; it rides the tightened trail. A safety stop and naked-position
sweeper guarantee no position is ever without a stop. Everything is gated behind
one master switch and a handful of revertible sub-switches, all centralized in
config. When the master switch is off, the legacy behaviour is unchanged.

## The prerequisites, confirmed before building

The PnL-truth fix is in place: every force-close books the exchange's
authoritative net closedPnl, with a documented gross fallback only under indexer
lag. The over-tightening ratchet the blueprint warned about is not present as a
live runaway writer. A read-only log investigation across two real sessions
found only 208 actual stop writes in a 12-hour window and 377 in an 8.5-hour
window; the tens-of-thousands figure was throttled log noise (the M4_TRAIL_FLOOR
tag), not real stop moves. The dead methods that could have ratcheted have no
callers. So no separate over-tightening disable was needed, and the genuine
winner-clipping was the watchdog's close paths, which Phase 5 addresses.

## The blueprint's open questions, answered from the code

The existing sniper trail was already anchored to the high-water mark (a true
Chandelier in _compute_trail_stop), so technique four was refined, not created.
The ATR-zero hole's true cause was that the trail is only computed when live ATR
is above zero and the cache can silently return zero, so a profitable position
could lose all trailing protection. The SLGateway's max-step rule (a quarter of
a percent per write) would have blocked legitimate ladder steps, which is why the
ladder uses a dedicated, audited bypass source. The deadline is per-trade
(max_hold_minutes on the brain's TradePlan), so the time dial scales to each
trade's own window.

## Phase by phase — what each does and where it lives

### Phase 1 — Parameters and the time-dial engine

A new config section, profit_fetching, holds every tunable as a young-anchor and
old-anchor pair, plus the safety-stop distance and the fallbacks. A new
ProfitFetchingSettings dataclass is wired into Settings.load through a tolerant
builder. A new pure module, src/core/time_dial.py, slides every value smoothly
between its anchors as a simple proportional function of the trade's age over its
deadline. Nothing reads it yet; this is the foundation the later phases consume.
The master switch defaults on, per the operator's gate.

### Phase 2 — Hardened trail (techniques two and four)

When enabled, the ProfitSniper trail width is now set by the time-dialed ATR
multiple (three times ATR when young, one times ATR when old) instead of a static
value. The ATR-zero hole is fixed at the root: the effective ATR falls back from
the live value, to the ATR captured at entry, to a configured percent-of-price
floor, so the trail never silently vanishes. The trail stays anchored to the
high-water mark. The legacy per-coin volatility-class multiplier was dropped on
the enabled path because ATR already self-adjusts per coin and the extra factor
double-counted volatility. The disabled legacy path is byte-unchanged.

### Phase 3 — The stepped break-even ladder (technique one)

A pure computation, _compute_ladder_floor, locks a rising floor a fixed offset
behind each profit level crossed, driven by the high-water profit so the floor
only rises. All values come from the time dial, so steps are wider and locks
looser when young, tighter when old. A dedicated profit_sniper_ladder gateway
source was registered to clear the max-step cap for these legitimate locks; the
tighten-only, minimum-distance, and rate-limit rules still apply. The actual
per-tick write is done by the Phase 4 spine to keep one writer.

### Phase 4 — The highest-stop-wins spine

A single, auditable selection step (_pf_select_stop) chooses the tightest of the
ladder floor, the Chandelier trail, and the current stop — highest price for a
long, lowest for a short — and drops any candidate that does not beat the current
stop. _pf_apply_spine logs which candidate won (SNIPER_SPINE_SELECT) and writes
that one winner through the gateway each tick. The spine runs every tick, even
when the exploit-score action is hold, so a climbing winner's stop keeps
ratcheting. When enabled, the legacy score-driven trail writes are skipped so
there is exactly one stop-writer.

### Phase 5 — Ride the winner past the deadline, and subordinate the watchdog winner-cutters

This is the full reconciliation the operator chose. The PositionWatchdog
independently cut winners three ways; all three are now subordinated to the
sniper when enabled, each behind its own revertible switch. A still-profitable
trade at its deadline is no longer hard-closed; it rides the maximally-tightened
sniper trail and exits only on the defined give-back (this is self-limiting —
once it fades below profit, the deadline's non-climber tiers re-engage). The
plus-one-and-a-half-percent profit-take close is skipped. The watchdog's own
percentage trail (its activation, its stop pushes, and its trail-exit close) is
fully disabled so the sniper is the sole trailing-stop writer. The non-climber
backstops are untouched: the minus-three-percent hard stop, the loser timeout,
and the SENTINEL big-loss cut all still fire.

### Phase 6 — The safety stop and naked-position sweeper

The safety stop (a fixed loss cap a configured percent off entry) is now the
third candidate in the spine, so it is reconciled in the same single write. The
spine runs for every position every tick, so any position with no exchange stop
is always given a safety stop, filling the confirmed gap that nothing guaranteed
a position had a stop. The operator chose the stronger behaviour: the floor also
re-asserts (tighten-only) on a position whose existing stop is looser than the
cap, gated by safety_floor_reassert. For a climber the ladder or Chandelier sit
above the floor and win; for a non-climber the floor is the active loss cap. The
sweeper logs SNIPER_NAKED_POSITION_FIXED when it fixes a naked position.

### Phase 7 — Behavioural verification and sign-off

All six per-phase self-verification scripts pass. A broad regression of 283 tests
across the sniper, gateway, watchdog, time-decay, and SENTINEL subsystems passes.
The import and wiring smoke confirms the boot path is intact and the config
loads.

## Before and after, in plain prose

Before: a winning trade went green, climbed, and the system either left its
original stop in place or, at the deadline, hard-closed it; at plus one-and-a-half
percent past half its time it was force-closed; on a fifty-percent give-back the
watchdog closed it. Nothing rode a winner, and a position could run with no stop
at all if the exchange dropped it. After: as the trade climbs, the ladder locks a
rising guaranteed-profit floor and the Chandelier trail follows the peak at a
volatility-and-age-sized distance; the tighter of the two is always the active
stop. As the trade ages, every distance tightens smoothly. At the deadline the
trade is on its tightest leash but is not cut; it rides as long as it makes new
highs and exits only when it gives back the small tight distance. A trade that
never climbs is capped by the safety stop, and no position is ever left naked.

## Behavioural verification result

Every behaviour in the blueprint's trial section is demonstrated. The time dial
slides smoothly from loose to tight with age and saturates at the deadline. The
trail is anchored to the high-water mark, uses the time-driven distance, and
engages a non-zero fallback when ATR is zero. The ladder locks at the correct
offsets, the floor only rises, short mirrors long, and late steps are tighter.
The spine selects the highest candidate for longs and the lowest for shorts and
honours tighten-only. A still-profitable expired trade is not closed when enabled
and is closed when disabled, while a losing expired trade is still closed. The
safety floor attaches to a naked position, re-asserts on a looser stop, and never
loosens a tighter one.

## Honest limitation — profit-outcome verification is provisional

This build puts the structure in place and is behaviourally correct. It does not
by itself prove more profit. The profit-outcome verification (did round-trips
fall, did captured profit rise, measured in truthful after-cost money) is
deferred until a live trial under the master switch and is to be read against the
authoritative PnL. Every numeric value in the new config is a tuning starting
point, not a final number, and is to be tuned against that truthful measurement.

## Cross-cutting confirmations

Trade frequency, direction, and aggression are unchanged: no entry, direction, or
order-sizing code was touched — this is exit management only. The SLGateway's four
rules remain enforcing; the only change is two audited bypass sources for the
ladder and the safety floor, which bypass the max-step rule only. The
direction-flip switches remain off. No protected table was touched; the changes
add no database writes, and the ride-the-winner change produces fewer closes,
never more. Every piece is behind the master switch and is individually
revertible.

## Files modified

config.toml (new profit_fetching section); src/config/settings.py (new
ProfitFetchingSettings and wiring); src/core/time_dial.py (new); 
src/workers/profit_sniper.py (time-dialed trail, ATR-zero fallback, ladder, 
spine, safety floor); src/workers/sniper_models.py (new LadderResult); 
src/core/sl_gateway.py (two bypass sources); src/workers/position_watchdog.py 
(deadline ride and winner-cutter subordination); 
tests/test_t2_5_sl_gateway_breakeven.py and 
tests/test_sniper_partial_close_disabled.py (test alignments); and 
scripts/verify_profit_fetching_phase1.py through phase6.py (the self-verifications).

## How to operate

The master switch is config.toml profit_fetching.enabled. It is on; the system
goes live on the next restart of the trading-workers service. To run the legacy
behaviour, set it to false. Each reconciliation behaviour can be reverted on its
own with its switch (ride_winner_past_deadline, subordinate_profit_take,
subordinate_watchdog_trail_exit, safety_floor_reassert). Watch the logs for
PROFIT_FETCHING_CONFIG_LOADED and PF_WATCHDOG_RECONCILE at boot, then
SNIPER_SPINE_SELECT, SNIPER_ATR_FALLBACK, SNIPER_DEADLINE_RIDE, and
SNIPER_NAKED_POSITION_FIXED during operation.

## Independent cross-check and complete test (2026-05-29)

After the build, an independent adversarial cross-check was run: six reviewers
(one per phase plus a prompt-rule compliance critic and an integration/naming
auditor) read the spec and the live code looking for real defects, and the
complete test battery was run in full.

The complete test battery ran 3,621 tests passing, 8 skipped, and 2 failed. Both
failures are pre-existing and unrelated to this work: a brain-prompt assertion
(rsi_caution) and a stale migration test that pins the schema version to 32 when
it is now 40. Neither touches any file changed here.

The reviewers judged fifteen of the seventeen hard rules cleanly met and
confirmed the bulk of the integration as correct, and they found two real gaps,
both now fixed:

First, the single-writer invariant had a hole. Phase 5 disabled the watchdog's
CHECK 2/3 percentage trail, but a second autonomous watchdog winner-trail — the
"Smart trailing stop (via coordinator)" lock-peak and breakeven block — was left
active, so with the system enabled it would have raised stops alongside the
sniper spine. The fix hoists the shared gate and disables this block too, so all
three autonomous watchdog winner-trails are subordinated to the sniper.

Second, the naked-position sweeper did not protect the most dangerous case. A
position with no stop that had already moved past the entry-based safety floor
(the blueprint's "trade broke the opposite way" case) produced a stop on the
wrong side of price that the exchange rejects, leaving it naked — and the
naked-fixed log fired before the write, claiming a fix that never landed. The
fix clamps the safety stop to a valid just-inside-price emergency cap so a stop
actually attaches, moves the success log to after the exchange accepts it, and
adds a distinct failure log so a persistently-naked position is visible.

Two low and nit items were judged safe by design and left as-is: the ATR-zero
fallback only yields zero when there is no live price at all (in which case the
trail simply no-ops, keeping the existing stop, and the safety floor still
applies), and the peak-minutes value is a deliberately reserved, documented knob
for a future curve bend. The two fixes shipped as commits f56fa4d and ccc12d6;
all six self-verifications, 309 focused tests, and the full battery pass.
