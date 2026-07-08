# Issue 3 — Phase 1 Investigation Synthesis

## What the report said

`LIVE_PIPELINE_MONITOR_2026-05-10.md` (Bug #19, Bug #23):

> The profit_sniper iterates over its tracked positions (a dict).
> Concurrently, the trade_coordinator's close_broadcast modifies the
> same dict (removing closed positions). When iteration and
> modification race, Python raises `RuntimeError: dictionary changed
> size during iteration`. The sniper crashes. Observed: sniper
> crashed during cascade. Restart took 11 minutes.

## What current code shows

### Iteration sites in `src/workers/profit_sniper.py` (3,741 lines)

| Line | Iteration | Already protected? | Has awaits inside? |
|-----:|-----------|:------------------:|:------------------:|
| 282 | `tracked_symbols = set(self._tracked.keys())` (snapshot, then iterate over set difference) | YES (set snapshot) | n/a |
| 311 | `for s, d in self._tracked.items()` (logging only) | NO | NO (sync) |
| **327** | `for symbol, tracked in self._tracked.items()` (M3 model loop) | **NO** | **YES** (regime, profiler, sniper-log writes) |
| 649 | `for symbol, tracked in list(self._tracked.items())` | YES (`list()`) | YES |
| 689 | `for symbol, tracked in list(self._tracked.items())` | YES (`list()`) | YES |

Lines 649 and 689 are already snapshot-iterated, presumably from a prior fix. **Line 327 was missed.**

### Mutation sites for `_tracked`

```
Line 816: self._tracked[symbol] = {...}          # _on_position_opened (called from tick line 287)
Line 856: tracked = self._tracked.pop(symbol, None)  # _on_position_closed (called from tick line 291)
Line 3730: self._tracked.clear()                  # shutdown
```

Critically, the original report stated the mutator is a TradeCoordinator close-callback. **This is incorrect for current code** — `profit_sniper.py` does NOT register any close-callback with `TradeCoordinator`. All `_tracked` mutations happen synchronously inside `tick()` itself (lines 287 and 291), BEFORE the line-327 iteration begins.

So how does line 327 still crash? The likely vector:

1. Sniper enters `tick()`, modifies `_tracked` at lines 287/291 (synchronous, safe)
2. Sniper enters the line-327 loop
3. Body of the loop awaits `self._get_regime()` (line 409), `self.volatility_profiler.get_profile(symbol)` (line 424), or `self._write_sniper_log(...)` (line 606)
4. While yielded, another async task or thread-bridged coroutine indirectly causes `_tracked` to be mutated
5. Iteration resumes, raises RuntimeError

Candidate yield-time mutators:
- pybit WebSocket callback thread bridging via `asyncio.run_coroutine_threadsafe` — could schedule a coroutine that ends up calling `_on_position_closed`
- TradeCoordinator close-callbacks may eventually trigger position polling that updates state
- Watchdog or scanner running concurrently may modify shared state that sniper consults

The exact mutator is not necessary to identify. The **fix is the same regardless** — snapshot the iteration. This was the conclusion that produced the existing list() guards at lines 649 and 689.

### Crash log evidence (current `data/logs/workers.log`)

```
2026-05-10 17:25:39.767 | WARNING | src.workers.base_worker:start:385 | WORKER_TICK_FAIL
| name=profit_sniper tier=None err_type=RuntimeError
err='dictionary changed size during iteration' restart_count=1
| tid=t-XRPUSDT-sniper
```

The `tid=t-XRPUSDT-sniper` confirms the crash happens INSIDE the line-327 loop body (the only place the sniper sets a per-symbol tid). Prior known crash: 2026-05-09 15:38:43 (MONUSDT) per the report's evidence.

### Similar pattern in `src/core/trade_coordinator.py`

`get_status()` at line 935 iterates `self._trades.items()` synchronously (no awaits inside — currently safe). Defensive snapshot is cheap and protects against future refactors that introduce internal awaits.

`cleanup_stale()` at line 959 uses a list comprehension as a snapshot then pops separately — already safe.

## Recommended fix point

1. Wrap line 327's iteration with `list(self._tracked.items())`. Pattern matches lines 649, 689.
2. Wrap `TradeCoordinator.get_status` line 937 with `list(self._trades.items())`. Defensive.

## Estimated impact

- profit_sniper crash count: 1/2h → 0
- profit_sniper restart events: → 0
- No behavior change otherwise; snapshot is a single-pass `list()` over ≤ 8 keys (typical position count)
- No DB or schema changes
- Shadow mode unaffected
