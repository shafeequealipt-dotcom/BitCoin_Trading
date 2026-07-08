# J1 Phase 1 Step 1.1.4 — Zombie Reconciler Audit

Captured 2026-05-14 22:45 UTC. Read-only.

## Why The Operator Asked For This

The operator's plan-mode answer requested that J1's investigation include the zombie reconciler path because the four current stale rows have `trade_thesis` rows closed with `close_reason='zombie_reconciler'`. The hypothesis to test: does zombie_reconciler close theses too aggressively, producing brain-side blindness while Bybit still has the position open?

## Contract

`ThesisManager.reconcile_with_shadow(shadow_symbols: set[str]) -> int` at `src/core/thesis_manager.py:544-619`. Called every 300 seconds from `src/workers/position_watchdog.py:542-552`:

```python
_shadow_syms = {p.symbol for p in positions}
await self.thesis_manager.reconcile_with_shadow(_shadow_syms)
```

Where `positions` is the current tick's confirmed-live result from `get_positions_with_confirmation`. The name "shadow_symbols" is historical; it actually receives whatever mode's live truth the watchdog last fetched (Shadow, bybit_demo, or live).

Logic:
1. Read all open theses (`get_open_theses()` at line 560).
2. Compute `orphans = [t for t in open_theses if t.symbol not in shadow_symbols]`.
3. For each orphan, call `close_thesis(close_reason="zombie_reconciler", actual_pnl_usd=0.0, close_price=0.0, lesson="Orphan thesis closed by watchdog reconciler — no matching Shadow position...")`.
4. Emit `ZOMBIE_CLEANUP` per closure and `ZOMBIE_RECONCILE` summary at the end.

Important: **it never DELETES rows from `trade_thesis`** — only flips `status` from `'open'` to `'closed'` with pnl=0. This is by design: `trade_thesis` rows are cumulative learning data. The lesson field explicitly says "do not learn from this row."

## The Reverse Race (already fixed by `0a1d825`)

The reverse race: zombie_reconciler closes a thesis at pnl=0 just before the watchdog's `_detect_and_record_closes` fires the authoritative close. Pre-fix, `close_thesis`'s WHERE clause matched only `status='open'`, so the authoritative close found `status='closed'` and silently no-op'd, leaving the zombie row as durable truth.

Fix at `src/core/thesis_manager.py:333-336, 351-354`:

```sql
WHERE symbol = ? AND order_id = ?
  AND (status = 'open'
       OR (status = 'closed'
           AND actual_pnl_usd = 0
           AND close_reason = 'zombie_reconciler'))
```

The authoritative close can now overwrite the zombie close. Audit at the time found 36 historical rows with the zombie signature; the fix prevents new ones.

## The Forward Race (theoretical)

Could the zombie_reconciler close a thesis while Bybit still has the position open? In other words, does the 300s reconciler timer ever fire on a stale `shadow_symbols` snapshot?

### Order of operations inside the watchdog tick

`src/workers/position_watchdog.py`, in the `tick()` body:

```
Line 534:  positions = await self.position_service.get_positions()   # live, current
Line 542:  if _now - self._last_reconcile_at >= 300.0:
Line 547:      _shadow_syms = {p.symbol for p in positions}
Line 548:      await self.thesis_manager.reconcile_with_shadow(_shadow_syms)
Line 581:  await self._detect_and_record_closes(_live_open_symbols)
```

`positions` is fetched once and used for both the zombie reconciler input AND the close-detection input. The zombie reconciler's `_shadow_syms` IS the current live set — it cannot be stale relative to this tick. The reconciler fires at most once per 300s and uses freshly-fetched live data.

### When could the reconciler close a still-open position's thesis?

Only if the position is genuinely missing from Bybit's response at this tick:

1. **Bybit API transient hiccup.** `get_positions_with_confirmation` returns `PositionsQueryResult(confirmed=True, positions=())` for an error other than `ret_code=10002` (which sets `confirmed=False`). Per `bybit_demo_adapter.py:238-244`:

   ```python
   # Other adapter errors (rate-limit, auth, network) — preserve
   # the legacy contract that returns "confirmed empty" so
   # existing callers behave unchanged. The phantom-close
   # vulnerability for these paths is documented in
   # i1_phase1_investigation.md §"cluster sweep" for potential
   # follow-up.
   return PositionsQueryResult(confirmed=True)
   ```

   So rate-limit / auth / network errors return `confirmed=True, positions=()`. If this happens at a 300-second boundary, the zombie reconciler would see ALL open theses as orphans and close them all at pnl=0.

   This is acknowledged in the adapter comment as a known follow-up. It is the **forward race** scenario.

2. **Bybit-side position genuinely closed.** This is the correct path — the thesis SHOULD be closed.

3. **Pagination missing tail** (H3). If Bybit's response is paginated and the adapter only reads page 1, positions on later pages are missing. Then the reconciler closes their theses incorrectly. Today's volumes are well under the default limit=50, so this is theoretical.

