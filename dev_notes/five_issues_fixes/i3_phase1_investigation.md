# I3 Phase 1 — F-28 WD_PNL_MISMATCH Investigation

**Status:** Phase 1 complete. Root cause confirmed. Fix shipped per
Option B (block-with-retry).

---

## TL;DR

`src/workers/position_watchdog.py:3463-3470` emits the
`WD_PNL_MISMATCH` ERROR diagnostic when `pnl_pct == 0 AND
entry_price > 0`, then **falls through unconditionally** to
`coordinator.on_trade_closed()` at L3470 with the corrupted values.
No guard, no return, no skip. TIAS / enforcer / capital tier learn
from `pnl=0` corrupted rows.

Two verified instances in audit window: ORCAUSDT 22:37:45 and
AEROUSDT 23:06:44. Both have `ent==ext` (entry_price == exit_price).
Both involve positions affected by I1 (TIMESTAMP_FAIL phantom closes
that fell back to ticker / last_tick_cache when authoritative price
was unreachable).

## Architectural ROOT cause

The integrity check (the `WD_PNL_MISMATCH` log line) is purely
advisory. It logs, then proceeds. The wider pattern: the watchdog
treats reconstructed close data as authoritative regardless of how
degraded the source was.

The price_source field already carries enough information to decide:
- `exchange_authoritative` / `bybit_ws_authoritative` /
  `shadow_authoritative`: trustworthy. entry==exit is genuine.
- `ticker_fallback` / `last_tick_cache` / `derived`: degraded.
  entry==exit means we lost the close price.

The check should differentiate.

## Fix (Option B — block + retry, recommended by prompt's Rule 3)

In `_detect_and_record_closes` after the mismatch detection:

1. If `pnl_pct == 0 AND entry_price > 0 AND price_source not in
   {exchange_authoritative, bybit_ws_authoritative, shadow_authoritative}`:
   - Emit `WD_PNL_MISMATCH_BLOCKED` WARNING
   - Increment `self._pnl_mismatch_retries[symbol]`
   - `continue` — skip `on_trade_closed` for this tick
2. Next watchdog tick re-runs `_detect_and_record_closes` for the
   same vanished symbol (because the position is still gone from
   exchange). On retry, Bybit's closed-pnl API may have indexed
   the close → `price_source` becomes authoritative → commit normally.
3. After `_PNL_MISMATCH_RETRY_LIMIT = 5` consecutive blocks (~50s),
   emit `WD_PNL_MISMATCH_FORCED` and commit anyway so the trade
   doesn't permanently stick.

This preserves the aggressive-exploitation philosophy: trades are
never permanently silenced. The block is a deferred-retry, not a drop.

## Forbidden options (per prompt Rule 3)

- Removing the integrity check (makes corruption invisible)
- Lowering the entry==exit threshold (hides real problems)
- Auto-correcting the corrupted values without understanding why
- Writing to a separate corrupted_rows table (doesn't solve the issue)

## Verification gate (Phase 4)

- 24+ hour soak after deploy
- `WD_PNL_MISMATCH` events still emit (visibility preserved)
- Corrupted pnl=0 rows do NOT enter trade_log unless WD_PNL_MISMATCH_FORCED
  fires (which is logged for audit)
- TIAS sees no pnl=0 corruption (cross-check trade_intelligence table)
- The expected I1+I3 interaction holds: post-I1 fewer TIMESTAMP_FAIL
  events → fewer degraded price_source → fewer I3 triggers
