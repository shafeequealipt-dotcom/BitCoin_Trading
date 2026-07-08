# I2 Phase 1 — F-17 Orphan Positions Investigation

**Status:** Phase 1 complete. Root cause identified. **The Phase 0
optimistic mapping was wrong** — there IS an active leak path.

---

## TL;DR (root cause)

The `_positions_table_cleanup_on_close` callback at
`src/workers/manager.py:2198-2222` has THREE distinct gaps that cause
positions table rows to leak:

1. **Mode-gating silent return (PRIMARY).** L2209-2210 reads
   `_mode = transformer.current_mode` at close-time. If the
   transformer is not yet attached, mid-switch, or mode isn't
   exactly the string ``"bybit_demo"``, the callback silently
   returns — no log, no fallback. **Every close event during a
   non-bybit_demo moment leaks.**
2. **No success-side observability.** `_close_cb_done` only emits
   `CLOSE_CB_FAIL` on task failure (L1847-1851). Silent successful
   cleanups make it impossible to verify the callback actually ran.
3. **Fire-and-forget event-loop task.** L2212 uses
   `asyncio.get_event_loop().create_task(...)`. If the loop is closed
   (shutdown, SEGV recovery), the task never starts. The done-callback
   never fires either. Failure is invisible.

The audit's claim "ticker_fallback doesn't call delete_position" was
**partially correct**: ticker_fallback DOES go through
`on_trade_closed` which DOES fire the callback. But the callback's
own mode-gating logic silently swallows the cleanup. The bug isn't
where the audit said — it's one layer deeper.

Plus a fourth gap: the SEGV-recovery path
`position_watchdog.py:3304` (`WD_CLOSE_THESIS_RECOVERY`) reconstructs
trade_thesis state from DB but does NOT call delete_position. Any
position recovered via this path is destined to become an orphan.

---

## Evidence

### Phase 0 live DB query: 14 orphans, all `exchange_mode='bybit_demo'`

```
SELECT COUNT(*) FROM positions  →  14
SELECT exchange_mode, COUNT(*) FROM positions GROUP BY exchange_mode
→  bybit_demo: 14
```

### Cross-reference: which orphans had close events?

8 COORD_CLOSE_START events fired in audit window (22:10:24 - 23:18:41):
ALICEUSDT, PLUMEUSDT, DYDXUSDT, MONUSDT, AEROUSDT, ORCAUSDT,
ALGOUSDT, INJUSDT.

Of the 14 orphans, only 2 had close events in the audit window
(DYDXUSDT, MONUSDT). The other 12 (SANDUSDT, AXSUSDT, ADAUSDT,
ATOMUSDT, EGLDUSDT, RUNEUSDT, AAVEUSDT, MNTUSDT, HBARUSDT, SEIUSDT,
LTCUSDT, XRPUSDT) closed BEFORE the audit window (timestamps
07:50-12:22 same day).

### Where are the close events for the other 12?

The audit log starts at 21:53. The 12 orphans closed earlier in the
day. Their close events are in earlier rotated log files (`general.*`
or out of retention). We cannot directly inspect those events, but
the pattern is consistent: all 14 went through `coordinator.on_trade_closed`
yet the row persists.

### Cleanup callback emission frequency

```
grep -c "positions_cleanup\|POSITION_ROW_DELETED" workers.log → 0
grep -c "CLOSE_CB_FAIL" workers.log → 0
```

**Zero observable cleanup callback activity.** Either the callback
isn't firing OR it's firing silently. Both interpretations are bad.

### Mode-gating bug in detail

```python
# src/workers/manager.py:2198-2222
def _positions_table_cleanup_on_close(record: dict) -> None:
    sym = record.get("symbol", "")
    if not sym:
        return
    _xfm = self._services.get("transformer")
    _mode = ""
    if _xfm is not None:
        try:
            _mode = str(_xfm.current_mode or "")
        except Exception:
            _mode = ""
    if _mode != "bybit_demo":
        return                              # ← silent skip; no log
    try:
        _t = _pt_aio.get_event_loop().create_task(
            bd_trading_repo.delete_position(sym)
        )
        _t.add_done_callback(_close_cb_done("positions_cleanup", sym))
    except Exception as e:
        log.warning(
            f"CLOSE_CB_FAIL | cb=positions_cleanup sym={sym} "
            f"err='{str(e)[:150]}' | {ctx()}"
        )
```

