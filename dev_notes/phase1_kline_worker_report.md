# Phase 1 — KlineWorker Universe Integration

**Date:** 2026-04-26
**Engagement:** `IMPLEMENT_SEVEN_WORKERS_UNIVERSE_INTEGRATION_PROFESSIONAL.md`
**File:** `src/workers/kline_worker.py`

---

## Findings (from Phase 0)

Two HR violations against the brief's Hard Rules:

1. **HR-3 violation (silent stale retention).** `tick()` lines 97-105 (HEAD): if scanner returned `[]` or threw, the `if universe:` guard silently retained the previous `_tracked_symbols`. The brief explicitly forbids "use a stale snapshot."
2. **HR-1 violation (`_last_fetch` accumulates).** `_on_universe_change()` updated `_tracked_symbols` and backfilled `added` but did not prune `_last_fetch[f"{sym}:{tf}"]` or `_last_tick_per_symbol[sym]` for `removed` coins. Over time this dict grows unbounded as coins rotate.

## Changes Made

### A. `tick()` — three-reason-code empty-universe gate

Replaced lines 97-104 with the structure_worker pattern:

- `self._scanner is None` → `KLINE_UNIVERSE_EMPTY | reason=no_scanner_injected`, return.
- exception fetching universe → `KLINE_UNIVERSE_EMPTY | reason=scanner_error err=...`, return.
- empty universe → `KLINE_UNIVERSE_EMPTY | reason=scanner_returned_empty`, return.

The `default_symbols` initialization at `__init__` line 55 is now unreachable in `tick()` (the gate fires before iteration). Left in place as defensive non-empty default for any external reader that consults `_tracked_symbols` before first scanner update.

### B. `_on_universe_change()` — removed-coin cleanup

After `self._tracked_symbols = list(symbols)`, prune `_last_fetch` and `_last_tick_per_symbol` for every coin in `removed`:

```python
if removed:
    cleaned: list[str] = []
    for sym in removed:
        for timeframe in TIMEFRAME_SCHEDULE:
            self._last_fetch.pop(f"{sym}:{timeframe.value}", None)
        self._last_tick_per_symbol.pop(sym, None)
        cleaned.append(sym)
    log.info(f"KLINE_STATE_CLEANUP | removed={...} sample=[...] last_fetch_size=N | {ctx()}")
```

Steady-state `_last_fetch` size is now bounded at `len(active_universe) × len(TIMEFRAME_SCHEDULE)` ≈ 30 × 4 = 120 entries.

### C. Docstrings

Added universe-handling rationale to both `tick()` and `_on_universe_change()` so future readers know why the gate skips rather than falls back. Cross-references HR-1/HR-2/HR-3.

## What This Phase Does NOT Touch

- Circuit breaker (`_circuit_breaker_until`) — global by design, intentional, left untouched per brief.
- `KLINE_FETCH`, `KLINE_GAP`, `KLINE_CIRCUIT_BREAKER`, `KLINE_BACKFILL` log tags — unchanged.
- `KLINE_WRITE_LAG` post-write diagnostic (lines 175-225 in dirty state) — out of scope; preserved as-is.
- TIMEFRAME_SCHEDULE, fetch quality classifier — unchanged.

## Verification (static)

- `.venv/bin/python -c "from src.workers.kline_worker import KlineWorker"` → `IMPORT OK`
- `ast.parse` of file → `SYNTAX OK`
- `grep -n "default_symbols" src/workers/kline_worker.py` → only `__init__` line 55 (init default, unreachable in tick()); no functional fallback in tick() or `_on_universe_change`.
- `grep -n "KLINE_UNIVERSE_EMPTY\|KLINE_STATE_CLEANUP" src/workers/kline_worker.py` → both new tags present.

## Verification (runtime — covered by Phase 8 60-min observation)

The combined Phase 8 observation will validate:

- `KLINE_FETCH | quality=ok` for the 30-coin universe at the configured cadence (`market_data_interval`).
- 0 occurrences of `KLINE_UNIVERSE_EMPTY` after the first 30 s of startup.
- `KLINE_STATE_CLEANUP` fires on every rotation with non-empty `removed`.
- `_last_fetch` dict size at end of 60 min ≤ 1.5× steady-state expectation.

## Commit

`phase1: kline worker — last_fetch cleanup + empty-universe guard`
