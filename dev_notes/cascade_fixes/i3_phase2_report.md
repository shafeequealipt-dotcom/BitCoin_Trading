# Issue 3 — Phase 2 Operator Discussion Report

## Summary

The profit_sniper's main M3 model loop at `profit_sniper.py:327` iterates over a live `dict.items()` view. The body contains multiple awaits. While the sniper is yielded, another task can mutate `_tracked` (likely via a pybit WS thread bridging through `asyncio.run_coroutine_threadsafe`), causing `RuntimeError: dictionary changed size during iteration` when iteration resumes.

The fix is a one-line snapshot: `list(self._tracked.items())`. This pattern is already applied at lines 649 and 689 in the same file. Line 327 was missed.

A defensive companion fix is applied to `TradeCoordinator.get_status` at line 937 to protect against future regressions.

## Evidence

### Crash log (current `workers.log`, ~2h window)

```
2026-05-10 17:25:39.767 | WARNING | src.workers.base_worker:start:385 | WORKER_TICK_FAIL
| name=profit_sniper tier=None err_type=RuntimeError
err='dictionary changed size during iteration' restart_count=1
| tid=t-XRPUSDT-sniper
```

The `tid=t-XRPUSDT-sniper` tag is set at line 328, inside the line-327 loop. Prior known crash: MONUSDT 2026-05-09 15:38:43.

### Existing protection at lines 649, 689

```python
for symbol, tracked in list(self._tracked.items()):  # line 649
    with tid_scope(symbol, "sniper"):
        ...
```

Already snapshot-iterated by a prior fix. The pattern is established.

## Solution chosen

**Option A (snapshot iteration, recommended)**:

1. `src/workers/profit_sniper.py:327` — wrap with `list(...)`. Add explanatory comment referencing the empirical crash and the lines-649/689 precedent.
2. `src/core/trade_coordinator.py:937` — wrap `get_status` with `list(...)`. Defensive, no current bug.

## Trade-offs

### Pros
- Zero behavior change (snapshot is over ≤ 8 keys typically)
- Matches established convention in the same file
- Eliminates the crash-on-mutation failure mode
- Both fixes are easy to revert independently

### Cons
- Adds a tiny constant cost per tick (one list copy of ≤ 20 strings + dict refs)

### Risks
- None significant. The list copy completes in microseconds.

## Verification plan

After deploy:
1. `WORKER_TICK_FAIL | name=profit_sniper` count: should go to 0
2. profit_sniper `restart_count` stays 0 across multi-hour run
3. Sniper continues to function during high-throughput close events (e.g., simultaneous close of 3+ positions)
4. Shadow mode unaffected (file is mode-agnostic)

Tests:
- 4 tests pin: the live-view crash reproducer, the snapshot survival, source-level pin at profit_sniper.py:327, source-level pin at trade_coordinator.py:937
