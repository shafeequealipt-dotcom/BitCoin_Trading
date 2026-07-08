# Phase 3 — AltDataWorker Universe Integration

**Date:** 2026-04-26
**Engagement:** `IMPLEMENT_SEVEN_WORKERS_UNIVERSE_INTEGRATION_PROFESSIONAL.md`
**File:** `src/workers/altdata_worker.py`

---

## Findings (from Phase 0)

Three HR violations:

1. **HR-1 init violation.** `__init__` line 51 (HEAD): `self.symbols = settings.bybit.default_symbols`. Per the brief: "No worker may maintain its own list of coins."
2. **HR-2 violation (no `_on_universe_change` method).** Manager's master callback dispatcher (`manager.py:912-923`) loops over all workers and calls the method if it exists; AltDataWorker simply was not on the rotation-notification graph at all.
3. **HR-3 silent retention.** `tick()` lines 56-63 silently kept stale `self.symbols` on empty universe or scanner exception (no log, no skip).

## Changes Made

### A. `__init__` — drop `default_symbols` initialization

`self.symbols: list[str] = []` (was `settings.bybit.default_symbols`). The new tick() gate guarantees the empty list is never iterated; reading 0 symbols rather than 5 default ones eliminates the chance of a single pre-scanner cycle fetching for the wrong list. Type-annotated for clarity.

### B. `tick()` — three-reason-code empty-universe gate

Replaced silent retention with the structure_worker pattern:

- `self._scanner is None` → `ALTDATA_UNIVERSE_EMPTY | reason=no_scanner_injected`, return.
- exception fetching universe → `ALTDATA_UNIVERSE_EMPTY | reason=scanner_error err=...`, return.
- empty universe → `ALTDATA_UNIVERSE_EMPTY | reason=scanner_returned_empty`, return.

### C. New `_on_universe_change()` method

AltDataWorker now appears in the rotation-notification graph. The method:

- Updates `self.symbols = list(symbols)` immediately so the next tick operates on the current universe (without waiting for the tick() refresh).
- Logs `ALTDATA_ADDED | coins=N sample=[...]` and `ALTDATA_REMOVED | coins=N sample=[...]`.
- No state pruning is needed: funding rates, open interest, and on-chain rows live in the DB; cleanup_worker owns retention there. AltDataWorker holds no per-coin in-memory state beyond `self.symbols`.

## Verification (static)

- `.venv/bin/python -c "from src.workers.altdata_worker import AltDataWorker"` → `OK`
- `ast.parse` of file → OK
- `grep -n "default_symbols" src/workers/altdata_worker.py` → only docstring reference; no functional fallback.
- `grep -n "ALTDATA_UNIVERSE_EMPTY\|ALTDATA_ADDED\|ALTDATA_REMOVED\|_on_universe_change"` → 3 + 1 + 1 + 1 occurrences as designed.

## Verification (runtime — covered by Phase 8 60-min observation)

- `ALTDATA | fg=X funding=30 oi=30` per `altdata_interval` cycle (~5 min).
- 0 occurrences of `ALTDATA_UNIVERSE_EMPTY` after the first 30 s of startup.
- `ALTDATA_ADDED` and `ALTDATA_REMOVED` fire on rotations (matching scanner's `Scanner universe UPDATED` lines).
- No CoinGecko 429 rate-limit errors.

## Commit

`phase3: altdata worker — empty-universe guard + universe-change handler`
