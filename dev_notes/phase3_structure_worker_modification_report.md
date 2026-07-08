# Phase 3 — structure_worker Uses active_universe Directly (Drops CoinDiscovery)

**Date:** 2026-04-26
**Restart 1 (with bug):** 00:57:21 UTC (PID 45029) — crashed after 5 ticks due to a missed `self._scan_full` reference.
**Restart 2 (with fix):** 01:05:14 UTC (PID 46265) — clean.
**Trial window:** 01:05:14 → 01:08:27 UTC (~3 minutes, 3 structure_worker ticks, full sweep + 1 partial).

---

## 1. Code Changes

### 1.1 `src/workers/structure_worker.py`

**Constructor** — removed `coin_discovery=None` kwarg + `self._coin_discovery`, `self._scan_full`, `self._coin_refresh_interval`, `self._universe_refreshed_at`. Kept `_full_universe`, `_batch_start`, `_batch_size` (still needed for batching the 32-coin universe at 25/tick).

**Class docstring** — updated to note Layer 1 universe alignment (Phase 3) and that ScannerWorker is now the SOLE universe source.

**`_get_universe()`** (was 40 lines of dual-mode logic, now 25 lines including comments):
```python
async def _get_universe(self) -> list[str]:
    if not self._scanner:
        log.warning(f"XRAY_UNIVERSE_EMPTY | reason=no_scanner_injected | {ctx()}")
        return []
    try:
        universe = await self._scanner.get_active_universe()
    except Exception as e:
        log.warning(f"XRAY_UNIVERSE_EMPTY | reason=scanner_error err={...} | {ctx()}")
        return []
    if not universe:
        log.warning(f"XRAY_UNIVERSE_EMPTY | reason=scanner_returned_empty | {ctx()}")
        return []
    self._full_universe = universe          # source of truth refreshes each tick
    batch = self._full_universe[self._batch_start:self._batch_start + self._batch_size]
    self._batch_start += self._batch_size
    if self._batch_start >= len(self._full_universe):
        self._batch_start = 0               # wrap around to start of universe
    return batch if batch else self._full_universe[:self._batch_size]
```

Key behavior:
- Always pulls fresh from `scanner.get_active_universe()` — no separate refresh interval.
- Preserves the rolling batch cursor (`_batch_start`) — does NOT reset to 0 each tick, so the sweep rotates correctly across multi-tick batches.
- New `XRAY_UNIVERSE_EMPTY` warning with explicit reason on three failure modes (no scanner injected, scanner exception, scanner returned empty).
- DOES NOT fall back to `settings.bybit.default_symbols` — HR-1 (the watch_list path is the single source of truth).

**`tick()` XRAY_TICK log line** — `batch_tag` rebuilt to use ceiling division for `_batches_total` (since 32 / 25 = 1 with integer division but actually needs 2 batches). Now reports "batch=N/2" correctly for a 32-coin universe.

### 1.2 `src/workers/manager.py`

**StructureWorker construction** at line 935-948 — removed `coin_discovery=self._services.get("coin_discovery"),` kwarg. Added comment noting that the `coin_discovery` service stays REGISTERED (Phase 6 cleans it up) so the registration is harmless.

---

## 2. Bug Found and Fixed Mid-Trial

**Symptom (Restart 1, 00:58:31 → 01:01:08):**
```
ERROR | src.workers.base_worker:start:109 |
Worker 'structure_worker' tick failed (5/5):
'StructureWorker' object has no attribute '_scan_full'
CRITICAL | src.workers.base_worker:start:116 |
Worker 'structure_worker' exceeded max restarts (5). Stopping permanently.
```

**Root cause:** I removed `self._scan_full = settings.structure.scan_full_market` from `__init__` and the explicit `_scan_full` branch from `_get_universe()`, but missed a third reference at line 140 in the `XRAY_TICK` log line:
```python
batch_tag = f"batch=..." if self._scan_full else ""
```

**Fix:** rewrote the `batch_tag` computation to always emit a batch label (Phase 3 batching is always active) and use ceiling division for the total-batches count:
```python
if self._full_universe:
    _batches_total = max((len(self._full_universe) + self._batch_size - 1) // max(self._batch_size, 1), 1)
    _batch_idx = self._batch_start // max(self._batch_size, 1)
    batch_tag = f"batch={_batch_idx}/{_batches_total}"
else:
    batch_tag = "batch=n/a"
```

