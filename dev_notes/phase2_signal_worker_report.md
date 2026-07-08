# Phase 2 — SignalWorker Universe Integration

**Date:** 2026-04-26
**Engagement:** `IMPLEMENT_SEVEN_WORKERS_UNIVERSE_INTEGRATION_PROFESSIONAL.md`
**File:** `src/workers/signal_worker.py`

---

## Findings (from Phase 0)

One HR-3 violation, plus an HR-2 observability gap:

1. **HR-3 violation (default_symbols band-aid).** `tick()` HEAD lines 54-60 had two `default_symbols` fallbacks: one on exception fetching the universe, one when `_scanner is None`. The brief explicitly lists this exact pattern as a forbidden band-aid: *"if universe is empty, use default_symbols — this defeats the empty-universe rule"*.
2. **HR-2 partial.** `_on_universe_change()` only handled `added` (backfill), with no log line for `removed`. SignalWorker is stateless across rotations (no in-memory per-coin state) so cleanup is a no-op for state — but the observability gap meant operators had no visibility into rotation-outs from this worker's logs.

## Changes Made

### A. `tick()` — three-reason-code empty-universe gate

Replaced the dual `default_symbols` fallback with the structure_worker pattern:

- `self._scanner is None` → `SIGNAL_UNIVERSE_EMPTY | reason=no_scanner_injected`, return.
- exception fetching universe → `SIGNAL_UNIVERSE_EMPTY | reason=scanner_error err=...`, return.
- empty universe → `SIGNAL_UNIVERSE_EMPTY | reason=scanner_returned_empty`, return.

The two `symbols = self.settings.bybit.default_symbols` lines are GONE — the only remaining reference to `default_symbols` in this file is in the new docstring's explanation of why we removed it.

### B. `_on_universe_change()` — SIGNAL_REMOVED observability

Added the rotation-out log line:

```python
if removed:
    sample = ",".join(sorted(removed)[:10])
    log.info(f"SIGNAL_REMOVED | coins={len(removed)} sample=[{sample}] | {ctx()}")
```

No state to prune (SignalWorker is stateless); `signals` DB rows are retained as historical record (cleanup_worker owns DB retention).

### C. Docstrings

Added universe-handling rationale to `tick()` and updated `_on_universe_change()` docstring to explicitly document why no rotation-out cleanup is needed (HR-1: stateless wrapper).

## Verification (static)

- `.venv/bin/python -c "from src.workers.signal_worker import SignalWorker"` → `IMPORT OK`
- `ast.parse` of file → `SYNTAX OK`
- `grep -n "default_symbols" src/workers/signal_worker.py` → only docstring reference (line 56) explaining what was removed; **no functional fallback remains**.
- `grep -n "SIGNAL_UNIVERSE_EMPTY\|SIGNAL_REMOVED" src/workers/signal_worker.py` → 3 + 1 occurrences as designed.

## Verification (runtime — covered by Phase 8 60-min observation)

- `SIG_BATCH | n=30 coins=30` per `health_check_interval` cycle.
- 0 occurrences of `SIGNAL_UNIVERSE_EMPTY` after the first 30 s of startup.
- `SIGNAL_REMOVED` fires on every rotation with non-empty `removed`.
- `SIG_BATCH_STATS | conf_std > 0.05` — confidence distribution healthy (this is observability owned by the dirty-state addition, not part of this commit).

## Commit

`phase2: signal worker — drop default_symbols fallback, empty-universe guard`
