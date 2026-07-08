# HIGH-3 Phase 1+2 — Investigation and Operator Discussion

## Audit reference

HIGH-3 — `close_trigger` attribution still hardcoded to "exchange_match" in `get_last_close`.

## Phase 0 verification

5 references to "exchange_match" in current code:

| File:line | Type |
|---|---|
| `src/bybit_demo/bybit_demo_adapter.py:242` | **Hardcoded value in get_last_close return dict** (the audit-flagged defect) |
| `src/workers/position_watchdog.py:3107` | Comment |
| `src/workers/position_watchdog.py:3109` | Comment referencing the audit |
| `src/workers/position_watchdog.py:3111` | Hardcoded fallback `close_trigger = "exchange_match"  # default when unknown` (legitimate — applies when no other trigger known) |
| `src/workers/position_watchdog.py:3125` | Comment |

Adapter:242 is the bug. Watchdog:3111 is a legitimate fallback for the unknown-trigger case.

## Investigation

### Recent close_trigger work (commits 479ff5e, 637c524)

Git log shows two prior commits:
- `479ff5e feat(logging/lifecycle-phase-5-7/various): per-caller close_trigger= refinements + HMAC fail tag`
- `637c524 feat(logging/lifecycle-phase-7-8-10/various): close_trigger inference + lesson injection visibility + WD_POSITIONS_VANISHED`

These commits added the `close_trigger` param to `bybit_demo_adapter.close_position` (line 248-280). The param now propagates through to BYBIT_DEMO_POSITION_CLOSE log lines and get_last_close was supposed to receive it but was missed in those passes.

### Current data flow

```
Caller (sniper / watchdog / callb / time_decay / manual)
   ↓ close_trigger="sniper_p9" (or similar)
bybit_demo_adapter.close_position(symbol, close_trigger=...)
   ↓ logs BYBIT_DEMO_POSITION_CLOSE | close_trigger=... ✓
   ↓ places reduceOnly market order on Bybit
   ↓ Bybit fills it (matching engine)
   ↓ position becomes flat
   ↓ ... [some time later] ...
Watchdog poll detects flat position → calls coordinator.on_trade_closed
                                  → also calls position_service.get_last_close
get_last_close returns dict with close_trigger="exchange_match" (hardcoded — BUG)
   ↓ The watchdog's close-trigger inference code (position_watchdog.py:3107-3127)
      reads this dict's close_trigger and uses it for the close attribution.
   ↓ Result: original trigger ("sniper_p9") is LOST; everything looks like
      "exchange_match" downstream.
```

### Consumers of get_last_close's close_trigger

```
grep -rn "close_trigger" src/ --include="*.py" | grep -v test
```

The watchdog is the primary consumer. Per `position_watchdog.py:3107-3127`, the watchdog reads close_trigger from the get_last_close response and uses it for downstream attribution. Pre-fix, it always sees "exchange_match" — losing the caller's original reason.

### Design question — where to thread the trigger

The audit says: "thread close_trigger argument from close_position caller through to the dict that get_last_close returns." Two options:

Option A — `close_position` records the trigger in a per-symbol cache (TTL-bounded). `get_last_close` reads from the cache; falls back to "exchange_match" when no cache entry exists (= genuinely exchange-initiated close).

Option B — Caller passes close_trigger to get_last_close directly as a kwarg.

Option A is better because:
- The watchdog calls get_last_close as a polling mechanism — it doesn't know the original trigger.
- Only close_position knows the trigger AT TIME OF CALL. Stashing it then reading later matches the natural data flow.
- TTL prevents stale cache entries (a symbol re-opened later won't see a stale trigger from an old close).

Option B requires every get_last_close caller to know the trigger, which the watchdog doesn't (it's poll-driven, not trigger-driven).

## Three options considered

### Option A — Per-symbol close_trigger cache (recommended)

Add `_recent_close_triggers: dict[str, tuple[str, float]]` to BybitDemoPositionService. close_position stashes the trigger with a 60-second TTL expiry. get_last_close reads from cache and falls back to "exchange_match" if no entry.

Pros:
- Minimal blast radius (3 lines added to close_position, 5 lines added to get_last_close, 1 dict in __init__)
- Backwards compatible: old callers that didn't pass close_trigger still get the default "system_close" log + "exchange_match" cache fallback
- TTL-bounded so memory doesn't grow
- "exchange_match" remains the correct value for genuinely exchange-initiated closes (SL/TP hit on Bybit's side, manual UI close — these don't go through close_position so no cache entry)

Cons:
- Per-instance state (acceptable; service is singleton per WorkerManager boot)
- 60s TTL could be wrong if get_last_close polling takes longer than that — but watchdog polls within seconds of detecting a flat position

### Option B — Pass close_trigger into get_last_close as a kwarg

Caller threads the trigger through. Requires updating watchdog to pass it (and watchdog doesn't know the trigger — only close_position does).

Pros: explicit
Cons: doesn't fit the data flow; would require coupling watchdog to coordinator state

### Option C — Coordinator-level cache (per-symbol)

Move the trigger cache to the TradeCoordinator (which already has `_close_reasons` / similar caches per CRITICAL-1 phase 1 findings). Adapter's close_position would push the trigger into coordinator; get_last_close would query coordinator.

Pros: centralizes per-trade state in coordinator (consistent with TradeState pattern)
Cons: requires adapter to depend on coordinator (currently they're decoupled — adapter is below coordinator in the dependency graph)

## Recommendation

**Option A.** Smallest blast radius, fits the natural data flow, preserves all existing semantics.

## Implementation plan

Single atomic commit. Files modified:

1. `src/bybit_demo/bybit_demo_adapter.py`:
   - `BybitDemoPositionService.__init__`: initialize `self._recent_close_triggers: dict[str, tuple[str, float]] = {}`
   - `close_position` (line 281+): stash `self._recent_close_triggers[symbol] = (close_trigger, time.time() + 60.0)` after the BYBIT_DEMO_POSITION_CLOSE log line
   - `get_last_close:242`: replace hardcoded `"exchange_match"` with cache-lookup fallback chain
   - Add a small `_get_cached_close_trigger(symbol)` helper for clarity

2. `tests/test_high3_close_trigger_propagation.py`:
   - close_position stashes trigger in cache
   - get_last_close returns cached trigger when present
   - get_last_close falls back to "exchange_match" when no cache entry
   - Cache entries expire after TTL
   - "exchange_match" remains the value for genuinely exchange-initiated closes

## Open questions

None blocking. The 60s TTL is a sensible default; can be tuned later based on observed get_last_close latency.