**Lesson:** the project's CLAUDE.md is explicit — `Grep all usages across the entire file first`. Skipping that step caused this. Adding it explicitly to my mental checklist for Phase 4.

---

## 3. Trial Results — Live Workers Process (Restart 2)

### 3.1 XRAY_TICK lines (3 ticks since 01:05:14 restart)

```
01:06:16  XRAY_TICK | batch=1/2 symbols=25 analyzed=25 errors=0 cached=25
                     session=asian(early) setups=12 skips=13 el=12692ms
01:07:17  XRAY_TICK | batch=0/2 symbols=7  analyzed=7  errors=0 cached=32
                     session=asian(early) setups=12 skips=20 el=243ms
01:08:17  XRAY_TICK | batch=1/2 symbols=25 analyzed=25 errors=0 cached=32
                     session=asian(early) setups=12 skips=20 el=795ms
```

- **Universe size = 32 coins** (matches scanner's `_active_universe` after Phase 2: top 30 + 2 protected = 32).
- **Batching:** tick 1 takes batch [0:25] (25 coins), tick 2 takes [25:32] (7 coins) and wraps to 0, tick 3 takes [0:25] again. **2 ticks per full sweep ≈ 2 minutes** — exactly as the brief Section 10.5 predicted.
- **Cache** reaches 32 entries by tick 2 (was previously 134 with CoinDiscovery → 76% reduction).
- **Tick times:** 12,692 ms (cold start with cache=0 — every coin requires fresh shadow_reader + analysis), then 243 ms / 795 ms (warm cache). The cold-start spike is normal and self-resolves.
- **Errors = 0** in all ticks.

### 3.2 No XRAY_UNIVERSE_EMPTY warnings

```
$ grep "XRAY_UNIVERSE_EMPTY" workers.log | awk -v t=01:05:14 ...
0
```

The new warning tag has not fired in steady state. (It would fire if scanner returned empty — only expected during the brief window before scanner's first scan completes.)

### 3.3 No structure_worker errors (Restart 2)

```
$ awk -v t=01:05:14 ... | grep -E "structure_worker.*tick failed|WM_CRASH.*structure_worker|XRAY_TICK_ERR"
(empty)
```

Phase 3 fix is stable.

### 3.4 Test results

```
$ .venv/bin/pytest tests/test_universe_settings.py tests/test_scanner_filter.py -q
23 passed in 0.57s
```

Both Phase 1 + Phase 2 test suites continue to pass. No new tests added in Phase 3 (per the plan — behavior verified via live workers logs; existing structure_worker integration is preserved).

---

## 4. HR Compliance

- **HR-1:** ✓ structure_worker now reads scanner.get_active_universe() exclusively. CoinDiscovery is no longer consulted (still registered for Phase 6 deletion).
- **HR-2:** ✓ open-position coins (TRUMPUSDT, WCTUSDT in this trial) are included in scanner's active_universe (verified Phase 2), and structure_worker analyses them on every sweep.
- **HR-3:** ✓ This phase is one focused commit (3 files modified, no bundling).

---

## 5. Files Modified

- `src/workers/structure_worker.py` — constructor cleanup + new `_get_universe()` + fixed `batch_tag` log
- `src/workers/manager.py` — removed `coin_discovery=...` kwarg from StructureWorker construction (kept service registration for Phase 6 cleanup)

CoinDiscovery file (`src/analysis/structure/coin_discovery.py`) is **NOT deleted yet** — that's Phase 6.

---

## 6. Verification Gate (Phase 3 → Phase 4)

| Check | Status |
|---|---|
| `XRAY_TICK | batch=N/2 symbols=25` — full batch progresses | PASS (tick 1 batch=1/2, tick 3 batch=1/2) |
| Wrap-around to partial batch on tick 2 | PASS (tick 2 batch=0/2 symbols=7) |
| Full sweep ≈ 2 ticks | PASS (~2 minutes) |
| StructureCache size ~30 entries | PASS (cached=32 — 30 scored + 2 protected) |
| 0 XRAY_UNIVERSE_EMPTY warnings post-startup | PASS |
| 0 structure_worker errors (after fix) | PASS |
| `coin_discovery` no longer injected into StructureWorker | PASS (manager.py:942-948) |
| `coin_discovery.py` file untouched (Phase 6 will delete) | PASS |
| Existing tests still pass | PASS (23/23) |

**Verification gate PASSED. Proceeding to Phase 4 (Shadow CoinSelector reads watch_list).**
