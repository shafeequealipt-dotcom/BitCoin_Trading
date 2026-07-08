# Phase 5 — RegimeWorker Universe Integration

**Date:** 2026-04-26
**Engagement:** `IMPLEMENT_SEVEN_WORKERS_UNIVERSE_INTEGRATION_PROFESSIONAL.md`
**File:** `src/workers/regime_worker.py`
**Reads (no edits):** `src/strategies/regime.py` — confirmed `_confirmed_regimes` and `_pending_regime` are RegimeDetector instance attrs (lines 43, 44).

---

## Findings (from Phase 0)

Three HR violations:

1. **HR-1 violation (restore unfiltered).** `tick()` first-tick restore SQL (HEAD lines 50-58) restored every coin seen in the last 30 minutes regardless of current universe membership. After a restart, departed coins re-entered `_per_coin_regimes` and were never pruned (rotation-out happened pre-restart, no callback fires).
2. **HR-2 partial.** `_on_universe_change()` cleanly pruned `_per_coin_regimes` but left `_confirmed_regimes` and `_pending_regime` (hysteresis state) untouched. A coin that rotates out and back in would inherit its prior pending-vote count, short-circuiting the confirmation step.
3. **HR-3 partial.** When `coins_to_check` ended up empty (after filtering out the primary BTC symbol from an empty universe), the per-coin path silently skipped with no log.

## Changes Made

### A. `tick()` — fetch universe ONCE per tick

Moved `await self._scanner.get_active_universe()` to the top of `tick()` so the result is reused for both:

- the first-tick restore SQL filter (so it can scope to the current universe), and
- the per-coin detection branch (avoiding a second scanner round-trip per tick).

If the scanner is missing or throws, `universe = []` (with a `REGIME_UNIVERSE_FETCH_FAIL` log on exception). Empty universe → restore short-circuits, per-coin detection short-circuits with `REGIME_PERCOIN_EMPTY`. Global regime detection (BTC-only) still runs unconditionally — it does not depend on the universe.

### B. First-tick restore — universe filter

The SQL now has `AND symbol IN (?, ?, ...)` with `len(universe)` placeholders, applied to BOTH the outer `WHERE` and the inner subquery's `WHERE`. SQLite's `SQLITE_MAX_VARIABLE_NUMBER` default is 999; the universe is ≈ 30 symbols, well within bounds. Empty universe is handled explicitly (would otherwise produce `IN ()` syntax error):

```python
if not universe:
    log.info("REGIME_RESTORE_SKIP | reason=empty_universe | {ctx()}")
    rows = []
else:
    placeholders = ",".join("?" for _ in universe)
    rows = await self.db.fetch_all(SQL_WITH_FILTER, (*universe, *universe))
```

### C. `REGIME_PERCOIN_EMPTY` log

When `coins_to_check` is empty, emit a warning with reason code:

- `scanner_returned_empty` if the universe itself was empty.
- `no_coins_after_primary_filter` if the universe consisted solely of the primary BTC symbol.

### D. `_on_universe_change()` — full hysteresis cleanup

After the `added` backfill, prune all three RegimeDetector caches for `removed` coins:

```python
self.detector._per_coin_regimes.pop(sym, None)
if hasattr(self.detector, "_confirmed_regimes"):
    self.detector._confirmed_regimes.pop(sym, None)
if hasattr(self.detector, "_pending_regime"):
    self.detector._pending_regime.pop(sym, None)
```

`hasattr` guards make this resilient to future RegimeDetector refactors. Logs `REGIME_STATE_CLEANUP | removed=N sample=[...] per_coin_size=M`.

### E. DB cleanup (unchanged)

The periodic 24h-time-based cleanup at the end of tick() (every 100 ticks ≈ 16 hours) is preserved untouched. Per the brief: "correctness only, not retention policy."

## Verification (static)

- `.venv/bin/python -c "from src.workers.regime_worker import RegimeWorker"` → `OK`
- `ast.parse` of file → OK
- `grep -nE "REGIME_RESTORE|REGIME_PERCOIN_EMPTY|REGIME_STATE_CLEANUP|REGIME_UNIVERSE_FETCH_FAIL"` → all expected tags present.
- No edits to `src/strategies/regime.py` — confirmed RegimeDetector attribute names by read-only grep, only call into existing public/protected attrs.

## Verification (runtime — covered by Phase 8 60-min observation)

- `REGIME_RESTORE | loaded=N per-coin regimes from DB universe=M` on first tick after restart, with `N ≤ M` (filter is working).
- `REGIME_PERCOIN | detected=30 universe=30` per `regime.detection_interval_seconds` cycle (BTC excluded if `regime.primary_symbol="BTCUSDT"`).
- `REGIME_STATE_CLEANUP` fires on every rotation with non-empty `removed`; in-memory `_per_coin_regimes` size remains bounded.
- 0 entries in `_per_coin_regimes` for coins outside the current universe at any point (1-cycle delay acceptable on rotation tick).

## Commit

`phase5: regime worker — universe-filter restore + hysteresis cache cleanup`
