# HIGH-9 Phase 1+2 — Investigation and Operator Discussion

## Audit reference

HIGH-9 — Cross-symbol tid bleed (RENDERUSDT BYBIT_DEMO_SET_SL_FAIL logged with tid=t-ATOMUSDT-sniper).

## Phase 0 baseline + investigation findings

In the 2.85h audit window (sample of 2000 WARN/ERROR events), at least 8 distinct cross-symbol bleeds were observed (RENDERUSDT/ATOMUSDT 4x, OPUSDT/CRVUSDT 3x, ALICEUSDT/PLUMEUSDT 3x, plus others). All clustered around `-sniper` tids.

## Root cause traced

`src/workers/profit_sniper.py` has THREE per-symbol loops in `tick()`:

| Loop | Line | Has set_tid per iter? | Logs from this loop |
|---|---|---|---|
| Loop 1 (M3/M4 main) | 327-? | Yes (line 328) | M4_DECISION, M4_TRAIL_FLOOR, etc. |
| **Loop 2 (M5 Action Execution)** | **643** | **NO** | M4_TRAIL, SNIPER_CAP, SNIPER_TOO_CLOSE, M4_ACT_TIGHTEN, BYBIT_DEMO_SET_SL_FAIL (via _apply_trail_stop) |
| **Loop 3 (M7 lifecycle recording)** | **681** | **NO** | post-action recording logs |

When Loop 1 finishes its last iteration (e.g., KATUSDT), `_trade_id` ContextVar is left at `t-KATUSDT-sniper`. Loop 2 then iterates ALL tracked symbols (INJUSDT, MANAUSDT, RENDERUSDT, ...) without resetting the tid. Every log line emitted from Loop 2's body inherits `t-KATUSDT-sniper` — confirming the audit's observation.

Sample log evidence from 14:30:05.802-873 confirms:
```
14:30:05.802  M4_DECISION  sym=KATUSDT  tid=t-KATUSDT-sniper        (Loop 1 KATUSDT iter)
14:30:05.803  SNIPER_CAP   sym=INJUSDT  tid=t-KATUSDT-sniper        (Loop 2 INJUSDT iter — bleed!)
14:30:05.803  SL_GW_REJECT sym=INJUSDT  tid=t-KATUSDT-sniper        (Loop 2 — bleed continues)
14:30:05.803  SNIPER_CAP   sym=MANAUSDT tid=t-KATUSDT-sniper        (Loop 2 MANAUSDT iter — bleed!)
14:30:05.873  SL_GW_ACCEPT sym=MANAUSDT tid=t-KATUSDT-sniper        (Loop 2 — bleed)
```

## Same pattern in `src/workers/position_watchdog.py`

Four `for pos in positions` loops in `tick()`:

| Line | Purpose | Has set_tid per iter? |
|---|---|---|
| 526 | Data Lake snapshot per position | NO |
| 560 | Emergency close per position | NO |
| 593 | Duplicate position detection | NO |
| 618 | Main monitoring loop | Yes — but set_tid is at line 694, deep inside the body. Lines 619-693 inherit stale tid |

All four loops can leak/inherit stale tids. The audit's specific RENDERUSDT/ATOMUSDT case was tagged sniper, but watchdog loops have the same structural defect.

## Three options considered

### Option A — Per-loop set_tid only (minimum viable)

Add `set_tid(f"t-{sym}-...")` at the TOP of each affected loop body. No new abstractions.

Pros: smallest diff.
Cons: Easy to forget on a future loop; no automatic restoration of prior tid; verbose.

### Option B — Add `tid_scope` context manager + apply (recommended)

Add a `@contextmanager`-decorated helper `tid_scope(symbol: str, role: str = "")` in `log_context.py` that uses `ContextVar.set` with token-restore semantics. Apply to all affected loops.

Pros:
- Cleaner pattern (`with tid_scope(sym, "wd"): ...`)
- Automatic restoration of prior tid on exit (no leakage past the loop)
- Self-documenting (the `with` block scopes the tid visually)
- Future loops naturally use the same idiom

Cons:
- Adds ~15 lines to log_context.py
- Slightly more verbose at call site than bare `set_tid`

### Option C — Context manager + auto-clear at worker tick boundary

Add `tid_scope` plus add `set_tid("")` at the end of every worker tick.

Pros: maximum safety.
Cons: Sniper already does this at line 756. Watchdog already does this at line 735. Other workers may not — but adding it everywhere is a wider change than HIGH-9 strictly requires.

## Recommendation

**Option B.** Add `tid_scope` to log_context.py and apply to:

1. Sniper Loop 2 (line 643) and Loop 3 (line 681) — root cause for the audit's RENDERUSDT/ATOMUSDT case.
2. Watchdog Loops 526, 560, 593 — defense (per the prompt's "fix must be applied to all workers with the same pattern").
3. Watchdog Loop 618 — restructure: move the set_tid from line 694 deep inside to a `with tid_scope(...)` wrapping the loop body from line 619.

Existing per-iteration `set_tid` calls at sniper:328 and watchdog:694 could also be migrated to `tid_scope` for consistency, but that's an opportunistic cleanup — leave them as-is to keep diff focused.

## Implementation plan

Single atomic commit. Files modified:

1. `src/core/log_context.py` — add `tid_scope` context manager.
2. `src/workers/profit_sniper.py:643, 681` — wrap Loop 2 + Loop 3 bodies with `with tid_scope(symbol, "sniper"):`
3. `src/workers/position_watchdog.py:526, 560, 593, 618` — wrap the four loop bodies with `with tid_scope(pos.symbol, "wd"):`. Remove the now-redundant set_tid at line 694 (the `tid_scope` at the top of the loop covers it).
4. `tests/test_high9_tid_scope.py` — 5+ tests:
   - `tid_scope` sets and restores tid correctly
   - Nested scopes don't leak across iterations
   - tid is restored even on exception
   - Loop pattern: each iteration's logs see only its own tid
   - tid scope is async-safe (context propagates across await)

## Open questions

None blocking. The audit's "implications for past audit findings" (HIGH-9 invalidates prior tid attributions) is documented but does not require code action — past audits stay as historical records.