### Audit-window evidence (2026-05-14 20:35-21:46)

- `ZOMBIE_RECONCILE` events: **0**
- `ZOMBIE_CLEANUP` events: **0**
- `ZOMBIE_RECONCILE_FAIL` events: **0**

The zombie reconciler did not fire during the audit window. The four current stale rows' `close_reason='zombie_reconciler'` timestamps are from 2026-05-13, not from the audit session.

The watchdog's `_last_reconcile_at` defaults to 0.0 on boot (per the standard pattern), so the first tick after a worker restart fires the reconciler if 300s have passed since the wall-clock zero (which is always true). This means the FIRST tick after every restart fires the reconciler with whatever the first `get_positions` call returns. If that first call is a transient empty due to startup-API-not-ready, it could close every open thesis.

### Likelihood of forward-race firing today

The `BYBIT_DEMO_POSITIONS_UNKNOWN_STATE` count during the audit window: **0**. The watchdog's `get_positions_with_confirmation` either confirms positions or correctly distinguishes UNKNOWN_STATE (the I1/F-26 fix). For a `confirmed=True, positions=()` response, the failure cases would have to be rate-limit, auth, or network — none of which appeared in the audit.

The forward race is logically possible but not observed. Adding a guard (Option F) would be defence-in-depth.

## Recommended Guard If We Choose To Address The Forward Race

Add a confirmation requirement: zombie_reconciler only runs if `positions` is non-empty OR a separate confirmed-empty signal is asserted. Concretely:

```python
# In position_watchdog.tick() before line 542
_can_reconcile = bool(positions) or self._last_positions_confirmed_empty_recently
if _can_reconcile and _now - self._last_reconcile_at >= 300.0:
    ...
```

Where `self._last_positions_confirmed_empty_recently` is set by `get_positions_with_confirmation` returning `confirmed=True, positions=()` for two consecutive ticks (dwell time). This avoids the single-empty-response phantom-close cascade while preserving correct behaviour when positions truly empty.

This is small, focused, and addresses Issue I1's documented follow-up.

## Connection To The Four Current Stale Rows

`positions` table now has:

```
SANDUSDT  Sell 11155.0 @ 0.08068  updated_at=2026-05-13T07:50
EGLDUSDT  Buy   42.0   @ 4.761    updated_at=2026-05-13T10:13
RUNEUSDT  Sell  2209.8 @ 0.6109   updated_at=2026-05-13T10:31
AAVEUSDT  Sell  9.04   @ 99.54    updated_at=2026-05-13T10:31
```

`trade_thesis` for the same symbols (latest row each):

```
AAVEUSDT  closed by zombie_reconciler  2026-05-13 17:31:14
SANDUSDT  closed by bybit_demo_sl_tp   2026-05-13 07:38:39
EGLDUSDT  closed by bybit_demo_sl_tp   2026-05-13 10:00:34
RUNEUSDT  closed by bybit_demo_sl_tp   2026-05-13 10:00:33
```

AAVE is the zombie-closed one. SAND/EGLD/RUNE are bybit_demo_sl_tp closures. The `positions` rows have `updated_at` AFTER the `closed_at` of the corresponding thesis (e.g., SAND thesis closed at 07:38:39 but positions row updated_at is 07:50). This means **the position was still being written to the cache by `get_positions_with_confirmation` after the thesis was closed** — the thesis-close did not trigger the cleanup callback (pre-c4eef5c mode-gating bug), and the position kept being upserted by the adapter until Bybit's API stopped returning it.

For SAND specifically: SL hit on Bybit at some moment between 07:38:39 (thesis close) and the next watchdog tick. The bybit_demo cleanup callback should have fired but silently skipped due to the mode-gating bug. After the SL filled on Bybit, get_positions stopped returning SAND. Watchdog's vanished-detection didn't run because there was no `coordinator.on_trade_closed` call (skipped by the same bug). The row stayed forever.

This is consistent with the c4eef5c root cause story. **The forward-race interpretation of zombie_reconciler being the cause is not what produced these rows.** The reverse-race interpretation (zombie closed thesis before authoritative close arrived) MAY have happened for AAVE but the result is the same — stale row in cache.

## Conclusion

The zombie reconciler is correctly designed as a safety net. The forward race is theoretically possible but not observed in the audit window. Adding the dwell-time confirmation guard is good defence-in-depth but is not the primary J1 fix. The four current stale rows are pre-c4eef5c residue, not zombie-reconciler-caused.

## Recommendation For Operator

- Leave the zombie reconciler unchanged for now.
- Optionally add the dwell-time confirmation guard (Option F) as a small, low-risk hardening commit AFTER the primary J1 fixes (backfill + reconciler enhancement) land and verify.
- Track the BYBIT_DEMO_POSITIONS_UNKNOWN_STATE rate over the J1 verification window; if it remains zero, the dwell-time guard is unnecessary in practice.