Failure modes:
- **Transformer not yet attached at close-time** → `_xfm is None` →
  `_mode = ""` → silent skip
- **Mid-exchange-switch** → `current_mode` may briefly be
  non-bybit_demo → silent skip
- **Service container partially initialized after SEGV restart** →
  transformer.current_mode may be empty during early ticks → silent
  skip
- **`current_mode` returns a different casing or whitespace** →
  string-equality fails → silent skip

The record dict ALREADY contains `exchange_mode` (per the I4-cascade-
fix series at trade_coordinator.py the record carries the trade's
exchange_mode). The callback should read from the record, not from
the transformer.

### SEGV-recovery path is also blind

`src/workers/position_watchdog.py:3304` (`WD_CLOSE_THESIS_RECOVERY`)
fires for ETHUSDT, PLUMEUSDT, INJUSDT, DYDXUSDT at 22:44-22:52 after
the 22:42 SEGV. It reconstructs trade state from `trade_thesis` but
does NOT call delete_position. **Any position recovered through this
path leaves its positions-table row alive.** DYDXUSDT in the orphan
list at the same entry price as the WD_CLOSE_THESIS_RECOVERY event
(0.15001) is direct evidence.

---

## Architectural ROOT cause

The cleanup callback's correctness depends on a global service
attribute (`transformer.current_mode`) that wasn't designed as the
authority for per-trade routing. The trade's own `exchange_mode` —
captured at register_trade time and persisted in the close record —
IS the correct authority. The callback ignores it.

Beyond the mode-gating bug, the broader issue is:

- Cleanup runs as a fire-and-forget task with no SUCCESS emission
- The recovery path (WD_CLOSE_THESIS_RECOVERY) was added without
  including positions-table cleanup
- The 14 existing orphans accumulated invisibly because no
  observability surfaced the gap

---

## Fix options

### Option A — Read exchange_mode from the close record (NARROW)

Change `_positions_table_cleanup_on_close` to read
`record.get("exchange_mode", "")` instead of `transformer.current_mode`.
Add a structured `POSITION_ROW_DELETED` event on success and a
specific log for the mode-skip path so operators see when the gate
trips.

- Cost: 30 min code + 2 tests.
- Fixes the primary leak.
- Doesn't address SEGV-recovery path or success observability gaps.

### Option B — Option A + recovery-path cleanup (MEDIUM)

Option A plus:
- Extend `_detect_and_record_closes` recovery branch to call
  `delete_position` after `coordinator.on_trade_closed`
- Or simpler: add a `WD_CLOSE_DELETE_POSITION` emission and call
  `delete_position` from the watchdog directly when ticker_fallback
  detected the close

- Cost: 1 hour code + 3 tests.
- Closes the SEGV-recovery leak.

### Option C — Option B + one-shot backfill (RECOMMENDED)

Option B plus:
- One-shot backfill script `scripts/backfill_orphan_positions.py` that
  identifies orphans (positions table rows with no corresponding open
  trade in coordinator._trades or trade_thesis with status='open')
  and deletes them.
- Operator runs the script ONCE after deploy; not a cron.
- Script emits a structured log before each deletion so the cleanup
  is auditable.

- Cost: 2 hours code + 4 tests + 1 backfill script.
- Cleans the existing 14 orphans.

### Forbidden options (per Rule)

- Adding a cron-style sweeper (band-aid; doesn't fix source path)
- Disabling the ticker_fallback (removes fallback capability)
- Suppressing orphan symptoms in dashboard
- "Max age" timeout that auto-deletes (might delete legit positions)

---

## Recommendation

**Option C** — addresses the root cause at every layer:
- Root fix at the callback (read trade's mode, not global mode)
- Observability via POSITION_ROW_DELETED success emission
- Recovery-path closes the SEGV/restart leak
- One-shot backfill clears the legacy orphans

The new emissions per Rule 6 of the prompt:
- `WD_CLOSE_DELETE_POSITION` (watchdog-side cleanup when triggered)
- `POSITION_ROW_DELETED` (low-level confirmation from the callback)

---

## Verification gate (Phase 4)

- After deploy: orphan count = 0 within 1 hour
- New emissions visible on every close event
- Backfill script runs once, emits 14 deletion events
- WD_CLOSE_THESIS_RECOVERY events (if any during the verification
  window) trigger positions-table cleanup
- Sniper + watchdog no longer process phantoms (operator confirms
  via dashboard /positions matching Bybit /positions)
