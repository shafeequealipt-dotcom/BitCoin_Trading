# Phase 4 — PriceWorker Universe Integration

**Date:** 2026-04-26
**Engagement:** `IMPLEMENT_SEVEN_WORKERS_UNIVERSE_INTEGRATION_PROFESSIONAL.md`
**File:** `src/workers/price_worker.py`
**Risk profile:** Highest of the seven phases — WebSocket subscription state is real-time and visible to Bybit.

---

## Findings (from Phase 0)

Three HR violations:

1. **HR-1 init violation.** `__init__` line 43: `self._tracked_symbols = settings.bybit.default_symbols`. Not a runtime fallback (the runtime path is gated below) but it leaves "5 coins always" as the worker's notion of universe before scanner wires up.
2. **HR-1 partial (`_ws_quotes` keys retained).** `_ws_quotes: dict[str, tuple[float, float]]` had no rotation-out pruning. The 5-second read-side TTL filtered stale quotes on read, but the dict itself grew monotonically as coins rotated.
3. **HR-3 silent retention.** `tick()` lines 54-68 silently kept the prior `_tracked_symbols` (and the prior WS subscription state) on empty universe. No log warning, no skip.

## Changes Made

### A. `__init__` — drop `default_symbols` initialization

`self._tracked_symbols: list[str] = []` (was `settings.bybit.default_symbols`). Combined with the new tick() gate, PriceWorker will **not** subscribe to any symbols until scanner has wired up and produced a non-empty universe. This is the correct behavior under HR-3: don't connect to anything when there's nothing to subscribe to.

### B. `tick()` — three-reason-code empty-universe gate

Replaced the silent retention with the structure_worker pattern:

- `self._scanner is None` → `PRICE_UNIVERSE_EMPTY | reason=no_scanner_injected`, return.
- exception fetching universe → `PRICE_UNIVERSE_EMPTY | reason=scanner_error err=...`, return.
- empty universe → `PRICE_UNIVERSE_EMPTY | reason=scanner_returned_empty`, return.

Only when the universe is non-empty do we compare to `_tracked_symbols` and trigger a reconnect on change. The reconnect path (`subscribe_ticker(self._tracked_symbols, ...)` further down) sees the freshly-updated symbol list.

### C. `_on_universe_change()` — `_ws_quotes` cleanup

After updating `self._tracked_symbols`, prune `_ws_quotes` for every removed coin:

```python
if removed:
    pruned: list[str] = []
    for sym in removed:
        if self._ws_quotes.pop(sym, None) is not None:
            pruned.append(sym)
    if pruned:
        log.info(f"PRICE_UNSUB | coins={N} sample=[...] ws_quotes_size={M} | {ctx()}")
```

`pruned` is filtered to coins that actually had a quote in the dict (skips coins that were subscribed but never ticked). Steady-state `_ws_quotes` size is now bounded at `len(active_universe)` — typically ≤ 32.

### D. Reconnect path verified

Lines 90-95 (post-edit, around the `if not self._connected:` block) call `self.ws.subscribe_ticker(self._tracked_symbols, ...)`. Since `_tracked_symbols` is updated immediately above the reconnect trigger (line 79 in current file), every reconnect uses the freshly-fetched universe — no stale snapshot.

### pybit unsubscribe limitation

Bybit's pybit client exposes no unsubscribe primitive. The "drop subscription" mechanism is full WebSocket reconnect: setting `_connected = False` forces the next tick to call `connect_public()` and `subscribe_ticker(self._tracked_symbols)` fresh. Bybit's WS server forgets the old subscription set on disconnect. Verified: no code changes to the WS layer were necessary; the reconnect-on-rotation pattern already in place is the only mechanism and is correct.

## Verification (static)

- `.venv/bin/python -c "from src.workers.price_worker import PriceWorker"` → `OK`
- `ast.parse` of file → OK
- `grep -n "default_symbols" src/workers/price_worker.py` → only docstring reference; no functional fallback.
- `grep -n "PRICE_UNIVERSE_EMPTY\|PRICE_UNSUB"` → 3 + 1 occurrences as designed.
- `get_ws_quote()` (line 161-179) unchanged — read-side 5s TTL still in place as backstop for any quote staleness.

## Verification (runtime — covered by Phase 8 60-min observation)

- `PRICE_WS_CONN | symbols=30 sample=[...]` once at startup, once per universe change.
- 0 occurrences of `PRICE_UNIVERSE_EMPTY` after the first 30 s of startup.
- `PRICE_UNIVERSE_SYNC | added=A removed=R total=N` on every rotation; `PRICE_UNSUB` accompanies non-empty `removed`.
- `len(self._ws_quotes)` bounded at `len(active_universe)` ± transient delta during reconnect.
- No "subscribed to coin not in universe" drift visible in `PRICE_WS_CONN` sample lines.

## Commit

`phase4: price worker — empty-universe guard + ws_quotes cleanup on rotation`
